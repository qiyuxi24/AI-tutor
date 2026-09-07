"""
Quiz quality 质量过滤管道测试。
覆盖：长度过滤、自包含性过滤、去重、组合管道。
"""
from app.core.quiz.schema import Question, QuestionOption
from app.core.quiz.quality import (
    _check_length,
    _check_self_contained,
    _normalize,
    _deduplicate,
    filter_questions,
)


def _make_q(text: str, **kw) -> Question:
    defaults = {"id": "q1", "type": "single", "question": text}
    defaults.update(kw)
    return Question(**defaults)


# ─── _normalize ──────────────────────────────────────────────────

def test_normalize_strips_whitespace_punctuation():
    assert _normalize("Hello， World。") == "helloworld"
    assert _normalize("ABC!  def?") == "abcdef"
    assert _normalize("（括号）内容") == "括号内容"


def test_normalize_empty():
    assert _normalize("") == ""
    assert _normalize("   ") == ""


# ─── _check_length ──────────────────────────────────────────────

def test_check_length_valid():
    q = _make_q("这是一段长度适中的题干内容用于测试，大约二十多个字符。")
    ok, reason = _check_length(q)
    assert ok
    assert reason == "ok"


def test_check_length_too_short():
    q = _make_q("太短了")
    ok, reason = _check_length(q)
    assert not ok
    assert "过短" in reason


def test_check_length_minimum_20():
    q = _make_q("a" * 20)
    ok, _ = _check_length(q)
    assert ok


def test_check_length_below_20():
    q = _make_q("a" * 19)
    ok, _ = _check_length(q)
    assert not ok


def test_check_length_max_500():
    q = _make_q("a" * 500)
    ok, _ = _check_length(q)
    assert ok


def test_check_length_above_500():
    q = _make_q("a" * 501)
    ok, _ = _check_length(q)
    assert not ok


# ─── _check_self_contained ───────────────────────────────────────

def test_check_self_contained_ok():
    q = _make_q("什么是数据结构中的栈结构？")
    ok, reason = _check_self_contained(q)
    assert ok
    assert reason == "ok"


def test_check_self_contained_external_ref():
    q = _make_q("如上图所示，栈的特点是什么？这是一个二十个字符以上的题干。")
    ok, reason = _check_self_contained(q)
    assert not ok
    assert "外部信息" in reason


def test_check_self_contained_in_options():
    q = _make_q(
        "下列关于队列的描述正确的是什么意思题干。",
        options=[
            QuestionOption(label="见下图所示的内容", value="A"),
            QuestionOption(label="选项B的内容", value="B"),
        ],
    )
    ok, _ = _check_self_contained(q)
    assert not ok  # 选项中有"见下图"


def test_check_self_contained_all_patterns():
    patterns = ["如下图", "根据上文", "见下图", "参考上文", "由上图",
               "图中", "表格中", "上面第1题"]
    for pat in patterns:
        q = _make_q(f"关于{pat}的描述，请回答下列问题这是足够的长度。")
        ok, _ = _check_self_contained(q)
        assert not ok, f"应过滤'{pat}'"


# ─── _deduplicate ───────────────────────────────────────────────

def test_deduplicate_no_duplicates():
    qs = [
        _make_q("题目一内容足够长满足最低长度要求", id="q1"),
        _make_q("题目二内容足够长满足最低长度要求", id="q2"),
    ]
    result = _deduplicate(qs)
    assert len(result) == 2


def test_deduplicate_exact():
    q1 = _make_q("完全相同的题目内容足够长满足最低长度要求")
    q2 = _make_q("完全相同的题目内容足够长满足最低长度要求")
    result = _deduplicate([q1, q2])
    assert len(result) == 1


def test_deduplicate_normalized():
    q1 = _make_q("题目 ABC！")
    q2 = _make_q("题目abc")
    result = _deduplicate([q1, q2])
    assert len(result) == 1


def test_deduplicate_empty_list():
    assert _deduplicate([]) == []


# ─── filter_questions pipeline ───────────────────────────────────

def test_filter_questions_all_pass():
    qs = [
        _make_q("题目一内容足够长满足最低长度要求没有外部依赖", id="q1"),
        _make_q("题目二内容足够长满足最低长度要求没有外部依赖", id="q2"),
    ]
    passed, rejected = filter_questions(qs)
    assert len(passed) == 2
    assert rejected == 0


def test_filter_questions_too_short():
    qs = [_make_q("短题", id="q1")]
    passed, rejected = filter_questions(qs)
    assert len(passed) == 0
    assert rejected == 1


def test_filter_questions_external_dependency():
    qs = [_make_q("如下图所示这是关于栈的描述题干内容足够长", id="q1")]
    passed, rejected = filter_questions(qs)
    assert len(passed) == 0
    assert rejected == 1


def test_filter_questions_duplicates():
    text = "完全相同的题目内容足够长满足最低长度要求没有依赖"
    qs = [_make_q(text, id="q1"), _make_q(text, id="q2")]
    passed, rejected = filter_questions(qs)
    assert len(passed) == 1
    assert rejected == 1


def test_filter_questions_mixed():
    qs = [
        _make_q("短", id="q1"),                                           # 过短
        _make_q("如下图所示关于队列的描述题干内容足够长", id="q2"),          # 依赖外部
        _make_q("正常题目一内容足够长满足最低长度要求没有依赖", id="q3"),   # 通过
        _make_q("正常题目一内容足够长满足最低长度要求没有依赖", id="q4"),   # 重复
    ]
    passed, rejected = filter_questions(qs)
    assert len(passed) == 1
    assert rejected == 3


def test_filter_questions_empty_input():
    passed, rejected = filter_questions([])
    assert len(passed) == 0
    assert rejected == 0
