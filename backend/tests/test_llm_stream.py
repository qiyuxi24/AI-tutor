"""`core/llm/stream.py`（对话流式变体）单元测试 —— 全离线，桩掉 `chat_create`。

覆盖（对齐 llm/stream.py 的模块说明）：
- 正文逐 chunk 累积 + 逐 chunk 回调 `on_delta`（首字延迟的来源）；
- tool_calls delta 按 index 聚合（id/name 只在首片、arguments 分片拼接）；
- reasoning_details 累积成与非流式同形的 list[dict]；
- usage/model 取末 chunk（`stream_options.include_usage`）；
- 网关拒绝 `stream_options`（400）→ 去掉该参数重开，不把聊天搞崩；其他错误原样抛。

真机依据（2026-10-10 探针，MiniMax-M3 + 备用网关）：tool_calls 确以 delta 下发、
`stream_options` 被支持、reasoning_details 元素形状与非流式一致 —— 见本文件末注释。
"""
import asyncio
from types import SimpleNamespace

import httpx
import pytest
from openai import APIStatusError

from app.core.llm import stream as stream_mod


def _api_err(status: int) -> APIStatusError:
    req = httpx.Request("POST", "http://test")
    return APIStatusError(
        f"HTTP {status}", response=httpx.Response(status, request=req), body=None
    )


def _tc_delta(index=0, tc_id=None, name=None, args=None):
    fn = SimpleNamespace(name=name, arguments=args)
    return SimpleNamespace(index=index, id=tc_id, type="function", function=fn)


def _chunk(*, content=None, tool_calls=None, reasoning=None, usage=None,
           model="m-1", finish=None, choices=True):
    delta = SimpleNamespace(content=content, tool_calls=tool_calls,
                            reasoning_details=reasoning)
    return SimpleNamespace(
        model=model, usage=usage,
        choices=[SimpleNamespace(delta=delta, finish_reason=finish)] if choices else [],
    )


class _FakeStream:
    def __init__(self, chunks):
        self._chunks = list(chunks)
        self._it = None

    def __aiter__(self):
        self._it = iter(self._chunks)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


def _install(monkeypatch, streams, calls):
    """桩掉 stream.chat_create：按调用序返回 `_FakeStream`（元素可为异常），记录 kwargs。"""
    queue = list(streams)

    async def fake_chat_create(*, user_id=None, **kwargs):
        calls.append({"user_id": user_id, "kwargs": kwargs})
        item = queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return _FakeStream(item)

    monkeypatch.setattr(stream_mod, "chat_create", fake_chat_create)


# ── 1. 正文：逐 chunk 累积 + 逐 chunk 回调 ───────────────────────────

def test_accumulates_content_and_calls_on_delta_per_chunk(monkeypatch):
    calls, deltas = [], []
    _install(monkeypatch, [[
        _chunk(content="二重"),
        _chunk(content="积分是"),
        _chunk(content="对面积的积分。", finish="stop",
               usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15)),
    ]], calls)

    resp = asyncio.run(stream_mod.chat_create_stream(
        user_id=7, on_delta=deltas.append,
        messages=[{"role": "user", "content": "x"}], temperature=0.3))

    assert deltas == ["二重", "积分是", "对面积的积分。"]
    assert resp.choices[0].message.content == "二重积分是对面积的积分。"
    assert resp.choices[0].finish_reason == "stop"
    # 传了 user_id 且自动带 stream=True / stream_options
    assert calls[0]["user_id"] == 7
    assert calls[0]["kwargs"]["stream"] is True
    assert calls[0]["kwargs"]["stream_options"] == {"include_usage": True}
    # usage / model 取自 chunk
    assert resp.usage.total_tokens == 15
    assert resp.model == "m-1"


def test_no_on_delta_still_accumulates(monkeypatch):
    """不传 on_delta（只想要一个同形响应）也不炸。"""
    calls = []
    _install(monkeypatch, [[_chunk(content="a"), _chunk(content="b", finish="stop")]], calls)
    resp = asyncio.run(stream_mod.chat_create_stream(messages=[], temperature=0.3))
    assert resp.choices[0].message.content == "ab"


