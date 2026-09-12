"""先修关系多准则推断测试（P0-3）

纯函数测试：不碰数据库、不调 LLM、不联网（嵌入用注入的假向量/假嵌入器）。
"""
import pytest

from app.core.prerequisite import (
    TOTAL_WEIGHT, PrereqCandidate, apply_candidates, infer_prerequisites,
)


def _node(nid, name, difficulty=3, created_at=None, summary=""):
    return {"id": nid, "name": name, "difficulty": difficulty, "summary": summary,
            "created_at": created_at or f"2026-01-01T00:00:{ord(nid[0]) % 60:02d}"}


def _pairs(cands):
    return {(c.source, c.target) for c in cands}


def _score(cands, src, dst):
    return next(c.score for c in cands if c.source == src and c.target == dst)


def _vote(cands, src, dst, criterion):
    """取某对候选里某条准则的投票值（+1/0/-1）"""
    cand = next(c for c in cands if c.source == src and c.target == dst)
    return next(v.value for v in cand.votes if v.criterion == criterion)


# ══════════════════════════════════════════════════════════════════
#  基础方向性
# ══════════════════════════════════════════════════════════════════

NODES = [
    _node("a", "数组", difficulty=1, created_at="2026-01-01T00:00:00"),
    _node("b", "栈", difficulty=2, created_at="2026-01-02T00:00:00"),
    _node("c", "表达式求值", difficulty=4, created_at="2026-01-03T00:00:00"),
]

CONTENT = {
    "a": "数组是连续存储的线性结构。",
    "b": "栈是一种线性表，可以用数组实现。",
    "c": "表达式求值需要用栈保存运算符，栈的先进后出特性是关键。",
}


def test_citation_gives_direction():
    """C1 正文引用：C 的正文引用「栈」→ 推断 栈 → C，且不产生反向边"""
    cands = infer_prerequisites(NODES, [], content=CONTENT)
    assert ("b", "c") in _pairs(cands)
    assert ("c", "b") not in _pairs(cands)
    assert _vote(cands, "b", "c", "C1正文引用") == 1


def test_single_char_name_needs_two_hits():
    """单字概念只被引用 1 次时不计证据（"树"命中"二叉树"的误报抑制）"""
    nodes = [_node("t", "树", difficulty=2, created_at="2026-01-01T00:00:00"),
             _node("bt", "二叉树", difficulty=3, created_at="2026-01-02T00:00:00")]
    # 「二叉树」中虽然含「树」字，但"树"作为独立词只出现 1 次 → 不计证据
    once = infer_prerequisites(nodes, [], content={"bt": "二叉树的递归遍历。"},
                               threshold=-1.0)
    assert _vote(once, "t", "bt", "C1正文引用") == 0
    twice = infer_prerequisites(nodes, [], content={"bt": "树是递归结构，树的遍历分四类。"},
                                threshold=-1.0)
    assert _vote(twice, "t", "bt", "C1正文引用") == 1


def test_weak_criteria_alone_is_not_enough():
    """只有弱准则（次序 + 难度差）不足以认定先修关系"""
    cands = infer_prerequisites(NODES, [], content=CONTENT)
    # a→c 只有 C3(次序) + C6(难度) 两条弱票 = 2.0/11.5 ≈ 0.17 < 0.30
    assert ("a", "c") not in _pairs(cands)


def test_score_bounds_and_threshold_monotonicity():
    """阈值单调性：阈值越高，候选只会变少；分数落在 [-1, 1]"""
    loose = infer_prerequisites(NODES, [], content=CONTENT, threshold=0.10)
    tight = infer_prerequisites(NODES, [], content=CONTENT, threshold=0.60)
    assert _pairs(tight) <= _pairs(loose)
    assert all(-1.0 <= c.score <= 1.0 for c in loose)


