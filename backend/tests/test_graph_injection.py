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
