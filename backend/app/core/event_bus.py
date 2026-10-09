"""
事件总线（Event Hub）

职责：进程内异步事件的发布-订阅，支持 per-user 路由。

事件类型注册表：
  - text_delta    — AI 文本增量（兼容旧 "token"）
  - thinking      — AI 思考过程（MiniMax reasoning_details）
  - tool_start    — 工具开始执行 {tool, args_head}
  - tool_result   — 工具执行完毕 {tool, ok, summary, duration_ms}
  - agent_start   — Agent 循环开始 {max_rounds}
  - agent_done    — Agent 循环结束 {rounds, total_llm_calls}
  - graph_updated — 知识图谱数据变更（向后兼容）
  - quiz_ready    — 对话内出题完成 {ok, questions, node_id, subject}（后台异步）
  - error         — 后端错误 {code, message, module, detail}

路由模式（A2-1b 起：隐式语义拆成两个**显式**函数）：
  - broadcast(type, data=None)   → 显式全局：全局队列 + 每个已存在的订阅队列各一份
  - notify(user_id, type, data)  → 定向：只投该用户的每一条订阅队列（多端 fan-out）
  - subscribe(user_id=None)      → user_id 存在时只收自己的事件 + 全局事件
  - subscribe()                  → 全局队列（兼容旧 /knowledge/events 端点）

⚠️ 为什么取消 `publish(type, data, user_id=None)`（A2-1b）：同一个**可选参数**兼职了
  两种完全不同的路由 —— 不带 = 广播给所有人，带上 = 只给这一个人。调用点少写一个
  关键字，语义就从"发给一个人"静默变成"发给所有人"，且代码读起来完全一样。
  拆成 `broadcast` / `notify` 后路由意图写在函数名上，也不可能"忘传"。

不依赖任何外部中间件，同一进程内 asyncio.Queue 通信。
"""

import asyncio
import json
import logging
import time
from typing import AsyncGenerator, Optional

from app.core import notices

logger = logging.getLogger("ai-tutor")

# ── 事件类型常量 ──

TEXT_DELTA = "text_delta"
THINKING = "thinking"
TOOL_START = "tool_start"
TOOL_RESULT = "tool_result"
AGENT_START = "agent_start"
AGENT_DONE = "agent_done"
GRAPH_UPDATED = "graph_updated"
QUIZ_READY = "quiz_ready"   # 对话内出题完成（后台异步，约 40s 后到达，见 quiz/chat_quiz.py）
ERROR = "error"

# 向后兼容：旧 "token" 事件 → text_delta
TOKEN = "token"

# ── 全局状态 ──

# 全局事件队列（兼容旧 subscribe() 无参数调用 + 广播事件）
_global_queue: "Optional[asyncio.Queue]" = None

# per-user 队列集合 {user_id → {Queue, ...}}（惰性创建）
# **每端一条**：同一账户可在多端登录，各端建立各自的 SSE 长连接；通知必须
# 各投一份（fan-out），否则事件被先醒的一端抢走、另一端永远收不到。
_user_queues: dict[int, set["asyncio.Queue"]] = {}

# 事件循环引用（保证同步上下文也能安全 enqueue）
_loop: asyncio.AbstractEventLoop | None = None

# 用户队列 TTL（秒）：空闲超时自动清理，避免内存泄漏
_USER_QUEUE_TTL = 300  # 5 分钟
_user_queue_last_access: dict[int, float] = {}

# 每个用户当前的活跃订阅者数量（长连接 /knowledge/events）。
# 有活跃订阅者的队列**不允许**被 TTL 清理 —— 见 _cleanup_stale_queues。
_active_subs: dict[int, int] = {}


def _get_global_queue() -> asyncio.Queue:
    """获取或创建全局事件队列（惰性初始化）。"""
    global _global_queue
    if _global_queue is None:
        _global_queue = asyncio.Queue()
    return _global_queue


def _new_user_queue(user_id: int) -> asyncio.Queue:
    """为一个**订阅者**创建独立队列并登记（fan-out：每端一份）。"""
    q: asyncio.Queue = asyncio.Queue()
    _user_queues.setdefault(user_id, set()).add(q)
    _user_queue_last_access[user_id] = time.monotonic()
    _cleanup_stale_queues()
    return q


