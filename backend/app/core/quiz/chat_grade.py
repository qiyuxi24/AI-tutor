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
        kg.update_node_info(node_id, {"mastery": new}, caller="ai",
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
        return {
            "ok": False,
            "message": ("当前没有等待作答的题目。请确认学生是否在回答你出的题；"
                        "如果还没出过题，先调用 quiz_generate 出一道。"),
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
    node_id = q.get("knowledge_point") or ""
    mastery_note = ""
    if kg is not None:
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

    lines = [
        f"判分结果：{'答对' if r['correct'] else '答错'}"
        f"（{r['score']}/{r['max_score']} 分）。{r['comment']}",
        f"题目：{r['question']}",
    ]
    if r["analysis"]:
        lines.append(f"参考答案/解析：{r['analysis']}")
    if r["mastery_note"]:
        lines.append(r["mastery_note"])
    lines.append(
        "接下来：答对 → 简短肯定 + 点出关键要点，然后自然推进到下一步；"
        "答错 → **不要直接给出答案**，回到苏格拉底式追问，把学生引到正确思路上。"
    )
    return "\n".join(lines)
