"""删节点 / 删学科必须一并清掉题库里的孤儿题（方案 §3 A1a 步骤 5）。

回归来源：图谱节点被删后，题库里 `knowledge_point = node_id` 的题成了孤儿 ——
题库页归不到任何节点，判分还会把掌握度写回一个已经不存在的节点。
"""
import pytest

from app.core.knowledge_graph import KnowledgeGraph
from app.core.quiz import quiz_store
from app.core.quiz.quiz_store import QuizStore


@pytest.fixture
def kg(tmp_path):
    g = KnowledgeGraph(user_id=1, data_dir=tmp_path / "knowledge")
    with g._conn:  # nodes.user_id 是外键，先备 users 行
        g._conn.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (1, 't', 'x')")
    yield g
    g.close()


@pytest.fixture
def quiz(tmp_path, monkeypatch):
    """题库域根指到 tmp —— 断言清的是这里，绝不碰真实 backend/data/quiz。"""
    monkeypatch.setattr(quiz_store, "_QUIZ_DIR", tmp_path / "quiz")
    store = QuizStore(tmp_path / "quiz" / "1")
    yield store
    store.close()


def _add_question(store, node_id: str) -> None:
    store.save_questions([{
        "id": f"q-{node_id}", "type": "single", "question": f"{node_id} 是什么？",
        "options": [{"label": "选项", "value": "A"}], "answer": ["A"],
        "knowledge_point": node_id,
    }], subject="数据结构")


def _points_with_questions(store) -> set[str]:
    return {q["knowledge_point"] for q in store.list_questions()}


def test_remove_node_purges_its_questions(kg, quiz):
    kg.create_node_with_content({"id": "n1", "name": "数组", "tags": ["数据结构"]}, "正文")
    kg.create_node_with_content({"id": "n2", "name": "链表", "tags": ["数据结构"]}, "正文")
    _add_question(quiz, "n1")
    _add_question(quiz, "n2")

    kg.remove_node("n1")

    assert _points_with_questions(quiz) == {"n2"}   # 只清被删节点的题


def test_remove_subject_purges_questions_of_its_nodes(kg, quiz):
    kg.create_node_with_content(
        {"id": "d1", "name": "数组", "tags": ["数据结构"], "subject": "数据结构"}, "正文")
    kg.create_node_with_content(
        {"id": "o1", "name": "进程", "tags": ["操作系统"], "subject": "操作系统"}, "正文")
    _add_question(quiz, "d1")
    _add_question(quiz, "o1")

    kg.remove_subject("数据结构")

    assert _points_with_questions(quiz) == {"o1"}   # 别的学科的题不动


def test_purge_does_not_create_quiz_db(tmp_path, monkeypatch):
    """只用了图谱的用户删节点，不该凭空多出一份 quiz.db（否则测试会污染真实 data/quiz）。"""
    monkeypatch.setattr(quiz_store, "_QUIZ_DIR", tmp_path / "quiz")
    g = KnowledgeGraph(user_id=1, data_dir=tmp_path / "knowledge")
    try:
        with g._conn:
            g._conn.execute(
                "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (1, 't', 'x')")
        g.create_node_with_content({"id": "n1", "name": "数组", "tags": ["数据结构"]}, "正文")
        g.remove_node("n1")
    finally:
        g.close()

    assert not (tmp_path / "quiz").exists()
