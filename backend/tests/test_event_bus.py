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
from app.core.agent import events as agent_events
from app.core.agent.events import AgentEventEmitter
from app.core.event_bus import (
    publish, subscribe, get_user_queue,
    GRAPH_UPDATED, ERROR, TOOL_START, TOOL_RESULT,
    THINKING, TEXT_DELTA, AGENT_START, AGENT_DONE,
)
from app.services.chat_service import _consume_agent_events


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


# ── 新增：对话流与常驻长连接的队列隔离（2026-09-26 故障回归）──


def test_chat_stream_not_starved_by_long_lived_subscriber():
    """/knowledge/events 长连接常驻时，/chat/stream 仍必须拿到 text_delta。

    故障现象（2026-09-26 实测）：两者共用 event_bus 的同一条 per-user 队列，
    常驻长连接把 text_delta 抢走（它只认 graph_updated/quiz_ready，正文被丢弃），
    对话消费方拿到空流 → 前端 fullReply 恒为空 → onDone('') 删掉 AI 气泡
    → 用户侧"对话没有反应"（后端 agent_runs 却有完整回复）。

    修法：对话事件走本请求私有队列（AgentEventEmitter(queue=...)），
    不再占用 per-user 广播队列；该队列留给常驻长连接（quiz_ready/graph_updated）。
    """
    _reset_state()
    chat_sse = []
    ks_events = []

    async def _run():
        # 常驻长连接：等价前端 chatStore.connectSSE()（进对话页即建立并常驻）
        ks_task = asyncio.ensure_future(
            _collect_all(subscribe(user_id=1), ks_events, max_events=99)
        )
        await asyncio.sleep(0.05)  # 让它先挂起在 q.get() 上，形成"抢事件"的竞争

        event_queue = asyncio.Queue()

        async def fake_agent():
            em = AgentEventEmitter("r1", user_id=1, queue=event_queue)
            em.emit(THINKING, text="想想")
            em.emit(TEXT_DELTA, text="正文")
            em.emit(AGENT_DONE, rounds=1, total_llm_calls=1)

        agent_task = asyncio.ensure_future(fake_agent())
        async for sse in _consume_agent_events(event_queue, agent_task):
            chat_sse.append(sse)

        ks_task.cancel()
        try:
            await ks_task
        except (asyncio.CancelledError, Exception):
            pass

    asyncio.run(asyncio.wait_for(_run(), timeout=2.0))

    payloads = [json.loads(s.replace("data: ", "").strip()) for s in chat_sse]
    # text_delta 在 SSE 上以旧兼容格式 {"token": ...} 承载（前端 parsed.token 消费）
    assert any(p.get("token") == "正文" for p in payloads), f"对话流没拿到正文: {payloads}"
    assert [p["type"] for p in payloads if "type" in p] == ["thinking", "agent_done"]
    assert ks_events == [], f"常驻长连接抢到了对话事件: {ks_events}"
    _reset_state()


# ── 新增：请求内事件的路由不变量守卫（2026-09-26 故障）──


