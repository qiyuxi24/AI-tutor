"""
dataset_quiz 适配器测试（B3.1）。
覆盖：样例 JSON 映射（QuestionOption 字段序 / answer 字母化 / type 归一）、
非法行跳过、import_from_json 入库（subject/difficulty/source 整批参数）、配额检查。
"""

import json

from app.core.collector.adapters import dataset_quiz
from app.core.quiz.quiz_store import QuizManager

USER = 42


def _patch(monkeypatch, tmp_path) -> QuizManager:
    mgr = QuizManager(tmp_path)
    monkeypatch.setattr(dataset_quiz, "quiz_manager", mgr)
    return mgr


def _write_rows(tmp_path, rows) -> str:
    p = tmp_path / "ds.json"
    p.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return str(p)


SAMPLE = [
    {   # 标准行：单答案 str
        "question": "栈的特点是什么？",
        "choices": ["先进后出", "先进先出", "随机访问"],
        "answer": "A",
        "knowledge_point": "栈",
    },
    {   # list 答案 + 小写字母 → multiple
        "question": "下列哪些是线性结构？",
        "choices": ["链表", "图", "树", "队列"],
        "answer": ["a", "d"],
    },
    {   # 带显式 id/analysis
        "id": "cmmlu_001",
        "question": "队列的特点是什么？",
        "choices": ["后进先出", "先进先出"],
        "answer": "B",
        "analysis": "队列是 FIFO 结构",
    },
]


def test_map_row_fields_and_option_order():
    q = dataset_quiz.map_row_to_question(SAMPLE[0], index=0)
    assert q is not None
    # QuestionOption 字段序：label=内容，value=字母
    assert q.options[0].label == "先进后出" and q.options[0].value == "A"
    assert q.answer == ["A"]
    assert q.type == "single"
    assert q.knowledge_point == "栈"


def test_map_row_multiple_and_id_fallback():
    q2 = dataset_quiz.map_row_to_question(SAMPLE[1], index=1)
    assert q2.type == "multiple" and q2.answer == ["A", "D"]
    q3 = dataset_quiz.map_row_to_question(SAMPLE[2], index=2)
    assert q3.id == "cmmlu_001" and q3.analysis == "队列是 FIFO 结构"
    # 无 id → 稳定序号 id
    assert dataset_quiz.map_row_to_question(SAMPLE[0], index=7).id == "ds_7"


def test_map_row_invalid_type_falls_back_single():
    row = {"question": "题干", "choices": ["甲", "乙"], "answer": "A", "type": "作文题"}
    assert dataset_quiz.map_row_to_question(row).type == "single"


def test_map_row_skips_invalid():
    bad_rows = [
        "not a dict",                                   # 非 dict
        {"choices": ["甲", "乙"], "answer": "A"},       # 缺题干
        {"question": "题干", "choices": ["唯一选项"], "answer": "A"},  # 选项 < 2
        {"question": "题干", "choices": ["甲", "乙"]},  # 缺答案
        {"question": "题干", "choices": ["甲", "乙"], "answer": "C"},  # 答案越界
        {"question": "题干", "choices": ["甲", "乙"], "answer": 3},    # 答案类型错
    ]
    for r in bad_rows:
        assert dataset_quiz.map_row_to_question(r) is None, r


def test_load_dataset_json_rejects_non_list(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text('{"question": "x"}', encoding="utf-8")
    try:
        dataset_quiz.load_dataset_json(p)
        assert False, "应抛 ValueError"
    except ValueError:
        pass


def test_import_from_json_inserts_with_batch_params(tmp_path, monkeypatch):
    mgr = _patch(monkeypatch, tmp_path)
    rows = SAMPLE + ["垃圾行"]
    path = _write_rows(tmp_path, rows)

    result = dataset_quiz.import_from_json(
        path, USER, subject="数据结构", source="cmmlu")

    assert result == {"total": 4, "inserted": 3, "skipped": 1}
    store = mgr._get_store(USER)
    saved = store.list_questions(subject="数据结构")
    assert len(saved) == 3
    # subject/difficulty/source 是整批参数（Question 内不含，须落库可见）
    # list_questions 按 id DESC → 用题干定位"栈"那道（最早插入的）
    first = next(q for q in store.list_questions(subject="数据结构")
                 if q["question"] == "栈的特点是什么？")
    first = store.get_question(first["id"])
    assert first["subject"] == "数据结构"
    assert first["difficulty"] == "medium"
    # source 存库但 _row_to_question 不返回 → SQL 直查
    src = store._conn.execute(
        "SELECT source FROM questions WHERE id = ?", (first["id"],)
    ).fetchone()["source"]
    assert src == "cmmlu"
    # 题内容映射正确（answer 落库为字母 JSON）
    assert first["answer"] == ["A"]
    assert first["options"][0] == {"label": "先进后出", "value": "A"}


def test_import_quota_check_blocks_and_passes(tmp_path, monkeypatch):
    mgr = _patch(monkeypatch, tmp_path)
    path = _write_rows(tmp_path, SAMPLE[:1])

    # 超额 → 拒绝，不入库
    def over(current, incoming):
        return False, f"quota exceeded: {current}+{incoming}"

    try:
        dataset_quiz.import_from_json(path, USER, check_quota_fn=over)
        assert False, "应抛 RuntimeError"
    except RuntimeError as e:
        assert "quota exceeded" in str(e)
    assert mgr._get_store(USER).stats()["total_questions"] == 0

    # 未超额 → 正常入库（计数走 stats()，用真实当前值验证）
    seen = {}

    def ok(current, incoming):
        seen["current"], seen["incoming"] = current, incoming
        return True, ""

    dataset_quiz.import_from_json(path, USER, check_quota_fn=ok)
    assert seen == {"current": 0, "incoming": 1}
    assert mgr._get_store(USER).stats()["total_questions"] == 1
