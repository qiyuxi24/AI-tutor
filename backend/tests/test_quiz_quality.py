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


def test_check_length_below_20_without_options():
    """没选项的题没有信息兜底，仍按 20 字下限把关（旧阈值不变）。"""
    q = _make_q("a" * 19)
    ok, _ = _check_length(q)
    assert not ok


def test_check_length_allows_short_stem_with_rich_options():
    """2026-09-30 真机回归：15 字题干 + 4 个实质选项，是正常客观题，不得误杀。

    日志：`出题过滤: 题干过短(15字) | 下列关于图的描述中，正确的是？`
    """
    q = _make_q(
        "下列关于图的描述中，正确的是？",
        options=[
            QuestionOption(label="有向图中每条边都有方向", value="A"),
            QuestionOption(label="图不能有孤立顶点", value="B"),
            QuestionOption(label="无向图的边数必为偶数", value="C"),
            QuestionOption(label="图中必存在回路", value="D"),
        ],
    )
    ok, reason = _check_length(q)
    assert ok, reason


def test_check_length_rejects_very_short_stem():
    """放宽到 12 字，但仍然拦掉明显不成句的题干（含选项也拦）。"""
    q = _make_q(
        "下列说法正确的是？",
        options=[QuestionOption(label="甲", value="A"),
                 QuestionOption(label="乙", value="B"),
                 QuestionOption(label="丙", value="C"),
                 QuestionOption(label="丁", value="D")],
    )
    ok, reason = _check_length(q)
    assert not ok
    assert "过短" in reason


def test_check_length_short_answer_needs_20():
    """简答题没有选项承载信息 → 保持 20 字下限。"""
    q = _make_q("请简述图的定义。", type="short_answer")
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
                "图中所标注的边", "表格中", "上面第1题", "如图所示"]
    for pat in patterns:
        q = _make_q(f"关于{pat}的描述，请回答下列问题这是足够的长度。")
        ok, _ = _check_self_contained(q)
        assert not ok, f"应过滤'{pat}'"


def test_check_self_contained_bare_tuzhong_is_allowed():
    """2026-09-30 真机回归：图论题自带的"有向图中/无向图中"**不是**外部依赖，不得误杀。

    日志：`出题过滤: 题目依赖外部信息(图中) | 根据参考材料中关于弧集与边集的转换规则，下列说法正确的是？`
    —— 题干没毛病，是**选项**里的"…图中…"被裸正则命中的。
    """
    q = _make_q(
        "根据参考材料中关于弧集与边集的转换规则，下列说法正确的是？",
        options=[
            QuestionOption(label="有向图中每条边都有方向", value="A"),
            QuestionOption(label="无向图中度数为奇数的顶点个数为偶数", value="B"),
            QuestionOption(label="弧集与边集可以互换使用", value="C"),
            QuestionOption(label="简单图中允许出现自环", value="D"),
        ],
    )
    ok, reason = _check_self_contained(q)
    assert ok, reason


def test_check_self_contained_still_rejects_figure_reference():
    """收窄后仍要拦住真正指图的题（题干或选项里都拦）。"""
    assert not _check_self_contained(
        _make_q("图中所标注的层次关系体现了树的哪条性质？"))[0]
    assert not _check_self_contained(_make_q(
        "关于图中A点的度数下列说法正确的是什么来着？",
        options=[QuestionOption(label="选项A", value="A"),
                 QuestionOption(label="选项B", value="B"),
                 QuestionOption(label="选项C", value="C"),
                 QuestionOption(label="选项D", value="D")]))[0]
    # 选项里指图同样拦
    assert not _check_self_contained(_make_q(
        "下列关于图的说法正确的是哪一个选项？",
        options=[QuestionOption(label="图里标出的那条边是桥", value="A"),
                 QuestionOption(label="选项B内容", value="B")]))[0]


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


# ─── 2026-09-30 真机回归："图论节点连续 3 道被误杀 → 0 道题" ──────────

def test_filter_questions_graph_node_real_machine_regression():
    """
    复刻真机日志里被枪决的 3 道题（节点 graph_definition_and_terminology），
    断言管道**至少放行 1 道** —— 这是"出题不再全灭"的最小回归网。

    原始日志：
      01:04:44 出题过滤: 题目依赖外部信息(图中) | 根据参考材料中关于弧集与边集的转换规则，下列说法正确的是？
      01:05:04 出题过滤: 题干过短(15字)        | 下列关于图的描述中，正确的是？
      01:05:14 出题过滤: 题干过短(18字)        | 下列关于图的定义的叙述中，错误的是？
      01:05:14 WARNING 出题数量不足：请求 1 道，实际 0 道（补题 2 轮）
    """
    def opts(*labels):
        return [QuestionOption(label=x, value=v) for x, v in zip(labels, "ABCD")]

    questions = [
        _make_q("根据参考材料中关于弧集与边集的转换规则，下列说法正确的是？",
                options=opts("有向图中每条边都有方向", "无向图中度数为奇数的顶点个数为偶数",
                             "弧集与边集可以互换", "简单图中允许自环"), id="q1"),
        _make_q("下列关于图的描述中，正确的是？",
                options=opts("图由顶点集和边集组成", "图中必存在回路",
                             "孤立顶点不属于图", "无向图的边有方向"), id="q2"),
        _make_q("下列关于图的定义的叙述中，错误的是？",
                options=opts("图可以用二元组表示", "边一定是两个顶点的有序对",
                             "无向边记作无序对", "顶点集不能为空"), id="q3"),
    ]
    passed, rejected = filter_questions(questions)
    assert len(passed) == 3, [q.question for q in passed]
    assert rejected == 0
