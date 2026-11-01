"""TurnPlan（回合计划）单测 —— 2026-10-31，架构审查 §4。

锁四件事：
1. `plan=None` = 全量（与加 plan 之前逐字节等价的入口条件）；
2. 子集按**注册表顺序**发（顺序变了 = 缓存前缀变了 = 白花钱）；
3. 白名单全不认识 → 回落全量（"漏发工具 = 模型没能力"这条底线）；
4. **每个原生工具都被归类**（学习子集 or `NON_LEARN_TOOLS`）—— 新增工具忘了归类时，
   学习模式会默默看不到它，只有这条断言能拦住。
"""
from app.core.agent.plan import (
    LEARN_TOOLSETS, NON_LEARN_TOOLS, TurnPlan,
    plan_for_learn, specs_for_plan, tools_for_plan,
)
from app.core.agent_tools import KG_TOOLS
from app.core.agent_tools.registry import build_tools_prompt
from app.core.agent_tools.tools import NATIVE_SPECS
from app.core.learning.session import ACTION_QUIZ, ACTION_TEACH, ACTION_WRAPUP


# ─── 派生口径 ────────────────────────────────────────────────

def test_plan_none_means_full():
    """None = 全量（聊天路径不传 plan 时行为不变）。"""
    assert tools_for_plan(None) is None
    assert specs_for_plan(None) is None


def test_empty_subset_means_no_tools():
    """()= 一个工具都不发（学习模式开场轮 / 收尾轮）。"""
    p = TurnPlan(allowed_tools=())
    assert tools_for_plan(p) == []
    assert specs_for_plan(p) == []


def test_subset_preserves_registry_order():
    names = ("rag_search", "kg_snapshot")
    p = TurnPlan(allowed_tools=names)
    got = [t["function"]["name"] for t in tools_for_plan(p)]
    assert set(got) == set(names)
    assert got == [t["function"]["name"] for t in KG_TOOLS
                   if t["function"]["name"] in names]
    assert [s["name"] for s in specs_for_plan(p)] == got   # 提示词侧同序


def test_unknown_names_fall_back_to_full():
    """白名单全不认识 → 全量。宁可不省 token，也不能让模型"没能力"。"""
    p = TurnPlan(allowed_tools=("no_such_tool",))
    assert tools_for_plan(p) is None
    assert specs_for_plan(p) is None


def test_every_native_tool_is_covered():
    """每个原生工具必须归入某个学习子集或 NON_LEARN_TOOLS（防"悄悄漏发"）。"""
    covered = set(NON_LEARN_TOOLS)
    for names in LEARN_TOOLSETS.values():
        covered |= set(names)
    missing = {s["name"] for s in NATIVE_SPECS} - covered
    assert not missing, f"未归类的工具（学习模式会看不到它）：{sorted(missing)}"


# ─── 意图映射 ────────────────────────────────────────────────

def test_learn_opener_plan_has_no_tools():
    p = plan_for_learn("opener")
    assert p is not None
    assert p.allowed_tools == () and p.tool_choice == "none"
    assert p.label == "learn:opener"


def test_learn_quiz_plan_keeps_quiz_and_drops_graph_writes():
    p = plan_for_learn("normal", ACTION_QUIZ)
    assert p.label == "learn:quiz"
    assert "quiz_generate" in p.allowed_tools
    assert "grade_answer" in p.allowed_tools
    assert "add_knowledge_node" not in p.allowed_tools
    assert p.finish == "structured"


def test_learn_teach_plan_keeps_quiz_for_consent_turn():
    """讲解轮也要留着 quiz_generate：学生当场说"直接考我"时，同一轮就要能出题。"""
    p = plan_for_learn("normal", ACTION_TEACH)
    assert p.label == "learn:teach"
    assert "quiz_generate" in p.allowed_tools
    assert "mark_section_understood" in p.allowed_tools
    assert "delete_node" not in p.allowed_tools


def test_learn_wrapup_has_no_tools_and_structured_finish():
    p = plan_for_learn("normal", ACTION_WRAPUP)
    assert p.allowed_tools == () and p.tool_choice == "none"
    assert p.finish == "structured"


def test_no_plan_outside_learn_mode():
    """非学习会话 → None（= 全量，聊天路径不受影响）。"""
    assert plan_for_learn("", "") is None


# ─── 提示词侧与 tools= 同源（2026-10-31）────────────────────────

def test_tool_guidance_follows_plan():
    """工具指南必须与发给模型的工具一致：不教它调拿不到的工具，也不白写用不上的段落。"""
    from app.services import chat_service

    full = chat_service._tool_capability_prompt(None)
    assert full == chat_service.TOOL_CAPABILITY_PROMPT      # None = 逐字节等于改动前
    assert chat_service._tool_capability_prompt([]) == ""   # 本轮禁工具 → 不注入

    specs = specs_for_plan(plan_for_learn("normal", ACTION_TEACH))
    subset = chat_service._tool_capability_prompt(specs)
    guide = build_tools_prompt(specs)          # 逐工具指南段（跨工具策略那段与子集无关）
    assert "### `quiz_generate`" in guide
    assert "### `add_knowledge_node`" not in guide
    assert len(subset) < len(full)
