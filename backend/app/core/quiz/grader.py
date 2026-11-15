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

from app.core.llm import call_llm
from app.core.quiz.schema import QuizGradeRequest

logger = logging.getLogger("ai-tutor")


def _norm(v: str) -> str:
    """归一化用户作答（去空白、转小写、去空格）"""
    return re.sub(r"\s+", "", str(v)).strip().lower()


# 否定词 + 分句断点：用于排除「不是栈」这类**含答案但语义相反**的作答（见 _fuzzy_match）
_NEGATION_CHARS = "不没无非未别勿"
_CLAUSE_BREAK = "，。、；：,.!?;:()（）【】[]「」\"'"


def _negated_before(u: str, pos: int, window: int = 6) -> bool:
    """`u[pos:]` 之前同一分句内（最多回看 window 字）是否出现否定词。"""
    seen = 0
    i = pos - 1
    while i >= 0 and seen < window:
        ch = u[i]
        if ch in _CLAUSE_BREAK:
            break
        if ch in _NEGATION_CHARS:
            return True
        seen += 1
        i -= 1
    return False


def _occurrences(haystack: str, needle: str) -> list[int]:
    out, i = [], haystack.find(needle)
    while i >= 0:
        out.append(i)
        i = haystack.find(needle, i + 1)
    return out


def _fuzzy_match(user: str, answers: list[str]) -> bool:
    """模糊匹配：完全一致，或作答里出现了参考答案（填空题）。

    ⚠️ 纯子串匹配会把**否定句**判对：`'不是栈'` / `'队列不是栈'` 都含 '栈'。
    实测（2026-11-15）这三类假阳性都真实存在，故对"答案出现在作答中"这一方向
    加否定词守卫 —— 只要有一处**未被否定**的出现就算对。
    """
    u = _norm(user)
    if not u:
        return False
    for a in answers:
        na = _norm(a)
        if not na:
            continue
        if u == na:
            return True
        if na in u and any(not _negated_before(u, i) for i in _occurrences(u, na)):
            return True
        if u in na:
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
    """多选题：全对满分，否则按命中率给部分分。

    ⚠️ 选项字母的抽取**不能**用一个 `[A-Za-z]` 全抓：学生常写
    「我选A，因为stack是LIFO」—— 全抓会把 s/t/a/c/k 都当成选项 → 误选一堆 → 判 0 分
    （2026-11-15 实测）。区分两种输入：
      · 整段只由选项字母构成（`AC` / `A,C` / `a c`）→ 按字符拆，允许小写；
      · 夹带其它文字 → 只认**独立出现**的**大写**选项字母（`选A` / `A 和 C` 里的 A/C）。
    """
    u = _norm(user)
    if not u:
        return {"score": 0, "max_score": points, "correct": False,
                "comment": "未作答", "detail": ""}
    correct_set = set(_norm(a) for a in answers)
    stripped = re.sub(r"[\s,，、;；/|]+", "", u)
    if stripped and set(stripped) <= set("abcdefghij"):
        user_set = set(stripped)
    else:
        # 大写且两侧不是英文字母（避免命中 LIFO / ABC 这类词内部）
        letters = {m.group(0) for m in re.finditer(r"(?<![A-Za-z])([A-J])(?![A-Za-z])", user)}
        user_set = {c.lower() for c in letters} or set()
    if not user_set:
        return {"score": 0, "max_score": points, "correct": False,
                "comment": "没有识别到选项", "detail": ""}
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
        "comment": f"正确答案是 {','.join(sorted(correct_set)).upper()}，"
                   f"你选了 {','.join(sorted(user_set)).upper()}",
        "detail": f"命中 {hit} 个，误选 {wrong} 个",
    }


# 判断题的同义写法归一（学生按常识作答，不该被字面规则判错）
_JUDGE_SYNONYMS = {
    "对": "对", "正确": "对", "是": "对", "真": "对", "t": "对", "true": "对",
    "√": "对", "✓": "对", "y": "对", "yes": "对",
    "错": "错", "错误": "错", "否": "错", "假": "错", "f": "错", "false": "错",
    "×": "错", "✗": "错", "x": "错", "n": "错", "no": "错",
    # 带否定词的整词（必须先于"对/错"的字面比较，否则"不对"会被当成"对"）
    "不对": "错", "不正确": "错", "不成立": "错", "不符合": "错",
    "没错": "对", "成立": "对", "符合": "对",
}


def _judge_norm(v: str) -> str:
    """判断题作答归一：先去标点空白，再查同义词表；查不到原样返回。"""
    s = re.sub(r"[\s。．，,]|^[（(]|[）)]$", "", str(v or "")).strip()
    return _JUDGE_SYNONYMS.get(s, _JUDGE_SYNONYMS.get(s.lower(), s))


def _grade_judge(user: str, answers: list[str], points: int) -> dict:
    """判断题：对/错（含同义写法）一致满分"""
    u = _judge_norm(user)
    expect = _judge_norm(answers[0]) if answers else ""
    correct = bool(expect) and u == expect
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
            kind="quiz_grade",
            # 判分只输出 {"score","comment"} 一小段 JSON，思考与正文共享 max_tokens
            # → 思考既拖慢判分（学生在等）又可能把正文挤空，故关闭。
            thinking=False,
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
