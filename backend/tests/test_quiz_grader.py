"""
Quiz grader 判分器测试。
覆盖：单选/多选/判断/填空规则判分 + 简答题 LLM 判分（mock）+ 未知题型降级。
"""
import asyncio
import json

from app.core.quiz.grader import (
    _norm,
    _fuzzy_match,
    _grade_single,
    _grade_multiple,
    _grade_judge,
    _grade_fill,
    _grade_short_answer,
    grade_question,
)
from app.core.quiz.schema import QuizGradeRequest


# ─── _norm ───────────────────────────────────────────────────────

def test_norm_basic():
    assert _norm("  Hello  ") == "hello"
    assert _norm("A B C") == "abc"
    assert _norm("  对  ") == "对"


def test_norm_none_int():
    assert _norm(123) == "123"
    assert _norm(None) == "none"


# ─── _fuzzy_match ────────────────────────────────────────────────

def test_fuzzy_match_exact():
    assert _fuzzy_match("栈", ["栈"]) is True


def test_fuzzy_match_contains():
    assert _fuzzy_match("栈是一种数据结构", ["栈"]) is True  # 答案在用户作答中


def test_fuzzy_match_reverse():
    assert _fuzzy_match("栈", ["栈是一种数据结构"]) is True  # 用户在答案中


def test_fuzzy_match_no_match():
    assert _fuzzy_match("队列", ["栈"]) is False


def test_fuzzy_match_empty():
    assert _fuzzy_match("", ["栈"]) is False
    assert _fuzzy_match("  ", ["栈"]) is False


def test_fuzzy_match_multiple_answers():
    assert _fuzzy_match("O(n)", ["O(n)", "O(n^2)"]) is True
    assert _fuzzy_match("O(n^2)", ["O(n)", "O(n^2)"]) is True
    assert _fuzzy_match("O(1)", ["O(n)", "O(n^2)"]) is False


def test_fuzzy_match_rejects_negated_answer():
    """否定句不能判对：`'不是栈'` 里有 '栈' 但语义相反（2026-11-15 实测假阳性）。"""
    assert _fuzzy_match("不是栈", ["栈"]) is False
    assert _fuzzy_match("队列不是栈", ["栈"]) is False
    assert _fuzzy_match("没有用到栈", ["栈"]) is False


def test_fuzzy_match_still_accepts_plain_explanation():
    """补否定词守卫不能误伤正常作答（'栈是一种LIFO结构' 仍算对）。"""
    assert _fuzzy_match("栈是一种LIFO结构", ["栈"]) is True
    assert _fuzzy_match("栈溢出", ["栈"]) is True


# ─── _grade_single ───────────────────────────────────────────────

def test_grade_single_correct():
    r = _grade_single("A", ["A"], 10)
    assert r["score"] == 10
    assert r["correct"] is True
    assert r["max_score"] == 10


def test_grade_single_wrong():
    r = _grade_single("B", ["A"], 10)
    assert r["score"] == 0
    assert r["correct"] is False


def test_grade_single_case_insensitive():
    r = _grade_single("a", ["A"], 10)
    assert r["score"] == 10
    assert r["correct"] is True


def test_grade_single_with_spaces():
    r = _grade_single(" A ", ["A"], 10)
    assert r["score"] == 10


def test_grade_single_no_answer():
    r = _grade_single("A", [], 10)
    assert r["score"] == 0
    assert r["correct"] is False


# ─── _grade_multiple ─────────────────────────────────────────────

def test_grade_multiple_all_correct():
    r = _grade_multiple("A,C", ["A", "C"], 10)
    assert r["score"] == 10
    assert r["correct"] is True


def test_grade_multiple_all_correct_no_comma():
    r = _grade_multiple("AC", ["A", "C"], 10)
    assert r["score"] == 10
    assert r["correct"] is True


def test_grade_multiple_partial():
    r = _grade_multiple("A", ["A", "C"], 10)
    assert r["score"] == 5  # 1 hit / 2 correct = 0.5 * 10
    assert r["correct"] is False
    assert "命中 1" in r["detail"]


def test_grade_multiple_wrong_selection():
    r = _grade_multiple("A,B,D", ["A", "C"], 10)
    # hit=1 (A), wrong=2 (B,D), ratio = max(0, (1-2)/2) = 0
    assert r["score"] == 0
    assert r["correct"] is False


