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
  - error         — 后端错误 {code, message, module, detail}

路由模式：
  - publish(type, data, user_id=None) → user_id 存在时只投递到该用户队列
  - publish(type, data)               → 全局广播（兼容旧调用）
  - subscribe(user_id=None)           → user_id 存在时只收自己的事件 + 全局事件
  - subscribe()                       → 全局队列（兼容旧 /knowledge/events 端点）

不依赖任何外部中间件，同一进程内 asyncio.Queue 通信。
"""

import asyncio
import json
import logging
import time
from typing import AsyncGenerator, Optional

logger = logging.getLogger("ai-tutor")

# ── 事件类型常量 ──

TEXT_DELTA = "text_delta"
THINKING = "thinking"
TOOL_START = "tool_start"
TOOL_RESULT = "tool_result"
AGENT_START = "agent_start"
AGENT_DONE = "agent_done"
GRAPH_UPDATED = "graph_updated"
ERROR = "error"

# 向后兼容：旧 "token" 事件 → text_delta
TOKEN = "token"

# ── 全局状态 ──

# 全局事件队列（兼容旧 subscribe() 无参数调用 + 广播事件）
_global_queue: "Optional[asyncio.Queue]" = None

# per-user 队列 {user_id → Queue}（惰性创建）
_user_queues: dict[int, "asyncio.Queue"] = {}

# 事件循环引用（保证同步上下文也能安全 enqueue）
_loop: asyncio.AbstractEventLoop | None = None

# 用户队列 TTL（秒）：空闲超时自动清理，避免内存泄漏
_USER_QUEUE_TTL = 300  # 5 分钟
_user_queue_last_access: dict[int, float] = {}


def _get_global_queue() -> asyncio.Queue:
    """获取或创建全局事件队列（惰性初始化）。"""
    global _global_queue
    if _global_queue is None:
        _global_queue = asyncio.Queue()
    return _global_queue


def _get_user_queue(user_id: int) -> asyncio.Queue:
    """获取或创建指定用户的专属事件队列（惰性创建）。"""
    q = _user_queues.get(user_id)
    if q is None:
        q = asyncio.Queue()
        _user_queues[user_id] = q
    _user_queue_last_access[user_id] = time.monotonic()
    _cleanup_stale_queues()
    return q


def _cleanup_stale_queues():
    """清理超时未访问的用户队列，避免内存泄漏。"""
    now = time.monotonic()
    stale = [uid for uid, t in _user_queue_last_access.items()
             if now - t > _USER_QUEUE_TTL]
    for uid in stale:
        _user_queues.pop(uid, None)
        _user_queue_last_access.pop(uid, None)


def _ensure_loop() -> "asyncio.AbstractEventLoop | None":
    """
    获取当前可用的循环引用，用于跨线程安全投递事件。

    策略：
    1. 已保存的 loop 且未关闭 → 直接返回（线程池场景复用主线程 loop）
    2. 当前线程有运行中 loop → 保存并返回（首次从 async 上下文调用）
    3. 都不可用 → 返回 None（publish 静默丢弃，不创建幽灵 loop）

    旧实现的致命缺陷：线程池上下文中 get_running_loop() 抛 RuntimeError →
    new_event_loop() 创建一个无人运行的幽灵 loop → call_soon_threadsafe
    调度到幽灵 loop 上，消费者永远收不到事件。改为返回 None 让 publish
    静默跳过，杜绝幽灵 loop。
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
    """获取或创建指定用户的事件队列（公开接口，供调用方预创建队列消除竞态）。"""
    return _get_user_queue(user_id)


# ── 公开 API ──

def publish(event_type: str, data: dict | None = None,
            user_id: Optional[int] = None) -> None:
    """
    发布事件到订阅者。

    参数:
        event_type: 事件类型（见模块顶部常量）
        data:       可选附加数据
        user_id:    用户 ID。传入时只投递到该用户队列；
                    不传时全局广播（兼容旧调用）。

    用法:
        from app.core.event_bus import publish, GRAPH_UPDATED
        publish(GRAPH_UPDATED)                          # 全局广播
        publish(GRAPH_UPDATED, {"node_id": "recursion"})# 全局广播 + 数据
        publish("tool_start", {"tool": "rag_search"}, user_id=1)  # 精准路由
    """
    event: dict = {"type": event_type}
    if data:
        event.update(data)

    loop = _ensure_loop()
    if loop is None:
        logger.debug(f"publish({event_type}): 无可用事件循环，事件已丢弃")
        return

    # 精准路由到用户队列
    if user_id is not None:
        q = _user_queues.get(user_id)
        if q is not None:
            loop.call_soon_threadsafe(q.put_nowait, event)
            return
        # 用户队列不存在 = 该用户未订阅，静默丢弃（避免内存浪费）
        logger.debug(f"publish({event_type}): user {user_id} 未订阅，事件已丢弃")
        return

    # 全局广播：同时投递到全局队列 + 所有用户队列
    loop.call_soon_threadsafe(_get_global_queue().put_nowait, event)
    for q in _user_queues.values():
        loop.call_soon_threadsafe(q.put_nowait, event)


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
        # 用户专属模式：订阅自己的队列
        q = _get_user_queue(user_id)
        while True:
            event_data = await q.get()
            yield f"data: {json.dumps(event_data, ensure_ascii=False)}\n\n"
    else:
        # 全局模式（兼容旧端点）
        q = _get_global_queue()
        while True:
            event_data = await q.get()
            yield f"data: {json.dumps(event_data, ensure_ascii=False)}\n\n"
