"""
出题模块 — 判分器（grader）

判分策略：
- single / multiple / judge / fill：规则判分（快、准、零成本）
    - single/judge/fill：答对满分，否则 0 分（fill 用模糊匹配，接受近似答案）
    - multiple：全对满分，部分正确按比例得分（避免过于严苛）
- short_answer：LLM 判分（参考 OpenMAIC quiz-grade 逻辑，正则提 JSON + 兜底）

返回统一结构：
    {"score": int, "max_score": int, "correct": bool, "comment": str, "detail": str}
"""

import json
import logging
import re
from typing import Optional

from app.core.llm_client import call_llm
from app.core.quiz.schema import QuizGradeRequest

logger = logging.getLogger("ai-tutor")


def _norm(v: str) -> str:
    """归一化用户作答（去空白、转小写、去空格）"""
    return re.sub(r"\s+", "", str(v)).strip().lower()


def _fuzzy_match(user: str, answers: list[str]) -> bool:
    """模糊匹配：完全一致 或 用户作答包含参考答案关键内容（填空题）"""
    u = _norm(user)
    if not u:
        return False
    for a in answers:
        na = _norm(a)
        if na and (u == na or na in u or u in na):
            return True
    return False


def _grade_single(user: str, answers: list[str], points: int) -> dict:
    """单选题：完全一致满分，否则 0 分"""
    u = _norm(user)
    correct = bool(answers) and u == _norm(answers[0])
    return {
        "score": points if correct else 0,
        "max_score": points,
        "correct": correct,
        "comment": "回答正确" if correct else "回答错误",
        "detail": "",
    }


def _grade_multiple(user: str, answers: list[str], points: int) -> dict:
    """多选题：全对满分，否则按命中率给部分分"""
    u = _norm(user)
    if not u:
        return {"score": 0, "max_score": points, "correct": False,
                "comment": "未作答", "detail": ""}
    # 用户按 A,C 或 AC 输入，拆成集合
    user_set = set(re.findall(r"[A-Za-z]", u))
    correct_set = set(_norm(a) for a in answers)
    if user_set == correct_set:
        return {"score": points, "max_score": points, "correct": True,
                "comment": "回答正确", "detail": ""}
    # 部分正确：命中数 / 正确答案数 * 分数（并扣掉误选）
    hit = len(user_set & correct_set)
    wrong = len(user_set - correct_set)
    ratio = max(0.0, (hit - wrong) / max(len(correct_set), 1))
    score = round(points * ratio)
    return {
        "score": score,
        "max_score": points,
        "correct": False,
        "comment": f"正确答案是 {','.join(sorted(correct_set))}，你选了 {','.join(sorted(user_set))}",
        "detail": f"命中 {hit} 个，误选 {wrong} 个",
    }


def _grade_judge(user: str, answers: list[str], points: int) -> dict:
    """判断题：对/错完全一致满分"""
    u = _norm(user)
    correct = bool(answers) and (u in (_norm(answers[0]), "对", "错")) \
        and u == _norm(answers[0])
    return {
        "score": points if correct else 0,
        "max_score": points,
        "correct": correct,
        "comment": "回答正确" if correct else "回答错误",
        "detail": "",
    }


def _grade_fill(user: str, answers: list[str], points: int) -> dict:
    """填空题：模糊匹配（接受近似答案）"""
    correct = _fuzzy_match(user, answers)
    return {
        "score": points if correct else 0,
        "max_score": points,
        "correct": correct,
        "comment": "回答正确" if correct else f"参考答案: {' / '.join(answers)}",
        "detail": "",
    }


# ────────────────────────────────────────────
#  简答题：LLM 判分
# ────────────────────────────────────────────

_SHORT_ANSWER_SYSTEM_PROMPT = """你是一位专业的教育评估专家。请根据题目和学生答案进行评分并给出简短评语。
必须以如下 JSON 格式回复（不要包含其他内容）：
{{"score": <0到{points}的整数>, "comment": "<一两句评语>"}}"""


async def _grade_short_answer(
    question: str, user_answer: str, points: int,
    analysis: str, comment_prompt: str,
) -> dict:
    """简答题：LLM 判分（OpenMAIC quiz-grade 逻辑）"""
    system_prompt = _SHORT_ANSWER_SYSTEM_PROMPT.format(points=points)
    user_prompt = (
        f"题目：{question}\n"
        f"满分：{points}分\n"
        f"参考答案要点：{analysis}\n"
        + (f"评分量规：{comment_prompt}\n" if comment_prompt else "")
        + f"学生答案：{user_answer}"
    )
    try:
        raw = await call_llm(
            system_prompt,
            [{"role": "user", "content": user_prompt}],
        )
        # 提取 JSON
        m = re.search(r"\{[\s\S]*\}", raw or "")
        if not m:
            raise ValueError("No JSON found")
        parsed = json.loads(m.group(0))
        score = max(0, min(points, round(float(parsed.get("score", 0)))))
        comment = str(parsed.get("comment", ""))
        return {
            "score": score,
            "max_score": points,
            "correct": score >= points,
            "comment": comment,
            "detail": "",
        }
    except Exception as e:
        logger.warning(f"简答题 LLM 判分失败，降级给半分: {e}")
        return {
            "score": round(points * 0.5),
            "max_score": points,
            "correct": False,
            "comment": "已作答，请参考参考答案要点。",
            "detail": "",
        }


async def grade_question(req: QuizGradeRequest) -> dict:
    """
    判分入口。

    规则判分（single/multiple/judge/fill）同步完成；
    简答题（short_answer）异步调用 LLM。
    """
    qtype = req.type
    points = req.points

    if qtype == "single":
        return _grade_single(req.user_answer, req.answer, points)
    if qtype == "multiple":
        return _grade_multiple(req.user_answer, req.answer, points)
    if qtype == "judge":
        return _grade_judge(req.user_answer, req.answer, points)
    if qtype == "fill":
        return _grade_fill(req.user_answer, req.answer, points)
    if qtype == "short_answer":
        return await _grade_short_answer(
            req.question, req.user_answer, points,
            req.analysis, req.comment_prompt,
        )
    # 未知题型：给半分隔离
    return {
        "score": round(points * 0.5),
        "max_score": points,
        "correct": False,
        "comment": f"未知题型 {qtype}，按部分分处理",
        "detail": "",
    }