def test_grade_multiple_empty():
    r = _grade_multiple("", ["A", "C"], 10)
    assert r["score"] == 0
    assert r["correct"] is False
    assert r["comment"] == "未作答"


def test_grade_multiple_case_insensitive():
    r = _grade_multiple("a,c", ["A", "C"], 10)
    assert r["score"] == 10
    assert r["correct"] is True


def test_grade_multiple_ignores_letters_inside_words():
    """夹带文字时不能把英文单词的字母当成选项（2026-11-15 实测 `'选A因为它是LIFO'` → 误选 4 个）。"""
    r = _grade_multiple("选A因为它是LIFO", ["A"], 10)
    assert r["correct"] is True
    assert r["score"] == 10


def test_grade_multiple_partial_with_explanation():
    """带解释的部分答案仍按命中率算分，且不会把解释里的字母算成误选。"""
    r = _grade_multiple("我选A，因为stack是LIFO", ["A", "C"], 10)
    assert r["score"] == 5
    assert r["correct"] is False


# ─── _grade_judge ────────────────────────────────────────────────

def test_grade_judge_correct_right():
    r = _grade_judge("对", ["对"], 10)
    assert r["score"] == 10
    assert r["correct"] is True


def test_grade_judge_correct_wrong():
    r = _grade_judge("错", ["错"], 10)
    assert r["score"] == 10
    assert r["correct"] is True


def test_grade_judge_incorrect():
    r = _grade_judge("对", ["错"], 10)
    assert r["score"] == 0
    assert r["correct"] is False


def test_grade_judge_no_answer():
    r = _grade_judge("", ["对"], 10)
    assert r["score"] == 0


def test_grade_judge_accepts_common_synonyms():
    """学生按常识写「正确 / 错误 / T / F / √ / ×」，不该被规则判错（2026-11-15 实测全是 0 分）。"""
    for user, answer in [("正确", "对"), ("对", "对"), ("T", "对"), ("true", "对"),
                         ("√", "对"), ("是", "对"), ("错误", "错"), ("错", "错"),
                         ("F", "错"), ("false", "错"), ("×", "错"), ("否", "错"),
                         ("不对", "错"), ("不正确", "错"), ("没错", "对")]:
        r = _grade_judge(user, [answer], 10)
        assert r["correct"] is True, f"{user!r} 对 {answer!r} 应判对"


def test_grade_judge_still_rejects_mismatch():
    """同义词归一不能把相反答案判对。"""
    assert _grade_judge("正确", ["错"], 10)["correct"] is False
    assert _grade_judge("错误", ["对"], 10)["correct"] is False


# ─── _grade_fill ─────────────────────────────────────────────────

def test_grade_fill_exact():
    r = _grade_fill("栈", ["栈"], 10)
    assert r["score"] == 10
    assert r["correct"] is True


def test_grade_fill_fuzzy():
    r = _grade_fill("栈是一种LIFO数据结构", ["栈"], 10)
    assert r["score"] == 10
    assert r["correct"] is True


def test_grade_fill_wrong():
    r = _grade_fill("队列", ["栈"], 10)
    assert r["score"] == 0
    assert r["correct"] is False
    assert "参考答案" in r["comment"]


def test_grade_fill_multiple_acceptable():
    r = _grade_fill("O(n)", ["O(n)", "线性复杂度"], 10)
    assert r["score"] == 10


# ─── _grade_short_answer (LLM mock) ──────────────────────────────

def test_grade_short_answer_llm_success(monkeypatch):
    raw = json.dumps({"score": 8, "comment": "回答较好，但缺少复杂度分析"})
    async def fake_call_llm(system, messages, **kw):
        return raw
    monkeypatch.setattr("app.core.quiz.grader.call_llm", fake_call_llm)

    result = asyncio.run(_grade_short_answer(
        "请解释快速排序", "快速排序是分治算法", 10,
        "分治+原地排序+平均O(nlogn)", "提到分治得40%，复杂度得30%，实现得30%",
    ))
    assert result["score"] == 8
    assert result["max_score"] == 10
    assert result["correct"] is False  # 8 < 10
    assert "回答较好" in result["comment"]


