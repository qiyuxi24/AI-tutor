"""题量预算（`app/core/quiz/budget.py`）的纯函数回归。

钉的是「出几道题由内容决定」这条契约：改动前题量是前端下拉框里的
`[3,5,8,10]`，与正文长度、小节数都无关（17377 字只出过 4 道）。这里逐条锁死
docstring 记录的算例、上下限夹取、以及 `reason` 里必须带的可核验数字 ——
前端要把 `reason` 直接显示给用户，数字没了就等于退回"没有来由的数字"。

全部是纯函数测试：不读库、不调 LLM、不碰文件系统。
"""

import pytest

from app.core.quiz.budget import (
    QUIZ_CHARS_PER_QUESTION,
    QUIZ_MAX_QUESTIONS,
    QUIZ_MIN_QUESTIONS,
    CountBudget,
    auto_question_count,
    compute_budget,
)


# ─── auto_question_count：docstring 里的四个算例，逐个钉死 ────────────


def test_auto_count_8_sections_17377_chars_is_capped_at_max():
    """8 节 / 17377 字 → 15（撞上限）。

    这是改造的起点：同一个节点改动前只出过 4 道题，而正文按 350 字/题能撑 49 道，
    上限 15 是并发批次与前端 300s 超时的交集，不是内容算出来的 49。
    """
    assert auto_question_count(section_count=8, content_chars=17377) == QUIZ_MAX_QUESTIONS


def test_auto_count_5_sections_11040_chars_is_capped_at_max():
    """5 节 / 11040 字 → 15。

    第二个实测节点（参考系与坐标系）。cap=31、floor=5，仍然被 MAX 截断 ——
    说明"长正文 + 少小节"也是撞上限的典型形状。
    """
    assert auto_question_count(section_count=5, content_chars=11040) == QUIZ_MAX_QUESTIONS


def test_auto_count_thin_node_1_section_1186_chars_is_min_floor():
    """1 节 / 1186 字 → 3（下限）。

    cap = 1186 // 350 = 3，恰好等于下限：正文只撑得住 3 道，不能因为"想要更多"
    就硬出 —— 这正是旧下拉里 8/10 从未被用到的反面（数字该由内容说）。
    """
    assert auto_question_count(section_count=1, content_chars=1186) == QUIZ_MIN_QUESTIONS


def test_auto_count_8_sections_400_chars_prefers_coverage_over_content_cap():
    """8 节 / 400 字 → 8：取 max 而不是 min，即**覆盖率优先**。

    cap 只有 max(3, 1)=3，但 8 个小节每节都该被考到，否则学生只被考了 1/8。
    这条是"取 min 会让骨架节点漏考"的判据：实现若把 max 写成 min，这里必红。
    """
    assert auto_question_count(section_count=8, content_chars=400) == 8


# ─── 边界：小节数下限、空正文、极大正文 ────────────────────────────


@pytest.mark.parametrize("bad_sections", [0, -1, -99])
def test_auto_count_non_positive_sections_treated_as_single_section(bad_sections):
    """section_count=0 或负数按 1 处理（未小节化的老节点）。

    图谱里未小节化的老节点没有小节清单，调用方可能传 0；若不夹到 1，
    `section_floor=0` 会让长正文节点的下限计算悄悄失真。
    """
    assert auto_question_count(section_count=bad_sections, content_chars=1186) == 3
    # 与"显式传 1"逐字节等价，证明是"按 1 处理"而不是别的兜底
    assert auto_question_count(section_count=bad_sections, content_chars=1186) == \
        auto_question_count(section_count=1, content_chars=1186)


def test_auto_count_zero_chars_returns_min_questions():
    """content_chars=0 → QUIZ_MIN_QUESTIONS（不是 0 道）。

    `content_cap` 先被夹到下限再参与 max：正文为 0 时算出来的 0 道是荒谬的，
    一套题至少 3 道（下限的定义）。同时验证下限常量没有被改小。
    """
    assert auto_question_count(section_count=1, content_chars=0) == QUIZ_MIN_QUESTIONS
    assert auto_question_count(section_count=1, content_chars=0) == 3


def test_auto_count_huge_body_never_exceeds_max():
    """极大正文（千万字 / 50 节）也不超过 QUIZ_MAX_QUESTIONS。

    上限是硬约束：超了就要改成后台任务 + 通知，不能靠"内容多"绕过 ——
    否则前端 300s 超时会被打穿。顺带确认不会因大整数溢出之类返回怪值。
    """
    assert auto_question_count(section_count=50, content_chars=10_000_000) == QUIZ_MAX_QUESTIONS
    assert auto_question_count(section_count=50, content_chars=10_000_000) == 15


def test_chars_per_question_constant_is_the_documented_value():
    """每题的正文配额 = 350 字（Questgen 60 词≈90 汉字，再留 4 倍富余）。

    常量本身是口径：改了它，所有算例都会漂。这里显式钉住，让改动必须是有意的。
    """
    assert QUIZ_CHARS_PER_QUESTION == 350
    assert QUIZ_MIN_QUESTIONS == 3
    assert QUIZ_MAX_QUESTIONS == 15


# ─── compute_budget：三个 reason 分支各一例（只断言关键数字，不断言整句）───


