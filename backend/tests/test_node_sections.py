"""知识图谱「节点小节化」的存储层收口（manifest + 平行小节 MD）。

设计参照 `docs/知识图谱/知识图谱_节点小节化_设计与实现方案.md` §3 存储布局 / §5 读写收口。

本测试逐条锁定存储层不变量（全部落在 `KnowledgeGraph` 上，不碰生成管线/API/前端）：
- **布局**：`nodes/{user_id}/{node_id}/` 目录 = `manifest.json` + `{section_id}_{安全title}.md`；
  老节点仍是同名单文件（`{node_id}.md`），与小节文件夹天然共存（D5 不迁移）。
- **manifest 语义**：`name/subject` 取自节点行；`sections[]` 元数据不含正文；
  `status`：传了正文 → `filled`，没传 → `pending`；`write_section` 后置 `filled`。
- **原子写**：临时文件 + `os.replace`；manifest JSON 损坏时 `read_manifest` 返回 None 不抛。
- **section_id**：零填充两位（`s01`），取现有最大编号 +1。
- **级联删除**：`remove_node` / `remove_subject` 连小节文件夹一起删。
- **用户隔离**：小节文件按 `nodes/{user_id}/` 分目录，另一用户看不到。

全离线：不碰 LLM，全部用 tmp_path 临时库，不污染 `data/knowledge/`。
"""
import json
import sqlite3

import pytest

from app.core.knowledge_graph import (
    SECTION_STATUS_FILLED,
    SECTION_STATUS_PENDING,
    KnowledgeGraph,
    section_filename,
)


# ── 夹具与工具 ─────────────────────────────────────────────────
# 注意：nodes.id 是全局主键（非 per-user），建节点前先备 users 行。

@pytest.fixture
def kg(tmp_path):
    g = KnowledgeGraph(user_id=1, data_dir=tmp_path)
    with g._conn:
        g._conn.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (1, 's1', 'x')")
    yield g
    g.close()


def _add_user(data_dir, uid: int) -> KnowledgeGraph:
    g = KnowledgeGraph(user_id=uid, data_dir=data_dir)
    with g._conn:
        g._conn.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (?, ?, 'x')",
            (uid, f"u{uid}"))
    return g


def _seed_node(kg, node_id="n1", name="二重积分", subject="高等数学"):
    """建一个小节化前的普通节点（带正文，保持读取侧兼容）。"""
    kg.create_node_with_content(
        {"id": node_id, "name": name, "subject": subject}, "正文")


# ── 布局与探测：无 manifest = 老节点 ────────────────────────────

def test_plain_node_has_no_sections(kg):
    _seed_node(kg)
    assert kg.manifest_path("n1") == kg.nodes_dir / "n1" / "manifest.json"
    assert kg.has_sections("n1") is False
    assert kg.read_manifest("n1") is None
    assert kg.list_sections("n1") == []
    assert kg.read_section("n1", "s01") == ""


def test_missing_node_is_inert(kg):
    """节点不存在：读类方法全部安全返回空值。"""
    assert kg.read_manifest("ghost") is None
    assert kg.has_sections("ghost") is False
    assert kg.list_sections("ghost") == []
    assert kg.read_section("ghost", "s01") == ""
    assert kg.delete_section("ghost", "s01") is False


# ── create_section / list / read 往返 ──────────────────────────

def test_create_section_roundtrip_with_content(kg):
    """带正文建节 → filled；list 只给元数据；read 拿到正文。"""
    _seed_node(kg)

    sid = kg.create_section("n1", "定义与几何意义", kind="definition",
                            content="# 定义\n\n二重积分是……", brief="讲清几何意义")

    assert sid == "s01"
    sections = kg.list_sections("n1")
    assert len(sections) == 1
    meta = sections[0]
    assert meta["id"] == "s01"
    assert meta["title"] == "定义与几何意义"
    assert meta["kind"] == "definition"
    assert meta["status"] == SECTION_STATUS_FILLED
    assert meta["brief"] == "讲清几何意义"
    assert meta["origin"] == "section_gen"
    assert meta["file"] == "s01_定义与几何意义.md"
    assert "content" not in meta, "list 只返元数据，不含正文"
    assert "created_at" in meta and "updated_at" in meta

    assert kg.read_section("n1", "s01") == "# 定义\n\n二重积分是……"
    assert (kg.nodes_dir / "n1" / "s01_定义与几何意义.md").is_file()
    assert kg.has_sections("n1") is True


def test_create_section_without_content_is_pending(kg):
    """没传正文 → pending；文件占位为空；read 返回空串。"""
    _seed_node(kg)

    kg.create_section("n1", "计算方法")

    meta = kg.list_sections("n1")[0]
    assert meta["status"] == SECTION_STATUS_PENDING
    assert kg.read_section("n1", "s01") == ""


def test_section_ids_increment(kg):
    """section_id = s01 / s02 / s03（现有最大编号 +1）。"""
    _seed_node(kg)
    assert kg.create_section("n1", "一") == "s01"
    assert kg.create_section("n1", "二") == "s02"
    assert kg.create_section("n1", "三") == "s03"
    assert [s["id"] for s in kg.list_sections("n1")] == ["s01", "s02", "s03"]


