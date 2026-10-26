"""运行中主事件循环引用 + 同步→异步桥（同步工具 handler 用）。

为什么存在（硬伤，2026-10-11）：同步工具 handler 由 `dispatch.execute_kg_tool_async`
放进 `asyncio.to_thread` 的工作线程，该线程**没有运行中的事件循环**。原先各工具
各自 `asyncio.run` 新建临时 loop 跑协程 —— 但下列资源都绑定**主 loop**：
- `llm/clients.py` 的 AsyncOpenAI 单例（跨 loop 复用 → "Event loop is closed"
  → 嵌入 `embed_texts` 静默返回 [] / 归属判定返回 {}）
- whoosh `IndexWriter`（非线程安全，`node_indexer` 的按用户 `asyncio.Lock` 也绑主 loop）
在临时 loop 里复用它们会**静默降级**（不报错、功能残缺）。本模块把协程丢回主 loop
执行（`run_coroutine_threadsafe`），一处修复以上问题。

无主 loop（脚本直跑、同步测试入口）→ 退回 `asyncio.run`（与旧行为一致）。
"""
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

logger = logging.getLogger("ai-tutor")

_main_loop: Optional[asyncio.AbstractEventLoop] = None

# 主 loop 不可用、但当前线程**已有**运行中 loop 时的兜底：丢线程池另起临时 loop
# （旧行为；极少走到 —— 同步 handler 都在 to_thread 里，无 running loop）。
_POOL = ThreadPoolExecutor(max_workers=2)

# 后台任务强引用：`asyncio.create_task` 的返回值只被事件循环**弱引用**持有，未保存
# 引用的任务可能在执行中途被 GC（CPython 官方警告）→ 用集合持强引用，完成后自动移除。
_bg_tasks: set = set()


def set_main_loop(loop) -> None:
    """由 app lifespan 在启动/关闭时设置/清除主事件循环引用。"""
    global _main_loop
    _main_loop = loop


def get_main_loop():
    """当前进程的主事件循环（未启动/已关闭时为 None）。"""
    return _main_loop


def run_sync(coro):
    """在同步上下文里跑协程：优先复用主 loop，否则退回临时 loop。

    ⚠️ 严禁在**主 loop 自身线程**里调用（`run_coroutine_threadsafe(...).result()`
    会阻塞主 loop 等待自己 → 死锁）；同步 handler 都在 `to_thread` 工作线程里，不涉此坑。
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        main = _main_loop
        if main is not None and main.is_running():
            return asyncio.run_coroutine_threadsafe(coro, main).result()
        return asyncio.run(coro)
    # 当前线程已有运行中的 loop（同步调用意外发生在 async 上下文）：不能阻塞等待主 loop
    # （可能就是它自己）→ 退回旧语义（线程池另起独立临时 loop，不依赖主 loop）。
    return _POOL.submit(asyncio.run, coro).result()


def spawn(coro) -> "asyncio.Task":
    """起一个后台任务并保留强引用（防 GC）。调用方无需保存返回值。"""
    task = asyncio.create_task(coro)
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)
    return task


def pending_bg_tasks() -> int:
    """当前未完成的后台任务数（测试/观测用）。"""
    return len(_bg_tasks)
