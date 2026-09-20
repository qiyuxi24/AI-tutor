"""工具 `grade_answer` —— 给学生刚作答的题判分（**掌握度的唯一主信号**）。

**规则判分**：single / multiple / judge / fill 四种题型全部 0 token、瞬时返回
（实现见 `core/quiz/chat_grade.py::grade_pending_answer`）。

答对 → 该知识点掌握度**确定性 +20**（`mastery_bucket()` 分档：WEAK 30 / MASTERED 70）。
这是掌握度更新的**唯一主信号**（`update_mastery` 只处理 3 种硬证据，见 `update_mastery.py`）。

⚠️ 提示词要点：判分结果里含参考答案与解析 —— 必须写清"答错时**不要**直接给答案"，
否则模型会拿解析当讲解直接念出来，破坏苏格拉底式教学。
"""

from ..registry import _spec

DESCRIPTION = ("对学生刚作答的题目判分并记录（规则判分，瞬时返回）。"
               "学生回答你出的题之后调用，只需把学生的原话传进来，系统会自动找到那道待作答的题。"
               "答对会自动提升该知识点的掌握度；判分结果里含参考答案与解析，"
               "请据此决定是『简短肯定后推进』还是『回到苏格拉底式追问』——答错时**不要**直接给答案。")

PARAMETERS = {
    "type": "object",
    "properties": {
        "user_answer": {
            "type": "string",
            "description": "学生的原始作答内容，如 'A'、'AC'、'对'、'栈'",
        },
    },
    "required": ["user_answer"],
}

GUIDANCE = """
判分：把学生的原话传进来，系统自动找到那道待作答的题（规则判分，瞬时返回，0 token）。
- **何时用**：学生作答了你出的题之后。
- 判分结果里含参考答案与解析，据此决定后续：
  答对 → 简短肯定 + 点出关键要点，自然推进到下一步；
  答错 → **不要直接给出答案**，回到苏格拉底式追问，把学生引到正确思路上。
- 答对会自动提升该知识点掌握度（系统行为，你不需要再调 `update_mastery`）。
"""


async def handler(args, kg) -> str:
    """协程 handler：`chat_grade.grade_pending_answer` 是 async，由 dispatch 直接 await。"""
    from app.core.quiz.chat_grade import grade_pending_answer

    user_answer = str(args.get("user_answer", "") or "").strip()
    if not user_answer:
        return "缺少 user_answer（学生的原始作答内容），请把它一并传进来。"
    return await grade_pending_answer(kg, user_answer)


SPEC = _spec("grade_answer", DESCRIPTION, PARAMETERS, handler, guidance=GUIDANCE)
