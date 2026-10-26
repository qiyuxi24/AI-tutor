"""`core/runtime.py`：同步→异步桥复用主事件循环 + 后台任务强引用。

背景（硬伤）：同步工具 handler 由 `dispatch` 放进 `asyncio.to_thread` 的工作线程，
该线程**没有运行中的事件循环**。原先各工具各自 `asyncio.run` 新建临时 loop —— 但
`llm/clients.py` 的 AsyncOpenAI 单例、whoosh writer、`node_indexer` 的按用户锁都绑定
主 loop，在临时 loop 里复用会**静默降级**（嵌入返回 [] / "Event loop is closed"）。
现在统一把协程丢回主 loop 执行，三处问题一并消失。

全离线：不碰 LLM/网络，纯协程调度。
"""
import asyncio
import threading

from app.core import runtime


def test_run_sync_from_worker_thread_runs_on_main_loop():
    """worker 线程调 run_sync → 协程在**主 loop 线程**执行（而非 worker 线程）。"""
    main_thread_id = threading.get_ident()
    seen = {}

    async def _scenario():
        runtime.set_main_loop(asyncio.get_running_loop())
        try:
            def _worker():
                async def _work():
                    return threading.get_ident()
                seen["thread"] = runtime.run_sync(_work())

            t = threading.Thread(target=_worker)
            t.start()
            while t.is_alive():
                await asyncio.sleep(0.01)
            t.join()
        finally:
            runtime.set_main_loop(None)

    asyncio.run(_scenario())

    assert seen["thread"] == main_thread_id, "协程必须回到主 loop 线程执行"


def test_run_sync_falls_back_without_main_loop():
    """无主 loop（脚本直跑 / 同步测试入口）→ 退回 asyncio.run，在自身线程跑。"""
    runtime.set_main_loop(None)
    seen = {}

    def _worker():
        async def _work():
            return threading.get_ident()
        seen["thread"] = runtime.run_sync(_work())

    t = threading.Thread(target=_worker)
    t.start()
    t.join()

    assert seen["thread"] == t.ident


def test_spawn_keeps_reference_until_done():
    """spawn 起的后台任务在完成前保留强引用（否则可能被 GC 掉，_INFLIGHT 永不释放）。"""

    async def _scenario():
        gate = asyncio.Event()

        async def _work():
            await gate.wait()

        task = runtime.spawn(_work())
        assert runtime.pending_bg_tasks() == 1
        gate.set()
        await task
        await asyncio.sleep(0)          # 让 done callback 跑
        assert runtime.pending_bg_tasks() == 0

    asyncio.run(_scenario())


def _run_in_worker(fn):
    """起一个工作线程跑 fn（模拟 `asyncio.to_thread` 里的同步 handler）。"""
    def _worker():
        fn()

    t = threading.Thread(target=_worker)
    t.start()
    return t


def test_rag_search_bridge_runs_on_main_loop():
    """`rag_search._run_async` 从 worker 线程调用 → 协程回主 loop（不再临时 loop）。"""
    from app.core.agent_tools.tools import rag_search as rs

    main_thread_id = threading.get_ident()
    seen = {}

    async def _scenario():
        runtime.set_main_loop(asyncio.get_running_loop())
        try:
            def _work():
                async def _coro():
                    return threading.get_ident()
                seen["thread"] = rs._run_async(_coro())

            t = _run_in_worker(_work)
            while t.is_alive():
                await asyncio.sleep(0.01)
            t.join()
        finally:
            runtime.set_main_loop(None)

    asyncio.run(_scenario())

    assert seen["thread"] == main_thread_id


def test_taxonomy_sync_runs_on_main_loop(monkeypatch):
    """`assign_taxonomy_sync` 的临时 loop 换成主 loop → 共享 AsyncOpenAI 单例不再跨 loop。"""
    from app.core import kg_taxonomy

    main_thread_id = threading.get_ident()
    seen = {}

    async def _fake_assign(kg, node_data):
        seen["thread"] = threading.get_ident()
        return {}

    monkeypatch.setattr(kg_taxonomy, "assign_taxonomy", _fake_assign)

    async def _scenario():
        runtime.set_main_loop(asyncio.get_running_loop())
        try:
            t = _run_in_worker(lambda: kg_taxonomy.assign_taxonomy_sync(None, {}))
            while t.is_alive():
                await asyncio.sleep(0.01)
            t.join()
        finally:
            runtime.set_main_loop(None)

    asyncio.run(_scenario())

    assert seen["thread"] == main_thread_id
