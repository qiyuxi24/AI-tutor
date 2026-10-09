"""学习会话（Phase B1）：**「本轮该做什么」的唯一归属对象**。

为什么需要它
------------
在此之前，"本轮该处理哪一节 / 学生同意出题没有 / 学生说了什么"这三件事靠挂在
**每次请求新建的 `KnowledgeGraph` 实例**上的临时属性传递（`kg.learn_target` /
`kg.quiz_consent` / `kg.student_input`）。问题不是"不好看"，而是**没有归属**：

* 写入方在 `services/chat_service.py`，读取方在 `core/agent_tools/tools/*`（工具 handler
  只拿得到 `(args, kg)`，没有会话上下文）→ 两边只靠注释对齐；
* 状态随请求消失 → 模型没有跨轮的记忆主体，只能靠提示词里一句"状态权威（优先于你自己
  说过的话）"去对冲它自己上一轮的承诺；
* 谁都可以再挂一个新属性 → "下一步做什么"的判定越散越多。

本模块给出**一个对象 + 一个落点**：

* `LearnTurn` —— 一轮学习的判定（当前节 / 意图 / 同意 / 理由），工具层只读它；
* `learning_sessions` 表（`knowledge.db`）—— 跨请求可读回，供 B3 的"主动推进"与对账使用；
  存储实现在 `KnowledgeGraph.upsert_learning_session` / `get_learning_session`；
* `reconcile()` —— 库里记的当前节 vs 图谱实际小节状态：这是"模型上一轮的承诺"与现状
  不一致时**唯一**的发现手段（B1 的对账用例）。

`kg` 上的挂载点（为什么只有一个）
--------------------------------
工具 handler 的签名是 `(args, kg)`（见 `core/agent_tools/tools/README.md`），所以"本轮判定"
必须能从这个 `kg` 拿到。本模块把它挂在**一个**私有属性 `_learn_turn` 上，且**只有本模块
读写它**（`bind_*` 写、`turn_of` 读）—— "走私"只有一处、有契约、有静态守卫测试
（`tests/test_learning_session.py::test_retired_temp_attrs_are_gone_from_app`），
而不是散在各处的临时属性。

刻意**不**做（避免变成第二个事实源）
-----------------------------------
不缓存/不复制 `pending_question_id`（"手上有没有未作答的题"已由题库
`_has_pending_quiz` 拥有）、不存 `student_input` 原文（用户消息的真相在 `conversations.db`）。
计划里这两列等 B3 的复习调度真正需要时再谈 —— 先删重复，再谈抽象。
"""
from dataclasses import dataclass
from typing import Optional

from app.core.knowledge_graph import read_learn_state

# ── 意图（intended_action）：本轮该对当前节做什么 ──────────────────────────
# 只定义**现在真用得上**的三个。计划里的 `review`（复习队列）与 `ask_consent`
# （先问再出）分别是 B3 与 B2 的事 —— 现在就加 = 定义一批没人读的取值。
ACTION_TEACH = "teach"      # 讲这一节（不懂 / 没看过）
ACTION_QUIZ = "quiz"        # 出题验证这一节（已懂，待验证）
ACTION_WRAPUP = "wrapup"    # 该收尾（当前节或全部小节已通过）

# 本轮判定在 kg 上的挂载点。**只有本模块读写它**（见模块 docstring）。
_ATTR = "_learn_turn"


@dataclass
class LearnTurn:
    """一轮学习的判定结果。

    字段来源分两类（别混）：
      · **从图谱派生的**（`index` / `title` / `mark` / `passed` / `needs_quiz`）——
        每轮重新派生，**不落库**（落库就等于缓存一个会过期的值，是新的 bug 源）；
      · **本轮判定的结论**（`section_id` / `action` / `consent` / `reason`）—— 落
        `learning_sessions`，跨请求可读回。
    """

    node_id: str = ""
    section_id: str = ""
    index: int = 0
    title: str = ""
    mark: str = ""
    passed: bool = False
    needs_quiz: bool = False
    action: str = ""
    consent: str = ""
    student_input: str = ""
    reason: str = ""

    @property
    def active(self) -> bool:
        """本轮是否真的在推进某个小节（否 = 普通对话 / 没有小节的节点）。"""
        return bool(self.node_id and self.section_id)

    def as_target(self) -> Optional[dict]:
        """兼容既有 `learn_target` 字典形状（工具层文案分支与旧测试在用）。

        非学习轮返回 None —— 与"不设目标"同义，调用方不必再判 `active`。
        """
        if not self.active:
            return None
        return {
            "node_id": self.node_id,
            "index": self.index,
            "section_id": self.section_id,
            "title": self.title,
            "mark": self.mark,
            "passed": self.passed,
            "needs_quiz": self.needs_quiz,
        }


def action_for(*, passed: bool, needs_quiz: bool) -> str:
    """当前节状态 → 本轮意图（唯一的映射处）。

    `passed` 优先：全通过时起点节的 `needs_quiz` 也是 False，但语义是"收尾"而不是"开讲"。
    """
    if passed:
        return ACTION_WRAPUP
    return ACTION_QUIZ if needs_quiz else ACTION_TEACH


def _reason_for(turn: LearnTurn) -> str:
    """给这一轮判定留下一句"为什么"（进库，供排查；AGENTS.md：只记判定了什么、为什么）。"""
    if turn.action == ACTION_WRAPUP:
        return f"第 {turn.index} 节「{turn.title}」已通过 —— 本轮收尾"
    if turn.action == ACTION_QUIZ:
        return f"第 {turn.index} 节「{turn.title}」是「已懂」，本轮出题验证"
    return f"第 {turn.index} 节「{turn.title}」为「{turn.mark or '未标记'}」，本轮讲解"


