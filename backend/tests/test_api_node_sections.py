"""节点小节化的 HTTP 层契约断言（GET 详情 / GET 单节 / POST 生成 / DELETE 单节）。

数据层行为（manifest 原子写、CRUD）在 `test_node_sections.py`（存储收口）与
`test_section_generator.py`（生成管线）里锁；这里**只锁 API 契约**：

- `GET  /knowledge/node/{id}`        响应新增 `has_sections` + 小节**元数据**列表
  （`{id,title,kind,status,sources,updated_at}`，**不含正文**）；**兼容铁律**：老节点（无
  manifest）→ `has_sections=false`、`sections=[]`，`content` 仍返回单文件全文；
- `GET  /knowledge/node/{id}/section/{sid}`  单节正文 `{id,title,kind,status,content}`；
  节点或小节不存在 → 404；
- `POST /knowledge/node/{id}/sections/generate`  直接透传 `SectionGenerator.generate`
  的返回 dict；缺省/空体 `force=false`；节点不存在 → 404；
- `POST /knowledge/node/{id}/section/{sid}/quiz`  按小节出题：起后台任务并带 `section_id`；
  节点/小节不存在 → 404；已在出题 → 409；
- `GET  /knowledge/source/{doc_id}/nodes`  资料→节点反查（图谱高亮数据源）：`subjects`
  按节点出现顺序去重；账本里的孤儿 node_id 跳过；无账本 → 空列表（不是错误）；
- `DELETE /knowledge/node/{id}/section/{sid}`  `{"deleted": bool}`；不存在 → 404；
- `GET  /knowledge/node/{id}/quizzes`  侧边栏试题链接：题目按 `knowledge_point =
  图谱节点 id` 取自题库、`section_id` 从 manifest 的 `quizzes` 路由回填（未挂号 →
  `""`）；节点不存在 → 404；**题库异常降级为空列表**（不抛）。

全离线：`KnowledgeGraph` 被替换为假实现（`nodes_dir` 用**真临时目录**，让详情端点
仍走真实文件读取路径），`SectionGenerator` 用 `sys.modules` 注入的假模块打桩，
不碰 `data/`、不碰 LLM。**注意**：`has_sections/list_sections/read_section/
delete_section` 与 `SectionGenerator` 由并行 agent 实现，本文件的断言依赖上述**冻结
签名**；行为正确性由它们各自的单元测试覆盖。
"""
import sys
import types

import pytest
from fastapi.testclient import TestClient

from app.api.v1 import knowledge as knowledge_module
from app.core.auth import get_current_user

USER_ID = 5

# 小节元数据带一堆**不该外泄**的字段（file/brief/content）→ 投影后只留契约里的 6 个键
_SECTION_S01 = {
    "id": "s01", "title": "定义与几何意义", "kind": "definition", "status": "filled",
    "updated_at": "2026-01-01T00:00:00",
    "sources": [{"doc_id": 7, "doc_name": "高等数学.md", "section": "第9章", "chunk_id": None}],
    "file": "s01_定义与几何意义.md", "brief": "一句话说明", "content": "不该出现在列表里",
}
_SECTION_S02 = {
    "id": "s02", "title": "例题_交换积分次序", "kind": "example", "status": "failed",
    "updated_at": "2026-01-02T00:00:00",
    "file": "s02_例题.md", "brief": "另一句", "content": "也不该出现",
}
_SECTION_BODIES = {("new_node", "s01"): "# 定义\n\n二重积分的定义……"}


