"""
事件总线（Event Hub）单元测试。

覆盖：
- 全局广播：publish 不带 user_id → 全局 subscribe 收到
- per-user 路由：publish 带 user_id → 只有该用户 subscribe 收到，其他用户收不到
- 向后兼容：旧 publish(type, data) + subscribe() 仍正常工作
- 惰性创建：用户队列在 subscribe 时才创建
- 事件类型常量：常量值与事件 type 一致
"""
import asyncio
import json

from app.core import event_bus
from app.core.event_bus import (
    publish, subscribe,
    GRAPH_UPDATED, ERROR, TOOL_START, TOOL_RESULT,
    THINKING, TEXT_DELTA, AGENT_START, AGENT_DONE,
)


def _drain_events(gen, count, timeout=1.0):
    """从 subscribe 生成器取 count 条事件，超时报错。"""
    events = []

    async def _collect():
        async for sse in gen:
            payload = json.loads(sse.replace("data: ", "").strip())
            events.append(payload)
            if len(events) >= count:
                break

    asyncio.run(asyncio.wait_for(_collect(), timeout=timeout))
    return events


async def _collect_all(gen, events_list, max_events=1):
    """从生成器收集最多 max_events 条事件。"""
    async for sse in gen:
        payload = json.loads(sse.replace("data: ", "").strip())
        events_list.append(payload)
        if len(events_list) >= max_events:
            break


def test_global_broadcast_backward_compat():
    """publish 不带 user_id → 全局 subscribe() 收到（兼容旧端点）。"""
    _reset_state()
    events = []

    async def _run():
        gen = subscribe()
        collect_task = asyncio.ensure_future(_collect_all(gen, events, max_events=1))
        await asyncio.sleep(0.05)
        publish(GRAPH_UPDATED, {"node_id": "test_node"})
        await asyncio.wait_for(collect_task, timeout=1.0)

    asyncio.run(_run())
    assert len(events) == 1
    assert events[0]["type"] == GRAPH_UPDATED
    assert events[0]["node_id"] == "test_node"
    _reset_state()


def test_per_user_routing():
    """publish 带 user_id → 只有该用户的 subscribe(user_id) 收到。"""
    _reset_state()
    user1_events = []
    user2_events = []

    async def _run():
        gen1 = subscribe(user_id=1)
        gen2 = subscribe(user_id=2)
        t1 = asyncio.ensure_future(_collect_all(gen1, user1_events, max_events=1))
        t2 = asyncio.ensure_future(_collect_all(gen2, user2_events, max_events=1))
        await asyncio.sleep(0.05)
        publish(TOOL_START, {"tool": "rag_search"}, user_id=1)
        await asyncio.wait_for(t1, timeout=1.0)
        t2.cancel()
        try:
            await t2
        except asyncio.CancelledError:
            pass

    asyncio.run(_run())
    assert len(user1_events) == 1
    assert user1_events[0]["type"] == TOOL_START
    assert user1_events[0]["tool"] == "rag_search"
    assert len(user2_events) == 0
    _reset_state()


def test_global_broadcast_reaches_all_users():
    """publish 不带 user_id → 全局广播到所有用户队列。"""
    # 重置全局状态，避免跨 test 函数的 event loop 绑定冲突
    event_bus._global_queue = None
    event_bus._user_queues.clear()
    event_bus._user_queue_last_access.clear()
    event_bus._loop = None

    user1_events = []
    user2_events = []

    async def _run():
        gen1 = subscribe(user_id=1)
        gen2 = subscribe(user_id=2)
        t1 = asyncio.ensure_future(_collect_all(gen1, user1_events, max_events=1))
        t2 = asyncio.ensure_future(_collect_all(gen2, user2_events, max_events=1))
        await asyncio.sleep(0.05)
        publish(GRAPH_UPDATED)
        await asyncio.wait_for(asyncio.gather(t1, t2), timeout=1.0)

    asyncio.run(_run())
    assert len(user1_events) == 1
    assert len(user2_events) == 1

    # 清理
    event_bus._global_queue = None
    event_bus._user_queues.clear()
    event_bus._user_queue_last_access.clear()
    event_bus._loop = None


def test_user_queue_lazy_creation():
    """用户队列在 subscribe 时才创建。"""
    _reset_state()

    assert 99 not in event_bus._user_queues
    asyncio.run(_start_and_cancel_subscribe(99, sleep=0.1))
    assert 99 in event_bus._user_queues

    _reset_state()


def test_unpublished_user_event_silently_dropped():
    """publish 给未订阅的用户 → 静默丢弃，不报错。"""
    _reset_state()
    publish(TOOL_RESULT, {"tool": "rag_search", "ok": True}, user_id=888)
    _reset_state()


def test_event_type_constants():
    """事件类型常量值正确。"""
    assert GRAPH_UPDATED == "graph_updated"
    assert ERROR == "error"
    assert TOOL_START == "tool_start"
    assert TOOL_RESULT == "tool_result"
    assert THINKING == "thinking"
    assert TEXT_DELTA == "text_delta"
    assert AGENT_START == "agent_start"
    assert AGENT_DONE == "agent_done"


# ── helpers ──

def _reset_state():
    """重置 event_bus 全局状态（避免跨 test 的 event loop 绑定冲突）。"""
    event_bus._global_queue = None
    event_bus._user_queues.clear()
    event_bus._user_queue_last_access.clear()
    event_bus._loop = None

async def _collect_all(gen, events_list, max_events=1):
    """从生成器收集最多 max_events 条事件。"""
    async for sse in gen:
        payload = json.loads(sse.replace("data: ", "").strip())
        events_list.append(payload)
        if len(events_list) >= max_events:
            break


async def _start_and_cancel_subscribe(user_id, sleep=0.1):
    """启动 subscribe 然后取消（触发队列惰性创建）。"""
    gen = subscribe(user_id=user_id)
    task = asyncio.ensure_future(_collect_all(gen, [], max_events=1))
    await asyncio.sleep(sleep)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
