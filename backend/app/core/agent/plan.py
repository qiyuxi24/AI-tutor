"""回合级计划（TurnPlan）—— loop 的第二个输入（2026-10-31，架构审查 §4）。

## 为什么有这个东西
`core/teaching/policy.decide()` 早就算出了「本轮该讲还是该考、对哪一节、为什么」，
但那份 `Directive` 只被拿去**渲染提示词**；loop 拿到的是「一坨字符串 + 一个 no_tools 布尔」，
于是每一轮都发全量工具（schema ≈2k tokens + 指南 ≈5.8k tokens），"该给哪些工具"只能靠
模型自觉。本模块把已有的判定接到 loop 上 —— 不是新增一层，是**少绕一圈**。

## 只做三件事
1. 定义 `TurnPlan`（loop 认得的形状）；
2. 按意图给出**工具子集**（白名单，稳定顺序 = 缓存前缀稳定）；
3. 把子集翻译成 loop 要的 `tools=` 与提示词要的 `specs`。

判定本身不在这里 —— 意图来自 `policy.Directive.action`，这里只做映射（别抄第二份策略）。

## 兼容口径
`plan=None` 或 `allowed_tools=None` → 全量，行为与加 plan 之前**逐字节相同**。
子集算出来是空（名字全不认识）→ 回落全量：「漏发工具 = 模型没能力」，宁可不省。
"""
from dataclasses import dataclass

from app.core.agent_tools import KG_TOOLS
from app.core.agent_tools.registry import all_specs
from app.core.learning.session import ACTION_QUIZ, ACTION_TEACH, ACTION_WRAPUP

__all__ = [
    "TurnPlan", "LEARN_TOOLSETS", "NON_LEARN_TOOLS",
    "plan_for_learn", "tools_for_plan", "specs_for_plan",
]

# ─── 学习模式的工具子集（白名单）──────────────────────────────────
# 口径：学习轮 = 按小节教，不建图、不下载资料、不删节点。这些动作在学习会话里从未
# 被真机触发过，却每轮都要付它们的 schema + 指南钱。
#
# ⚠️ 新增工具**默认不在任何子集里** = 学习模式看不到它。所以要有意识地把它加进
#    `LEARN_TOOLSETS` 或 `NON_LEARN_TOOLS`（`tests/test_agent_plan.py` 逐个断言，
#    漏了会红）—— 这是「工具子集漏发」唯一的守卫。
_LEARN_BASE = (
    "rag_search",              # 按需检索（小节正文不够时）
    "kg_snapshot",             # 看图谱结构
    "update_mastery",          # 掌握度（判分/确认后）
    "update_user_profile",     # 画像
    "mark_section_understood",  # 讲完记账（学习模式的硬触发）
)
LEARN_TOOLSETS = {
    # 开场轮只报菜单：一个工具都不给（原 no_tools 的语义并入 plan）
    "learn:opener": (),
    # 讲解轮：保留 quiz_generate（学生当场说"直接考我"时下一轮才用，但同一轮同意也算）
    "learn:teach": _LEARN_BASE + ("quiz_generate", "update_node_content"),
    # 出题轮：判分回执要读，改正文/建图不必要
    "learn:quiz": _LEARN_BASE + ("quiz_generate", "grade_answer"),
    # 收尾轮：全部小节已通过 → 只出文本
    "learn:wrapup": (),
}

# 学习模式明确不需要的工具（其余工具都必须属于上面某个子集，测试逐个核对）
NON_LEARN_TOOLS = frozenset({
    "add_knowledge_node", "plan_knowledge_nodes", "update_node_sections",
    "add_edge", "delete_node",
    "search_resources", "collect_resources", "fetch_webpage", "download_resource",
})

FINISH_NATURAL = "natural"        # 模型自己说完
FINISH_STRUCTURED = "structured"  # 强制收尾时要「已知 / 未解 / 下一步」三段


@dataclass(frozen=True)
class TurnPlan:
    """一轮对话的执行计划。字段刻意少 —— 只放 loop 真正要用的，别当配置中心。

    allowed_tools: None=全量（现行行为）；()=不给工具；否则=白名单（保持注册顺序）
    tool_choice:   "auto" / "none"（语义并入原 `no_tools`，但可与子集共存）
    finish:        "natural" / "structured"（只在强制收尾时生效）
    label:         日志与证据里认得出是哪一种回合（"learn:quiz" 等）
    """

    allowed_tools: tuple[str, ...] | None = None
    tool_choice: str = "auto"
    finish: str = FINISH_NATURAL
    label: str = ""


def plan_for_learn(kind: str, action: str = "") -> TurnPlan | None:
    """学习模式的轮次 → TurnPlan；不适用（非学习会话）→ None（= 全量，零行为变化）。

    `kind` 来自 `chat_service.learn_mode_kind()`，`action` 来自
    `policy.Directive.action`（`teach`/`quiz`/`wrapup`）—— 两处都是既有判定，不重算。
    """
    if kind == "opener":
        return TurnPlan(allowed_tools=(), tool_choice="none", label="learn:opener")
    if kind != "normal":
        return None
    if action == ACTION_WRAPUP:
        return TurnPlan(allowed_tools=(), tool_choice="none",
                        finish=FINISH_STRUCTURED, label="learn:wrapup")
    key = "learn:quiz" if action == ACTION_QUIZ else "learn:teach"
    return TurnPlan(allowed_tools=LEARN_TOOLSETS[key],
                    finish=FINISH_STRUCTURED, label=key)


def tools_for_plan(plan: TurnPlan | None) -> list | None:
    """TurnPlan → 发给模型的 `tools=` 值（None=全量，[]=没有工具）。"""
    if plan is None or plan.allowed_tools is None:
        return None
    if not plan.allowed_tools:
        return []
    names = set(plan.allowed_tools)
    subset = [t for t in KG_TOOLS if t["function"]["name"] in names]
    return subset or None      # 全不认识 → 回落全量（漏发 = 模型没能力）


def specs_for_plan(plan: TurnPlan | None) -> list[dict] | None:
    """TurnPlan → 提示词侧要的工具 spec（None=全量，[]=不注入工具指南）。"""
    if plan is None or plan.allowed_tools is None:
        return None
    if not plan.allowed_tools:
        return []
    names = set(plan.allowed_tools)
    subset = [s for s in all_specs() if s["name"] in names]
    return subset or None