class _FakeKG:
    """只实现端点用到的读/删方法；`nodes_dir` 是真目录，单文件读取路径保持真实。"""

    def __init__(self, user_id, *, nodes_dir, nodes, sections, bodies, manifests=None,
                 doc_marks=None):
        self.user_id = user_id
        self.nodes_dir = nodes_dir
        self._nodes = nodes
        self._sections = sections
        self._bodies = bodies
        self._manifests = manifests or {}   # node_id -> manifest（含 quizzes 路由）
        self._doc_marks = doc_marks or {}   # doc_id -> [{node_id, ...}]（资料→节点账本）
        self.deleted_calls = []          # 记录 delete_section 调用，供断言

    # ── 既有读接口 ──
    def get_node(self, node_id):
        return self._nodes.get(node_id)

    def get_prerequisites(self, node_id):
        return []

    @property
    def edges(self):
        return []

    def node_subject(self, node):
        return node.get("subject", "") or (node.get("tags") or [""])[0]

    def close(self):
        pass

    # ── 小节化读/删接口（冻结签名）──
    def has_sections(self, node_id):
        return bool(self._sections.get(node_id))

    def list_sections(self, node_id):
        return self._sections.get(node_id, [])

    def read_section(self, node_id, section_id):
        return self._bodies.get((node_id, section_id), "")

    def read_manifest(self, node_id):
        return self._manifests.get(node_id)

    # 小节学习状态（2026-09-29 新增：节点详情带回 learn、独立写入端点用它）——
    # 本文件只锁小节化的读/删契约，所以这里给最小实现（不参与断言）
    def section_learn_progress(self, node_id):
        return {"total": len(self._sections.get(node_id, [])), "passed": 0, "read": 0,
                "mark_unknown": 0, "mark_understood": 0, "mark_confused": 0,
                "all_passed": False}

    def set_section_learn(self, node_id, section_id, **kw):
        return {"mark": kw.get("mark") or "unknown", "read": bool(kw.get("read")),
                "passed": bool(kw.get("passed")), "attempts": 0, "updated_at": ""}

    def delete_section(self, node_id, section_id):
        self.deleted_calls.append((node_id, section_id))
        self._sections[node_id] = [
            s for s in self._sections.get(node_id, []) if s["id"] != section_id]
        return True

    def list_doc_marks(self, doc_id, status=None):
        return self._doc_marks.get(doc_id, [])


@pytest.fixture
def gen_calls(monkeypatch):
    """把 `app.core.kb.section_generator` 注入为假模块，记录 generate 调用参数。"""
    calls: dict = {}
    mod = types.ModuleType("app.core.kb.section_generator")

    class _FakeSectionGenerator:
        def __init__(self, user_id):
            self.user_id = user_id

        async def generate(self, kg, node_id, *, force=False):
            calls.update(user_id=self.user_id, node_id=node_id, force=force)
            # 形状对齐真实 generate：created/failed 是**列表**（见 section_generator.py）
            return {"status": "ok",
                    "created": [{"id": "s01", "title": "定义", "kind": "definition"}],
                    "failed": [{"title": "例题", "error": "截断"}],
                    "message": "done"}

    mod.SectionGenerator = _FakeSectionGenerator
    monkeypatch.setitem(sys.modules, "app.core.kb.section_generator", mod)
    return calls


@pytest.fixture
def api(tmp_path, monkeypatch):
    """TestClient + 假 KG（真临时 nodes_dir）+ 固定登录态 + 临时题库。"""
    from app.main import app
    from app.core.quiz.quiz_store import QuizManager

    nodes_dir = tmp_path / "nodes" / str(USER_ID)
    nodes_dir.mkdir(parents=True)
    (nodes_dir / "old_node.md").write_text("# 老节点\n\n单文件全文。", encoding="utf-8")

    # 临时题库：new_node（图谱节点 id）名下三道题，用于试题链接端点。
    store = QuizManager(tmp_path / "quiz")._get_store(USER_ID)
    qids = store.save_questions([
        {"id": "q1", "type": "single", "question": "二重积分交换积分次序的第一题？",
         "options": [{"value": "A", "label": "选项A"}, {"value": "B", "label": "选项B"}],
         "answer": ["A"], "knowledge_point": "new_node"},
        {"id": "q2", "type": "judge", "question": "关于二重积分几何意义的判断题。",
         "answer": ["对"], "knowledge_point": "new_node"},
        {"id": "q3", "type": "fill", "question": "二重积分计算方式的填空题（未挂小节）。",
         "answer": ["dxdy"], "knowledge_point": "new_node"},
    ], subject="高等数学", difficulty="medium")

    # manifest 的 quizzes 路由：id 故意用 int / str 两种形态，验证比对统一 str()
    manifests = {"new_node": {"quizzes": [
        {"id": qids[0], "section_id": "s02", "source": "quiz_store",
         "created_at": "2026-01-03T00:00:00"},
        {"id": str(qids[1]), "section_id": "s03", "source": "quiz_store",
         "created_at": "2026-01-03T00:00:01"},
        # qids[2] 刻意不挂 → 期望 section_id 回填为 ""
    ]}}

    fk = _FakeKG(
        USER_ID,
        nodes_dir=nodes_dir,
        nodes={
            "old_node": {"id": "old_node", "name": "老节点", "tags": ["高等数学"],
                         "summary": "旧摘要", "mastery": 10},
            "new_node": {"id": "new_node", "name": "二重积分", "tags": ["高等数学"],
                         "summary": "新摘要", "mastery": 0},
        },
        sections={"old_node": [], "new_node": [_SECTION_S01, _SECTION_S02]},
        bodies=dict(_SECTION_BODIES),
        manifests=manifests,
    )
    monkeypatch.setattr(knowledge_module, "KnowledgeGraph", lambda **kw: fk)
    monkeypatch.setattr(knowledge_module.quiz_manager, "_get_store", lambda uid: store)

    app.dependency_overrides[get_current_user] = lambda: USER_ID
    yield {"client": TestClient(app), "kg": fk, "qids": qids}
    app.dependency_overrides.clear()
    store.close()


