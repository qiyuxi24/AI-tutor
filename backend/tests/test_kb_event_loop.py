"""事件循环健康：文档解析不得占住调用方线程 / 事件循环（架构审查 P0-2 的回归）。

为什么需要这条用例：
    单 worker 部署（AGENTS.md §1 的硬约束）下，`upload_and_index` 若在事件循环里
    同步跑 OCR / ebook-convert（分钟级），期间**所有用户的对话与 SSE 一起停摆**。
    症状是"别人的请求超时"，不是"上传慢" —— 靠日志几乎无法归因，故用用例锁死。

口径（确定性、零墙钟）：
    - 断言**解析实际执行的线程身份** ≠ 调用方（pytest 主线程 / asyncio.run 的 loop）线程，
      不再采样心跳间隔、不再设任何时间阈值 —— Windows 定时器精度、GC、机器负载都曾
      让旧版 `max(gaps) < 0.15s` 随机飘（全量回归第 2 轮 0.206s 误报）。
    - 假解析器 `time.sleep(0.3)` 仍模拟 OCR 阻塞，但只用于让"解析在别的线程"这件事可观察，
      断言本身与耗时完全无关。
    - 实现无关：真代码走 `asyncio.to_thread` → 解析在 worker 线程 → 通过；若有人把
      `asyncio.to_thread(...)` 改回直接 `parse_document(...)` → 解析落在事件循环线程 → 必红；
      换成 `run_in_executor` / 裸 `threading.Thread` 只要没占住 loop，同样通过（本就不该
      用墙钟去区分这些实现）。
"""
import asyncio
import threading
import time
import types

import pytest

from app.core.kb.kb_manager import KbManager

USER = 90001
# 入库文本质量下限 MIN_PARSE_TEXT_LEN = 200（低于此值会被判"不可解析"而拒绝入库）
BODY = "栈是后进先出的线性表。" * 25

PARSE_BLOCK_SECS = 0.3    # 模拟 OCR / subprocess 的阻塞时长，仅为便于观察解析线程


def _fake_parsed(text: str = BODY):
    """最小 ParseResult 形状（parse_document(verbose=True) 的返回）。"""
    return types.SimpleNamespace(text=text, ok=True, meta={}, error=None)


@pytest.fixture
def manager(tmp_path):
    # 预热解析器注册表：`_ensure_builtin()` 在**首次调用门面函数**时才探测 RapidOCR /
    # calibre / libreoffice（源码注释自述 0.7–1s，本机实测 ~200ms）。
    # 预热让首次探测不落在被测的 `upload_and_index` 路径上，保持用例干净快速。
    from app.core.kb.parsers import parse_document as _real_parse
    _real_parse("warmup.txt", b"warm up the parser registry")
    return KbManager(tmp_path)


def test_parse_runs_off_the_event_loop(manager, monkeypatch):
    """解析必须跑在事件循环线程之外：断言解析线程身份 ≠ 调用方线程。"""
    import app.core.kb.parsers as parsers

    # 同步测试函数 == pytest 主线程；asyncio.run(...) 的事件循环也跑在这个线程上。
    main_ident = threading.get_ident()
    observed = {}

    def slow_parse(filename, content, verbose=False):
        observed["parse_thread"] = threading.get_ident()
        time.sleep(PARSE_BLOCK_SECS)
        return _fake_parsed()

    # 函数内 `from ... import parse_document` 每次调用都从模块取属性，patch 模块属性必然生效
    monkeypatch.setattr(parsers, "parse_document", slow_parse)

    asyncio.run(manager.upload_and_index(
        USER, "讲义.txt", BODY.encode(), None, vectorize=False))

    assert observed.get("parse_thread") is not None, "解析函数没被调用"
    assert observed["parse_thread"] != main_ident, (
        "解析跑在事件循环线程（调用方线程）—— 会阻塞所有对话与 SSE；"
        "应交给 asyncio.to_thread / 线程池"
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
