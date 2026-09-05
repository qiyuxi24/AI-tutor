"""B1.2 wikibooks adapter：复用 wikipedia 同栈逻辑，仅换 host（mock 响应复用）

不重复测 wikipedia 全部用例；重点验证：换 host 后 URL/结果正确、name/L0。
"""
import asyncio

import httpx

from app.core.collector.http import CollectorHttp
from app.core.collector.adapters import get_adapter
from app.core.collector.adapters.wikibooks import WikibooksAdapter
from tests._mediawiki_fixtures import SEARCH_OK, FETCH_OK


def _run(coro):
    return asyncio.run(coro)


def test_search_uses_wikibooks_host():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["host"] = str(request.url)
        return httpx.Response(200, json=SEARCH_OK)

    async def go():
        http = CollectorHttp(retries=0, transport=httpx.MockTransport(handler))
        adapter = WikibooksAdapter(http=http)
        try:
            return await adapter.search("数据结构")
        finally:
            await http.close()

    candidates = _run(go())
    assert len(candidates) == 2
    assert candidates[0].title == "栈"
    # host 换成 wikibooks
    assert "zh.wikibooks.org" in candidates[0].source_url
    assert "wikipedia.org" not in candidates[0].source_url
    assert candidates[0].meta["adapter"] == "wikibooks"
    assert seen["host"].startswith("https://zh.wikibooks.org/w/api.php")


def test_fetch_works_through_inherited_logic():
    """继承自 wikipedia 的 fetch/转 MD 逻辑对 wikibooks 同样生效"""
    def handler(request: httpx.Request) -> httpx.Response:
        assert "zh.wikibooks.org" in str(request.url)
        return httpx.Response(200, json=FETCH_OK)

    async def go():
        http = CollectorHttp(retries=0, transport=httpx.MockTransport(handler))
        adapter = WikibooksAdapter(http=http)
        try:
            from app.core.collector.types import CollectCandidate
            cand = CollectCandidate(
                title="栈", source_url="https://zh.wikibooks.org/wiki/%E6%A0%88",
                meta={"page_title": "栈"})
            return await adapter.fetch(cand)
        finally:
            await http.close()

    md = _run(go())
    assert "## 基本操作" in md
    assert "$x^2$" in md


def test_registered_instance_uses_wikibooks_settings():
    """全局注册的 wikibooks 实例参数正确（离线，不触网）"""
    adapter = get_adapter("wikibooks")
    assert adapter.name == "wikibooks"
    assert adapter.license_level == "L0"
    assert adapter.api_host == "https://zh.wikibooks.org"
    assert isinstance(adapter, WikibooksAdapter)
