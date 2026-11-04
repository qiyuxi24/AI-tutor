"""事件循环健康：文档解析不得占住对话（架构审查 P0-2 的回归）。

为什么需要这条用例：
    单 worker 部署（AGENTS.md §1 的硬约束）下，`upload_and_index` 若在事件循环里
    同步跑 OCR / ebook-convert（分钟级），期间**所有用户的对话与 SSE 一起停摆**。
    症状是"别人的请求超时"，不是"上传慢" —— 靠日志几乎无法归因，故用用例锁死。

口径：
    - 断言的是**心跳最大间隔**，不是总耗时。总耗时变长是允许的，也是必然的。
    - 假解析器 `time.sleep(0.3)` 模拟 OCR；阈值 150ms 远小于 300ms，且给 Windows
      定时器精度（默认 ~15.6ms）留足余量。
    - 若有人把 `asyncio.to_thread` 改回直接调用，第一条用例必红。
"""
import asyncio
import time
import types

import pytest

from app.core.kb.kb_manager import KbManager

USER = 90001
# 入库文本质量下限 MIN_PARSE_TEXT_LEN = 200（低于此值会被判"不可解析"而拒绝入库）
BODY = "栈是后进先出的线性表。" * 25

PARSE_BLOCK_SECS = 0.3    # 模拟 OCR / subprocess 的阻塞时长
HEARTBEAT_TICK = 0.03
MAX_GAP_SECS = 0.15       # 远小于 0.3：主线程被占住时必超


def _fake_parsed(text: str = BODY):
    """最小 ParseResult 形状（parse_document(verbose=True) 的返回）。"""
    return types.SimpleNamespace(text=text, ok=True, meta={}, error=None)


@pytest.fixture
def manager(tmp_path):
    # 预热解析器注册表：`_ensure_builtin()` 在**首次调用门面函数**时才探测 RapidOCR /
    # calibre / libreoffice（源码注释自述 0.7–1s，本机实测 ~200ms）。
    # 不预热它就会算进第一条心跳间隔，让用例误报"事件循环被阻塞"。
    from app.core.kb.parsers import parse_document as _real_parse
    _real_parse("warmup.txt", b"warm up the parser registry")
    return KbManager(tmp_path)


def test_parse_runs_off_the_event_loop(manager, monkeypatch):
    """解析阻塞 300ms 期间，事件循环心跳间隔仍 < 150ms。"""
    import app.core.kb.parsers as parsers

    def slow_parse(filename, content, verbose=False):
        time.sleep(PARSE_BLOCK_SECS)
        return _fake_parsed()

    # 函数内 `from ... import parse_document` 每次调用都从模块取属性，patch 模块属性必然生效
    monkeypatch.setattr(parsers, "parse_document", slow_parse)

    gaps: list[float] = []
    HEARTBEAT_TICKS = 16        # 16 × 30ms ≈ 480ms，完整覆盖 300ms 的解析阻塞窗口

    async def heartbeat(ticks: int):
        last = time.perf_counter()
        for _ in range(ticks):
            await asyncio.sleep(HEARTBEAT_TICK)
            now = time.perf_counter()
            gaps.append(now - last)
            last = now

    async def scenario():
        hb = asyncio.create_task(heartbeat(HEARTBEAT_TICKS))
        # 先让心跳进入第一个 sleep：否则解析若同步阻塞，它在阻塞期间一次都跑不到
        await asyncio.sleep(0)
        await manager.upload_and_index(
            USER, "讲义.txt", BODY.encode(), None, vectorize=False)
        await hb          # 跑满固定 tick 数，不靠 cancel 抢先 —— 取消早了会丢采样

    asyncio.run(scenario())

    assert len(gaps) == HEARTBEAT_TICKS, f"采样异常（{len(gaps)}/{HEARTBEAT_TICKS}）"
    worst = max(gaps)
    assert worst < MAX_GAP_SECS, (
        f"事件循环被阻塞 {worst:.3f}s（阈值 {MAX_GAP_SECS}s）—— "
        f"解析很可能又回到了主线程。实测间隔: {[round(g, 3) for g in gaps]}"
    )


def test_parse_error_still_surfaces_as_value_error(manager, monkeypatch):
    """搬进线程后异常语义不变：解析失败仍是 ValueError（API 层按它回 400）。"""
    import app.core.kb.parsers as parsers

    def broken_parse(filename, content, verbose=False):
        raise ValueError("文档解析失败: 模拟损坏文件")

    monkeypatch.setattr(parsers, "parse_document", broken_parse)

    with pytest.raises(ValueError, match="模拟损坏文件"):
        asyncio.run(manager.upload_and_index(
            USER, "讲义.txt", BODY.encode(), None, vectorize=False))
