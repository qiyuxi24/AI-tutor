"""按对象串行：同一个 key 的异步区块排队执行，不同 key 互不阻塞。

背景（2026-10-07 多端登录）：同一账户可在多端登录，可能**同时**对同一个对话
发起请求（两端各跑一个 agent 回合）。两端各改同一份历史 → 后写覆盖、内容错乱。
按 `conv_id` 串行后，第二端等第一端的回合跑完再开始；不同对话、无 conv_id 的
请求完全不受影响。

为什么是应用层锁而不是数据库事务：一个对话回合是"LLM 调用 + 多轮工具"的长流程
（单回合墙钟上限 180s），数据库事务不可能覆盖它。

为什么不用账户级全局锁：那会让"两端各自在不同对话里聊天"也互相等待，
第二端最长干等 3 分钟（用户明确否掉了这个方案）。

单 uvicorn worker（项目硬约束 `--workers 1`）下进程内锁即可；多 worker 需要
跨进程锁，届时另说。
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

# key → 锁；`_waiters` 统计"持有者 + 等待者"，归零即清（避免 dict 随对话数增长）
_locks: dict[str, asyncio.Lock] = {}
_waiters: dict[str, int] = {}


@asynccontextmanager
async def keyed_lock(key: str) -> AsyncIterator[None]:
    """以 `key` 为粒度串行：同一 key 的区块排队，不同 key 并行。

    空 key（老前端未带 conv_id）→ 不加锁，直接执行（保持向后兼容）。
    区块内抛异常 / 被取消（客户端断开）都会释放锁（`async with` 保证）。
    """
    if not key:
        yield
        return

    lock = _locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _locks[key] = lock
    _waiters[key] = _waiters.get(key, 0) + 1
    try:
        async with lock:
            yield
    finally:
        # 同步段（其间无 await）→ 不与其它协程交错，计数归零即可安全清理
        _waiters[key] -= 1
        if _waiters[key] <= 0:
            _waiters.pop(key, None)
            if _locks.get(key) is lock:
                _locks.pop(key, None)