# ── GET 节点详情：小节元数据 + 老节点兼容铁律 ──────────────────

def test_old_node_compat(api):
    """老节点（无 manifest）：has_sections=false、sections=[]、content 仍是单文件全文。"""
    r = api["client"].get("/api/v1/knowledge/node/old_node")

    assert r.status_code == 200
    body = r.json()
    assert body["has_sections"] is False
    assert body["sections"] == []
    assert body["content"] == "# 老节点\n\n单文件全文。"


def test_sectioned_node_metadata_no_body(api):
    """小节化节点：返回元数据列表（5 键），**不含正文**；无主 MD → content 为空串。"""
    r = api["client"].get("/api/v1/knowledge/node/new_node")

    assert r.status_code == 200
    body = r.json()
    assert body["has_sections"] is True
    assert [s["id"] for s in body["sections"]] == ["s01", "s02"]
    for s in body["sections"]:
        # sources 是新增的溯源键：无来源的小节投影成 []（老 manifest 无此键也一样）
        # learn 是小节级**学习状态**（2026-09-29 新增，与 status=生成状态 是两回事）
        assert set(s.keys()) == {"id", "title", "kind", "status", "sources",
                                 "updated_at", "learn"}
    assert body["sections"][0]["sources"] == [
        {"doc_id": 7, "doc_name": "高等数学.md", "section": "第9章", "chunk_id": None}]
    assert body["sections"][1]["sources"] == []
    assert body["sections"][0]["title"] == "定义与几何意义"
    assert body["sections"][1]["status"] == "failed"
    assert body["content"] == "", "小节化节点没有概述主文件（D1），content 应为空"


def test_node_detail_missing_404(api):
    assert api["client"].get("/api/v1/knowledge/node/nope").status_code == 404


# ── GET 单节正文 ────────────────────────────────────────────

def test_get_section_ok(api):
    r = api["client"].get("/api/v1/knowledge/node/new_node/section/s01")

    assert r.status_code == 200
    body = r.json()
    assert body == {"id": "s01", "title": "定义与几何意义", "kind": "definition",
                    "status": "filled", "content": "# 定义\n\n二重积分的定义……"}


def test_get_section_missing_section_404(api):
    assert api["client"].get(
        "/api/v1/knowledge/node/new_node/section/nope").status_code == 404


def test_get_section_missing_node_404(api):
    assert api["client"].get(
        "/api/v1/knowledge/node/nope/section/s01").status_code == 404


# ── POST 生成管线 ───────────────────────────────────────────

def test_generate_passthrough_default_force_false(api, gen_calls):
    r = api["client"].post("/api/v1/knowledge/node/new_node/sections/generate")

    assert r.status_code == 200
    assert r.json() == {
        "status": "ok",
        "created": [{"id": "s01", "title": "定义", "kind": "definition"}],
        "failed": [{"title": "例题", "error": "截断"}],
        "message": "done",
    }
    assert gen_calls == {"user_id": USER_ID, "node_id": "new_node", "force": False}


def test_generate_force_true(api, gen_calls):
    r = api["client"].post("/api/v1/knowledge/node/new_node/sections/generate",
                           json={"force": True})

    assert r.status_code == 200
    assert gen_calls["force"] is True


def test_generate_missing_node_404(api, gen_calls):
    r = api["client"].post("/api/v1/knowledge/node/nope/sections/generate")

    assert r.status_code == 404
    assert gen_calls == {}, "节点不存在时不应触发生成"


# ── GET 按资料反查节点（右键文件 → 在图谱中显示）───────────────

