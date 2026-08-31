"""RAG 管道编排测试：注册 / 路由开关 / 异常隔离 / 超时 / 跨源融合去重 / 排序

覆盖 pipeline.run 的核心鲁棒性承诺：
- 任何情况下不抛异常（空查询、问候跳过、无活动源、单源异常、单源超时）
- 跨源按 content 去重（保留高分）
- 结果按分数降序
"""
import asyncio

import pytest

from app.core.rag_pipeline.pipeline import RagPipeline
from app.core.rag_pipeline.types import RagContext, RagHit


class FakeSource:
    """最小可测数据源（实现 RagSource 协议）。"""

    def __init__(self, name="fake", hits=None, should=True, exc=None, delay=0.0):
        self.name = name
        self._hits = hits or []
        self._should = should
        self._exc = exc
        self._delay = delay

    def should_query(self, ctx):
        return self._should

    async def retrieve(self, ctx):
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._exc:
            raise self._exc
        return self._hits


def _hit(content="c", score=0.5, source="fake", **kw):
    return RagHit(source=source, content=content, score=score, **kw)


@pytest.fixture
def pl():
    return RagPipeline()


def _run(pl, query="什么是栈", **kw):
    ctx = RagContext(user_id=1, query=query, **kw)
    return asyncio.run(pl.run(ctx))


# ── 基础流程 ────────────────────────────────────────────────

def test_run_empty_query(pl):
    assert _run(pl, query="") == []
    assert _run(pl, query="   ") == []


def test_run_greeting_skipped(pl):
    pl.register(FakeSource(hits=[_hit()]))
    assert _run(pl, query="你好") == []


def test_run_no_active_source(pl):
    pl.register(FakeSource(should=False))
    assert _run(pl, query="什么是栈") == []


def test_run_single_source(pl):
    pl.register(FakeSource(hits=[_hit("a", 0.9), _hit("b", 0.5)]))
    out = _run(pl)
    assert [h.content for h in out] == ["a", "b"]


def test_run_sorted_by_score(pl):
    pl.register(FakeSource(hits=[_hit("low", 0.2), _hit("mid", 0.6), _hit("high", 0.9)]))
    out = _run(pl)
    assert [h.content for h in out] == ["high", "mid", "low"]


# ── 跨源融合去重 ────────────────────────────────────────────

def test_run_cross_source_dedup_keep_higher(pl):
    pl.register(FakeSource(name="s1", hits=[_hit("同一内容", 0.9, "s1")]))
    pl.register(FakeSource(name="s2", hits=[_hit("同一内容", 0.3, "s2")]))
    out = _run(pl)
    assert len(out) == 1
    assert out[0].source == "s1"


def test_run_dedup_blank_content(pl):
    pl.register(FakeSource(hits=[_hit("", 0.9), _hit("ok", 0.8)]))
    out = _run(pl)
    assert [h.content for h in out] == ["ok"]


# ── 鲁棒隔离 ────────────────────────────────────────────────

def test_run_source_exception_isolated(pl):
    pl.register(FakeSource(name="bad", exc=RuntimeError("boom")))
    pl.register(FakeSource(name="good", hits=[_hit("ok", 0.8)]))
    out = _run(pl)
    assert [h.content for h in out] == ["ok"]


def test_run_all_sources_fail_returns_empty(pl):
    pl.register(FakeSource(name="bad1", exc=RuntimeError("a")))
    pl.register(FakeSource(name="bad2", exc=RuntimeError("b")))
    assert _run(pl) == []


def test_run_source_timeout_isolated(pl, monkeypatch):
    # 注意：rag_pipeline/__init__.py 的 from ...pipeline import pipeline 会把包属性
    # pipeline 从子模块覆盖成单例实例，故必须用 importlib 取真正的模块。
    import importlib
    pipe_mod = importlib.import_module("app.core.rag_pipeline.pipeline")
    monkeypatch.setattr(pipe_mod, "_SOURCE_TIMEOUT", 0.05)
    pl.register(FakeSource(name="slow", delay=10.0))
    pl.register(FakeSource(name="fast", hits=[_hit("ok", 0.8)]))
    out = _run(pl)
    assert [h.content for h in out] == ["ok"]


def test_run_should_query_exception_isolated(pl):
    class BadShould:
        name = "badshould"

        def should_query(self, ctx):
            raise RuntimeError("should boom")

        async def retrieve(self, ctx):
            return [_hit("x", 0.9)]

    pl.register(BadShould())
    pl.register(FakeSource(name="good", hits=[_hit("ok", 0.8)]))
    out = _run(pl)
    assert [h.content for h in out] == ["ok"]


# ── 注册管理 ────────────────────────────────────────────────

def test_register_unregister(pl):
    pl.register(FakeSource(name="a"))
    assert "a" in pl.sources()
    pl.unregister("a")
    assert "a" not in pl.sources()
    assert _run(pl) == []


def test_register_override(pl):
    pl.register(FakeSource(name="a", hits=[_hit("旧", 0.1)]))
    pl.register(FakeSource(name="a", hits=[_hit("新", 0.9)]))
    out = _run(pl)
    assert [h.content for h in out] == ["新"]
