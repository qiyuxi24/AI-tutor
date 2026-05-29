"""
知识图谱事件总线

职责：只处理知识图谱数据变更的发布-订阅。
- 后端 API/function calling 写入数据后 → publish("graph_updated")
- 前端 SSE 端点 → subscribe() → 收到事件后自动刷新图谱

不依赖任何外部中间件，同一进程内异步队列通信。
解耦原则：所有调用者只需 import publish，不依赖其他模块。
"""

import asyncio
import json
from typing import AsyncGenerator, Optional

# ── 全局状态 ──

# 事件队列：所有 SSE 订阅者共享同一个队列实例
_event_queue: "Optional[asyncio.Queue]" = None

# 在模块加载时保存事件循环引用
# 这样即使在同步上下文中调用 publish()，也能安全地 enqueue
_loop: asyncio.AbstractEventLoop | None = None


def _get_queue() -> asyncio.Queue[dict]:
    """获取或创建全局事件队列（惰性初始化）"""
    global _event_queue
    if _event_queue is None:
        _event_queue = asyncio.Queue()
    return _event_queue


def _ensure_loop() -> asyncio.AbstractEventLoop:
    """
    获取当前可用的循环引用。
    优先用已保存的 loop，找不到则尝试获取运行中的循环。
    保证在任何上下文（async def / 同步函数 / 线程池）中都能拿到循环。
    """
    global _loop
    if _loop is None or _loop.is_closed():
        try:
            _loop = asyncio.get_running_loop()
        except RuntimeError:
            _loop = asyncio.new_event_loop()
            asyncio.set_event_loop(_loop)
    return _loop


# ── 公开 API ──

def publish(event_type: str, data: dict | None = None) -> None:
    """
    发布事件到所有 SSE 订阅者。

    参数:
        event_type: 事件类型，当前支持:
                    - "graph_updated" — 知识图谱数据（nodes/edges）有更改
                    - "error"         — 后端模块发生错误，data 包含:
                        { "code": "E-LLM-001", "message": "...", "module": "llm", "detail": "..." }
        data:       可选附加数据，如 {"node_id": "xxx"},
                    会自动合并到事件对象中。

    用法:
        from app.core.event_bus import publish
        publish("graph_updated")
        publish("graph_updated", {"node_id": "recursion_def"})
        publish("error", {"code": "E-LLM-001", "message": "超时", "module": "llm"})
    """
    event: dict = {"type": event_type}
    if data:
        event.update(data)

    loop = _ensure_loop()
    loop.call_soon_threadsafe(_get_queue().put_nowait, event)


async def subscribe() -> AsyncGenerator[str, None]:
    """
    SSE 订阅生成器。前端连接到 SSE 端点时持续接收事件流。

    用法（在 FastAPI 路由中）:
        from fastapi.responses import StreamingResponse
        return StreamingResponse(subscribe(), media_type="text/event-stream")
    """
    queue = _get_queue()
    while True:
        event_data = await queue.get()
        yield f"data: {json.dumps(event_data, ensure_ascii=False)}\n\n"