def test_section_id_uses_max_after_delete(kg):
    """编号口径 = **现有**最大编号 +1：删掉 s02 后现有最大是 s01 → 下一个又是 s02。"""
    _seed_node(kg)
    kg.create_section("n1", "一")
    kg.create_section("n1", "二")
    assert kg.delete_section("n1", "s02") is True
    assert kg.create_section("n1", "三") == "s02"


def test_manifest_fields_match_node(kg):
    """manifest 头部字段取自节点行（name/subject/version）。"""
    _seed_node(kg)
    kg.create_section("n1", "一")
    m = kg.read_manifest("n1")
    assert m["node_id"] == "n1"
    assert m["name"] == "二重积分"
    assert m["subject"] == "高等数学"
    assert m["version"] == 1
    assert m["quizzes"] == []


# ── write_section / set_section_status ────────────────────────

def test_write_section_fills_and_refreshes_updated_at(kg):
    _seed_node(kg)
    kg.create_section("n1", "定义")  # pending
    before = kg.list_sections("n1")[0]["updated_at"]

    kg.write_section("n1", "s01", "# 定义\n\n正文")

    meta = kg.list_sections("n1")[0]
    assert meta["status"] == SECTION_STATUS_FILLED
    assert meta["updated_at"] >= before
    assert kg.read_section("n1", "s01") == "# 定义\n\n正文"


def test_set_section_status_to_failed(kg):
    """生成管线标 failed（单节失败可重试）。"""
    _seed_node(kg)
    kg.create_section("n1", "定义")

    kg.set_section_status("n1", "s01", "failed")

    assert kg.list_sections("n1")[0]["status"] == "failed"


def test_write_section_unknown_section_raises(kg):
    _seed_node(kg)
    kg.create_section("n1", "定义")
    with pytest.raises(ValueError):
        kg.write_section("n1", "s99", "x")


def test_create_section_unknown_node_raises(kg):
    with pytest.raises(ValueError):
        kg.create_section("ghost", "定义")


# ── delete_section ────────────────────────────────────────────

def test_delete_section_removes_file_and_entry(kg):
    _seed_node(kg)
    kg.create_section("n1", "一", content="A")
    kg.create_section("n1", "二", content="B")

    assert kg.delete_section("n1", "s01") is True

    assert [s["id"] for s in kg.list_sections("n1")] == ["s02"]
    assert not (kg.nodes_dir / "n1" / "s01_一.md").exists()
    assert (kg.nodes_dir / "n1" / "s02_二.md").exists()


def test_delete_section_unknown_returns_false(kg):
    _seed_node(kg)
    kg.create_section("n1", "一")
    assert kg.delete_section("n1", "s99") is False


# ── 原子写 / 损坏 manifest ─────────────────────────────────────

def test_read_manifest_returns_none_on_corrupt_json(kg):
    """manifest.json 损坏 → read_manifest 返回 None，不抛异常。"""
    _seed_node(kg)
    node_dir = kg.nodes_dir / "n1"
    node_dir.mkdir(parents=True, exist_ok=True)
    (node_dir / "manifest.json").write_text("{ 这不是合法 JSON", encoding="utf-8")

    assert kg.read_manifest("n1") is None
    assert kg.has_sections("n1") is False
    assert kg.list_sections("n1") == []


def test_create_section_recovers_from_corrupt_manifest(kg):
    """损坏的 manifest 不阻塞建节：按新 manifest 重来。"""
    _seed_node(kg)
    node_dir = kg.nodes_dir / "n1"
    node_dir.mkdir(parents=True, exist_ok=True)
    (node_dir / "manifest.json").write_text("坏", encoding="utf-8")

    assert kg.create_section("n1", "定义", content="正文") == "s01"
    assert kg.read_manifest("n1")["sections"][0]["id"] == "s01"


