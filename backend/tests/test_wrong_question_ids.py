"""错题本派生口径：不建表、不加列，只从 attempts 读「最近一次作答为错」。"""
from app.core.quiz.quiz_store import QuizStore


def _seed(store):
    # ⚠️ `Question` 的 `id` 是**必填**（`core/quiz/schema.py` 里无默认值）——
    # 计划书那句"多传 id 会 TypeError"写反了：真实约束是**不传** `id` 会
    # `ValidationError: id Field required`（2026-11-16 实跑抓到）。
    return store.save_questions([{
        "id": "q1", "type": "single", "question": "下面哪个说法正确？",
        "options": [{"value": "A", "label": "甲"}, {"value": "B", "label": "乙"}],
        "answer": ["A"], "knowledge_point": "n1",
    }], subject="测试", source="chat")[0]


def test_wrong_question_ids_empty_for_fresh_db(tmp_path):
    assert QuizStore(tmp_path / "quiz").wrong_question_ids() == set()


def test_latest_wrong_attempt_marks_question(tmp_path):
    store = QuizStore(tmp_path / "quiz")
    qid = _seed(store)
    store.record_attempt(qid, "B", 0, 10, False, "错")
    assert store.wrong_question_ids() == {qid}


def test_corrected_question_leaves_wrong_book(tmp_path):
    """订正（换答案再答对）→ 自动移出，无需「移出」接口。"""
    store = QuizStore(tmp_path / "quiz")
    qid = _seed(store)
    store.record_attempt(qid, "B", 0, 10, False, "错")
    store.record_attempt(qid, "A", 10, 10, True, "对")
    assert store.wrong_question_ids() == set()
    store.close()
