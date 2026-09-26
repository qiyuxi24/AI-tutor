"""HyDE 查询扩展测试：生成/失败回退 + pipeline 多 query 融合。

覆盖的承诺：
- 假设答案生成为空或抛错 → 返回 None，pipeline 回退到"只检索原 query"
- 开启后每个源各按 原 query / 假设答案 检索一次
- 多 query 命中按 RRF 融合（不同 query 的分数不可比）
"""
import asyncio
import importlib

import pytest

from app.core.rag_pipeline.pipeline import RagPipeline
from app.core.rag_pipeline.types import RagContext, RagHit

qe = importlib.import_module("app.core.rag_pipeline.query_expansion")
pipe_mod = importlib.import_module("app.core.rag_pipeline.pipeline")


# ────────────────────────────────────────────
#  hyde_query
# ────────────────────────────────────────────

def test_hyde_returns_text(monkeypatch):
    async def _fake(system, messages, **kw):
        return "栈是一种后进先出的线性表。"
    monkeypatch.setattr(qe, "call_llm", _fake)
    assert asyncio.run(qe.hyde_query("什么是栈")) == "栈是一种后进先出的线性表。"


def test_hyde_empty_response_is_none(monkeypatch):
    async def _fake(system, messages, **kw):
        return "   "
    monkeypatch.setattr(qe, "call_llm", _fake)
    assert asyncio.run(qe.hyde_query("什么是栈")) is None


def test_hyde_exception_is_none(monkeypatch):
    async def _fake(system, messages, **kw):
        raise RuntimeError("E-LLM-001 超时")
    monkeypatch.setattr(qe, "call_llm", _fake)
    assert asyncio.run(qe.hyde_query("什么是栈")) is None


def test_hyde_passes_short_budget_and_no_thinking(monkeypatch):
    """短文本生成：不走思考、输出预算收在 HYDE_MAX_TOKENS（思考会吃掉预算）。"""
    seen = {}

    async def _fake(system, messages, **kw):
        seen.update(kw)
        return "答案"
    monkeypatch.setattr(qe, "call_llm", _fake)
    asyncio.run(qe.hyde_query("什么是栈", user_id=7))
    assert seen["thinking"] is False
    assert seen["max_tokens"] == qe.HYDE_MAX_TOKENS
    assert seen["kind"] == "rag_hyde"


# ────────────────────────────────────────────
#  pipeline 集成
# ────────────────────────────────────────────

class QueryAwareSource:
    """记录被检索的 query，为每个 query 返回一条可区分的命中。"""

    def __init__(self, name="q"):
        self.name = name
        self.seen: list[str] = []

    def should_query(self, ctx):
        return True

    async def retrieve(self, ctx):
        self.seen.append(ctx.query)
        return [RagHit(source=self.name, content=f"{ctx.query}|{self.name}", score=0.9)]


def _enable(monkeypatch, hypo="假设答案文本"):
    async def _fake(query, user_id=None):
        return hypo
    monkeypatch.setattr(pipe_mod, "hyde_query", _fake)
    monkeypatch.setattr(pipe_mod.settings, "rag_hyde_enabled", True)


def test_pipeline_hyde_queries_twice(monkeypatch):
    _enable(monkeypatch, hypo="栈是后进先出的线性表")
    src = QueryAwareSource()
    pl = RagPipeline()
    pl.register(src)
    out = asyncio.run(pl.run(RagContext(user_id=1, query="什么是栈", top_k=5)))
    assert src.seen == ["什么是栈", "栈是后进先出的线性表"]
    assert len(out) == 2


def test_pipeline_hyde_failure_falls_back(monkeypatch):
    """假设答案生成失败 → 只按原 query 检索一次（检索是增强，不能因为扩展失败而空手）。"""
    _enable(monkeypatch, hypo=None)
    src = QueryAwareSource()
    pl = RagPipeline()
    pl.register(src)
    out = asyncio.run(pl.run(RagContext(user_id=1, query="什么是栈", top_k=5)))
    assert src.seen == ["什么是栈"]
    assert [h.content for h in out] == ["什么是栈|q"]


def test_pipeline_hyde_off_by_default():
    """默认关闭：每轮对话不该为召回多付一次 LLM 调用。"""
    src = QueryAwareSource()
    pl = RagPipeline()
    pl.register(src)
    asyncio.run(pl.run(RagContext(user_id=1, query="什么是栈", top_k=5)))
    assert src.seen == ["什么是栈"]


def test_pipeline_multi_query_rrf_promotes_agreeing_hit(monkeypatch):
    """两个 query 都命中的片段（RRF 累加两路）应排在只有单路命中的前面。"""

    class Both:
        name = "both"

        def should_query(self, ctx):
            return True

        async def retrieve(self, ctx):
            shared = RagHit(source=self.name, content="共同命中", score=0.1)
            only = RagHit(source=self.name, content=f"仅-{ctx.query}", score=0.9)
            return [only, shared]

    _enable(monkeypatch, hypo="栈是后进先出的线性表")
    pl = RagPipeline()
    pl.register(Both())
    out = asyncio.run(pl.run(RagContext(user_id=1, query="什么是栈", top_k=5)))
    assert out[0].content == "共同命中"
