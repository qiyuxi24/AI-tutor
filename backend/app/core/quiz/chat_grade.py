"""
对话内判分 —— 「学生作答 → 规则判分 → 记录作答 → 掌握度回写」闭环。

**可被任何模块单独调用**（不依赖对话、不依赖出题模块）：

    r = await grade_pending("A", store=my_store)      # 只要分数（结构化 dict）
    r = await grade_pending("A", user_id=7)           # 有用户 id → 走默认题库
    r = await grade_pending("A", kg=kg)               # 顺带回写掌握度
    text = await grade_pending_answer(kg, "A")        # 给模型看的自然语言文案

三条可复用性保证
    1. **题库可注入**：`store=` 直接指定题库实例；不传再按 `user_id`（或 `kg.user_id`）
       取默认单例，且题库模块是**延迟导入**的 —— 注入时不会把它连带拉起
    2. **运行时不依赖知识图谱**：`KnowledgeGraph` 只出现在类型标注（`TYPE_CHECKING`），
       运行期零导入；`kg` 是鸭子类型（只用到 `user_id` / `get_node` / `update_node_info`）
       —— **不传 kg 就是纯判分，完全不碰图谱**
    3. **结构化返回值**：`grade_pending()` 回 dict（分数 / 对错 / 解析 / 掌握度说明），
       需要文案再用 `grade_pending_answer()` 包一层

为什么与出题分开（`chat_quiz.py`）
    两者生命周期、依赖、失败模式完全不同：
      - 出题：**后台异步**、重 LLM（单题 ~40s）、失败要推 `QUIZ_READY(ok=False)` 事件
      - 判分：**同步**、规则（0 token、瞬时）、失败只是返回一句文案
    合在一个文件里时，"出题改动"很容易误伤判分链路（也会把 40s 的超时语义
    传染给本该瞬时的判分）。故拆成两个模块，各自独立演进。

职责边界
    chat_quiz.py    出题：起后台任务 → 生成 → 入库 → 推 `QUIZ_READY`
    chat_grade.py   判分：反查待作答题目 → 判分 → 记录作答 → 掌握度回写（本模块）
    grader.py       判分**规则**本身（题型分发：single / multiple / judge / fill / short_answer）

为什么题目 id 不给模型
    题目是后台异步生成的，模型调 `quiz_generate` 时题目还不存在，所以它不可能知道
    题库 id。判分靠 `QuizStore.get_pending_question()` 反查"最近一道待作答的题"。

掌握度为什么在这里更新（而不是让模型调 `update_mastery`）
    实测模型**从不主动调用** `update_mastery`（12 次运行 0 次）—— 软约束压不过教学铁律。
    改成"判分答对 = 确定性 +20"这一硬信号后，进度才真正动起来。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:                       # 只在类型检查期导入 → 运行时不依赖图谱模块
    from app.core.knowledge_graph import KnowledgeGraph

logger = logging.getLogger("ai-tutor")

# 判分默认只认"对话内出的题"；传 source="" 表示不限来源（题库页历史题也算）
DEFAULT_SOURCE = "chat"

# 答对一道题的确定性掌握度增益（"答对就直接更新进度"）
MASTERY_CORRECT_GAIN = 20

# 小节题答错几次后把「已懂」降级为「不懂」（用户口径：先讲解 + 再出一题，第二次仍错才降级）
SECTION_RETRY_BEFORE_DOWNGRADE = 1


def apply_section_after_answer(kg: KnowledgeGraph, *, node_id: str, section_id: str,
                               correct: bool, evidence: str = "") -> dict:
    """
    按**小节**判定答题结果（题目带 `section_id` 时走这里，不再用节点级 +20 的口径）。

    用户口径（2026-09-29）：
      · 答对 → 该小节 `passed=True`；**全部小节通过 → 节点掌握度直接置 100**；
      · 答错 → 第一次**不降级**（`action=retry`，调用方立刻再出该节一题）；
                第二次仍错 → 把小节标成「不懂」（`action=downgrade`），转入教学。

    返回 `{applied, action: passed|retry|downgrade|none, note}`；
    `note` 是给模型看的自然语言（可空）。

    为什么掌握度回写用 `caller="human"`：这是**用户答题**的结果，不是 AI 自作主张；
    用 `"ai"` 会被 `_guard_human_content` 拦下（AI 无权改人类创建的节点）。
    """
    from app.core.knowledge_graph import (      # 延迟导入：保持本模块运行时不依赖图谱
        LEARN_MARK_BY_AI, LEARN_MARK_CONFUSED, MASTERY_ALL_SECTIONS_PASSED,
    )
    try:
        state = kg.get_section_learn(node_id, section_id)
    except Exception as e:                      # 小节被删 / manifest 损坏 → 不拦判分
        logger.warning(f"读小节学习状态失败（{node_id}/{section_id}）: {e}")
        return {"applied": False, "action": "none", "note": ""}

    if correct:
        kg.set_section_learn(node_id, section_id, passed=True)
        progress = kg.section_learn_progress(node_id)
        note = f"本节已通过（{progress['passed']}/{progress['total']} 节）"
        if progress["all_passed"]:
            node = kg.get_node(node_id) or {}
            if int(node.get("mastery") or 0) < MASTERY_ALL_SECTIONS_PASSED:
                kg.update_node_info(
                    node_id, {"mastery": MASTERY_ALL_SECTIONS_PASSED}, caller="human",
                    mastery_reason="section_all_passed", mastery_evidence=evidence)
                note += f"；该知识点全部小节已通过 → 掌握度置 {MASTERY_ALL_SECTIONS_PASSED}"
            note += "。这个知识点拿下了，可以往下一个推进。"
        return {"applied": True, "action": "passed", "note": note, "progress": progress}

    attempts = int(state.get("attempts") or 0)
    kg.set_section_learn(node_id, section_id, bump_attempts=True)
    if attempts < SECTION_RETRY_BEFORE_DOWNGRADE:
        # ⚠️ 这里**不再自动重出题**（2026-09-30 用户口径：出题前必须先问学生）——
        # 旧版在判分那一刻就排期并写"系统已自动再推一道本节题"，学生没点过头就被考；
        # 现在改成"先讲错点 + 问一句要不要再来一道"，他答应后（下一轮）才出。
        return {"applied": True, "action": "retry",
                "note": ("这一节第一次没答对：**先讲清错在哪**（本次不降级）；"
                         "然后**问一句**「要不要再来一道同类的练练？」，"
                         "他答应了下一轮再出题 —— **不要直接出**。")}
    kg.set_section_learn(node_id, section_id, mark=LEARN_MARK_CONFUSED,
                         mark_by=LEARN_MARK_BY_AI)
    return {"applied": True, "action": "downgrade",
            "note": "这一节连续两次没答对：已把它的标记改成「不懂」，接下来就按不懂来讲。"}


def apply_mastery_after_answer(kg: KnowledgeGraph, node_id: str,
                               correct: bool, evidence: str = "") -> str:
    """
    判分后**确定性**更新掌握度（这就是"答对就直接更新进度"）。

    规则（刻意简单可解释）：
      - 答对 → mastery = min(100, 当前 + MASTERY_CORRECT_GAIN)
      - 答错 → **不变**。不做惩罚性扣分：答错说明还没学牢，交给 AI 用苏格拉底追问补救，
        扣分只会打击学生。

    参数:
        evidence: 证据引用（题目 id），随掌握度事件落库（KG-D4）→ 可回溯"这 20 分哪来的"。

    返回一句给模型看的状态描述（空字符串 = 没更新）。
    """
    if not node_id or not correct:
        return ""
    node = kg.get_node(node_id)
    if node is None:
        return ""
    current = int(node.get("mastery", 0) or 0)
    name = node.get("name") or node_id
    if current >= 100:
        return f"「{name}」掌握度已是满值 100，无需再提升。"
    new = min(100, current + MASTERY_CORRECT_GAIN)
    try:
        # caller="human"：这是**用户答题**的结果，不是 AI 自作主张。
        # 原先写 "ai" 会被 `_guard_human_content` 拦下（AI 无权改人类创建的节点）→
        # 用户手动建的知识点**永远无法通过答题提分**（静默写不进去，只留一条 warning）。
        # 2026-09-29 补测试时暴露。
        kg.update_node_info(node_id, {"mastery": new}, caller="human",
                            mastery_reason="quiz_correct", mastery_evidence=evidence)
    except Exception as e:
        # 人类创建的节点 AI 无权改（PermissionError）——判分照常返回，只是不改进度
        logger.warning(f"判分后更新掌握度失败（{node_id}）: {e}")
        return ""
    logger.info(f"判分答对 → 掌握度 {node_id}: {current} → {new}")
    return f"已把「{name}」的掌握度从 {current} 提升到 {new}。"


def _resolve_store(store=None, *, user_id: Optional[int] = None, kg=None):
    """
    定位题库 —— 判分模块唯一的运行期外部依赖，且**可被注入替换**。

    优先用调用方注入的 `store`；否则按 `user_id` / `kg.user_id` 取全局默认单例。
    延迟导入 `quiz_store`：注入 store 的调用方不会被连带拉起题库模块。
    """
    if store is not None:
        return store
    uid = user_id if user_id is not None else getattr(kg, "user_id", None)
    if uid is None:
        raise ValueError(
            "无法定位题库：请传入 store（题库实例），或 user_id（或带 user_id 的 kg）"
        )
    from app.core.quiz.quiz_store import quiz_manager
    return quiz_manager._get_store(uid)


async def grade_pending(user_answer: str, *, kg=None, store=None,
                        user_id: Optional[int] = None,
                        source: str = DEFAULT_SOURCE) -> dict:
    """
    判分"最近一道待作答的题" —— **结构化返回，任何模块都能直接调用**。

    参数:
        user_answer: 学生原始作答（'A' / 'AC' / '对' / '栈'）
        kg:          可选。**传了才会回写掌握度**（鸭子类型：只用到 user_id /
                     get_node / update_node_info）；不传就是纯判分、不碰图谱
        store:       可选。题库实例（可注入，测试与其他模块首选）
        user_id:     可选。没有 kg 时用它定位题库
        source:      只认哪个来源的题（默认 "chat" 对话内；传 "" 表示不限来源）

    返回: dict
        ok=False → {"ok": False, "message": "..."}（没有待作答的题）
        ok=True  → question_id / question / type / user_answer / correct /
                   score / max_score / comment / analysis / knowledge_point /
                   mastery_note（"" = 未更新掌握度）

    规则判分（single/multiple/judge/fill）不调 LLM —— 0 token、瞬时、结果可复核。
    """
    from app.core.quiz.grader import grade_question
    from app.core.quiz.schema import QuizGradeRequest

    store = _resolve_store(store, user_id=user_id, kg=kg)
    q = store.get_pending_question(source=source)
    if not q:
        # ⚠️ 这段文案会被模型当"下一步指令"照做。旧版写的是"如果还没出过题，
        # 先调用 quiz_generate 出一道" —— 在"学生重复提交同一题 / 这轮不是作答"的场景里
        # 它把模型直接推向**再出一道**，真机因此陷入
        # "答错 → 出题 → 再答错 → 又出题"的死循环（2026-09-30 01:21:25 日志铁证）。
        # 触发类文案必须写清"**不要**做什么" —— 本项目反复验证过的一条铁律。
        return {
            "ok": False,
            "message": ("当前没有等待作答的题目（学生重复提交了同一题，或这一轮不是在作答）。"
                        "⛔ **不要**因为这轮没题可判就再出一道新题 —— 先按学生的真实意图回应："
                        "重复提交就告诉他这题已经判过了、接着说上一题的结果；"
                        "确实想再练一道，也要先把上一题的错点讲清楚。"),
        }

    result = await grade_question(QuizGradeRequest(
        question_id=q["id"],
        question=q["question"],
        type=q["type"],
        user_answer=user_answer,
        answer=q.get("answer") or [],
        points=q.get("points", 10),
        analysis=q.get("analysis", ""),
        comment_prompt=q.get("comment_prompt", ""),
    ))
    store.record_attempt(q["id"], user_answer, result["score"], result["max_score"],
                         bool(result["correct"]), result["comment"])

    # 题目关联的知识点（source="chat" 时 = 图谱节点 id）→ 回写进度
    # 没传 kg 就跳过 —— 纯判分场景不该动图谱
    # 带 section_id 的题（按小节出的题 / 「去学习」教学里出的题）走**小节口径**：
    # 答对只将本节置为通过，不再给节点加 20 分（用户口径：全部小节通过才置 100）
    node_id = q.get("knowledge_point") or ""
    section_id = q.get("section_id") or ""
    mastery_note = ""
    section_note = ""
    next_action = ""
    if kg is not None:
        if section_id:
            sec = apply_section_after_answer(
                kg, node_id=node_id, section_id=section_id,
                correct=bool(result["correct"]), evidence=f"question:{q['id']}",
            )
            section_note = sec.get("note", "")
            next_action = sec.get("action", "")
            mastery_note = section_note
        else:
            mastery_note = apply_mastery_after_answer(
                kg, node_id, bool(result["correct"]), evidence=f"question:{q['id']}",
            )

    return {
        "ok": True,
        "question_id": q["id"],
        "question": q["question"],
        "type": q["type"],
        "user_answer": user_answer,
        "correct": bool(result["correct"]),
        "score": result["score"],
        "max_score": result["max_score"],
        "comment": result["comment"],
        "analysis": q.get("analysis", ""),
        "knowledge_point": node_id,
        "section_id": section_id,
        "section_note": section_note,
        "next_action": next_action,
        "mastery_note": mastery_note,
    }


async def grade_pending_answer(kg, user_answer: str, *, store=None,
                               source: str = DEFAULT_SOURCE) -> str:
    """
    判分并生成**给模型看的自然语言文案**（agent 工具 `grade_answer` 用）。

    只是 `grade_pending()` 的薄格式化层 —— 其他模块想要**数据**请直接用
    `grade_pending()`，不要去解析这段文案。
    """
    r = await grade_pending(user_answer, kg=kg, store=store, source=source)
    if not r["ok"]:
        return r["message"]
    return _format_grade_for_model(r)


def _format_grade_for_model(r: dict) -> str:
    """
    把 `grade_pending()` 的结构化结果转成给模型看的文案（工具与自动判分共用）。

    为什么结论必须**单独成行、放在第一行**（2026-09-30 真机，别合并回长句）：
        学生那道题答案键 B、选了 C，系统判 0/10，注进去的是
        "判分结果：答错（0/10 分）。…" —— 而模型回的是
        "**Bingo，答对了！**这一节就这么过了"，随后按"已通过"跳去讲下一节。
        （学生干等下一节的题等不到，见开发日志补七。）
        思考型模型对"长句 + 结论后置"很容易读串，所以：结论提第一行 + 加粗 + 明写"不得改写"。
    """
    verdict = "答对" if r["correct"] else "答错"
    lines = [
        f"⛔ 判分结论：**{verdict}**（{r['score']}/{r['max_score']} 分）。"
        "这是系统判分，**不得改写** —— 答错就不能说「答对了」「这一节过了」。",
        f"题目：{r['question']}",
        f"学生作答：{r.get('user_answer', '')}",
    ]
    if r.get("comment"):
        lines.append(f"评语：{r['comment']}")
    if r["analysis"]:
        lines.append(f"参考答案/解析：{r['analysis']}")
    if r.get("section_note"):
        lines.append(r["section_note"])
    if r.get("next_action") == "downgrade":
        # 连续两次答错 → 已降级为「不懂」：这是最容易被模型"强行宣布通过"的场景
        lines.append("⛔ 本节连续两次答错，已降级为「不懂」：**不要**宣布「这一节过了 / 通过」，"
                     "接下来按「不懂」把这一节讲一遍。")
    if r["mastery_note"] and r["mastery_note"] != r.get("section_note"):
        lines.append(r["mastery_note"])
    lines.append(
        "接下来：答对 → 简短肯定 + 点出关键要点，然后自然推进到下一步；"
        "答错 → **不要直接给出答案**，回到苏格拉底式追问，把学生引到正确思路上。"
    )
    return "\n".join(lines)


async def auto_grade_pending(kg, user_answer: str, *, store=None,
                             source: str = DEFAULT_SOURCE) -> str:
    """
    对话入口的**确定性判分钩子**：只要存在待作答的题，就把本轮学生输入当作答判一次。

    为什么必须有这一层（真机问题，2026-09-27）
        判分的入口原本是"模型主动调 `grade_answer`"这个**软约束** ——
        而本项目已实测同一类软约束不管用（见本文件顶部：模型 12 次运行 0 次主动调
        `update_mastery`）。后果：学生答完题，AI 把它当普通话说、不判题、
        掌握度原地不动。这里把"检测到作答"也改成确定性路径，模型只负责"怎么回应"。

    返回给模型看的文案（空字符串 = 没有待作答的题 / 判分不可用，调用方无需注入）。
    """
    try:
        store = _resolve_store(store, user_id=getattr(kg, "user_id", None), kg=kg)
        if store.get_pending_question(source=source) is None:
            return ""
        r = await grade_pending(user_answer, kg=kg, store=store, source=source)
        if not r.get("ok"):
            return ""
        lines = [
            "【系统自动判分】学生这一轮的输入已按「作答」机械判分（无需你再调 grade_answer）：",
            _format_grade_for_model(r),
        ]
        lines.append("⚠️ 例外：如果学生这轮其实不是在作答，而是在提新问题 / 换话题，"
                     "请忽略上面的判分结果，按学生的真实意图回应。")
        return "\n".join(lines)
    except Exception as e:
        # 判分是增强项，任何异常都不能阻断对话
        logger.warning(f"自动判分失败（不阻断对话）: {e}")
        return ""
