"""
Quiz store 题库存储测试。
覆盖：建表、保存题目、查询、统计、作答记录、_row_to_question 转换。
"""
import json
from pathlib import Path

from app.core.quiz.schema import Question, QuestionOption
from app.core.quiz.quiz_store import QuizStore


def _make_question(**kw) -> Question:
    defaults = {
        "id": "q1",
        "type": "single",
        "question": "栈的特点是什么？这是一个足够长的题干。",
        "options": [
            QuestionOption(label="先进后出", value="A"),
            QuestionOption(label="先进先出", value="B"),
            QuestionOption(label="随机访问", value="C"),
            QuestionOption(label="以上都对", value="D"),
        ],
        "answer": ["A"],
        "analysis": "栈是LIFO结构",
        "points": 10,
        "knowledge_point": "栈",
    }
    defaults.update(kw)
    return Question(**defaults)


def _run(coro):
    import asyncio
    return asyncio.run(coro)


def test_store_creates_tables(tmp_path):
    store = QuizStore(tmp_path)
    assert store.db_path.exists()
    # 验证表存在
    tables = store._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    table_names = {r["name"] for r in tables}
    assert "questions" in table_names
    assert "attempts" in table_names
    store.close()


def test_store_creates_index(tmp_path):
    store = QuizStore(tmp_path)
    indexes = store._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'"
    ).fetchall()
    index_names = {r["name"] for r in indexes}
    assert "idx_questions_subject" in index_names
    store.close()


def test_save_and_get_question(tmp_path):
    store = QuizStore(tmp_path)
    q = _make_question()
    ids = store.save_questions([q], subject="数据结构")
    assert len(ids) == 1

    retrieved = store.get_question(ids[0])
    assert retrieved is not None
    assert retrieved["type"] == "single"
    assert retrieved["question"] == q.question
    assert retrieved["subject"] == "数据结构"
    assert retrieved["answer"] == ["A"]
    assert retrieved["points"] == 10
    assert retrieved["analysis"] == "栈是LIFO结构"
    store.close()


def test_get_question_not_found(tmp_path):
    store = QuizStore(tmp_path)
    assert store.get_question(999) is None
    store.close()


def test_save_multiple_questions(tmp_path):
    store = QuizStore(tmp_path)
    qs = [
        _make_question(id="q1", question="题目一内容足够长满足最低长度要求。"),
        _make_question(id="q2", question="题目二内容足够长满足最低长度要求。"),
        _make_question(id="q3", question="题目三内容足够长满足最低长度要求。"),
    ]
    ids = store.save_questions(qs, subject="数据结构")
    assert len(ids) == 3
    assert ids[0] != ids[1] != ids[2]
    store.close()


def test_list_questions_by_subject(tmp_path):
    store = QuizStore(tmp_path)
    store.save_questions([_make_question(id="q1")], subject="数据结构")
    store.save_questions([_make_question(id="q2")], subject="算法")
    store.save_questions([_make_question(id="q3")], subject="数据结构")

    ds = store.list_questions(subject="数据结构")
    assert len(ds) == 2
    algo = store.list_questions(subject="算法")
    assert len(algo) == 1
    store.close()


def test_list_questions_all(tmp_path):
    store = QuizStore(tmp_path)
    store.save_questions([_make_question(id="q1")], subject="A")
    store.save_questions([_make_question(id="q2")], subject="B")

    all_qs = store.list_questions()
    assert len(all_qs) == 2
    store.close()


def test_list_questions_limit_offset(tmp_path):
    store = QuizStore(tmp_path)
    for i in range(5):
        store.save_questions([_make_question(id=f"q{i}", question=f"题目{i}内容足够长。")],
                             subject="test")
    page1 = store.list_questions(subject="test", limit=2, offset=0)
    page2 = store.list_questions(subject="test", limit=2, offset=2)
    assert len(page1) == 2
    assert len(page2) == 2
    store.close()


def test_stats(tmp_path):
    store = QuizStore(tmp_path)
    assert store.stats() == {"total_questions": 0}
    store.save_questions([_make_question(id="q1")])
    assert store.stats() == {"total_questions": 1}
    store.save_questions([_make_question(id="q2"), _make_question(id="q3")])
    assert store.stats() == {"total_questions": 3}
    store.close()


def test_record_attempt(tmp_path):
    store = QuizStore(tmp_path)
    ids = store.save_questions([_make_question()])
    attempt_id = store.record_attempt(
        question_id=ids[0],
        user_answer="A",
        score=10,
        max_score=10,
        correct=True,
        comment="回答正确",
    )
    assert attempt_id > 0

    row = store._conn.execute(
        "SELECT * FROM attempts WHERE id = ?", (attempt_id,)
    ).fetchone()
    assert row["score"] == 10
    assert row["correct"] == 1
    assert row["user_answer"] == "A"
    store.close()


def test_row_to_question():
    row = {
        "id": 42,
        "node_id": 5,
        "subject": "数据结构",
        "type": "single",
        "question": "题干",
        "options_json": json.dumps([{"label": "A", "value": "A"}]),
        "answer_json": json.dumps(["A"]),
        "points": 15,
        "difficulty": "hard",
        "analysis": "解析",
        "comment_prompt": "量规",
        "knowledge_point": "栈",
        "created_at": "2026-01-01 00:00:00",
    }
    result = QuizStore._row_to_question(row)
    assert result["id"] == 42
    assert result["type"] == "single"
    assert result["options"] == [{"label": "A", "value": "A"}]
    assert result["answer"] == ["A"]
    assert result["points"] == 15


def test_row_to_question_empty_json():
    row = {
        "id": 1,
        "node_id": None,
        "subject": "",
        "type": "judge",
        "question": "题干",
        "options_json": "",
        "answer_json": "",
        "points": 10,
        "difficulty": "medium",
        "analysis": "",
        "comment_prompt": "",
        "knowledge_point": "",
        "created_at": "2026-01-01 00:00:00",
    }
    result = QuizStore._row_to_question(row)
    assert result["options"] == []
    assert result["answer"] == []


def test_save_with_node_id_and_difficulty(tmp_path):
    store = QuizStore(tmp_path)
    q = _make_question()
    ids = store.save_questions([q], subject="OS", node_id=10, difficulty="hard")
    retrieved = store.get_question(ids[0])
    assert retrieved["node_id"] == 10
    assert retrieved["difficulty"] == "hard"
    store.close()


def test_user_isolation(tmp_path):
    """不同用户数据目录隔离"""
    store1 = QuizStore(tmp_path / "user1")
    store2 = QuizStore(tmp_path / "user2")
    store1.save_questions([_make_question(id="q1")], subject="A")
    store2.save_questions([_make_question(id="q1")], subject="B")

    assert len(store1.list_questions()) == 1
    assert len(store2.list_questions()) == 1
    assert store1.list_questions()[0]["subject"] == "A"
    assert store2.list_questions()[0]["subject"] == "B"
    store1.close()
    store2.close()
