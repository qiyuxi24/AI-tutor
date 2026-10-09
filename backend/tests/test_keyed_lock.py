"""按对象串行（keyed_lock）—— 多端同时追问同一对话时的排队语义（2026-10-07）。

设计取舍（用户口径）：**按对象串行**而不是账户级排队 ——
同一对话的写操作排队（第二端等第一端回合结束），不同对话 / 无 conv_id 可并发。
所以本文件既锁"同 key 串行"，也锁"不同 key 不互相阻塞"（后者同样是不变量，
误改成全局锁会让所有对话互相等待）。
"""
import asyncio
import time

from app.core.keyed_lock import keyed_lock


def test_same_key_runs_serially():
    """同一 key 的两个区块严格串行：后一个在前一个退出后才进入。"""
    order = []

    async def worker(tag, delay):
        async with keyed_lock("conv-1"):
            order.append(f"{tag}-enter")
            await asyncio.sleep(delay)
            order.append(f"{tag}-exit")

    async def _run():
        await asyncio.gather(worker("a", 0.05), worker("b", 0.0))

    asyncio.run(_run())
    assert order == ["a-enter", "a-exit", "b-enter", "b-exit"]


def test_different_keys_run_concurrently():
    """不同 key 互不阻塞（不同对话可并行，不能退化成全局锁）。"""
    async def _run():
        started = time.monotonic()

        async def worker(key):
            async with keyed_lock(key):
                await asyncio.sleep(0.1)

        await asyncio.gather(worker("c1"), worker("c2"))
        return time.monotonic() - started

    elapsed = asyncio.run(_run())
    assert elapsed < 0.18, f"不同 key 被串行化了：{elapsed:.2f}s"


def test_empty_key_is_noop():
    """空 key（老前端不带 conv_id）→ 不加锁，直接执行（向后兼容）。"""
    async def _run():
        async with keyed_lock(""):
            return "ok"

    assert asyncio.run(_run()) == "ok"


def test_lock_released_when_body_raises():
    """区块内抛异常也必须释放锁，否则该对话此后永远卡死。"""
    async def _run():
        with_exc = keyed_lock("conv-2")
        try:
            async with with_exc:
                raise RuntimeError("boom")
        except RuntimeError:
            pass

        got = False
        async with keyed_lock("conv-2"):
            got = True
        return got

    assert asyncio.run(_run()) is True


def test_entries_are_cleaned_up_after_use():
    """用完即清：否则每个聊过的对话都留一条锁，长期运行会泄漏。"""
    import app.core.keyed_lock as mod

    async def _run():
        async with keyed_lock("conv-3"):
            assert "conv-3" in mod._locks

    asyncio.run(_run())
    assert "conv-3" not in mod._locks
    assert "conv-3" not in mod._waiters
