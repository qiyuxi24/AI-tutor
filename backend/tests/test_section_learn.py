"""小节「学习状态」（已懂 / 不懂 / 已读 / 出题通过）的存储层与 API 契约。

与 `SECTION_STATUS_*`（**生成**状态：pending/filled/failed）严格分开 ——
本文件锁的是"用户学到哪了"，两者生命周期不同：重跑生成会改 status，但绝不能碰 learn。

锁定的事：
  1. 存储形态：`manifest.sections[].learn = {mark, read, passed, attempts, mark_by, updated_at}`；
     老 manifest 没有该字段 → 读默认值、**不写盘**（零迁移）。
  2. 局部更新：传 `None` 的字段不动；`bump_attempts` 单独给 attempts +1；非法 mark → ValueError。
  3. 聚合：`section_learn_progress` 的计数与 `all_passed`（**没有小节 → 恒 False**）。
  4. 两套状态互不干扰：`write_section`（重写正文）不碰 learn。
  5. API：`PUT /knowledge/node/{id}/section/{sid}/learn`；**全部小节 passed → 节点掌握度 100**，
     并只在这个时候改掌握度（缺一节就不改）。
  6. `mark_by`（谁标的）：本端点恒写 `user`；AI 讲完自动记的 `ai` 走工具，不经过这里。

全离线：tmp_path 真实存储，不碰 LLM。
"""
import pytest
from fastapi.testclient import TestClient

from app.api.v1 import knowledge as knowledge_module
from app.core.auth import get_current_user
from app.core.knowledge_graph import (
    LEARN_MARK_BY_AI,
    LEARN_MARK_BY_USER,
    LEARN_MARK_CONFUSED,
    LEARN_MARK_UNDERSTOOD,
    MASTERY_ALL_SECTIONS_PASSED,
    KnowledgeGraph,
)

USER_ID = 7
URL = "/api/v1/knowledge/node/{nid}/section/{sid}/learn"


# ── 夹具 ────────────────────────────────────────────────────────

@pytest.fixture
def kg(tmp_path):
    g = KnowledgeGraph(user_id=USER_ID, data_dir=tmp_path)
    with g._conn:
        g._conn.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (?, 's', 'x')",
            (USER_ID,))
    g.create_node_with_content({"id": "n1", "name": "二重积分", "subject": "高等数学"}, "正文")
    yield g
    g.close()


def _seed_two_sections(kg) -> None:
    kg.create_section("n1", "定义", content="定义正文" * 50)
    kg.create_section("n1", "例题", content="例题正文" * 50)


# ── 存储层 ──────────────────────────────────────────────────────

def test_new_section_starts_unmarked(kg):
    """新建小节默认：未标记 / 未读 / 未通过 / 0 次作答。"""
    _seed_two_sections(kg)
    st = kg.get_section_learn("n1", "s01")
    assert st["mark"] == "unknown"
    assert st["read"] is False
    assert st["passed"] is False
    assert st["attempts"] == 0


def test_legacy_manifest_without_learn_reads_default(kg):
    """老 manifest（无 learn 键）读默认值，且**不写盘**（读操作不该产生副作用）。"""
    _seed_two_sections(kg)
    manifest = kg.read_manifest("n1")
    for s in manifest["sections"]:
        s.pop("learn", None)
    kg._write_manifest("n1", manifest)

    assert kg.get_section_learn("n1", "s01")["mark"] == "unknown"
    # 读之后再查一次原始 manifest：仍然没有 learn 键
    assert "learn" not in kg.read_manifest("n1")["sections"][0]


def test_partial_update_keeps_other_fields(kg):
    """只传 mark 时，read / passed / attempts 都不能被重置。"""
    _seed_two_sections(kg)
    kg.set_section_learn("n1", "s01", read=True)
    kg.set_section_learn("n1", "s01", mark=LEARN_MARK_UNDERSTOOD)
    st = kg.get_section_learn("n1", "s01")
    assert st["mark"] == LEARN_MARK_UNDERSTOOD
    assert st["read"] is True          # 没被 mark 更新冲掉
    assert st["passed"] is False


def test_bump_attempts_increments_once(kg):
    """`bump_attempts` 单独给 attempts +1（"答错再出一题"靠它计数）。"""
    _seed_two_sections(kg)
    kg.set_section_learn("n1", "s01", mark=LEARN_MARK_UNDERSTOOD, bump_attempts=True)
    kg.set_section_learn("n1", "s01", bump_attempts=True)
    assert kg.get_section_learn("n1", "s01")["attempts"] == 2


def test_invalid_mark_rejected(kg):
    _seed_two_sections(kg)
    with pytest.raises(ValueError):
        kg.set_section_learn("n1", "s01", mark="maybe")


def test_mark_by_records_origin_and_clears_on_unmark(kg):
    """来源随 mark 一起写；标记退回 unknown（取消标记）时来源一并清空，别留旧值。"""
    _seed_two_sections(kg)
    assert kg.get_section_learn("n1", "s01")["mark_by"] == ""     # 默认：不知道谁标的

    kg.set_section_learn("n1", "s01", mark=LEARN_MARK_CONFUSED, mark_by=LEARN_MARK_BY_AI)
    assert kg.get_section_learn("n1", "s01")["mark_by"] == LEARN_MARK_BY_AI

    kg.set_section_learn("n1", "s01", mark="unknown")
    st = kg.get_section_learn("n1", "s01")
    assert st["mark"] == "unknown" and st["mark_by"] == ""


