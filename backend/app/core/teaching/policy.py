"""教学策略（Phase B2）：把「这一轮该怎么教」从提示词渲染里抽出来。

要解决的问题
------------
「本轮该处理哪一节 / 这一轮该讲还是该出题 / 为什么」的判定原先散在
`services/chat_service.py` 里，并且**有两份**：

* `_learn_start()` —— 起点 = 顺序上第一个未通过的小节（学习块与工具层目标共用）；
* `learn_target_section()` 与 `_build_learn_block()` 各自把「标记 → 该讲/该考」的理由
  文案写了一遍（"已懂 —— 先出题验证" vs "学生标了不懂，从这里开始讲"…）。

改一处忘另一处就会出现**两个事实源**：提示词里说"该出题"，工具层却按"该讲"硬拦 ——
这正是 B1/B2 要消灭的那类漂移。

本模块给出**一个纯函数出口** `decide(...)`
----------------------------
输入 = 小节清单 + 各自学习状态（都是纯数据，`kg` 不进来），输出 = `Directive`：

    Directive(action, target, reason, passed_count, total)

* `action` —— 本轮意图（`teach` / `quiz` / `wrapup`，取值来自 `core/learning/session.py`
  的 `ACTION_*`，**不另立一套**）；
* `target` —— 本轮该处理的节（`TargetSection`，可 `as_dict()` 还原工具层沿用的字典形状）；
* `reason` —— 一句"为什么"（进提示词，也让人读日志时不必反推）。

**本模块不产出任何提示词文本** —— 文案（模板、清单渲染、正文截断）仍在
`chat_service._build_learn_block`。这样"改策略"与"改文案"互不牵连；
教学策略的等价重构因此可以只用 **golden 逐字符比对**来验收
（见 `tests/test_learn_block_golden.py`）。

刻意**不**做
------------
不读 `kg`、不落库、不抛业务异常、不认识 `KnowledgeGraph` —— 纯数据进、纯数据出，
所以离线用例可以逐个分支钉死，不需要造图谱。
"""
from dataclasses import dataclass

from app.core.knowledge_graph import (
    LEARN_MARK_BY_AI,
    LEARN_MARK_CONFUSED,
    LEARN_MARK_UNDERSTOOD,
)
from app.core.learning.session import ACTION_QUIZ, ACTION_TEACH, ACTION_WRAPUP, action_for

__all__ = [
    "TargetSection",
    "Directive",
    "pick_start",
    "decide",
    "REASON_ALL_PASSED",
    "REASON_DOWNGRADED",
    "REASON_CONFUSED",
    "REASON_UNDERSTOOD",
    "REASON_UNSEEN",
]

# ── 起点理由文案（**唯一来源**：提示词注入的就是这几句，别在调用方再抄一份）──
REASON_ALL_PASSED = "全部小节都已通过 —— 只需简短肯定并告诉学生这个知识点拿下了"
REASON_DOWNGRADED = "连续两次答错、系统已降级为「不懂」—— 从这里开始把它讲一遍"
REASON_CONFUSED = "学生标了不懂，从这里开始讲"
REASON_UNDERSTOOD = "已懂 —— **先出题验证**，别重讲"
REASON_UNSEEN = "还没看过 —— 按新课讲"


@dataclass(frozen=True)
class TargetSection:
    """本轮该处理的那一节（连同它此刻的状态快照）。

    `mark` / `passed` / `needs_quiz` 是**从图谱现算的快照**，不落库（落库即缓存一个会过期的值）。
    """

    node_id: str
    index: int              # 1-based 序号（提示词与日志都用它）
    section_id: str
    title: str
    mark: str
    passed: bool
    needs_quiz: bool

    def as_dict(self) -> dict:
        """工具层沿用的字典形状（`learning.bind_target` 与旧测试按这个键名读）。"""
        return {
            "node_id": self.node_id,
            "index": self.index,
            "section_id": self.section_id,
            "title": self.title,
            "mark": self.mark,
            "passed": self.passed,
            "needs_quiz": self.needs_quiz,
        }


@dataclass(frozen=True)
class Directive:
    """本轮教学指令：做什么（`action`）、对哪一节（`target`）、为什么（`reason`）。

    `target is None` = 本轮不适用学习模式（老节点没有小节 / 没有焦点节点）。
    """

    action: str
    target: TargetSection | None
    reason: str
    passed_count: int = 0
    total: int = 0

    @property
    def active(self) -> bool:
        """是否真的在推进某个小节（False = 普通对话，调用方按原逻辑走）。"""
        return self.target is not None


def pick_start(states: list) -> tuple:
    """起点 = **按顺序**第一个还没通过的小节（用户口径原话："按照先后顺序"）。

    全通过时返回最后一节（理由文案另有分支说明）。刻意不按"不懂优先"重排 ——
    那会让学生刚标记的节被后面的未标记节插队，顺序感就没了。

    `states` = `[(1-based 序号, 小节 dict, 学习状态 dict), ...]`（由调用方读图谱得到）。
    """
    return next((it for it in states if not it[2]["passed"]), states[-1])


def decide(*, node_id: str, states: list) -> Directive:
    """**唯一的策略判定出口**：小节状态快照 → 本轮教学指令。

    参数:
        node_id: 焦点知识点 id（写进 `target`，供工具层硬校验）
        states:  `[(序号, 小节, 学习状态), ...]`；空 → 本轮不适用学习模式

    返回的 `action` 与 `learning.action_for(passed, needs_quiz)` 同源同值 ——
    B1 的 `LearnTurn` 落库时直接用这个 `action`（缺省才回退 `action_for`），
    所以"提示词里说的动作"与"工具层硬拦时认的动作"不可能分叉。
    """
    if not states:
        return Directive(action="", target=None, reason="", passed_count=0, total=0)

    index, section, state = pick_start(states)
    passed = bool(state["passed"])
    mark = state["mark"]
    needs_quiz = (not passed) and mark == LEARN_MARK_UNDERSTOOD

    if passed:
        reason = REASON_ALL_PASSED
    elif mark == LEARN_MARK_CONFUSED:
        # 系统降级（连续两次答错，mark_by=ai）**不能**说成"学生标了不懂"（2026-09-30）：
        # 两者都进入讲解，但归因不同 —— 前者是系统判的，后者是学生自己说的。
        reason = (REASON_DOWNGRADED if state.get("mark_by") == LEARN_MARK_BY_AI
                  else REASON_CONFUSED)
    elif mark == LEARN_MARK_UNDERSTOOD:
        reason = REASON_UNDERSTOOD
    else:
        reason = REASON_UNSEEN

    return Directive(
        action=action_for(passed=passed, needs_quiz=needs_quiz),
        target=TargetSection(
            node_id=str(node_id),
            index=int(index),
            section_id=str(section.get("id") or ""),
            title=str(section.get("title") or ""),
            mark=str(mark or ""),
            passed=passed,
            needs_quiz=needs_quiz,
        ),
        reason=reason,
        passed_count=sum(1 for _, _, st in states if st["passed"]),
        total=len(states),
    )


# 导入即校验：意图取值必须来自 learning 的 ACTION_*（防止有人在别处手写字符串）
_ACTIONS = (ACTION_TEACH, ACTION_QUIZ, ACTION_WRAPUP)
assert set(_ACTIONS) == {"teach", "quiz", "wrapup"}, "ACTION_* 取值变了，检查 policy 的映射"
