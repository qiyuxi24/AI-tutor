"""
出题同意判定测试（`core/quiz/consent.py`，2026-09-30）。

用户口径：出题前必须先问学生，按他的回答行动 ——
  · 同意（"好""出吧""考考我"）→ 才允许出题；
  · 不要 / 要求先讲（"先讲讲""不用了"）→ 不出题，转讲解；
  · 说不清楚 → **不放行**（宁可多问一句，也不要擅自出题）。

纯函数、全离线。
"""
import pytest

from app.core.quiz.consent import (
    CONSENT_QUIZ_NO,
    CONSENT_QUIZ_UNKNOWN,
    CONSENT_QUIZ_YES,
    ask_first_text,
    classify_quiz_consent,
    refusal_text,
)


# ─── 同意 ───────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "好", "好的", "好啊", "好了", "嗯嗯", "行", "可以", "要", "来吧", "开始吧",
    "继续", "出吧", "出题吧", "考考我", "测一下", "练一道", "试试", "没问题",
    "都行", "随便", "OK", "ok！", "yes",
    "再来一道", "再出题", "再练练", "再考考我",            # 主动续练
])
def test_consent_yes(text):
    assert classify_quiz_consent(text) == CONSENT_QUIZ_YES


# ─── 拒绝 / 要求先讲 ─────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "不要", "不用了", "不行", "先讲讲吧", "再讲一遍", "别出了", "算了",
    "我不会", "听不懂", "不清楚", "先解释一下", "等等", "复习一下",
])
def test_consent_no(text):
    assert classify_quiz_consent(text) == CONSENT_QUIZ_NO


def test_negative_wins_over_positive():
    """中文否定坑："不要" 含 "要"、"不行" 含 "行" —— 拒绝词必须先匹配。"""
    assert classify_quiz_consent("不要") == CONSENT_QUIZ_NO
    assert classify_quiz_consent("不行") == CONSENT_QUIZ_NO
    assert classify_quiz_consent("先讲讲吧") == CONSENT_QUIZ_NO
    assert classify_quiz_consent("再讲一遍") == CONSENT_QUIZ_NO   # 与"再来一道"一字之差


# ─── 不确定 ─────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "", "   ", None,
    "为什么栈是后进先出？",                     # 提问 ≠ 同意
    "这个我还是有点模糊，你能再说说二叉树的性质吗？",   # 长句不猜
    "A", "ACD",                                # 作答 ≠ 同意
])
def test_consent_unknown(text):
    assert classify_quiz_consent(text) == CONSENT_QUIZ_UNKNOWN


def test_unknown_is_not_no():
    """不确定 ≠ 拒绝：工具会分别给"先问一句"和"按他说的先讲"两种回执。"""
    assert CONSENT_QUIZ_UNKNOWN != CONSENT_QUIZ_NO


# ─── 给模型的两句回执 ───────────────────────────────────────────

def test_refusal_text_points_back_to_teaching():
    out = refusal_text("先讲讲吧")
    assert "不要出题" in out and "讲清楚" in out
    assert "先讲讲吧" in out          # 把学生原话回显给模型，避免它理解偏


def test_ask_first_text_demands_asking():
    out = ask_first_text("为什么栈是后进先出？")
    assert "没有明确同意出题" in out
    assert "要不要出一道题检验一下" in out
    assert "不要" in out and "题目稍等" in out    # 明确禁止用承诺代替提问
