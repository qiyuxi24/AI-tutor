"""独立出题页的判分闭环：判分后要回写图谱掌握度（反馈闭环断裂修复）。

背景（2026-11-15）：
    对话内出题（`chat_quiz` → `chat_grade.grade_pending`）答对会 `add_mastery(+20)`，
    但**独立出题页**（`QuizView` → `POST /quiz/{id}/grade`）只写 `attempts`，
    完全不碰图谱 → 学生判完分回图谱，节点颜色不变。演示与真实使用都被这条坑到。

本次锁住三件事：
  1. 题上有 `knowledge_point`（= 图谱节点 id）→ 答对回写 +20，响应带 `mastery_note`/`mastery`；
  2. 答错不扣分（`apply_mastery_after_answer` 的既有口径）；
  3. 题上没有 `knowledge_point`（老题 / 未传 graph_node_id）→ 不构造图谱、不写、不报错。

另锁出题侧：`POST /quiz/generate` 带 `graph_node_id` 时，入库题目的
`knowledge_point` 必须被改写成该图谱节点 id（否则下一跳判分无从回写）。

全离线：真实 KnowledgeGraph / QuizStore 落在 tmp_path，判分走规则（single 题零 LLM）。
"""
import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import quiz as quiz_api
from app.core.auth import get_current_user
from app.core.knowledge_graph import KnowledgeGraph
from app.core.quiz.quiz_store import QuizStore

USER_ID = 11


@pytest.fixture()
def kg_dir(tmp_path):
    """图谱目录 + 一个节点（`_open_kg` 每次新开连接读同一份库）。"""
    d = tmp_path / "kg"
    g = KnowledgeGraph(user_id=USER_ID, data_dir=d)
    with g._conn:
        g._conn.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (?, 's', 'x')",
            (USER_ID,))
    g.create_node_with_content({"id": "n1", "name": "二叉树遍历", "subject": "数据结构"}, "正文")
    g.create_section("n1", "前序遍历", content="前序正文" * 60)
    # 第二节：让「全部小节通过」不成立 —— 否则单节节点答对会直接把掌握度置 100，
    # 就看不出"小节口径不按 +20 走"这件事（测试 4 要区分的就是这个）。
    g.create_section("n1", "中序遍历", content="中序正文" * 60)
    g.close()
    return d


@pytest.fixture()
def kg(kg_dir):
    """断言用连接（端点自己开的那条关掉不影响它）。"""
    g = KnowledgeGraph(user_id=USER_ID, data_dir=kg_dir)
    yield g
    g.close()


@pytest.fixture()
def store(tmp_path):
    s = QuizStore(tmp_path / "quiz" / str(USER_ID))
    yield s
    s.close()


