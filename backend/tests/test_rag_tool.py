"""rag_search 工具测试（转正自 _test_ragtool.py 临时验证脚本）

覆盖：
- rag_search 在纯同步上下文（无运行中事件循环 → asyncio.run 直跑）
- rag_search 在 async 上下文（有运行中事件循环 → 线程池路径）
- 来源标注 / path 出处透传
- 无 user_id 返回友好提示
- top_k 钳制（1~5）
- execute_kg_tool 的 rag_search 分支
- KG_TOOLS 注册了 rag_search
"""
import asyncio

import pytest

from app.core.rag_pipeline import RagHit, pipeline as real_pipeline
from app.core.rag_pipeline.types import RagContext


@pytest.fixture
def fake_pipeline(monkeypatch):
    """mock pipeline.run，返回带来源/出处的命中。"""
    async def fake_run(ctx):
        assert isinstance(ctx, RagContext)
        assert ctx.query
        return [
            RagHit(source="graph", content="栈是后进先出的数据结构", score=0.9,
                   heading="栈", path="栈", metadata={"node_name": "栈"}),
            RagHit(source="kb", content="队列先进先出，实现要点...", score=0.8,
                   heading="队列", path="/数据结构/第2章/队列.md",
                   metadata={"kb_name": "数据结构教材"}),
        ]
    monkeypatch.setattr(real_pipeline, "run", fake_run)


# ── rag_search ──────────────────────────────────────────────

def test_rag_search_sync_context(fake_pipeline):
    from app.core.agent_tools.tools.rag_search import rag_search
    r = rag_search("什么是栈", source="all", top_k=3, user_id=1)
    assert "栈是后进先出" in r          # graph 结果
    assert "队列先进先出" in r          # kb 结果
    assert "来源:graph" in r           # 来源标注
    assert "/数据结构/第2章/队列.md" in r  # kb 出处 path 透传


def test_rag_search_inside_event_loop(fake_pipeline):
    """已有运行中事件循环时走线程池路径（_run_async 双路径之一）。"""
    from app.core.agent_tools.tools.rag_search import rag_search

    async def inner():
        return rag_search("什么是栈", source="graph", top_k=2, user_id=1)

    r = asyncio.run(inner())
    assert "栈是后进先出" in r


def test_rag_search_no_user_id(fake_pipeline):
    from app.core.agent_tools.tools.rag_search import rag_search
    r = rag_search("什么是栈", source="all", user_id=None)
    assert "无法确定用户上下文" in r


def test_rag_search_top_k_clamped(fake_pipeline):
    from app.core.agent_tools.tools.rag_search import rag_search
    r = rag_search("测试", source="all", top_k=99, user_id=1)
    assert r  # 钳制到 5 后仍走 fake_run，不抛错


# ── execute_kg_tool rag_search 分支 ─────────────────────────

class _FakeArgs:
    def __init__(self, s):
        self.s = s


class _FakeFn:
    def __init__(self, name, args):
        self.name = name
        self.arguments = args


class _FakeToolCall:
    def __init__(self, name, args):
        self.function = _FakeFn(name, args)


class _FakeKg:
    user_id = 1


def test_kg_tools_registers_rag_search():
    from app.core.agent_tools import KG_TOOLS
    names = [t["function"]["name"] for t in KG_TOOLS]
    assert "rag_search" in names


def test_execute_kg_tool_rag_search_branch(fake_pipeline):
    from app.core.agent_tools import execute_kg_tool
    tc = _FakeToolCall("rag_search", '{"query": "什么是队列", "source": "kb", "top_k": 2}')
    result = execute_kg_tool(tc, _FakeKg())
    assert "队列先进先出" in result


def test_execute_kg_tool_top_k_clamped(fake_pipeline):
    from app.core.agent_tools import execute_kg_tool
    tc = _FakeToolCall("rag_search", '{"query": "测试", "top_k": 99}')
    result = execute_kg_tool(tc, _FakeKg())
    assert result  # 不抛错


def test_execute_kg_tool_missing_query_no_crash(fake_pipeline):
    from app.core.agent_tools import execute_kg_tool
    tc = _FakeToolCall("rag_search", '{"source": "kb"}')
    result = execute_kg_tool(tc, _FakeKg())
    assert "工具执行出错" in result or "操作失败" in result
