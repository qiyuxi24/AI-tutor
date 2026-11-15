"""P1-1 S4「按 query 选片」：全量骨架常驻 + 焦点邻域给细节（契约 §5 G1 第 1、2 步）。

不变量（本文件逐条钉住）：
  AC-N1  同一 kg、两个不同 query → 注入文本**不同**，且差异只在 S4-b（骨架段逐字符一致）
  AC-N2  注入文本仍含**全部**节点的 `[id]`（骨架不缩水 → 不违反 I1-1/I1-3）
  AC-N3  字符数 ≤ 上限；超限时仍有"展示范围/未列出并非不存在"说明（AC-L1-3 不破）
  AC-N4  50 节点 + 1 焦点时，S4 token ≤ 现状（全量含摘要）× 0.6

⚠ 这不是"检索命中才注入"：`GraphStructureSource` 仍是 required（每次必在），
`focus_ids` 只决定**细节给哪一块**（契约 §8）。
"""
import re

from app.core.graph_analyzer import build_graph_context
from app.core.token_counter import count_tokens


class FakeKg:
    """build_graph_context 用到的成员（鸭子类型）：nodes / edges / 内容预览 / 学科。"""

    def __init__(self, n_nodes: int = 50, preview_chars: int = 200,
                 edges_per_node: int = 1):
        self.nodes = [
            {"id": f"n{i}", "name": f"知识点{i}", "mastery": i % 100,
             "tags": ["数学"], "subject": "数学", "board": "基础" if i < 25 else "进阶"}
            for i in range(n_nodes)
        ]
        if edges_per_node <= 1:
            self.edges = [
                {"from_node": f"n{i}", "to_node": f"n{i + 1}",
                 "relation": "prerequisite", "label": "前置"}
                for i in range(max(0, n_nodes - 1))
            ]
        else:
            # 真实库的形状：每节点 ~2 条边（含 4 类 relation），用来复现
            # 「边块恒为全量 → 阶梯 ①②③④ 全部超限 → 焦点段永不输出」。
            rels = ("prerequisite", "related", "extension", "confusion")
            self.edges = []
            for i in range(n_nodes):
                for k in range(1, edges_per_node + 1):
                    j = (i + k) % n_nodes
                    if i == j:
                        continue
                    self.edges.append({
                        "from_node": f"n{i}", "to_node": f"n{j}",
                        "relation": rels[k % len(rels)], "label": "前置知识"})
        self._preview = "内容" * (preview_chars // 2)

    def get_node_content_preview(self, node_id: str) -> str:
        return self._preview

    def node_subject(self, node: dict) -> str:
        return node.get("subject") or ""


def _ids(text: str) -> set[str]:
    return set(re.findall(r"\[([A-Za-z0-9_]+)\]", text))


# ── AC-N1：两个 query → 文本不同，差异只在本 S4-b ─────────────────

def test_two_queries_differ_only_in_focus_segment():
    kg = FakeKg(20)

    a = build_graph_context(kg, detailed=True, max_chars=0, focus_ids=["n3"])
    b = build_graph_context(kg, detailed=True, max_chars=0, focus_ids=["n15"])

    assert a != b, "不同 query（不同焦点）必须给出不同的注入文本"
    # 骨架段（焦点段之前）逐字符相同 —— 差异只允许出现在 S4-b
    before_a = a.split("### 本次焦点邻域")[0]
    before_b = b.split("### 本次焦点邻域")[0]
    assert before_a == before_b
    assert "n3" in a.split("### 本次焦点邻域")[1].split("> 说明")[0]
    assert "n15" in b.split("### 本次焦点邻域")[1].split("> 说明")[0]


def test_no_focus_falls_back_to_legacy_full_injection():
    """焦点判不出（focus_ids 为空）→ 退化为现状全量，绝不劣化。"""
    kg = FakeKg(5)

    legacy = build_graph_context(kg, detailed=True, max_chars=0)

    assert "### 现有节点" in legacy          # 旧格式（含摘要）
    assert "摘要:" in legacy
    assert "知识可行域" not in legacy


# ── AC-N2：骨架不缩水（全部 [id] 都在）─────────────────────────────

def test_all_node_ids_present():
    kg = FakeKg(50)

    out = build_graph_context(kg, detailed=True, max_chars=0, focus_ids=["n10"])

    assert {n["id"] for n in kg.nodes} - _ids(out) == set(), "骨架段不得少给任何节点"


# ── AC-N3：上限与展示范围说明 ─────────────────────────────────────

def test_cap_never_exceeded_with_focus():
    kg = FakeKg(50)

    for cap in (400, 1200, 3000, 12000):
        out = build_graph_context(kg, detailed=True, max_chars=cap, focus_ids=["n10"])
        assert len(out) <= cap, (cap, len(out))


def test_over_cap_keeps_scope_note():
    kg = FakeKg(200)

    out = build_graph_context(kg, detailed=True, max_chars=1500, focus_ids=["n10"])

    assert len(out) <= 1500
    assert "展示范围" in out or "并非不存在" in out


# ── AC-N4：token 收敛（50 节点 + 1 焦点 ≤ 现状 × 0.6）──────────────

def test_focus_slicing_cuts_tokens():
    kg = FakeKg(50)

    baseline = count_tokens(build_graph_context(kg, detailed=True, max_chars=0))
    sliced = count_tokens(build_graph_context(
        kg, detailed=True, max_chars=0, focus_ids=["n10"]))

    assert sliced <= baseline * 0.6, (sliced, baseline)


# ── 焦点邻域确实带细节（否则选片就没意义）────────────────────────

def test_focus_neighborhood_has_summary():
    kg = FakeKg(50)

    out = build_graph_context(kg, detailed=True, max_chars=0, focus_ids=["n10"])

    segment = out.split("### 本次焦点邻域")[1]
    assert "摘要:" in segment
    # ±2 跳：n8..n12 都应在焦点段里
    for nid in ("n8", "n12"):
        assert f"[{nid}]" in segment


# ── 真实规模（2026-11-15）：边块恒为全量 → 焦点段从未输出过 ──────────
#
# 线上实测（user 5：123 节点 / 233 边）：注入 11910 字，但 `摘要:` 出现 **0 次**
# —— 阶梯 ①②③④ 的边块都是全量（17492 字），逐档恒超 12000 上限，
# 于是永远落到 ⑤「骨架截断 + 子图边」，焦点邻域被静默丢弃。
# 上面的 FakeKg(50) 边太少（49 条），这个 bug 不可能复现。

def test_focus_survives_at_realistic_scale():
    """真实规模下焦点邻域必须仍在（否则"按 query 选片"等于没实现）。"""
    kg = FakeKg(120, edges_per_node=3)

    out = build_graph_context(kg, detailed=True, max_chars=12000, focus_ids=["n60"])

    assert len(out) <= 12000, f"超上限：{len(out)}"
    assert "本次焦点邻域" in out, "真实规模下焦点段被整段丢弃（阶梯①②③④ 恒超限）"
    assert "摘要:" in out, "焦点摘要（选片唯一的收益）没有进注入"
    assert "[n60]" in out.split("### 本次焦点邻域")[1]


def test_skeleton_never_loses_nodes_at_realistic_scale():
    """I1-1/I1-3：可行域骨架不得因体量而丢节点（截断必须发生在细节层，不在骨架层）。"""
    kg = FakeKg(120, edges_per_node=3)

    out = build_graph_context(kg, detailed=True, max_chars=12000, focus_ids=["n60"])

    assert {n["id"] for n in kg.nodes} - _ids(out) == set(), "骨架段不得少给任何节点"


def test_legacy_fallback_keeps_all_nodes_at_realistic_scale():
    """判不出焦点（focus_ids 为空）时同样不得丢节点 —— 这才是线上最常见的形状。

    线上实测：这一步丢 54/123 个节点（整个「高中物理」消失），根因与焦点路径同一个：
    全量表 17549 字单独就顶穿 12000 上限，于是只能靠删节点往下压。
    """
    kg = FakeKg(120, edges_per_node=3)

    out = build_graph_context(kg, detailed=True, max_chars=12000)

    assert {n["id"] for n in kg.nodes} - _ids(out) == set(), "退化为全量时骨架不得丢节点"