def test_nodes_by_source_groups_subjects_and_skips_orphans(api):
    """资料 → 节点：按出现顺序去重学科；账本里的孤儿 node_id 静默跳过"""
    kg = api["kg"]
    kg._nodes["old_node"]["tags"] = ["考研数学"]        # 造一个跨学科场景
    kg._doc_marks[42] = [{"node_id": "new_node"}, {"node_id": "old_node"},
                         {"node_id": "ghost"}]          # ghost 不在 nodes 里

    body = api["client"].get("/api/v1/knowledge/source/42/nodes").json()

    assert body["doc_id"] == 42
    assert [n["id"] for n in body["nodes"]] == ["new_node", "old_node"]
    assert body["subjects"] == ["高等数学", "考研数学"]
    assert body["nodes"][0] == {"id": "new_node", "name": "二重积分", "subject": "高等数学"}


def test_nodes_by_source_empty_when_no_marks(api):
    """没建过图 / 手动建的节点 → 空列表（不是错误，前端据此提示"还没有关联知识点"）"""
    body = api["client"].get("/api/v1/knowledge/source/999/nodes").json()

    assert body == {"doc_id": 999, "subjects": [], "nodes": []}


# ── POST 按小节出题 ──────────────────────────────────────────

def test_section_quiz_triggers_background(api, monkeypatch):
    """按小节出题：校验通过 → 起后台任务（带 section_id），返回 200"""
    from app.core.quiz import chat_quiz
    calls = []
    monkeypatch.setattr(
        chat_quiz, "start_background_generation",
        lambda uid, *, node_id, section_id="": calls.append((uid, node_id, section_id)) or True)

    r = api["client"].post("/api/v1/knowledge/node/new_node/section/s01/quiz")

    assert r.status_code == 200
    assert r.json() == {"status": "ok", "node_id": "new_node", "section_id": "s01"}
    assert calls == [(USER_ID, "new_node", "s01")]


def test_section_quiz_404_for_missing_node_or_section(api, monkeypatch):
    """节点不存在 / 小节不存在 → 404，且**不起**后台任务"""
    from app.core.quiz import chat_quiz
    calls = []
    monkeypatch.setattr(
        chat_quiz, "start_background_generation",
        lambda uid, *, node_id, section_id="": calls.append((uid, node_id, section_id)) or True)

    assert api["client"].post(
        "/api/v1/knowledge/node/nope/section/s01/quiz").status_code == 404
    assert api["client"].post(
        "/api/v1/knowledge/node/new_node/section/s99/quiz").status_code == 404
    assert calls == []


def test_section_quiz_409_when_already_generating(api, monkeypatch):
    """同用户已有出题任务在跑 → 409（复用 chat_quiz 的 per-user 去重位）"""
    from app.core.quiz import chat_quiz
    monkeypatch.setattr(chat_quiz, "start_background_generation",
                        lambda uid, *, node_id, section_id="": False)

    assert api["client"].post(
        "/api/v1/knowledge/node/new_node/section/s01/quiz").status_code == 409


# ── DELETE 单节 ─────────────────────────────────────────────

def test_delete_section_ok(api):
    r = api["client"].delete("/api/v1/knowledge/node/new_node/section/s01")

    assert r.status_code == 200
    assert r.json() == {"deleted": True}
    assert api["kg"].deleted_calls == [("new_node", "s01")]


def test_delete_section_missing_404(api):
    # delete_section 对"无此节"返回 False → 端点转 404
    api["kg"].delete_section = lambda node_id, section_id: False
    r = api["client"].delete("/api/v1/knowledge/node/new_node/section/nope")

    assert r.status_code == 404


# ── 真实 KnowledgeGraph 端到端（老节点兼容铁律 + 小节读删闭环）──
# 上面用假 KG 锁死 HTTP 契约；这里用**真实** KG（临时 data_dir）证明：
# 老节点（单文件、无 manifest）走同一端点时行为不变；小节化节点读/删闭环成立。

@pytest.fixture
def real_api(tmp_path, monkeypatch):
    from app.main import app
    from app.core.knowledge_graph import KnowledgeGraph as RealKG

    def _factory(user_id=None, **kw):
        return RealKG(user_id=user_id, data_dir=tmp_path)

    # 播种：真实 KG 建一个**老节点**（写单文件 legacy.md，无 manifest）
    seed = RealKG(user_id=USER_ID, data_dir=tmp_path)
    with seed._conn:  # nodes.user_id 是外键，先备 users 行
        seed._conn.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (?, 't', 'x')",
            (USER_ID,))
    seed.create_node_with_content(
        {"id": "legacy", "name": "二叉树", "tags": ["数据结构"]},
        "# 二叉树\n\n老节点单文件正文标记 LEGACY_BODY。")

    monkeypatch.setattr(knowledge_module, "KnowledgeGraph", _factory)
    app.dependency_overrides[get_current_user] = lambda: USER_ID
    yield {"client": TestClient(app), "seed": seed}
    app.dependency_overrides.clear()
    seed.close()