def _drop_user_queue(user_id: int, q: "asyncio.Queue") -> None:
    """订阅者断开：只摘掉自己的队列；该用户已无队列时删除条目。"""
    qs = _user_queues.get(user_id)
    if qs is not None:
        qs.discard(q)
        if not qs:
            _user_queues.pop(user_id, None)
    _user_queue_last_access[user_id] = time.monotonic()


def _cleanup_stale_queues():
    """
    清理超时未访问的用户队列，避免内存泄漏。

    ⚠️ 跳过仍有活跃订阅者的用户。长连接 /knowledge/events 只在**建立时**登记一次，
    之后不再刷新访问时间；若不跳过，连接空闲超过 TTL 后整组队列会被清掉，
    而订阅者仍在 await 那个已被移除的队列对象 —— 从此再也收不到 per-user 事件
    （notify 查不到队列就无处可投：留库待补拉，见 core/notices.py）。
    """
    now = time.monotonic()
    stale = [uid for uid, t in _user_queue_last_access.items()
             if now - t > _USER_QUEUE_TTL and not _active_subs.get(uid)]
    for uid in stale:
        _user_queues.pop(uid, None)
        _user_queue_last_access.pop(uid, None)


def _ensure_loop() -> "asyncio.AbstractEventLoop | None":
    """
    获取当前可用的循环引用，用于跨线程安全投递事件。

    策略：
    1. 已保存的 loop 且未关闭 → 直接返回（线程池场景复用主线程 loop）
    2. 当前线程有运行中 loop → 保存并返回（首次从 async 上下文调用）
    3. 都不可用 → 返回 None（投递方跳过推送，不创建幽灵 loop）

    旧实现的致命缺陷：线程池上下文中 get_running_loop() 抛 RuntimeError →
    new_event_loop() 创建一个无人运行的幽灵 loop → call_soon_threadsafe
    调度到幽灵 loop 上，消费者永远收不到事件。改为返回 None 让投递方
    放弃推送，杜绝幽灵 loop。
    """
    global _loop
    if _loop is not None and not _loop.is_closed():
        return _loop
    _loop = None
    try:
        _loop = asyncio.get_running_loop()
    except RuntimeError:
        pass
    return _loop


def get_user_queue(user_id: int) -> asyncio.Queue:
    """为调用方创建一个该用户的事件队列（**每次调用都是新队列**，已登记进 fan-out 集合）。

    用途：调用方想预先挂一个消费者，或测试里直接观察投递。
    ⚠️ 旧语义"同用户返回同一实例"已随多端 fan-out 废弃 —— 共享一条队列会让
    两端互相抢事件（见 `_new_user_queue`）。
    """
    return _new_user_queue(user_id)


# ── 公开 API ──

def _build_event(event_type: str, data: dict | None) -> dict:
    event: dict = {"type": event_type}
    if data:
        event.update(data)
    return event


def broadcast(event_type: str, data: dict | None = None) -> None:
    """
    显式**全局广播**：全局队列 + 每个已存在的订阅队列**各投一份**（不是"投一份进池子
    让人抢"，那样只有一个订阅者能收到）。未订阅的用户没有队列 → 收不到（惰性创建）。

    ⚠️ 请求内事件禁止走本函数（见 `notify` 的说明 C 段）。

    用法：
        broadcast(GRAPH_UPDATED, {"node_id": "recursion"})
    """
    event = _build_event(event_type, data)
    loop = _ensure_loop()
    if loop is None:
        logger.debug(f"broadcast({event_type}): 无可用事件循环，事件已丢弃")
        return
    loop.call_soon_threadsafe(_get_global_queue().put_nowait, event)
    for qs in list(_user_queues.values()):
        for q in list(qs):
            loop.call_soon_threadsafe(q.put_nowait, event)