def test_grade_short_answer_full_score(monkeypatch):
    raw = json.dumps({"score": 10, "comment": "完美"})
    async def fake_call_llm(system, messages, **kw):
        return raw
    monkeypatch.setattr("app.core.quiz.grader.call_llm", fake_call_llm)

    result = asyncio.run(_grade_short_answer("题", "答", 10, "参考", ""))
    assert result["score"] == 10
    assert result["correct"] is True


def test_grade_short_answer_score_clamp(monkeypatch):
    raw = json.dumps({"score": 999, "comment": "溢出"})
    async def fake_call_llm(system, messages, **kw):
        return raw
    monkeypatch.setattr("app.core.quiz.grader.call_llm", fake_call_llm)

    result = asyncio.run(_grade_short_answer("题", "答", 10, "参考", ""))
    assert result["score"] == 10  # clamp to max


def test_grade_short_answer_llm_failure_degrades(monkeypatch):
    async def fake_call_llm(system, messages, **kw):
        raise RuntimeError("LLM 不可用")
    monkeypatch.setattr("app.core.quiz.grader.call_llm", fake_call_llm)

    result = asyncio.run(_grade_short_answer("题", "答", 10, "参考", ""))
    assert result["score"] == 5  # 降级给半分
    assert result["correct"] is False
    assert "参考" in result["comment"]


def test_grade_short_answer_llm_invalid_json(monkeypatch):
    async def fake_call_llm(system, messages, **kw):
        return "这不是JSON"
    monkeypatch.setattr("app.core.quiz.grader.call_llm", fake_call_llm)

    result = asyncio.run(_grade_short_answer("题", "答", 10, "参考", ""))
    assert result["score"] == 5  # 降级给半分


def test_grade_short_answer_llm_json_with_extra_text(monkeypatch):
    async def fake_call_llm(system, messages, **kw):
        return f'好的，评分如下：{json.dumps({"score": 7, "comment": "不错"})}'
    monkeypatch.setattr("app.core.quiz.grader.call_llm", fake_call_llm)

    result = asyncio.run(_grade_short_answer("题", "答", 10, "参考", ""))
    assert result["score"] == 7


def test_grade_short_answer_disables_thinking(monkeypatch):
    """判分只输出 {"score","comment"} 一小段 JSON → 思考纯浪费（M3 思考与正文共享 max_tokens）"""
    seen = {}
    async def fake_call_llm(system, messages, **kw):
        seen.update(kw)
        return json.dumps({"score": 6, "comment": "尚可"})
    monkeypatch.setattr("app.core.quiz.grader.call_llm", fake_call_llm)

    asyncio.run(_grade_short_answer("题", "答", 10, "参考", ""))

    assert seen.get("thinking") is False


# ─── grade_question (入口) ───────────────────────────────────────

def test_grade_question_single():
    req = QuizGradeRequest(type="single", user_answer="A", answer=["A"])
    r = asyncio.run(grade_question(req))
    assert r["score"] == 10
    assert r["correct"] is True


def test_grade_question_multiple():
    req = QuizGradeRequest(type="multiple", user_answer="A,C", answer=["A", "C"])
    r = asyncio.run(grade_question(req))
    assert r["score"] == 10


def test_grade_question_judge():
    req = QuizGradeRequest(type="judge", user_answer="对", answer=["对"])
    r = asyncio.run(grade_question(req))
    assert r["score"] == 10


def test_grade_question_fill():
    req = QuizGradeRequest(type="fill", user_answer="栈", answer=["栈"])
    r = asyncio.run(grade_question(req))
    assert r["score"] == 10


def test_grade_question_short_answer(monkeypatch):
    raw = json.dumps({"score": 6, "comment": "基本正确"})
    async def fake_call_llm(system, messages, **kw):
        return raw
    monkeypatch.setattr("app.core.quiz.grader.call_llm", fake_call_llm)

    req = QuizGradeRequest(
        type="short_answer",
        question="题",
        user_answer="答",
        analysis="参考",
        comment_prompt="量规",
    )
    r = asyncio.run(grade_question(req))
    assert r["score"] == 6


def test_grade_question_unknown_type():
    req = QuizGradeRequest(type="unknown_type", user_answer="x")
    r = asyncio.run(grade_question(req))
    assert r["score"] == 5  # 半分
    assert "未知题型" in r["comment"]