def turn_of(kg) -> Optional[LearnTurn]:
    """读本轮判定。没有（工具在无会话上下文下被直调：脚本 / 测试 / 节点详情接口）→ None。

    `None` 的语义是**放行**：没有会话上下文就不硬拦（与改造前"属性缺失 = 放行"一致）。
    """
    turn = getattr(kg, _ATTR, None)
    return turn if isinstance(turn, LearnTurn) else None


def _turn(kg) -> LearnTurn:
    """取本轮的判定对象，没有就建一个（只挂，不落库 —— 落库要等有 node_id）。"""
    turn = turn_of(kg)
    if turn is None:
        turn = LearnTurn()
        setattr(kg, _ATTR, turn)
    return turn


def _persist(kg, turn: LearnTurn) -> None:
    """落库：只有真的在推进某个小节时才写（普通对话不留行）。

    存储异常**不上抛**：学习会话是增强项，落库失败不该打断对话（与 `ensure_section_quiz_scheduled`
    的兜底口径一致）。但会 warning —— 静默失败正是这个项目吃过最多的亏。
    """
    if not turn.active:
        return
    try:
        kg.upsert_learning_session(
            turn.node_id, section_id=turn.section_id, intended_action=turn.action,
            consent=turn.consent, reason=turn.reason or _reason_for(turn),
        )
    except Exception as e:  # noqa: BLE001 —— 见 docstring
        import logging
        logging.getLogger(__name__).warning(
            f"学习会话落库失败（{turn.node_id}/{turn.section_id}）：{e}")


def bind_target(kg, target: Optional[dict]) -> Optional[LearnTurn]:
    """记录"本轮该处理哪一节"（`target` 为 None = 本轮不是学习模式）。

    返回同一个 `LearnTurn`（调用方直接读字段，不必再 `turn_of`）。
    `target` 的形状沿用既有 `learn_target_section()` 的字典（node_id/index/section_id/
    title/mark/passed/needs_quiz），**派生逻辑仍在 chat_service**（B2 才把它搬进策略模块）。
    """
    turn = _turn(kg)
    if not target:
        turn.node_id = turn.section_id = ""
        turn.index, turn.title, turn.mark = 0, "", ""
        turn.passed = turn.needs_quiz = False
        turn.action = turn.reason = ""
        return None
    turn.node_id = str(target.get("node_id") or "")
    turn.section_id = str(target.get("section_id") or "")
    turn.index = int(target.get("index") or 0)
    turn.title = str(target.get("title") or "")
    turn.mark = str(target.get("mark") or "")
    turn.passed = bool(target.get("passed"))
    turn.needs_quiz = bool(target.get("needs_quiz"))
    # `action` 优先用**策略层**（`core/teaching/policy.decide`）算好的那一份 —— 提示词里说的动作
    # 与工具层硬拦认的动作必须同源；缺省才回退本模块的 `action_for`（脚本 / 旧测试直构造 target）。
    turn.action = str(target.get("action") or "") or action_for(
        passed=turn.passed, needs_quiz=turn.needs_quiz)
    turn.reason = _reason_for(turn)
    _persist(kg, turn)
    return turn


def bind_consent(kg, *, consent: str, student_input: str = "") -> str:
    """记录"学生本轮说了什么 / 他同意出题吗"，供工具层硬拦（`quiz_generate` 读它）。

    这是**本轮独立**的判定（不是学习会话的一部分）：所以没有学习目标时也照样记，
    只是不落库。`student_input` 只留在内存：用户消息的真相在 `conversations.db`，
    在这里再存一份就是第二个事实源；它唯一的用途是给模型回一句"他说的是「…」"。
    """
    turn = _turn(kg)
    turn.consent = consent or ""
    turn.student_input = str(student_input or "")
    _persist(kg, turn)          # 有目标时顺带把同意一起落库（UPSERT，不新增行）
    return turn.consent


def load(kg, node_id: str) -> Optional[dict]:
    """读回该用户在该节点**上一次**的学习会话（跨请求记忆的地基）；没有则 None。"""
    return kg.get_learning_session(node_id)


def reconcile(kg, node_id: str) -> dict:
    """对账：库里记的"当前节" vs 图谱实际的节状态。

    "模型上一轮的承诺"与现状不一致时，只有这里能发现 —— 库里那一行是上一轮判定的
    快照，图谱（manifest 的小节 `learn`）才是事实。返回：

        {"stored": dict|None, "exists": bool, "passed": bool, "stale": bool, "why": str}

    只报事实，不做动作：该往下走 / 该提醒谁，是 B2 策略模块的事（本层不引入策略）。
    """
    stored = load(kg, node_id)
    if not stored:
        return {"stored": None, "exists": False, "passed": False,
                "stale": False, "why": "尚无会话记录"}
    sid = str(stored.get("section_id") or "")
    if not sid:
        return {"stored": stored, "exists": False, "passed": False,
                "stale": False, "why": "会话未记录当前节"}
    states = {s.get("id"): read_learn_state(s) for s in kg.list_sections(node_id)}
    if sid not in states:
        return {"stored": stored, "exists": False, "passed": False, "stale": True,
                "why": f"库里记的当前节 {sid} 已不在该节点的小节清单里（被删或改名）"}
    passed = bool(states[sid]["passed"])
    if passed:
        return {"stored": stored, "exists": True, "passed": True, "stale": True,
                "why": f"库里记的当前节 {sid} 已通过 —— 该往下走了"}
    return {"stored": stored, "exists": True, "passed": False, "stale": False,
            "why": "与图谱状态一致"}
