"""SSE 保活（heartbeat）回归。

债务背景（2026-11-04）：`subscribe()` 原来是裸 `await q.get()` —— 空闲连接一句话都不发。
nginx.conf 是 `proxy_read_timeout 300s`，于是**没有任何事件的常驻长连接会在第 300 秒
被反代静默掐断**，前端 EventSource 表现为"莫名掉线"。国产 ROM 后台把进程冻结更早。

修法：`subscribe(user_id, heartbeat_seconds=...)` 在 `q.get()` 上包一层
`asyncio.wait_for`，超时就 yield 一行 SSE **注释帧** `: keepalive`。

为什么必须是注释帧：
  - 不以 `data:` 开头 → EventSource 自动忽略、不触发 onmessage；
  - 不会被前端误当成业务事件；
  - 但足以满足反代的"读到字节就算活跃"。
"""
import asyncio
import json

import pytest

from app.core import event_bus
from app.core.event_bus import broadcast, subscribe, GRAPH_UPDATED


def _reset_state():
    event_bus._global_queue = None
    event_bus._user_queues.clear()
    event_bus._user_queue_last_access.clear()
    event_bus._active_subs.clear()


@pytest.fixture(autouse=True)
def _clean_bus():
    _reset_state()
    yield
    _reset_state()


def test_heartbeat_emitted_when_idle():
    """空闲超过 heartbeat 间隔 → 必须先吐一行注释帧，而不是一直挂着。"""
    async def _run():
        gen = subscribe(user_id=1, heartbeat_seconds=0.05)
        frame = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
        await gen.aclose()
        return frame

    frame = asyncio.run(_run())
    assert frame.startswith(":"), f"保活帧必须是 SSE 注释行，实际={frame!r}"
    assert "keepalive" in frame
    # 绝不能伪装成业务事件
    assert "data:" not in frame
    assert not frame.lstrip().startswith("data")


def test_heartbeat_frame_is_not_json_event():
    """注释帧不能被按 `data: ` 前缀解析（前端/测试统一按前缀判断）。"""
    async def _run():
        gen = subscribe(heartbeat_seconds=0.05)
        frame = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
        await gen.aclose()
        return frame

    frame = asyncio.run(_run())
    assert not frame.strip().startswith("data:")
    with pytest.raises(json.JSONDecodeError):
        json.loads(frame.strip().replace("data: ", ""))


def test_real_event_preempts_heartbeat():
    """事件先到就发事件，不会先发保活（保活只在"这 15 秒没东西可发"时出现）。"""
    async def _run():
        gen = subscribe(user_id=7, heartbeat_seconds=5.0)
        first = asyncio.ensure_future(gen.__anext__())
        await asyncio.sleep(0.05)          # 让生成器进入 await q.get()
        broadcast(GRAPH_UPDATED, {"node_id": "n1"})
        frame = await asyncio.wait_for(first, timeout=1.0)
        await gen.aclose()
        return frame

    frame = asyncio.run(_run())
    assert frame.startswith("data: ")
    payload = json.loads(frame.replace("data: ", "").strip())
    assert payload["type"] == GRAPH_UPDATED


def test_heartbeat_does_not_consume_queue():
    """保活之后真实事件仍能收到（wait_for 超时不会从队列里拿走任何东西）。"""
    async def _run():
        gen = subscribe(user_id=9, heartbeat_seconds=0.05)
        frames = []
        frames.append(await asyncio.wait_for(gen.__anext__(), timeout=1.0))  # 保活
        pending = asyncio.ensure_future(gen.__anext__())
        await asyncio.sleep(0.02)
        broadcast(GRAPH_UPDATED, {"node_id": "n2"})
        frames.append(await asyncio.wait_for(pending, timeout=2.0))
        await gen.aclose()
        return frames

    frames = asyncio.run(_run())
    assert frames[0].startswith(":")
    assert frames[1].startswith("data: ")
    assert json.loads(frames[1].replace("data: ", "").strip())["node_id"] == "n2"


def test_disconnect_still_cleans_up_active_subs():
    """保活循环里被取消（客户端断开）时，finally 仍要摘队列、减计数（防泄漏）。"""
    async def _run():
        gen = subscribe(user_id=42, heartbeat_seconds=0.05)
        await asyncio.wait_for(gen.__anext__(), timeout=1.0)
        assert event_bus._active_subs.get(42) == 1
        await gen.aclose()
        return dict(event_bus._active_subs), list(event_bus._user_queues.keys())

    active, queues = asyncio.run(_run())
    assert 42 not in active
    assert 42 not in queues


def test_default_heartbeat_interval_is_below_proxy_timeout():
    """默认间隔必须显著小于 nginx 的 proxy_read_timeout=300s。"""
    assert 0 < event_bus.SSE_HEARTBEAT_SECONDS < 300 / 4
