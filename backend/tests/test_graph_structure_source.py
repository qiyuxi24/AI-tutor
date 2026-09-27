"""图谱**结构**注入源（GraphStructureSource）契约测试。

背景：把「知识图谱结构注入」从 chat_service 直接调 `graph_analyzer.build_graph_context`,
改为经 `core/rag_pipeline` 的 **required** 数据源取回 —— 让消费层只依赖 `RagHit`,
不依赖具体检索实现（依赖倒置）。

关键不变量（等价重构）：注入文本与直接调 `build_graph_context` 的产物**逐字符一致**。
其它契约：
- required 源不受 query 节流（"你好"）影响 —— 图谱是教学的唯一边界，必须每次送达
- required 源不受 `ctx.sources` 源选择影响
- 未传 kg 时返回空，且**不得**自行打开 KnowledgeGraph（不碰真实数据库）
"""
import asyncio

import pytest

from app.core.graph_analyzer import build_graph_context
from app.core.knowledge_graph import KnowledgeGraph
from app.core.rag_pipeline.pipeline import RagPipeline
from app.core.rag_pipeline.sources import GraphStructureSource
from app.core.rag_pipeline.types import RagContext, RagHit
from app.services import chat_service as cs

USER = 7301


@pytest.fixture
def kg(tmp_path):
    """真 KnowledgeGraph（隔离到 tmp_path）：2 个带正文的节点。"""
    g = KnowledgeGraph(user_id=USER, data_dir=tmp_path)
    with g._conn:  # nodes.user_id 是外键，先备 users 行
        g._conn.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (?, 'kg', 'x')",
            (USER,),
        )
    g.create_node_with_content({"id": "rec", "name": "递归", "tags": ["算法"]},
                              content="递归指函数调用自身。")
    g.create_node_with_content({"id": "stack", "name": "栈", "tags": ["数据结构"]},
                              content="栈是后进先出的线性结构。")
    yield g
    g.close()


class _FixedSource:
    """最小非 required 数据源（用于验证普通源仍受节流/源选择约束）。"""

    def __init__(self, name, hits=None):
        self.name = name
        self._hits = hits or []

    def should_query(self, ctx):
        return True

    async def retrieve(self, ctx):
        return self._hits


@pytest.fixture
def pl():
    """只注册结构源的最小管道（隔离全局单例的其它源）。"""
    p = RagPipeline()
    p.register(GraphStructureSource())
    return p


# ── 等价重构：注入文本逐字符一致 ──────────────────────────────────

def test_structure_hit_matches_build_graph_context_chars(kg, pl):
    """结构源命中内容 == build_graph_context(kg, detailed=True)，逐字符相等。"""
    expected = build_graph_context(kg, detailed=True)
    hits = asyncio.run(pl.run(RagContext(
        user_id=USER, query="", metadata={"kg": kg})))

    assert [h.source for h in hits] == ["graph_structure"]
    assert hits[0].content == expected  # ← 逐字符相等的铁证


def test_chat_service_graph_summary_equals_direct_build(kg):
    """chat_service._build_graph_summary 的返回值 == 直接调 build_graph_context。"""
    expected = build_graph_context(kg, detailed=True)
    got = asyncio.run(cs._build_graph_summary(kg, detailed=True))
    assert got == expected


# ── required 语义：不受节流与源选择影响 ────────────────────────────

def test_structure_delivered_even_for_greeting(kg, pl):
    """"你好" 会被节流（should_retrieve=False），但结构源仍必须送达。"""
    from app.core.rag_pipeline.router import should_retrieve
    assert should_retrieve("你好") is False  # 前提：节流闸门确实会关

    hits = asyncio.run(pl.run(RagContext(
        user_id=USER, query="你好", metadata={"kg": kg})))
    assert any(h.source == "graph_structure" for h in hits)


def test_required_not_gated_by_ctx_sources(kg):
    """ctx.sources={"kb"} 时，普通源被选中、结构源作为 required 仍送达。"""
    pl = RagPipeline()
    pl.register(GraphStructureSource())
    pl.register(_FixedSource("kb", [RagHit(source="kb", content="kb命中", score=0.5)]))

    hits = asyncio.run(pl.run(RagContext(
        user_id=USER, query="什么是栈", sources={"kb"}, metadata={"kg": kg})))
    sources = {h.source for h in hits}

    assert "graph_structure" in sources  # required 不因源选择缺席
    assert "kb" in sources               # 被选中的普通源正常参与


# ── 无 kg：返回空且不碰数据库 ──────────────────────────────────────

def test_no_kg_returns_empty_and_touches_no_db(monkeypatch):
    """未提供 kg → 返回 []；且不得自行 new KnowledgeGraph（否则会碰真实数据库）。"""
    def _boom(*args, **kwargs):
        raise AssertionError("GraphStructureSource 不得自行创建 KnowledgeGraph")

    monkeypatch.setattr(KnowledgeGraph, "__init__", _boom)

    hits = asyncio.run(GraphStructureSource().retrieve(
        RagContext(user_id=USER, query="", metadata={})))
    assert hits == []
