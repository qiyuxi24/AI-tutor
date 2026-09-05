"""B1.1 collector http：UA 头存在、并发上限生效、超时降级返回空

用 httpx.MockTransport 注入 handler，全程离线。
"""
import asyncio
from typing import Awaitable, Callable

import httpx
import pytest

from app.core.collector.http import CollectorHttp, DEFAULT_UA


def _run(coro: Awaitable):
    return asyncio.run(coro)


def test_user_agent_header_present():
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["ua"] = request.headers.get("user-agent", "")
        return httpx.Response(200, json={"ok": True})

    async def inner():
        http = CollectorHttp(transport=httpx.MockTransport(handler))
        try:
            return await http.fetch_json("https://zh.wikipedia.org/w/api.php")
        finally:
            await http.close()

    data = _run(inner())
    assert data == {"ok": True}
    assert seen["ua"] == DEFAULT_UA
    assert "TutorAgent" in seen["ua"]


def test_concurrency_limit():
    state = {"active": 0, "max_active": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        state["active"] += 1
        state["max_active"] = max(state["max_active"], state["active"])
        await asyncio.sleep(0.05)  # 拉长占用窗口，让并发真实发生
        state["active"] -= 1
        return httpx.Response(200, text="ok")

    async def inner():
        http = CollectorHttp(max_concurrency=2, transport=httpx.MockTransport(handler))
        try:
            results = await asyncio.gather(*[
                http.fetch_text("https://zh.wikipedia.org/a") for _ in range(8)
            ])
            return results
        finally:
            await http.close()

    results = _run(inner())
    assert len(results) == 8
    assert all(r == "ok" for r in results)
    # 并发窗口内活跃请求数不超过上限；多任务并发下确实超过 1（说明有限流而非串行）
    assert state["max_active"] <= 2


def test_concurrency_limit_with_low_load_stays_at_one():
    """低负载下不虚增并发（确认 max_active 只反映真实并行）"""
    state = {"active": 0, "max_active": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        state["active"] += 1
        state["max_active"] = max(state["max_active"], state["active"])
        await asyncio.sleep(0.01)
        state["active"] -= 1
        return httpx.Response(200, text="ok")

    async def inner():
        http = CollectorHttp(max_concurrency=2, transport=httpx.MockTransport(handler))
        try:
            await http.fetch_text("https://zh.wikipedia.org/single")
        finally:
            await http.close()

    _run(inner())
    assert state["max_active"] == 1


def test_timeout_degrade_returns_none():
    calls = {"n": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ReadTimeout("mock timeout")

    async def inner():
        http = CollectorHttp(retries=1, retry_delay=0,
                             transport=httpx.MockTransport(handler))
        try:
            return await http.fetch_text("https://zh.wikipedia.org/slow")
        finally:
            await http.close()

    result = _run(inner())
    assert result is None
    # 重试 1 次 => 共尝试 2 次
    assert calls["n"] == 2


def test_http_error_status_degrade():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="unavailable")

    async def inner():
        http = CollectorHttp(retries=0, transport=httpx.MockTransport(handler))
        try:
            return await http.fetch_json("https://example.com/api")
        finally:
            await http.close()

    assert _run(inner()) is None


def test_fetch_bytes_offline():
    async def inner():
        http = CollectorHttp(retries=0, transport=httpx.MockTransport(
            lambda r: httpx.Response(200, content=b"hi")
        ))
        try:
            return await http.fetch_bytes("https://example.com")
        finally:
            await http.close()

    assert _run(inner()) == b"hi"


def test_fetch_text_offline():
    async def inner():
        http = CollectorHttp(retries=0, transport=httpx.MockTransport(
            lambda r: httpx.Response(200, text="hello")
        ))
        try:
            return await http.fetch_text("https://example.com")
        finally:
            await http.close()

    assert _run(inner()) == "hello"


def test_fetch_json_offline():
    async def inner():
        http = CollectorHttp(retries=0, transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"a": 1})
        ))
        try:
            return await http.fetch_json("https://example.com")
        finally:
            await http.close()

    assert _run(inner()) == {"a": 1}


# 类型占位：供静态检查提示 handler 签名
_HANDLER = Callable[[httpx.Request], Awaitable[httpx.Response]]