def test_explicit_order_overrides_created_at():
    """传入权威教材顺序时，C3 以 order 为准（覆盖 created_at 近似）"""
    order = {"a": 2, "b": 1, "c": 0}          # 教材顺序：C → 栈 → 数组
    cands = infer_prerequisites(NODES, [], content=CONTENT, order=order)
    # 与正文引用方向冲突时，次序票被拉反
    assert _vote(cands, "b", "c", "C3次序") == -1


# ══════════════════════════════════════════════════════════════════
#  名称包含 / 语义相似（反对票）
# ══════════════════════════════════════════════════════════════════

def test_name_containment():
    """C2 名称包含：术语构成依赖「树」⊂「二叉树」"""
    nodes = [_node("t", "树", difficulty=2, created_at="2026-01-01T00:00:00"),
             _node("bt", "二叉树", difficulty=3, created_at="2026-01-02T00:00:00")]
    cands = infer_prerequisites(nodes, [], threshold=0.20)
    assert ("t", "bt") in _pairs(cands)
    assert _vote(cands, "t", "bt", "C2名称包含") == 1


class _ConstEmbedder:
    """固定相似度的假嵌入器（避免测试联网/加载模型）"""

    def __init__(self, sim):
        self.sim = sim

    def embed(self, texts):
        return [[1.0, 0.0] for _ in texts]

    def similarity(self, a, b):
        return self.sim


def test_synonym_pair_is_opposed():
    """C5 语义相似：高相似 → 反对（那是同义词，应去重而不是连先修边）"""
    nodes = [_node("x", "哈希表", difficulty=3, created_at="2026-01-01T00:00:00"),
             _node("y", "散列表", difficulty=3, created_at="2026-01-02T00:00:00")]
    vectors = {"x": [1.0, 0.0], "y": [0.9, 0.1]}
    cands = infer_prerequisites(nodes, [], embedder=_ConstEmbedder(0.95),
                                vectors=vectors, threshold=-1.0)
    assert _vote(cands, "x", "y", "C5语义相似") == -1
    # 相似度低时该准则弃权
    cands2 = infer_prerequisites(nodes, [], embedder=_ConstEmbedder(0.40),
                                 vectors=vectors, threshold=-1.0)
    assert _vote(cands2, "x", "y", "C5语义相似") == 0


# ══════════════════════════════════════════════════════════════════
#  图结构约束：已有边、跨跳冗余、无环、入度上限
# ══════════════════════════════════════════════════════════════════

def test_existing_edge_is_not_duplicated():
    """已存在的 prerequisite 边不再作为候选输出"""
    edges = [{"from_node": "b", "to_node": "c", "relation": "prerequisite"}]
    cands = infer_prerequisites(NODES, edges, content=CONTENT)
    assert ("b", "c") not in _pairs(cands)


def test_transitive_edge_is_skipped():
    """已存在 a→b→c 时，a→c 属跨跳冗余，不再补直连"""
    edges = [{"from_node": "a", "to_node": "b", "relation": "prerequisite"},
             {"from_node": "b", "to_node": "c", "relation": "prerequisite"}]
    content = {"a": "数组是连续存储的线性结构。",
               "b": "栈是一种线性表，可以用数组实现。",
               "c": "表达式求值需要数组和栈。"}   # C 的正文同时引用两者
    cands = infer_prerequisites(NODES, edges, content=content)
    assert ("a", "c") not in _pairs(cands)


def test_cycle_is_rejected():
    """反向边已存在时，不得再生成会成环的边"""
    edges = [{"from_node": "b", "to_node": "a", "relation": "prerequisite"}]
    cands = infer_prerequisites(NODES, edges, content=CONTENT)
    assert ("a", "b") not in _pairs(cands)


