"""B1.2 wikipedia adapter：search / 分类递归翻页 continue / UA / fetch 转 MD（全 mock 离线）

[需API] 真网全链路联调（zh.wikipedia.org 1 条）不在本文件，另行手动标 1 条。
"""
import asyncio

import httpx

from app.core.collector.http import CollectorHttp, DEFAULT_UA
from app.core.collector.adapters.wikipedia import WikipediaAdapter
from app.core.collector.types import CollectCandidate
from tests._mediawiki_fixtures import (
    SEARCH_OK, SEARCH_EMPTY, CAT_PAGE1, CAT_PAGE2, CAT_EMPTY,
    FETCH_OK, STACK_URL, STACK_TITLE,
)


def _run(coro):
    return asyncio.run(coro)


def _adapter(handler):
    """用 MockTransport 注入 handler，构造离线可用 adapter"""
    http = CollectorHttp(retries=0, transport=httpx.MockTransport(handler))
    return WikipediaAdapter(http=http), http


def test_search_returns_candidates_with_ua():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["ua"] = request.headers.get("user-agent", "")
        return httpx.Response(200, json=SEARCH_OK)

    async def go():
        adapter, http = _adapter(handler)
        try:
            return await adapter.search("数据结构")
        finally:
            await http.close()

    candidates = _run(go())
    assert len(candidates) == 2
    first = candidates[0]
    assert first.title == "栈"
    assert first.source_url.startswith("https://zh.wikipedia.org/wiki/")
    # 无残留 HTML 标签，摘要清洗后保留原文
    assert "抽象数据类型" in first.description and "<span" not in first.description
    assert first.meta["adapter"] == "wikipedia"
    assert seen["ua"] == DEFAULT_UA and "TutorAgent" in seen["ua"]


def test_search_empty_results():
    async def go():
        adapter, http = _adapter(lambda r: httpx.Response(200, json=SEARCH_EMPTY))
        try:
            return await adapter.search("不存在的东西")
        finally:
            await http.close()

    assert _run(go()) == []


def test_search_network_fail_returns_empty():
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    async def go():
        adapter, http = _adapter(handler)
        try:
            return await adapter.search("x")
        finally:
            await http.close()

    assert _run(go()) == []


def test_category_discover_follows_continue_cursor():
    """分类发现：第 1 页带 continue 游标 → 带 cmcontinue 翻第 2 页 → 收尾"""
    pages = {"n": 0}
    seen_params = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        seen_params.append(params)
        pages["n"] += 1
        if pages["n"] == 1:
            return httpx.Response(200, json=CAT_PAGE1)
        return httpx.Response(200, json=CAT_PAGE2)

    async def go():
        adapter, http = _adapter(handler)
        try:
            return await adapter.category_discover("数据结构", max_items=50)
        finally:
            await http.close()

    titles = [c.title for c in _run(go())]
    assert titles == ["栈", "队列", "链表"]          # 两页聚合
    assert pages["n"] == 2                           # 确实翻页了
    # 首请求带 cmtitle/cmlimit；次请求带 continue 游标
    assert "Category:数据结构" in seen_params[0]["cmtitle"]
    assert seen_params[1].get("cmcontinue") == "数据结构|0|100"


def test_category_discover_respects_max_items():
    # 首页 2 条已到 max_items=1 -> 不再翻页
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=CAT_PAGE1)

    async def go():
        adapter, http = _adapter(handler)
        try:
            return await adapter.category_discover("数据结构", max_items=1)
        finally:
            await http.close()

    assert [c.title for c in _run(go())] == ["栈"]


def test_category_discover_empty():
    async def go():
        adapter, http = _adapter(lambda r: httpx.Response(200, json=CAT_EMPTY))
        try:
            return await adapter.category_discover("空分类")
        finally:
            await http.close()

    assert _run(go()) == []


def test_fetch_converts_wikitext_to_markdown():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["titles"] = request.url.params.get("titles", "")
        seen["redirects"] = request.url.params.get("redirects", "")
        return httpx.Response(200, json=FETCH_OK)

    async def go():
        adapter, http = _adapter(handler)
        try:
            cand = CollectCandidate(title=STACK_TITLE,
                                    source_url=STACK_URL,
                                    license_level="L0",
                                    meta={"page_title": STACK_TITLE})
            return await adapter.fetch(cand)
        finally:
            await http.close()

    md = _run(go())
    assert "**栈**（stack）是一种后进先出的ADT。" in md
    # 结构转换：标题 + 列表
    assert "## 基本操作" in md
    assert "* push：入栈" in md
    # 模板 / 分类 / interwiki / ref 全部清除
    assert "{{" not in md and "Infobox" not in md
    assert "Category" not in md and "Stack (abstract" not in md
    assert "<ref" not in md
    # LaTeX 公式原样保留
    assert "$x^2$" in md
    assert "\\sum_{i=1}^n i" in md
    # 请求参数正确
    assert seen["titles"] == "栈" and seen["redirects"] == "1"


def test_fetch_falls_back_to_url_title():
    """无 meta 的外部候选：从 source_url 反解标题"""
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params.get("titles") == STACK_TITLE
        return httpx.Response(200, json=FETCH_OK)

    async def go():
        adapter, http = _adapter(handler)
        try:
            cand = CollectCandidate(title="栈", source_url=STACK_URL)
            return await adapter.fetch(cand)
        finally:
            await http.close()

    assert "基本操作" in _run(go())


def test_fetch_fail_returns_empty_string():
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("mock timeout")

    async def go():
        adapter, http = _adapter(handler)
        try:
            cand = CollectCandidate(title="栈", source_url=STACK_URL)
            return await adapter.fetch(cand)
        finally:
            await http.close()

    assert _run(go()) == ""