def notify(user_id: int, event_type: str, data: dict | None = None, *,
           db_dir=None) -> None:
    """
    **定向通知**：只投该用户的每一条订阅队列（fan-out：多端各一份）。

    服务对象 = "与具体请求无关的通知类事件"：
      - GRAPH_UPDATED（图谱变更）      - QUIZ_READY（对话内出题完成，约 40s 后异步到达）
      - ERROR（后端错误）

    A2-3：**先落库再推**（`core/notices.py`）。推送是尽力而为 —— 该用户一条订阅都没有
    就无处可投；落库才是"不丢"的保证，客户端重连后经
    `GET /knowledge/notices/pending` 补拉。投递成功的那条立刻标记已投递，
    使"实时推送"与"重连补拉"互斥（否则同一张题卡会渲染两遍）。

    ⚠️ 请求内事件禁止走本函数：AGENT_START / THINKING / TOOL_START / TOOL_RESULT /
      TEXT_DELTA / AGENT_DONE 属于"某次请求的流"，必须走 `AgentEventEmitter(queue=...)`
      的**本请求私有队列**（见 core/agent/events.py）。
      为什么：per-user 广播队列有**两个消费者** —— 对话流(chat_service) 与常驻长连接
      (/knowledge/events)。若请求内事件也丢进这条队列，常驻长连接会把 TEXT_DELTA 整段
      抢走并丢弃（它只认 graph_updated/quiz_ready）→ 对话侧拿到空流 → 前端 onDone('')
      → 用户侧"对话没有反应"（后端 agent_runs 里却是完整的）。回归测试见
      tests/test_event_bus.py::test_chat_stream_not_starved_by_long_lived_subscriber。

    参数:
        user_id:    收件人（必填 —— "发给谁"不再是可省略的默认值）
        event_type: 事件类型（见模块顶部常量）
        data:       可选附加数据
        db_dir:     覆盖落库目录（测试注入 tmp_path 用，与 `store.save_run` 同约定）

    用法：
        notify(1, QUIZ_READY, {"ok": True, "questions": []})
    """
    event = _build_event(event_type, data)
    notice_id = notices.record(user_id, event_type, data, db_dir=db_dir)

    qs = _user_queues.get(user_id)
    if not qs:
        # 没有订阅者 = 无处可投：留库待补拉（这正是本节存在的理由）
        logger.debug(f"notify({event_type}): user {user_id} 未订阅 → 留待补拉（id={notice_id}）")
        return
    loop = _ensure_loop()
    if loop is None:
        logger.debug(f"notify({event_type}): 无可用事件循环 → 留待补拉（id={notice_id}）")
        return

    for q in list(qs):
        loop.call_soon_threadsafe(q.put_nowait, event)
    notices.mark_delivered(notice_id, db_dir=db_dir)


async def subscribe(user_id: Optional[int] = None) -> AsyncGenerator[str, None]:
    """
    SSE 订阅生成器。

    参数:
        user_id: 传入时订阅该用户专属队列（只收自己的事件 + 全局事件）；
                 不传时订阅全局队列（兼容旧 /knowledge/events 端点）。

    用法:
        # 旧端点兼容
        return StreamingResponse(subscribe(), media_type="text/event-stream")

        # 新端点：按用户路由
        return StreamingResponse(subscribe(user_id=current_user.id),
                                 media_type="text/event-stream")
    """
    if user_id is not None:
        # 用户专属模式：**本订阅者一条独立队列**（多端各收一份，互不抢事件）
        q = _new_user_queue(user_id)
        _active_subs[user_id] = _active_subs.get(user_id, 0) + 1
        try:
            while True:
                event_data = await q.get()
                yield f"data: {json.dumps(event_data, ensure_ascii=False)}\n\n"
        finally:
            # 断开时摘掉自己的队列并释放计数（该用户仍有别的订阅者则保留条目）
            _drop_user_queue(user_id, q)
            left = _active_subs.get(user_id, 1) - 1
            if left > 0:
                _active_subs[user_id] = left
            else:
                _active_subs.pop(user_id, None)
    else:
        # 全局模式（兼容旧端点）
        q = _get_global_queue()
        while True:
            event_data = await q.get()
            yield f"data: {json.dumps(event_data, ensure_ascii=False)}\n\n"