def test_request_scoped_event_bypasses_user_broadcast_queue(monkeypatch):
    """AgentEventEmitter(queue=...) 的事件走私有队列，绝不进 per-user 广播队列。

    锁死的不变量：传了私有 queue → 事件直投该队列，既不入 event_bus 的 per-user
    队列，常驻长连接（subscribe(user_id=...)）也收不到。若 events.py 的
    `if self.queue is not None:` 分支被移除，emit 会回落到 publish(..., user_id=1)，
    本测试三条断言全变红。

    断言#2 为何 spy `publish` 而非查"队列为空"：per-user 队列是**事后**观测 ——
    破坏态下事件虽进了该队列，却会被常驻长连接（正挂起在 q.get() 上）抢先消费，
    队列随即变空 → "为空"断言在破坏态**误绿**。改为 spy `events.publish`，直接
    断言"请求内事件**从未**发起过 publish"（动作语义，与事件事后是否被消费无关）。
    """
    _reset_state()
    ks_events = []
    publish_calls: list = []

    # spy 包住真正的 publish：既记录调用，又保留破坏态"事件真进了 per-user 队列"的行为
    real_publish = event_bus.publish

    def spy_publish(*args, **kwargs):
        publish_calls.append((args, kwargs))
        return real_publish(*args, **kwargs)

    monkeypatch.setattr(agent_events, "publish", spy_publish)

    async def _run():
        # 常驻长连接（等价前端进对话页即建立的 /knowledge/events），先挂起在 q.get() 上
        ks_task = asyncio.ensure_future(
            _collect_all(subscribe(user_id=1), ks_events, max_events=1)
        )
        await asyncio.sleep(0.05)

        q = asyncio.Queue()
        em = AgentEventEmitter("r1", user_id=1, queue=q)
        em.emit(TEXT_DELTA, text="正文")

        # 给"错误路径"（publish → call_soon_threadsafe）一个调度窗口：
        # 若事件真进了 per-user 队列，常驻长连接会在此窗口内收到并结束
        await asyncio.sleep(0.1)
        ks_task.cancel()
        try:
            await ks_task
        except (asyncio.CancelledError, Exception):
            pass

        return q

    q = asyncio.run(asyncio.wait_for(_run(), timeout=2.0))

    # 1) 事件进了私有队列
    assert q.qsize() == 1, f"私有队列没拿到事件: {q.qsize()}"
    assert q.get_nowait() == {"type": TEXT_DELTA, "run_id": "r1", "text": "正文"}
    # 2) 请求内事件从未走 event_bus.publish（动作语义，非"队列事后为空"）
    assert publish_calls == [], f"请求内事件串进了 event_bus.publish: {publish_calls}"
    # 3) 常驻长连接收不到
    assert ks_events == [], f"常驻长连接抢到了请求内事件: {ks_events}"
    _reset_state()


def test_non_stream_chat_also_passes_private_queue(monkeypatch):
    """非流式 POST /chat 也必须给 agent 传私有队列（2026-09-26 收口）。

    它和 /chat/stream 的差别只在"没人消费事件"，不在"该不该推"：不传队列时
    emitter 会回落到 publish(user_id=...)，把 thinking/text_delta 灌进 per-user
    广播队列 → 被常驻长连接抢走并污染通知流。若 chat_service.process_message
    里的 event_queue 参数被删掉，本测试变红。
    """
    from app.services import chat_service

    captured = {}

    async def fake_loop(prompt, messages, *, kg=None, user_id=None, event_queue=None,
                        db_dir=None, no_tools=False):
        # ⚠️ `no_tools` 必须留在签名里：学习模式开场轮用它禁工具（2026-09-30 加），
        # 少了这个形参 → `process_message` 传参直接 TypeError，整条断言变成 E-CHAT-002。
        captured["queue"] = event_queue
        captured["user_id"] = user_id

        class _Result:
            text = "ok"

        return _Result()

    async def fake_prompt(*args, **kwargs):
        return "sys", "hello"

    async def fake_analyze(*args, **kwargs):
        await asyncio.sleep(0)

    class FakeKG:
        def __init__(self, *args, **kwargs):
            pass

        def close(self):
            pass

    monkeypatch.setattr(chat_service, "run_agent_loop", fake_loop)
    monkeypatch.setattr(chat_service, "_build_system_prompt", fake_prompt)
    monkeypatch.setattr(chat_service, "trim_history_to_budget", lambda prompt, msgs: (msgs, None))
    monkeypatch.setattr(chat_service, "KnowledgeGraph", FakeKG)
    monkeypatch.setattr(chat_service, "_analyze_and_apply", fake_analyze)

    reply, _ = asyncio.run(chat_service.process_message(
        user_id=1, messages=[{"role": "user", "content": "hi"}]))

    assert reply == "ok"
    assert captured.get("queue") is not None, \
        "非流式路径没传私有队列 → 请求内事件会污染 per-user 广播队列"


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
