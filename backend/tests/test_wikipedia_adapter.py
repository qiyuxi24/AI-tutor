"""B1.2 wikipedia adapter：search / 分类递归翻页 continue / UA / fetch 转 MD（全 mock 离线）

[需API] 真网全链路联调（zh.wikipedia.org 1 条）不在本文件，另行手动标 1 条。
"""
import asyncio

import httpx

from app.core.collector.http import CollectorHttp, DEFAULT_UA
from app.core.collector.adapters.wikipedia import WikipediaAdapter, wikitext_to_md
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


def test_wikitext_variant_markup_stripped():
    """地区词/变体标记 -{…}- 不残留（2026-09-05 真网「堆栈」页遗留债 #1）"""
    md = wikitext_to_md(
        "'''堆栈'''（-{zh-cn:堆叠; zh-tw:堆棧;}-）是一种数据结构。\n"
        "-{H|zh-cn:栈;zh-tw:棧;}-\n"
        "常用-{zh-hans:数组}-或-{链表|数组}-实现。-{}-\n"
    )
    assert "-{" not in md and "}-" not in md
    assert "堆叠" in md                 # 多语言变体取首选（zh-cn）
    assert "zh-cn" not in md and "zh-tw" not in md and "zh-hans" not in md
    assert "H|" not in md               # 转换规则定义整行丢弃
    assert "数组" in md                 # 二选一取前者，且无残留分隔符
    assert "链表|数组" not in md


def test_wikitext_inline_templates_keep_content():
    """内容型模板**保留参数**，不再整段丢弃（2026-09-23 真网遗留债 #2）：

    真网《贪心算法》原文 `'''贪心算法'''（{{langx|en|greedy algorithm}}）` 里整个模板被删，
    入库正文变成「贪心算法（）」——英文名/缩写丢失，是**信息损坏**而非样式问题。
    """
    md = wikitext_to_md(
        "'''贪心算法'''（{{langx|en|greedy algorithm}}），又称'''贪婪算法'''。\n"
        "'''栈'''（{{lang-en|stack}}）是{{nowrap|后进先出}}的[[抽象数据类型]]。\n"
        "缩写{{lang|en|ADT}}，读音{{IPA|en|/stæk/}}。\n"
    )
    assert "**贪心算法**（greedy algorithm）" in md
    assert "（stack）" in md
    assert "后进先出" in md and "nowrap" not in md
    assert "ADT" in md and "/stæk/" in md
    assert "（）" not in md                       # 不再留空括号
    assert "{{" not in md and "}}" not in md      # 花括号不残留


def test_wikitext_drops_maintenance_templates_entirely():
    """维护/元信息模板仍**整体丢弃**：不能因为"保留内容"把 {{Expand}}/{{NoteTA}} 灌进正文"""
    md = wikitext_to_md(
        "{{Expand|time=2013-03-12T12:00:06+00:00}}\n"
        "{{NoteTA|G1=IT}}\n"
        "{{unreferenced|time=2016-03-13T14:53:32+00:00}}\n"
        "正文第一段。\n"
    )
    assert md.strip() == "正文第一段。"
    assert "Expand" not in md and "unreferenced" not in md


def test_wikitext_cleans_magic_words_html_and_empty_items():
    """魔术字 / HTML 注释 / 展示标签 / 空列表项 / 游离链接括号（真网遗留债 #3）"""
    md = wikitext_to_md(
        "__NOTOC__\n"
        "<!-- 这是维护注释 -->\n"
        "调用 <code>push</code> 入栈。<div class=\"center\">居中说明</div>\n"
        "*     \n"
        "* 有效项\n"
        "[[数据结构]]条目里残留的 ]] 尾巴\n"
    )
    assert "NOTOC" not in md and "__" not in md
    assert "维护注释" not in md and "<!--" not in md
    assert "`push`" in md and "<code>" not in md
    assert "居中说明" in md and "<div" not in md
    assert [ln for ln in md.splitlines() if ln.strip() == "*"] == []   # 空列表项已删
    assert " * 有效项" in md or md.count("* 有效项") == 1
    assert "]]" not in md and "[[" not in md


def test_wikitext_drops_file_links_whole():
    """图片/文件链接整条丢弃（含中文前缀）：不留 `thumb|说明` 之类的碎片"""
    md = wikitext_to_md(
        "[[File:Treedatastructure.png|300px|thumb|一棵树]]\n"
        "[[文件:Stack.png|thumb|栈示意]]\n"
        "正文。\n"
    )
    assert "Treedatastructure" not in md and "Stack.png" not in md
    assert "thumb" not in md and "一棵树" not in md
    assert "正文。" in md


def test_wikitext_unifies_traditional_and_simplified():
    """简繁统一：源页面普遍简繁混写，出口统一为大陆简体（2026-09-23 用户反馈「字有问题」）

    背景：MediaWiki 只在**渲染时**按变体转换，走 `prop=revisions`（wikitext）路径会绕过它 ——
    真网《树 (数据结构)》整段繁体、《贪心算法》半简半繁，入库后同一篇里两种字形并存。
    """
    md = wikitext_to_md(
        "在計算機科學中，'''樹'''（{{langx|en|tree}}）是一種抽象資料型別，"
        "用來模擬具有樹狀結構性質的資料集合。\n"
        "本段本来就是简体字，不应被改动。\n"
    )
    assert "在计算机科学中，**树**（tree）是一种抽象" in md
    assert "計算機" not in md and "樹狀結構" not in md and "資料" not in md
    assert "本来就是简体字" in md


def test_wikitext_variant_conversion_keeps_math_intact():
    """简繁转换不得动公式：$…$ 里的 LaTeX 必须逐字保留"""
    md = wikitext_to_md(
        "期望值：<math>E = \\sum_{i=1}^n i</math>，另有 $\\frac{1}{2}$。\n"
        "'''資料'''是繁體字。\n"
    )
    assert "$E = \\sum_{i=1}^n i$" in md
    assert "$\\frac{1}{2}$" in md
    assert "**资料**" in md


def test_wikitext_variant_conversion_degrades_without_zhconv(monkeypatch):
    """zhconv 未安装 → 原样返回不抛错（与 trafilatura/readability「装则启用」同约定）"""
    import app.core.collector.adapters.wikipedia as wp

    monkeypatch.setattr(wp, "_zhconv", None)
    md = wp.wikitext_to_md("'''樹'''是一種資料結構。")
    assert "資料結構" in md                    # 未转换，但没有报错


def test_wikitext_external_links_become_markdown_links():
    """外部链接 `[url 说明]` → Markdown 链接（预览里可点）；裸 URL 与脚注标注不动

    维基外链语法不是 Markdown，原样入库时只是一对方括号文字（预览点不了）。
    转成 `[说明](url)` 不新增噪声（URL 本来就在正文里），只是换个写法让它能点。
    """
    md = wikitext_to_md(
        "参考[http://example.com/a 官方文档]与[https://example.org]两条。\n"
        "裸链接 https://plain.example.com 保持原样。\n"
        "脚注标注[1]与数值[2,3]都不动。\n"
    )
    assert "[官方文档](http://example.com/a)" in md
    assert "[https://example.org](https://example.org)" in md      # 无说明文字时用 URL 兜底
    assert "裸链接 https://plain.example.com 保持原样。" in md
    assert "脚注标注[1]与数值[2,3]都不动。" in md


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
