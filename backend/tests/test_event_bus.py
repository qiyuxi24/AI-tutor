"""
事件总线（Event Hub）单元测试。

覆盖：
- 全局广播：broadcast() → 全局 subscribe 收到
- per-user 路由：notify(user_id, …) → 只有该用户 subscribe 收到，其他用户收不到
- 广播覆盖面：broadcast() 同时到全局队列与**每个** per-user 队列
- 惰性创建：用户队列在 subscribe 时才创建
- 事件类型常量：常量值与事件 type 一致

⚠️ A2-1b：`publish(type, data, user_id=None)` 已拆成 `broadcast()` / `notify()`。原先
同一个**可选参数**兼职两种路由（不带=广播给所有人、带上=只给这一个人），调用点少写
一个关键字就从"发给一个人"静默变成"发给所有人"，而代码读起来一模一样。
"""
import asyncio
import json

from app.core import event_bus
from app.core.agent import events as agent_events
from app.core.agent.events import AgentEventEmitter, NullSink
from app.core.event_bus import (
    broadcast, notify, subscribe, get_user_queue,
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


def test_broadcast_reaches_global_queue():
    """broadcast() → 全局 subscribe() 收到（旧端点仍可用）。"""
    _reset_state()
    events = []

    async def _run():
        gen = subscribe()
        collect_task = asyncio.ensure_future(_collect_all(gen, events, max_events=1))
        await asyncio.sleep(0.05)
        broadcast(GRAPH_UPDATED, {"node_id": "test_node"})
        await asyncio.wait_for(collect_task, timeout=1.0)

    asyncio.run(_run())
    assert len(events) == 1
    assert events[0]["type"] == GRAPH_UPDATED
    assert events[0]["node_id"] == "test_node"
    _reset_state()


def test_per_user_routing():
    """notify(user_id) → 只有该用户的 subscribe(user_id) 收到。"""
    _reset_state()
    user1_events = []
    user2_events = []

    async def _run():
        gen1 = subscribe(user_id=1)
        gen2 = subscribe(user_id=2)
        t1 = asyncio.ensure_future(_collect_all(gen1, user1_events, max_events=1))
        t2 = asyncio.ensure_future(_collect_all(gen2, user2_events, max_events=1))
        await asyncio.sleep(0.05)
        notify(1, TOOL_START, {"tool": "rag_search"})
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


def test_broadcast_reaches_all_users():
    """broadcast() → 全局广播到所有用户队列。"""
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
        broadcast(GRAPH_UPDATED)
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
    """用户队列在 subscribe 时才创建（惰性），订阅断开即摘掉。

    ⚠️ 断言必须发生在**订阅存活期间**：多端 fan-out 之后，订阅者断开时
    `_drop_user_queue` 会摘掉自己的队列（该用户再无队列则条目消失），
    所以旧的"断开后条目仍在"不再成立。
    """
    _reset_state()
    assert 99 not in event_bus._user_queues

    async def _run():
        gen = subscribe(user_id=99)
        task = asyncio.ensure_future(_collect_all(gen, [], max_events=1))
        await asyncio.sleep(0.1)
        created = 99 in event_bus._user_queues   # 订阅期间 = 已创建
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return created

    assert asyncio.run(_run()) is True
    assert 99 not in event_bus._user_queues      # 断开即摘掉（不留僵尸队列）
    _reset_state()


def test_notify_unsubscribed_user_does_not_raise():
    """notify 给未订阅的用户 → 不报错，留库待补拉（A2-3 起不再"静默丢弃"）。"""
    _reset_state()
    notify(888, TOOL_RESULT, {"tool": "rag_search", "ok": True})
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


def test_get_user_queue_creates_independent_queue_per_subscriber():
    """多端 fan-out：每个订阅者拿到**独立**队列，不再是共享的那一条。

    旧实现（2026-10-07 前）每个 user_id 只有一条队列 —— 同一账户两端都
    `await` 同一个 `q.get()`，事件被先醒的一端抢走，另一端永远收不到。
    """
    _reset_state()
    q1 = get_user_queue(42)
    q2 = get_user_queue(42)
    assert q1 is not q2
    assert event_bus._user_queues[42] == {q1, q2}
    _reset_state()


def test_two_subscribers_of_same_user_both_receive():
    """同一账户两端订阅 → 同一条通知**两端都收到**（fan-out）。

    回归：`/knowledge/events` 在两端各建一条长连接时，graph_updated / quiz_ready
    只能被一端收到（另一端界面永远停在旧数据上）。
    """
    _reset_state()
    a_events, b_events = [], []

    async def _run():
        ta = asyncio.ensure_future(
            _collect_all(subscribe(user_id=1), a_events, max_events=1))
        tb = asyncio.ensure_future(
            _collect_all(subscribe(user_id=1), b_events, max_events=1))
        await asyncio.sleep(0.05)  # 两端都挂起在各自的 q.get() 上
        notify(1, GRAPH_UPDATED, {"node_id": "n1"})
        await asyncio.gather(ta, tb)

    asyncio.run(_run())
    assert len(a_events) == 1, f"A 端没收到: {a_events}"
    assert len(b_events) == 1, f"B 端没收到: {b_events}"
    assert a_events[0]["node_id"] == "n1"
    assert b_events[0]["node_id"] == "n1"
    _reset_state()


def test_get_user_queue_pre_creation_eliminates_race():
    """先 get_user_queue 再 publish → 事件不丢失（消除旧竞态）。"""
    _reset_state()
    collected = []

    async def _run():
        q = get_user_queue(1)  # 预创建
        get_task = asyncio.ensure_future(q.get())
        await asyncio.sleep(0.01)  # 让 get_task 挂起在 q.get() 上
        notify(1, TEXT_DELTA, {"text": "hello", "run_id": "r1"})
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


def test_notify_no_loop_does_not_raise():
    """无 loop 时 notify 不崩溃（无处可推 → 留库待补拉）。"""
    _reset_state()
    # 不在 async 上下文，不设置 _loop → notify 应跳过推送
    notify(1, TEXT_DELTA, {"text": "ghost"})  # 不抛异常
    _reset_state()


def test_consumer_drain_after_task_completion():
    """消费者在 agent_task 完成后排空剩余事件，不永久阻塞（复刻 _consume_agent_events 核心逻辑）。"""
    _reset_state()
    collected = []

    async def _run():
        q = get_user_queue(1)

        async def fake_agent():
            await asyncio.sleep(0.01)  # 让消费者先挂起在 q.get()
            notify(1, TEXT_DELTA, {"text": "hello", "run_id": "r1"})
            notify(1, AGENT_DONE, {"rounds": 1, "total_llm_calls": 1, "run_id": "r1"})

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
            notify(1, TEXT_DELTA, {"text": "partial", "run_id": "r1"})
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
    队列，常驻长连接（subscribe(user_id=...)）也收不到。

    ⚠️ 2026-10-08（A2-1a）后本测试的**机制变了、价值仍在**：A2-1a 起 run 事件
    根本不 import `publish`（回落路径物理不存在，另由
    `tests/test_event_contract.py::test_run_events_have_no_event_bus_path` 结构性守死），
    所以"断言#2"已由构造保证。保留它是**第二张网**（防有人重新引入一条经 event_bus 的路径）。

    断言#2 为何 spy 投递口而非查"队列为空"：per-user 队列是**事后**观测 ——
    破坏态下事件虽进了该队列，却会被常驻长连接（正挂起在 q.get() 上）抢先消费，
    队列随即变空 → "为空"断言在破坏态**误绿**。改为 spy，直接断言
    "请求内事件**从未**发起过投递"（动作语义，与事件事后是否被消费无关）。

    spy 目标 = **`event_bus` 的两个投递口**（源头）而非 events 模块里的引用点：
    引用点在 A2-1a 后已不存在（AttributeError）；且 spy 源头能盖住**任何**进
    event_bus 的路径，引用点只能盖住一处（模块加载时 `from ... import notify`
    绑定函数对象的写法就盖不住）。A2-1b 后投递口有两个（broadcast / notify），
    两个都 spy 才算完整。
    """
    _reset_state()
    ks_events = []
    publish_calls: list = []

    # spy 包住真正的 broadcast/notify：既记录调用，又保留破坏态"事件真进了 per-user 队列"的行为
    real_broadcast, real_notify = event_bus.broadcast, event_bus.notify

    def spy_broadcast(*args, **kwargs):
        publish_calls.append(("broadcast", args, kwargs))
        return real_broadcast(*args, **kwargs)

    def spy_notify(*args, **kwargs):
        publish_calls.append(("notify", args, kwargs))
        return real_notify(*args, **kwargs)

    monkeypatch.setattr(event_bus, "broadcast", spy_broadcast)
    monkeypatch.setattr(event_bus, "notify", spy_notify)

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
    # 2) 请求内事件从未走 event_bus 的任何投递口（动作语义，非"队列事后为空"）
    assert publish_calls == [], f"请求内事件串进了 event_bus: {publish_calls}"
    # 3) 常驻长连接收不到
    assert ks_events == [], f"常驻长连接抢到了请求内事件: {ks_events}"
    _reset_state()


def test_non_stream_chat_declares_explicit_null_sink(monkeypatch):
    """非流式 POST /chat 必须**显式声明**「本路径不推事件」（A2-2）。

    它和 /chat/stream 的差别只在"没人消费事件"，不在"该不该推"。A2 契约下
    `user_id` 有值就必须给出投递目标，漏传在 `AgentEventEmitter` 构造处直接抛
    ValueError；"不推"的显式写法是 `NullSink()`（可计数 → 落 agent_runs.events_written）。
    若 `chat_service.process_message` 把参数删掉、或改回"塞一个没人消费的 Queue"，
    本测试变红。
    """
    from app.services import chat_service

    captured = {}

    async def fake_loop(prompt, messages, *, kg=None, user_id=None, event_queue=None,
                        event_sink=None, db_dir=None, no_tools=False):
        # ⚠️ `no_tools` 必须留在签名里：学习模式开场轮用它禁工具（2026-09-30 加），
        # 少了这个形参 → `process_message` 传参直接 TypeError，整条断言变成 E-CHAT-002。
        captured["queue"] = event_queue
        captured["sink"] = event_sink
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
    assert isinstance(captured.get("sink"), NullSink), \
        "非流式路径必须显式传 NullSink —— 既不推事件，也不回落 event_bus"
    assert captured.get("queue") is None


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