def test_partial_update_does_not_touch_mark_by(kg):
    """只改 read / passed 时不能把来源冲掉（局部更新的通用契约）。"""
    _seed_two_sections(kg)
    kg.set_section_learn("n1", "s01", mark=LEARN_MARK_UNDERSTOOD, mark_by=LEARN_MARK_BY_USER)
    kg.set_section_learn("n1", "s01", read=True, passed=True)
    assert kg.get_section_learn("n1", "s01")["mark_by"] == LEARN_MARK_BY_USER


def test_invalid_mark_by_rejected(kg):
    _seed_two_sections(kg)
    with pytest.raises(ValueError):
        kg.set_section_learn("n1", "s01", mark=LEARN_MARK_UNDERSTOOD, mark_by="robot")


def test_missing_section_raises(kg):
    _seed_two_sections(kg)
    with pytest.raises(ValueError):
        kg.set_section_learn("n1", "s99", mark=LEARN_MARK_CONFUSED)
    with pytest.raises(ValueError):
        kg.get_section_learn("n1", "s99")


def test_progress_counts_and_all_passed(kg):
    """全部通过才算 all_passed；只通过一半 → False。"""
    _seed_two_sections(kg)
    kg.set_section_learn("n1", "s01", mark=LEARN_MARK_UNDERSTOOD, passed=True)
    p = kg.section_learn_progress("n1")
    assert (p["total"], p["passed"], p["all_passed"]) == (2, 1, False)
    assert p["mark_understood"] == 1

    kg.set_section_learn("n1", "s02", passed=True)
    p = kg.section_learn_progress("n1")
    assert (p["passed"], p["all_passed"]) == (2, True)


def test_progress_all_passed_false_without_sections(kg):
    """没有小节的节点（老节点）永远 all_passed=False —— 这套机制对它不适用。"""
    p = kg.section_learn_progress("n1")
    assert p["total"] == 0 and p["all_passed"] is False


def test_write_section_does_not_touch_learn(kg):
    """重写正文（生成管线动作）不得影响用户的学习痕迹。"""
    _seed_two_sections(kg)
    kg.set_section_learn("n1", "s01", mark=LEARN_MARK_CONFUSED, read=True)
    kg.write_section("n1", "s01", "重写后的正文")
    st = kg.get_section_learn("n1", "s01")
    assert st["mark"] == LEARN_MARK_CONFUSED and st["read"] is True


# ── API 层 ──────────────────────────────────────────────────────

@pytest.fixture
def client(kg, monkeypatch):
    """TestClient + 真实 KnowledgeGraph（临时目录）+ 固定登录态。"""
    from app.main import app
    monkeypatch.setattr(kg, "close", lambda: None)   # 端点 finally 会 close，测试里不真关
    monkeypatch.setattr(knowledge_module, "KnowledgeGraph", lambda **kw: kg)
    app.dependency_overrides[get_current_user] = lambda: USER_ID
    yield TestClient(app)
    app.dependency_overrides.pop(get_current_user, None)


def test_api_marks_and_reads_back(client, kg):
    _seed_two_sections(kg)
    r = client.put(URL.format(nid="n1", sid="s01"),
                   json={"mark": "understood", "read": True})
    assert r.status_code == 200
    body = r.json()
    assert body["learn"]["mark"] == "understood"
    assert body["learn"]["read"] is True
    assert body["progress"]["total"] == 2
    assert body["mastery"] == 0                       # 没通过，掌握度不动


def test_api_all_passed_sets_mastery_100(client, kg):
    """全部小节 passed → 节点掌握度置 100（用户口径：全通过 = 真掌握）。"""
    _seed_two_sections(kg)
    client.put(URL.format(nid="n1", sid="s01"), json={"passed": True})
    assert kg.get_node("n1")["mastery"] == 0          # 只差一节 → 不动

    r = client.put(URL.format(nid="n1", sid="s02"), json={"passed": True})
    assert r.status_code == 200
    assert r.json()["mastery"] == MASTERY_ALL_SECTIONS_PASSED
    assert r.json()["progress"]["all_passed"] is True
    assert kg.get_node("n1")["mastery"] == MASTERY_ALL_SECTIONS_PASSED


def test_api_rejects_bad_mark(client, kg):
    _seed_two_sections(kg)
    r = client.put(URL.format(nid="n1", sid="s01"), json={"mark": "maybe"})
    assert r.status_code == 400


def test_api_marks_origin_as_user(client, kg):
    """本端点 = **用户**入口 → mark_by 必须是 user（AI 讲完自动记走工具，不经过这里）。"""
    _seed_two_sections(kg)
    r = client.put(URL.format(nid="n1", sid="s01"), json={"mark": "understood"})
    assert r.status_code == 200
    assert r.json()["learn"]["mark_by"] == LEARN_MARK_BY_USER


def test_api_read_only_patch_keeps_mark_by(kg, client):
    """只勾「我已读完」时不该凭空写出一个 mark_by（mark 未变）。"""
    _seed_two_sections(kg)
    r = client.put(URL.format(nid="n1", sid="s01"), json={"read": True})
    assert r.json()["learn"]["mark_by"] == ""


def test_api_missing_section_404(client, kg):
    _seed_two_sections(kg)
    assert client.put(URL.format(nid="n1", sid="s99"), json={"read": True}).status_code == 404
    assert client.put("/api/v1/knowledge/node/ghost/section/s01/learn",
                      json={"read": True}).status_code == 404