# ── 2. tool_calls：delta 按 index 聚合 ──────────────────────────────

def test_aggregates_tool_call_deltas(monkeypatch):
    calls = []
    _install(monkeypatch, [[
        _chunk(content="我先查一下。"),
        _chunk(tool_calls=[_tc_delta(0, tc_id="call_1", name="rag_search", args='{"query": "二重')]),
        _chunk(tool_calls=[_tc_delta(0, args='积分"}')], finish="tool_calls"),
    ]], calls)

    resp = asyncio.run(stream_mod.chat_create_stream(messages=[], temperature=0.3))

    msg = resp.choices[0].message
    assert msg.content == "我先查一下。"
    assert len(msg.tool_calls) == 1
    tc = msg.tool_calls[0]
    assert (tc.id, tc.function.name) == ("call_1", "rag_search")
    assert tc.function.arguments == '{"query": "二重积分"}'


def test_no_tool_calls_is_falsy(monkeypatch):
    """纯文本轮：tool_calls 必须为假值（下游用 `if not msg.tool_calls` 判自然终止）。"""
    calls = []
    _install(monkeypatch, [[_chunk(content="ok", finish="stop")]], calls)
    resp = asyncio.run(stream_mod.chat_create_stream(messages=[], temperature=0.3))
    assert not resp.choices[0].message.tool_calls


# ── 3. reasoning_details 累积 ───────────────────────────────────────

def test_accumulates_reasoning_details(monkeypatch):
    calls = []
    _install(monkeypatch, [[
        _chunk(reasoning=[{"type": "reasoning.text", "text": "用户问的是"}]),
        _chunk(reasoning=[{"type": "reasoning.text", "text": "二重积分。"}], content="答案。"),
    ]], calls)
    resp = asyncio.run(stream_mod.chat_create_stream(messages=[], temperature=0.3))
    rd = resp.choices[0].message.reasoning_details
    assert [r["text"] for r in rd] == ["用户问的是", "二重积分。"]


# ── 4. stream_options 被拒（400）→ 去掉重开 ─────────────────────────

def test_stream_options_400_degrades_without_usage(monkeypatch):
    calls = []
    err = RuntimeError("[E-LLM-005] 服务异常")
    err.__cause__ = _api_err(400)
    _install(monkeypatch, [
        err,
        [_chunk(content="降级后仍能流。", finish="stop")],
    ], calls)

    resp = asyncio.run(stream_mod.chat_create_stream(messages=[], temperature=0.3))

    assert len(calls) == 2
    assert "stream_options" in calls[0]["kwargs"]
    assert "stream_options" not in calls[1]["kwargs"]
    assert calls[1]["kwargs"]["stream"] is True          # 仍然流式
    assert resp.choices[0].message.content == "降级后仍能流。"
    assert resp.usage is None                            # 丢 usage（降级代价）


def test_non_400_error_propagates_without_retry(monkeypatch):
    """500 / 认证失败等不该被当成"参数不支持"重开一次（避免把一次失败变成两次）。"""
    calls = []
    err = RuntimeError("[E-LLM-005] 服务异常")
    err.__cause__ = _api_err(500)
    _install(monkeypatch, [err, [_chunk(content="不该到这里")]], calls)

    with pytest.raises(RuntimeError):
        asyncio.run(stream_mod.chat_create_stream(messages=[], temperature=0.3))
    assert len(calls) == 1


# 真机探针记录（2026-10-10，.dsh_local/probe_stream*.py）：
#   · 主模型 MiniMax-M3：首字节 425ms / 首正文 2022ms / 整段 14369ms（非流式整段 52849ms）；
#   · 备用网关（与主模型不同的独立账号）：支持 stream + include_usage（chunk=9）；
#   · tool_calls 以 delta 下发（id/name 仅首片，arguments 分片）；finish_reason="tool_calls"；
#   · reasoning_details 元素 = {"type":"reasoning.text","id":...,"text":...}（与非流式同形）。