def test_real_legacy_node_compat(real_api):
    """兼容铁律硬验证：真实老节点 → has_sections=false、sections=[]、content 是单文件全文。"""
    r = real_api["client"].get("/api/v1/knowledge/node/legacy")

    assert r.status_code == 200
    body = r.json()
    assert body["has_sections"] is False
    assert body["sections"] == []
    assert "LEGACY_BODY" in body["content"], "老节点 content 必须仍是单文件全文"


def test_real_section_read_delete_roundtrip(real_api):
    """真实小节读/删闭环：详情回元数据 5 键、单节可读、删后 404。"""
    sid = real_api["seed"].create_section(
        "legacy", "定义与几何意义", kind="definition", content="# 定义\n\n正文。")

    detail = real_api["client"].get("/api/v1/knowledge/node/legacy").json()
    assert detail["has_sections"] is True
    assert [s["id"] for s in detail["sections"]] == [sid]
    assert set(detail["sections"][0]) == {"id", "title", "kind", "status", "sources",
                                          "updated_at", "learn"}

    sec = real_api["client"].get(
        f"/api/v1/knowledge/node/legacy/section/{sid}").json()
    assert sec["content"] == "# 定义\n\n正文。"
    assert sec["kind"] == "definition"

    assert real_api["client"].delete(
        f"/api/v1/knowledge/node/legacy/section/{sid}").json() == {"deleted": True}
    assert real_api["client"].get(
        f"/api/v1/knowledge/node/legacy/section/{sid}").status_code == 404


# ── 鉴权 ────────────────────────────────────────────────────

def test_requires_auth(api):
    from app.main import app

    app.dependency_overrides.pop(get_current_user, None)
    try:
        client = TestClient(app)
        assert client.get(
            "/api/v1/knowledge/node/new_node/section/s01").status_code == 401
    finally:
        app.dependency_overrides[get_current_user] = lambda: USER_ID


# ── GET 节点试题链接（侧边栏数据源）─────────────────────────────
# 题目来自题库（按 knowledge_point = 图谱节点 id 取），section_id 从 manifest 的
# quizzes 路由回填（题 id 与 ref.id 统一 str() 比对，manifest 没有该题 → ""）；
# **降级铁律**：题库查询异常 → quizzes: []，绝不把节点详情页打挂。

def test_node_quizzes_with_section_backfill(api):
    """返回该节点题目（id 倒序）；section_id 按 manifest 路由回填，未挂号 → ""。"""
    r = api["client"].get("/api/v1/knowledge/node/new_node/quizzes")

    assert r.status_code == 200
    body = r.json()
    assert body["node_id"] == "new_node"
    quizzes = body["quizzes"]
    assert [q["id"] for q in quizzes] == sorted(api["qids"], reverse=True), "按 id 倒序"
    by_id = {q["id"]: q for q in quizzes}
    assert by_id[api["qids"][0]]["section_id"] == "s02"   # manifest ref.id 是 int
    assert by_id[api["qids"][1]]["section_id"] == "s03"   # manifest ref.id 是 str
    assert by_id[api["qids"][2]]["section_id"] == ""      # 未挂 manifest → ""
    assert by_id[api["qids"][0]]["knowledge_point"] == "new_node"


def test_node_quizzes_no_manifest_no_quiz(api):
    """无 manifest、且该节点无题 → quizzes: []（不报错）。"""
    r = api["client"].get("/api/v1/knowledge/node/old_node/quizzes")

    assert r.status_code == 200
    assert r.json() == {"node_id": "old_node", "quizzes": []}


def test_node_quizzes_missing_node_404(api):
    assert api["client"].get(
        "/api/v1/knowledge/node/nope/quizzes").status_code == 404


def test_node_quizzes_store_failure_degrades(api, monkeypatch):
    """题库不可用/查询异常 → 返回空列表不抛（降级铁律，侧边栏不能打挂详情页）。"""
    def _boom(uid):
        raise RuntimeError("题库不可用")

    monkeypatch.setattr(knowledge_module.quiz_manager, "_get_store", _boom)
    r = api["client"].get("/api/v1/knowledge/node/new_node/quizzes")

    assert r.status_code == 200
    assert r.json() == {"node_id": "new_node", "quizzes": []}
