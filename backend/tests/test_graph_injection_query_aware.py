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

    def __init__(self, n_nodes: int = 50, preview_chars: int = 200):
        self.nodes = [
            {"id": f"n{i}", "name": f"知识点{i}", "mastery": i % 100,
             "tags": ["数学"], "subject": "数学", "board": "基础" if i < 25 else "进阶"}
            for i in range(n_nodes)
        ]
        self.edges = [
            {"from_node": f"n{i}", "to_node": f"n{i + 1}",
             "relation": "prerequisite", "label": "前置"}
            for i in range(max(0, n_nodes - 1))
        ]
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
