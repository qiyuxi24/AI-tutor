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
    publish, subscribe, get_user_queue,
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


# ── 新增：公开接口 + 线程安全 + 消费者排空模式 ──


def test_get_user_queue_returns_same_instance():
    """get_user_queue 返回的队列与 subscribe 内部使用的队列是同一实例。"""
    _reset_state()
    q1 = get_user_queue(42)
    q2 = get_user_queue(42)
    assert q1 is q2
    assert 42 in event_bus._user_queues
    _reset_state()


def test_get_user_queue_pre_creation_eliminates_race():
    """先 get_user_queue 再 publish → 事件不丢失（消除旧竞态）。"""
    _reset_state()
    collected = []

    async def _run():
        q = get_user_queue(1)  # 预创建
        get_task = asyncio.ensure_future(q.get())
        await asyncio.sleep(0.01)  # 让 get_task 挂起在 q.get() 上
        publish(TEXT_DELTA, {"text": "hello", "run_id": "r1"}, user_id=1)
        event = await asyncio.wait_for(get_task, timeout=1.0)
        collected.append(event)

    asyncio.run(_run())
    assert len(collected) == 1
    assert collected[0]["type"] == TEXT_DELTA
    assert collected[0]["text"] == "hello"
    _reset_state()


def test_ensure_loop_no_ghost_creation():
    """线程上下文无 running loop → _ensure_loop 返回 None，不创建幽灵 loop。"""
    import threading

    _reset_state()
    result_holder = {}

    def _from_thread():
        result_holder["loop"] = event_bus._ensure_loop()

    t = threading.Thread(target=_from_thread)
    t.start()
    t.join(timeout=2.0)
    assert result_holder["loop"] is None
    assert event_bus._loop is None  # 没有创建幽灵 loop
    _reset_state()


def test_publish_no_loop_silently_drops():
    """无 loop 时 publish 不崩溃，静默丢弃事件。"""
    _reset_state()
    # 不在 async 上下文，不设置 _loop → publish 应静默跳过
    publish(TEXT_DELTA, {"text": "ghost"}, user_id=1)  # 不抛异常
    _reset_state()


def test_consumer_drain_after_task_completion():
    """消费者在 agent_task 完成后排空剩余事件，不永久阻塞（复刻 _consume_agent_events 核心逻辑）。"""
    _reset_state()
    collected = []

    async def _run():
        q = get_user_queue(1)

        async def fake_agent():
            await asyncio.sleep(0.01)  # 让消费者先挂起在 q.get()
            publish(TEXT_DELTA, {"text": "hello", "run_id": "r1"}, user_id=1)
            publish(AGENT_DONE, {"rounds": 1, "total_llm_calls": 1, "run_id": "r1"}, user_id=1)

        agent_task = asyncio.ensure_future(fake_agent())

        while True:
            get_task = asyncio.ensure_future(q.get())
            done, _ = await asyncio.wait(
                {get_task, agent_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if get_task in done:
                collected.append(get_task.result())
            else:
                get_task.cancel()
                try:
                    await get_task
                except (asyncio.CancelledError, Exception):
                    pass
            if agent_task in done:
                await asyncio.sleep(0)
                while not q.empty():
                    collected.append(q.get_nowait())
                break

    asyncio.run(asyncio.wait_for(_run(), timeout=2.0))
    assert len(collected) == 2
    assert collected[0]["type"] == TEXT_DELTA
    assert collected[0]["text"] == "hello"
    assert collected[1]["type"] == AGENT_DONE
    _reset_state()


def test_consumer_handles_agent_exception():
    """agent_task 抛异常时消费者仍能正常退出，不阻塞。"""
    _reset_state()
    collected = []

    async def _run():
        q = get_user_queue(1)

        async def crashing_agent():
            await asyncio.sleep(0.01)
            publish(TEXT_DELTA, {"text": "partial", "run_id": "r1"}, user_id=1)
            raise RuntimeError("agent crashed")

        agent_task = asyncio.ensure_future(crashing_agent())

        while True:
            get_task = asyncio.ensure_future(q.get())
            done, _ = await asyncio.wait(
                {get_task, agent_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if get_task in done:
                collected.append(get_task.result())
            else:
                get_task.cancel()
                try:
                    await get_task
                except (asyncio.CancelledError, Exception):
                    pass
            if agent_task in done:
                await asyncio.sleep(0)
                while not q.empty():
                    collected.append(q.get_nowait())
                break

    asyncio.run(asyncio.wait_for(_run(), timeout=2.0))
    assert len(collected) == 1
    assert collected[0]["type"] == TEXT_DELTA
    assert collected[0]["text"] == "partial"
    _reset_state()


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
