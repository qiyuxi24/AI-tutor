"""图谱注入体量控制：build_graph_context 的降级阶梯与展示范围说明。

背景：主对话路径曾把全量节点（≈260 字符/节点 = 名称行 + 200 字摘要）与全量边
无上限注入 system prompt，约 200 节点即自身顶穿 32K token 预算，
context_guard 对此只能"照发 + warning"（静默失效）。
"""
from app.core.graph_analyzer import build_graph_context


class FakeKg:
    """只实现 build_graph_context 用到的三个成员（鸭子类型）。"""

    def __init__(self, n_nodes: int, n_edges: int = 0, preview_chars: int = 200):
        self.nodes = [
            {"id": f"n{i}", "name": f"知识点{i}", "mastery": i % 100,
             "difficulty": 3, "estimated_minutes": 15, "tags": ["数学", "基础"]}
            for i in range(n_nodes)
        ]
        self.edges = [
            {"from_node": f"n{i}", "to_node": f"n{i + 1}",
             "relation": "prerequisite", "label": "前置"}
            for i in range(min(n_edges, max(0, n_nodes - 1)))
        ]
        self._preview = "内容" * (preview_chars // 2)

    def get_node_content_preview(self, node_id: str) -> str:
        return self._preview


# ─── 未超限：与旧实现逐字符一致（零行为变化） ──────────────────

def test_under_cap_format_unchanged():
    kg = FakeKg(1)
    out = build_graph_context(kg, detailed=True)

    assert out == (
        "## 当前知识图谱\n\n"
        "### 现有节点（共 1 个）\n"
        "  [n0] 知识点0 (掌握度:0, 难度:3, 预计:15分)\n"
        "    摘要: " + kg._preview[:200] + "\n\n"
        "### 现有关系（共 0 条）\n"
        "  (暂无关系)"
    )
    assert "说明：" not in out          # 未降级就不该有展示范围说明


def test_zero_cap_means_unlimited():
    kg = FakeKg(300)
    out = build_graph_context(kg, detailed=True, max_chars=0)
    assert out.count("[n") == 300 and "说明：" not in out


def test_empty_graph_no_crash():
    out = build_graph_context(FakeKg(0), detailed=True, max_chars=500)
    assert "(暂无节点)" in out and "(暂无关系)" in out


# ─── 降级 ①：先省内容摘要（框架必须完整） ─────────────────────

def test_drops_preview_before_nodes():
    kg = FakeKg(20, n_edges=19)
    out = build_graph_context(kg, detailed=True, max_chars=2000)

    assert "摘要:" not in out                     # 摘要先被省
    assert out.count("[n") == 20                  # 节点一个不少（框架完整）
    assert "掌握度:" in out and "难度:" in out     # L2 定位所需字段仍在
    assert "省略内容摘要" in out
    assert "rag_search" in out                    # 指出内容从哪来


# ─── 降级 ②：再限量节点（当前教学节点邻域优先） ────────────────

def test_limits_nodes_with_scope_note():
    kg = FakeKg(100, n_edges=99)
    out = build_graph_context(kg, detailed=True, max_chars=1000)

    shown = out.count("[n")
    assert 1 < shown < 100
    assert "当前展示范围：节点" in out
    assert f"{shown}/100 个" in out               # 说明里的数字与实际展示一致
    assert "摘要:" not in out


def test_focus_node_survives_truncation():
    kg = FakeKg(100, n_edges=99)
    out = build_graph_context(kg, detailed=True, max_chars=1000, focus_node_id="n5")

    assert "[n5]" in out                          # 正在讲的节点不能被截掉
    assert "[n4]" in out and "[n6]" in out        # 其直接邻居同批保留


def test_cap_never_exceeded():
    """展示范围说明本身也不能把注入推过上限（AC-L1-3 是硬上限）。"""
    for n in (50, 200, 500):
        out = build_graph_context(FakeKg(n, n_edges=n - 1), detailed=True, max_chars=3000)
        assert len(out) <= 3000, (n, len(out))


def test_tiny_cap_still_returns_something():
    """上限小到连一个节点都放不下：照发兜底，不崩不循环。"""
    out = build_graph_context(FakeKg(50), detailed=True, max_chars=10)
    assert out.startswith("## 当前知识图谱")
    assert "[n0]" in out


# ─── 分析器路径（detailed=False）同样受上限约束 ────────────────

def test_overview_mode_capped_too():
    kg = FakeKg(500, n_edges=100)
    out = build_graph_context(kg, detailed=False, max_chars=2000)

    assert out.count("[n") < 500
    assert "(标签: 数学, 基础)" in out             # 概览格式不变
    assert "当前展示范围：节点" in out


# ─── A/B 幻觉率口径：对学生状态的错误断言（2026-09-23） ──────────
# 为什么能规则判定：掌握度有图谱真值（`nodes.mastery`），"你已经掌握了 X" 是否成立
# 可以直接对照。这是参照系 L2（"现在在哪"）的反面证据，零额外 LLM 调用。

from types import SimpleNamespace  # noqa: E402

from scripts.eval_graph_injection import (  # noqa: E402
    detect_mastery_misclaim,
    score_case,
)

# 真值：动态规划未学(0) / 递归已掌握(70) / 递归调用栈薄弱(35)
FACTS = {"dp": ("动态规划", 0), "rec": ("递归", 70), "stack": ("递归调用栈", 35)}


class _FakeResult:
    """run_agent_loop 结果的最小替身（score_case 只用到这几个字段）"""

    def __init__(self, text: str):
        self.text = text
        self.rounds = []
        self.total_llm_calls = 1
        self.token_usage = SimpleNamespace(total_tokens=10)


def test_flags_claim_of_mastery_on_weak_node():
    hits = detect_mastery_misclaim("你已经在动态规划上掌握得很好了，可以直接刷题。", FACTS)
    assert [h["node_id"] for h in hits] == ["dp"]
    assert hits[0]["claim"] == "已掌握"


def test_flags_claim_of_not_learned_on_mastered_node():
    hits = detect_mastery_misclaim("递归你还没学过，我们从最基础的开始。", FACTS)
    assert [h["node_id"] for h in hits] == ["rec"]


def test_state_claims_matching_truth_are_not_flagged():
    """与真值一致的断言不能算幻觉，否则指标会惩罚"答对"。"""
    assert detect_mastery_misclaim("你已经掌握了递归，可以往下走。", FACTS) == []
    assert detect_mastery_misclaim("动态规划你还没学过，先补前置。", FACTS) == []


def test_claim_without_node_name_not_flagged():
    """只笼统说"你掌握得不错"但没点名 → 无法与真值对照，不记（口径宁可漏报）。"""
    assert detect_mastery_misclaim("整体来看你掌握得不错，继续保持。", FACTS) == []


def test_same_node_reported_once():
    hits = detect_mastery_misclaim(
        "你已掌握动态规划。动态规划你已经很熟练了。", FACTS)
    assert len(hits) == 1


def test_node_id_match_requires_boundary():
    """node_id 必须作为独立标识才算命中：`ab_recursion` 不能因文本含 `ab_recursion_base` 而命中。"""
    from scripts.eval_graph_injection import _mentions_node_id

    assert _mentions_node_id("看 ab_recursion 这个节点", "ab_recursion")
    assert not _mentions_node_id("看 ab_recursion_base 这个节点", "ab_recursion")
    assert _mentions_node_id("看 ab_recursion_base 这个节点", "ab_recursion_base")


def test_located_counts_node_id_but_readable_hit_does_not():
    """注入图谱后模型直接抄 node_id 指代节点（实测），定位率要算命中、按名的可读命中不算。"""
    case = {"id": "d", "kind": "graph_dependent", "expect": ["ab_dp"]}
    names = {"ab_dp": "动态规划", "ab_memo": "记忆化搜索"}
    facts = {"ab_dp": ("动态规划", 0)}

    r = score_case(_FakeResult("你图谱里 ab_dp 的掌握度是 0。"), case, names, facts, 1.0)

    assert r["expect_hit"] == 0, "只说 ID 不算学生可读的覆盖"
    assert r["expect_located"] == 1, "但模型确实定位到了这个知识点"
    assert (r["mentions"], r["mentions_any"]) == (0, 1)
    assert r["cited_ids"] == ["ab_dp"], "引用真实 node_id = 模型确实读到了注入块"


def test_score_case_aggregates_both_misclaim_kinds():
    """state_misclaim = 误报图谱空 ∪ 掌握度误述（任一类都计入幻觉率）。"""
    case = {"id": "x", "kind": "graph_dependent", "expect": ["dp"]}
    names = {"dp": "动态规划"}

    empty = score_case(_FakeResult("你的知识图谱目前是空的。"), case, names, FACTS, 1.0)
    assert empty["false_empty"] and empty["state_misclaim"]

    mastery = score_case(_FakeResult("你已经熟练掌握动态规划了。"), case, names, FACTS, 1.0)
    assert not mastery["false_empty"] and mastery["state_misclaim"]
    assert mastery["mastery_misclaim"][0]["node_id"] == "dp"

    clean = score_case(_FakeResult("动态规划的前置是记忆化搜索。"), case, names, FACTS, 1.0)
    assert not clean["state_misclaim"]