def test_manifest_written_as_readable_utf8_json(kg):
    """落盘为 UTF-8、ensure_ascii=False、indent=2 的可读 JSON。"""
    _seed_node(kg)
    kg.create_section("n1", "定义")

    raw = (kg.nodes_dir / "n1" / "manifest.json").read_text(encoding="utf-8")
    assert "二重积分" in raw, "中文不转义（ensure_ascii=False）"
    assert "\n  " in raw, "缩进格式（indent=2）"
    assert json.loads(raw)["sections"][0]["id"] == "s01"
    # 无残留临时文件
    leftovers = [p.name for p in (kg.nodes_dir / "n1").iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []


# ── 文件名安全化 ───────────────────────────────────────────────

def test_section_filename_sanitizes_unsafe_chars():
    assert section_filename("s01", "定义/几何:意义?") == "s01_定义几何意义.md"
    assert section_filename("s02", "  前后空格  ") == "s02_前后空格.md"
    assert section_filename("s03", "") == "s03.md"
    assert section_filename("s04", "   ") == "s04.md"


def test_create_section_with_empty_title_uses_bare_id(kg):
    _seed_node(kg)
    kg.create_section("n1", "")
    assert (kg.nodes_dir / "n1" / "s01.md").is_file()


# ── add_quiz_ref ──────────────────────────────────────────────

def test_add_quiz_ref_records_route(kg):
    _seed_node(kg)
    kg.create_section("n1", "例题", content="X")

    kg.add_quiz_ref("n1", "q-1", section_id="s01")
    kg.add_quiz_ref("n1", "q-2")  # 节点级（section_id 默认 ""）

    quizzes = kg.read_manifest("n1")["quizzes"]
    q1 = quizzes[0]
    assert q1["id"] == "q-1" and q1["section_id"] == "s01"
    assert q1["source"] == "quiz_store" and q1["created_at"]
    assert quizzes[1]["id"] == "q-2" and quizzes[1]["section_id"] == ""


# ── 级联删除：remove_node / remove_subject ─────────────────────

def test_remove_node_deletes_section_folder(kg):
    _seed_node(kg)
    kg.create_section("n1", "定义", content="A")
    assert (kg.nodes_dir / "n1" / "manifest.json").is_file()

    kg.remove_node("n1")

    assert not (kg.nodes_dir / "n1").exists(), "整删小节文件夹"
    assert not (kg.nodes_dir / "n1.md").exists()


def test_remove_node_keeps_plain_file_behavior(kg):
    """老节点（无文件夹）仍只删单文件，别误伤。"""
    _seed_node(kg)
    kg.remove_node("n1")
    assert not (kg.nodes_dir / "n1.md").exists()


def test_remove_subject_deletes_section_folders(kg):
    kg.create_node_with_content(
        {"id": "d1", "name": "数组", "subject": "数据结构"}, "数组正文")
    kg.create_node_with_content(
        {"id": "d2", "name": "链表", "subject": "数据结构"}, "链表正文")
    kg.create_node_with_content(
        {"id": "o1", "name": "进程", "subject": "操作系统"}, "进程正文")
    kg.create_section("d1", "定义", content="A")
    kg.create_section("d2", "定义", content="B")
    kg.create_section("o1", "定义", content="C")

    kg.remove_subject("数据结构")

    assert not (kg.nodes_dir / "d1").exists()
    assert not (kg.nodes_dir / "d2").exists()
    assert (kg.nodes_dir / "o1" / "manifest.json").is_file(), "别课的小节文件夹不能删"
    assert not (kg.nodes_dir / "d1.md").exists()


# ── 用户隔离 ──────────────────────────────────────────────────

def test_sections_isolated_per_user(tmp_path):
    """小节文件按 nodes/{user_id}/ 分目录：另一用户看不到、也建不了。"""
    g1 = _add_user(tmp_path, 1)
    g1.create_node_with_content({"id": "shared", "name": "递归", "subject": "算法"}, "正文")
    g1.create_section("shared", "定义", content="user1 的定义")
    g1.close()

    g2 = _add_user(tmp_path, 2)
    try:
        assert g2.list_sections("shared") == []
        assert g2.read_manifest("shared") is None
        assert g2.read_section("shared", "s01") == ""
        with pytest.raises(ValueError):
            g2.create_section("shared", "定义")  # 他人节点视同不存在
    finally:
        g2.close()

    g1 = KnowledgeGraph(user_id=1, data_dir=tmp_path)
    try:
        assert g1.read_section("shared", "s01") == "user1 的定义"
    finally:
        g1.close()


# ── 正文读取口径：小节优先（node_content_text / 预览）────────────
# 小节化节点的主 MD 只剩骨架占位（甚至没有主 MD，设计 D1）→ 任何"读正文"的调用方
# 都要走 `node_content_text`，否则图谱注入 / RAG / 出题 / 导出会集体失明。

def test_node_content_text_prefers_sections_over_stub_md(kg):
    _seed_node(kg)                                   # 主 MD 正文 = "正文"
    kg.create_section("n1", "定义", content="定义内容")
    kg.create_section("n1", "例题", content="例题内容")

    text = kg.node_content_text("n1")

    assert "定义内容" in text and "例题内容" in text
    assert "待完善" not in text, "主 MD 的骨架占位不得顶替小节正文"
    assert "定义内容" in kg.get_node_content_preview("n1", max_lines=200, max_chars=4000)


def test_node_content_text_falls_back_to_single_md(kg):
    """老节点（无小节）行为不变：仍读单 MD"""
    _seed_node(kg)
    assert "正文" in kg.node_content_text("n1")


def test_preview_cache_invalidated_by_new_section(kg):
    """新建小节后预览必须刷新（否则注入给模型的还是旧口径）"""
    _seed_node(kg)
    assert "定义内容" not in kg.get_node_content_preview("n1", max_lines=200, max_chars=4000)

    kg.create_section("n1", "定义", content="定义内容")

    assert "定义内容" in kg.get_node_content_preview("n1", max_lines=200, max_chars=4000)


def test_node_content_text_missing_node_is_empty(kg):
    assert kg.node_content_text("ghost") == ""