def test_compute_budget_reason_capped_branch():
    """撞上限分支：8 节 / 17377 字 → count=15、capped=True、reason 带节数与字数。

    `capped` 是给前端做"已到上限"提示用的；`reason` 里的 8 与 17377 是可核验的
    依据。**只断言数字出现**，不断言整句文案 —— 文案会微调，数字不会。
    """
    b = compute_budget(sections=[{"id": f"s{i}", "title": f"小节{i}"} for i in range(8)],
                       content_chars=17377)
    assert isinstance(b, CountBudget)
    assert b.count == 15
    assert b.capped is True
    assert b.content_chars == 17377
    assert b.section_count == 8
    assert "8 个小节" in b.reason
    assert "17377" in b.reason


def test_compute_budget_reason_section_floor_branch():
    """「每节至少一道」分支：8 节 / 400 字 → count=8、capped=False。

    section_floor(8) >= content_cap(3)，此时解释应讲"小节数"而不是"正文长度"；
    reason 里必须出现 8 与 400，否则用户看不出这个 8 是从哪来的。
    """
    b = compute_budget(
        sections=[{"id": f"s{i}", "title": f"小节{i}", "chars": 50} for i in range(8)],
    )
    assert b.count == 8
    assert b.capped is False
    assert b.content_chars == 400
    assert b.section_count == 8
    assert "8 个小节" in b.reason
    assert "400" in b.reason


def test_compute_budget_reason_content_length_branch():
    """「按正文长度」分支：1 节 / 1186 字 → count=3、capped=False。

    content_cap(3) > section_floor(1)，此时题量完全由正文撑起；reason 必须带
    1186 这个数字，用户才能对照节点正文验证。
    """
    b = compute_budget(sections=[{"id": "s1", "title": "定义", "chars": 1186}])
    assert b.count == 3
    assert b.capped is False
    assert b.content_cap == 3
    assert b.section_floor == 1
    assert "1186" in b.reason
    assert "1 个小节" in b.reason


# ─── compute_budget：小节 chars 的求和与缺失容错 ────────────────────


def test_compute_budget_sums_section_chars_when_content_chars_omitted():
    """省略 content_chars 时由各节 chars 求和（调用方不必自己算一遍）。

    3 节各 1400 字 = 4200 字 → cap = 12，floor = 3，取 12。若实现改成"看第一节"
    或"看最大值"，这里会偏离 4200。
    """
    sections = [{"id": f"s{i}", "title": f"小节{i}", "chars": 1400} for i in range(3)]
    b = compute_budget(sections=sections)
    assert b.content_chars == 4200
    assert b.content_cap == 12
    assert b.count == 12


def test_compute_budget_explicit_content_chars_overrides_section_sum():
    """显式给了 content_chars 就以它为准（小节 chars 只用于逐节展示）。

    正文可能被裁剪过或含代码块，调用方传的数是唯一真值；两者不一致时不能
    悄悄用求和覆盖它 —— 那样 reason 里显示的字数会与调用方实际拿到的不符。
    """
    sections = [{"id": "s1", "title": "定义", "chars": 100}]
    b = compute_budget(sections=sections, content_chars=3500)
    assert b.content_chars == 3500
    assert b.content_cap == 10
    assert b.count == 10


def test_compute_budget_missing_chars_key_counts_as_zero_and_does_not_raise():
    """各节缺 `chars` 键时按 0 处理且不抛异常（老 manifest 没有这个字段）。

    小节 manifest 的 `chars` 是后加的字段，存量节点没有它。缺失就按 0 求和、
    并把补齐后的 `chars` 放回 `sections` 供前端逐节展示，绝不能 KeyError。
    """
    sections = [{"id": "s1", "title": "定义"}, {"id": "s2", "title": "性质"}]
    b = compute_budget(sections=sections)
    assert [s["chars"] for s in b.sections] == [0, 0]
    assert b.content_chars == 0
    # 2 节的下限是 2，但题量下限 3 兜住 → 3
    assert b.count == 3
    assert b.section_floor == 2


def test_compute_budget_does_not_mutate_caller_sections():
    """`compute_budget` 复制传入的小节字典，不就地改调用方的数据。

    调用方常常把 `kg.list_sections()` 的结果直接传进来；若在这里就地补 `chars`
    会污染调用方的缓存对象（同一份清单后续还要用）。
    """
    sections = [{"id": "s1", "title": "定义"}]
    compute_budget(sections=sections)
    assert "chars" not in sections[0]


# ─── compute_budget：两个参数都不给的兜底 ──────────────────────────


def test_compute_budget_without_arguments_falls_back_to_min_and_explains():
    """`compute_budget()` 无参 → count=下限、reason 非空、sections 为空列表。

    前端「自动」模式可能在节点还没选时先打一次预览；此时不能返回 0 道，
    也不能返回空 reason（空字符串会让前端显示"3 道 · 依据："这种断头话）。
    """
    b = compute_budget()
    assert b.count == QUIZ_MIN_QUESTIONS
    assert b.reason.strip() != ""
    assert b.sections == []
    assert b.section_count == 1
    assert b.content_chars == 0
    assert b.capped is False