@pytest.fixture()
def client(store, kg_dir, monkeypatch):
    app = FastAPI()
    app.include_router(quiz_api.router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: USER_ID
    monkeypatch.setattr(quiz_api.quiz_manager, "_get_store", lambda user_id: store)
    # 端点内部自建 KnowledgeGraph → 指到 tmp 目录（不能复用 fixture 那条，它会被 close）
    monkeypatch.setattr(
        quiz_api, "KnowledgeGraph",
        lambda user_id: KnowledgeGraph(user_id=user_id, data_dir=kg_dir))
    return TestClient(app)


def _add(store, *, qid="q1", answer="A", knowledge_point="n1", section_id=""):
    ids = store.save_questions([{
        "id": qid, "type": "single", "question": "下面哪个说法正确？这是一个足够长的题干。",
        "options": [{"value": "A", "label": "甲"}, {"value": "B", "label": "乙"}],
        "answer": [answer], "analysis": "解析文字", "knowledge_point": knowledge_point,
    }], subject="二叉树遍历", source="", section_id=section_id)
    return ids[0]


# ── 判分回写 ────────────────────────────────────────────────────

def test_correct_answer_writes_mastery(client, store, kg):
    """答对 → 图谱 +20，响应带可展示的说明与当前值。"""
    qid = _add(store)

    resp = client.post(f"/api/v1/quiz/{qid}/grade", params={"user_answer": "A"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["correct"] is True
    assert data["node_id"] == "n1"
    assert data["mastery"] == 20
    assert "掌握度" in data["mastery_note"]
    assert kg.get_node("n1")["mastery"] == 20


def test_wrong_answer_does_not_touch_mastery(client, store, kg):
    """答错不扣分（既有口径），也不该写出一条空说明。"""
    qid = _add(store)

    resp = client.post(f"/api/v1/quiz/{qid}/grade", params={"user_answer": "B"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["correct"] is False
    assert data["mastery_note"] == ""
    assert kg.get_node("n1")["mastery"] == 0


def test_question_without_knowledge_point_skips_graph(client, store):
    """老题没有知识点 → 不构造图谱、不写、不报错（判分本身照常）。"""
    qid = _add(store, knowledge_point="")

    resp = client.post(f"/api/v1/quiz/{qid}/grade", params={"user_answer": "A"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["correct"] is True
    assert "mastery" not in data
    assert "mastery_note" not in data


def test_section_question_marks_section_passed(client, store, kg):
    """带 section_id 的题走小节口径：答对只置本节通过，节点不 +20。"""
    qid = _add(store, section_id="s01")

    resp = client.post(f"/api/v1/quiz/{qid}/grade", params={"user_answer": "A"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["section_id"] == "s01"
    assert data["next_action"] == "passed"
    assert kg.get_section_learn("n1", "s01")["passed"] is True
    assert kg.get_node("n1")["mastery"] == 0


# ── 锚点解析：LLM 编的中文知识点名要能落回节点 id（2026-11-15）────────
#
# 实测（user 5 / 47 题）：`knowledge_point` 有 35 个唯一值，只有 2 个能 get_node()
# 命中 —— 从活动栏直接出题时它是 LLM 自由编的中文名，判分回写于是静默丢掉 94% 的题。
# 三档兜底：节点 id → 知识点名 → subject，实测能救回 89%。

def test_grade_resolves_mastery_by_knowledge_point_name(client, store, kg):
    """`knowledge_point` 是节点**名字**（不是 id）→ 按名字解析后照样回写。"""
    qid = _add(store, knowledge_point="二叉树遍历")

    resp = client.post(f"/api/v1/quiz/{qid}/grade", params={"user_answer": "A"})

    data = resp.json()
    assert data["node_id"] == "n1", "应解析回节点 id"
    assert kg.get_node("n1")["mastery"] == 20


def test_grade_resolves_mastery_by_subject_fallback(client, store, kg):
    """知识点名解析不出来、但 subject 正好是节点名 → 用 subject 兜底（实测救回 42/47）。"""
    qid = _add(store, knowledge_point="前序遍历规则")
    # subject 由 _add 固定为「二叉树遍历」，它正好是节点名

    resp = client.post(f"/api/v1/quiz/{qid}/grade", params={"user_answer": "A"})

    assert resp.json()["node_id"] == "n1"
    assert kg.get_node("n1")["mastery"] == 20


def test_grade_skips_when_nothing_resolves(client, store, kg):
    """三档全解析不到（如 subject='线性代数' 对应几十个节点）→ 如实不写，不假装成功。"""
    qid = store.save_questions([{
        "id": "q1", "type": "single", "question": "下面哪个说法正确？这是一个足够长的题干。",
        "options": [{"value": "A", "label": "甲"}, {"value": "B", "label": "乙"}],
        "answer": ["A"], "analysis": "解析", "knowledge_point": "线性代数",
    }], subject="线性代数")[0]

    resp = client.post(f"/api/v1/quiz/{qid}/grade", params={"user_answer": "A"})

    data = resp.json()
    assert data["correct"] is True
    assert "mastery" not in data
    assert kg.get_node("n1")["mastery"] == 0


# ── 判分幂等（2026-11-15）─────────────────────────────────────────
#
# 实测 q2 同一道题被判对两次 = 白送 40 分。锚点解析修好后，这条会直接把掌握度
# 刷到 100，所以幂等必须与解析同批修。

def test_repeated_identical_grade_does_not_double_count(client, store, kg):
    """同题同答案提交两次 → 第二次返回缓存结果，不再记 attempt、不再加掌握度。"""
    qid = _add(store)

    first = client.post(f"/api/v1/quiz/{qid}/grade", params={"user_answer": "A"}).json()
    second = client.post(f"/api/v1/quiz/{qid}/grade", params={"user_answer": "A"}).json()

    assert first["correct"] is True and second["correct"] is True
    assert second.get("idempotent") is True
    assert kg.get_node("n1")["mastery"] == 20, "只该加一次"
    n = store._conn.execute(
        "SELECT COUNT(*) AS n FROM attempts WHERE question_id = ?", (qid,)).fetchone()["n"]
    assert n == 1, "不该重复记作答"


def test_different_answer_is_graded_again(client, store, kg):
    """换答案 = 一次新的作答，照常判分并记 attempt（幂等只认「同题同答案」）。"""
    qid = _add(store)

    client.post(f"/api/v1/quiz/{qid}/grade", params={"user_answer": "B"})
    resp = client.post(f"/api/v1/quiz/{qid}/grade", params={"user_answer": "A"})

    assert resp.json()["correct"] is True
    assert resp.json().get("idempotent") is not True
    assert kg.get_node("n1")["mastery"] == 20
    n = store._conn.execute(
        "SELECT COUNT(*) AS n FROM attempts WHERE question_id = ?", (qid,)).fetchone()["n"]
    assert n == 2


# ── 出题侧：把图谱节点 id 带进题目 ──────────────────────────────

def test_generate_records_graph_node_id(client, store, monkeypatch):
    """出题带 graph_node_id → 入库题目的 knowledge_point 被改写（判分才有得回写）。"""

    async def fake_generate(**kwargs):
        return {
            "questions": [{
                "id": "q1", "type": "single",
                "question": "下面哪个说法正确？这是一个足够长的题干。",
                "options": [{"value": "A", "label": "甲"}, {"value": "B", "label": "乙"}],
                "answer": ["A"], "knowledge_point": "LLM 自己填的名称",
            }],
            "rejected": 0, "materials_used": 0, "requested": 1,
        }

    monkeypatch.setattr(quiz_api, "generate_quiz", fake_generate)

    resp = client.post("/api/v1/quiz/generate", json={
        "subject": "二叉树遍历", "question_count": 1, "graph_node_id": "n1",
    })

    assert resp.status_code == 200
    assert resp.json()["questions"][0]["knowledge_point"] == "n1"
    assert store.list_questions()[0]["knowledge_point"] == "n1"


def test_generate_without_graph_node_id_keeps_llm_value(client, store, monkeypatch):
    """不传 graph_node_id（老前端 / 从活动栏进入）→ 保留 LLM 填的知识点名，行为不变。"""

    async def fake_generate(**kwargs):
        return {
            "questions": [{
                "id": "q1", "type": "single",
                "question": "下面哪个说法正确？这是一个足够长的题干。",
                "options": [{"value": "A", "label": "甲"}, {"value": "B", "label": "乙"}],
                "answer": ["A"], "knowledge_point": "栈",
            }],
            "rejected": 0, "materials_used": 0, "requested": 1,
        }

    monkeypatch.setattr(quiz_api, "generate_quiz", fake_generate)

    resp = client.post("/api/v1/quiz/generate", json={"subject": "栈", "question_count": 1})

    assert resp.status_code == 200
    assert store.list_questions()[0]["knowledge_point"] == "栈"


def test_generate_records_source_docs(client, store, kg_dir, monkeypatch):
    """带 graph_node_id 出题 → 题目的 source_docs 记下该节点依据的**文件**（血统）。

    实测（user 5 / 47 题）独立出题路径 `source_docs` 非空 **0 条** —— 题从生下来
    就没有来源，学生点「看依据」看不到任何东西，也没法做"这题出自哪份资料"的统计。
    """

    async def fake_generate(**kwargs):
        return {
            "questions": [{
                "id": "q1", "type": "single",
                "question": "下面哪个说法正确？这是一个足够长的题干。",
                "options": [{"value": "A", "label": "甲"}, {"value": "B", "label": "乙"}],
                "answer": ["A"], "knowledge_point": "",
            }],
            "rejected": 0, "materials_used": 0, "requested": 1,
        }

    # 节点 n1 有溯源文件（chat_quiz._source_docs_for 读的就是这份）
    g = KnowledgeGraph(user_id=USER_ID, data_dir=kg_dir)
    g.add_sources("n1", [{"doc_id": 7, "doc_name": "数据结构.md"}])
    g.close()
    monkeypatch.setattr(quiz_api, "generate_quiz", fake_generate)

    resp = client.post("/api/v1/quiz/generate", json={
        "subject": "二叉树遍历", "question_count": 1, "graph_node_id": "n1",
    })

    assert resp.status_code == 200
    got = store.list_questions()[0]
    assert got["source_docs"] == [{"doc_id": 7, "doc_name": "数据结构.md"}]


def test_generate_without_graph_node_has_no_source_docs(client, store, monkeypatch):
    """不关联图谱 → 没有节点可查溯源，source_docs 留空（不报错、不瞎填）。"""

    async def fake_generate(**kwargs):
        return {
            "questions": [{
                "id": "q1", "type": "single",
                "question": "下面哪个说法正确？这是一个足够长的题干。",
                "options": [{"value": "A", "label": "甲"}, {"value": "B", "label": "乙"}],
                "answer": ["A"], "knowledge_point": "",
            }],
            "rejected": 0, "materials_used": 0, "requested": 1,
        }

    monkeypatch.setattr(quiz_api, "generate_quiz", fake_generate)

    resp = client.post("/api/v1/quiz/generate", json={"subject": "栈", "question_count": 1})

    assert resp.status_code == 200
    assert store.list_questions()[0]["source_docs"] == []



# ── 端到端：出题 → 判分 → 图谱颜色真的动了 ──────────────────────

def test_generate_then_grade_moves_mastery(client, store, kg, monkeypatch):
    """一次完整闭环：带 graph_node_id 出题 → 答对 → 图谱掌握度变化。"""

    async def fake_generate(**kwargs):
        return {
            "questions": [{
                "id": "q1", "type": "single",
                "question": "下面哪个说法正确？这是一个足够长的题干。",
                "options": [{"value": "A", "label": "甲"}, {"value": "B", "label": "乙"}],
                "answer": ["A"], "knowledge_point": "",
            }],
            "rejected": 0, "materials_used": 0, "requested": 1,
        }

    monkeypatch.setattr(quiz_api, "generate_quiz", fake_generate)
    qid = client.post("/api/v1/quiz/generate", json={
        "subject": "二叉树遍历", "question_count": 1, "graph_node_id": "n1",
    }).json()["question_ids"][0]

    resp = client.post(f"/api/v1/quiz/{qid}/grade", params={"user_answer": "A"})

    assert resp.json()["mastery"] == 20
    assert kg.get_node("n1")["mastery"] == 20