def test_max_parents_caps_indegree():
    """单节点入度上限生效"""
    nodes = [_node("p1", "甲概念", difficulty=1, created_at="2026-01-01T00:00:00"),
             _node("p2", "乙概念", difficulty=1, created_at="2026-01-02T00:00:00"),
             _node("p3", "丙概念", difficulty=1, created_at="2026-01-03T00:00:00"),
             _node("t", "总概念", difficulty=5, created_at="2026-01-04T00:00:00")]
    content = {"t": "总概念需要先掌握甲概念、乙概念和丙概念。"}
    capped = infer_prerequisites(nodes, [], content=content, max_parents_per_node=1)
    full = infer_prerequisites(nodes, [], content=content, max_parents_per_node=5)
    assert len([c for c in capped if c.target == "t"]) == 1
    assert len([c for c in full if c.target == "t"]) == 3


def _ctx(nodes, edges):
    from app.core.prerequisite import _Context
    return _Context(nodes, edges, {}, None, None, None)


def test_shared_predecessor_does_not_kill_chain_edge():
    """共享 1 个前置（链式场景）时 C4 必须弃权，否则会杀掉正确的传递边"""
    from app.core.prerequisite import _vote_common_pred
    nodes = [_node("a", "数组", difficulty=1, created_at="2026-01-01T00:00:00"),
             _node("b", "栈", difficulty=2, created_at="2026-01-02T00:00:00"),
             _node("c", "表达式求值", difficulty=4, created_at="2026-01-03T00:00:00")]
    edges = [{"from_node": "a", "to_node": "b", "relation": "prerequisite"},
             {"from_node": "a", "to_node": "c", "relation": "prerequisite"}]
    ctx = _ctx(nodes, edges)
    assert _vote_common_pred("b", "c", ctx).value == 0

    # 端到端：该传递边最终仍应被推断出来
    content = {"c": "表达式求值要用栈两次：栈保存运算符、栈保存操作数。"}
    cands = infer_prerequisites(nodes, edges, content=content)
    assert ("b", "c") in _pairs(cands)


def test_same_layer_concepts_are_opposed():
    """真正同层（≥2 共同前置且无包含关系）时 C4 投反对票"""
    from app.core.prerequisite import _vote_common_pred
    nodes = [_node("p1", "甲"), _node("p2", "乙"), _node("p3", "丙"), _node("p4", "丁"),
             _node("x", "哈夫曼树"), _node("y", "红黑树")]
    edges = [{"from_node": p, "to_node": t, "relation": "prerequisite"}
             for p, t in [("p1", "x"), ("p2", "x"), ("p3", "x"),
                          ("p1", "y"), ("p2", "y"), ("p4", "y")]]
    assert _vote_common_pred("x", "y", _ctx(nodes, edges)).value == -1


def test_no_candidate_for_tiny_graph():
    """节点不足 2 个时不推断"""
    assert infer_prerequisites([_node("a", "数组")], [], content={}) == []


def test_weights_are_normalized_by_total():
    """分数归一化基值 = 全部准则权重之和（改权重要同步改测试预期）"""
    assert TOTAL_WEIGHT == pytest.approx(11.5)


# ══════════════════════════════════════════════════════════════════
#  写库容错
# ══════════════════════════════════════════════════════════════════

class _FakeKG:
    def __init__(self, fail):
        self.fail = fail
        self.written = []

    def add_edge(self, edge, caller="human"):
        if edge["from"] in self.fail:
            raise self.fail[edge["from"]]
        self.written.append((edge, caller))


def test_apply_candidates_is_fault_tolerant():
    """单条边失败（重复/成环/权限）不影响其余边写入，并记录跳过原因"""
    cands = [PrereqCandidate("a", "b", 0.5, []),
             PrereqCandidate("b", "c", 0.4, []),
             PrereqCandidate("c", "d", 0.3, [])]
    kg = _FakeKG({"b": ValueError("边已存在"), "c": PermissionError("AI 无权")})
    result = apply_candidates(kg, cands)

    assert [c["from"] for c in result["created"]] == ["a"]
    assert {s["from"] for s in result["skipped"]} == {"b", "c"}
    assert "边已存在" in result["skipped"][0]["reason"]
    assert kg.written[0][1] == "ai"
    # 置信度带上了分数，便于前端展示与人工复核
    assert kg.written[0][0]["confidence"] == 0.5
