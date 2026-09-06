"""B2.1 oiwiki adapter：目录发现（mkdocs.yml 导航）+ 正文转 KB Markdown（全 mock 离线）

[需API] 真网全链路联调（oi-wiki.org + raw.githubusercontent 各 1 条）另行手动标 1 条。
"""
import asyncio

import httpx

from app.core.collector.http import CollectorHttp
from app.core.collector.adapters import get_adapter
from app.core.collector.adapters.oiwiki import OiwikiAdapter
from app.core.collector.types import CollectCandidate

# ── 离线夹具：mkdocs.yml 导航（真实结构简化，含多级板块） ──
NAV_YAML = """\
nav:
  - 数据结构:
    - 数据结构部分简介: ds/index.md
    - 栈: ds/stack.md
    - 队列: ds/queue.md
    - 堆:
      - 堆简介: ds/heap.md
      - 二叉堆: ds/binary-heap.md
  - 算法基础:
    - 复杂度: basic/complexity.md
    - 枚举: basic/enumerate.md
  - 数学:
    - 数学部分简介: math/index.md
    - 线性代数:
      - 线性方程组: math/linear-algebra/linear-equations.md
"""

# 某页 raw Markdown（含 MkDocs 代码页标签 / admonition / 相对图片 / 公式）
STACK_MD = """\
## 引入

![栈示意图](./images/stack.svg)

栈是 OI 中常用的一种线性数据结构，简称 LIFO 表。

???+ note "实现"

    === "C++"

        ```cpp
        int st[N];
        ```

    === "Python"

        ```python
        st = []
        ```

## 基本操作

- push：入栈
- pop：出栈

参考：$O(1)$。
"""


def _run(coro):
    return asyncio.run(coro)


def _mkdocs_handler(**kwargs):
    """按 URL 分派：mkdocs.yml → 导航；其余（raw .md）→ 正文"""
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/mkdocs.yml"):
            return httpx.Response(200, text=kwargs.get("nav", NAV_YAML))
        return httpx.Response(200, text=kwargs.get("md", STACK_MD))
    return handler


def _adapter(handler):
    http = CollectorHttp(retries=0, transport=httpx.MockTransport(handler))
    return OiwikiAdapter(http=http), http


def test_registered_instance_and_license():
    adapter = get_adapter("oiwiki")
    assert isinstance(adapter, OiwikiAdapter)
    assert adapter.name == "oiwiki"
    assert adapter.license_level == "L0"
    assert adapter._http is None  # 惰性：注册实例不建 HTTP 客户端


def test_search_section_board_lists_leaves():
    """按板块名「数据结构」→ 目录下全部叶子候选（含嵌套子板块）"""
    async def go():
        adapter, http = _adapter(_mkdocs_handler())
        try:
            return await adapter.search("数据结构")
        finally:
            await http.close()

    cands = _run(go())
    titles = [c.title for c in cands]
    assert titles == ["数据结构部分简介", "栈", "队列", "堆简介", "二叉堆"]
    stack = cands[1]
    assert stack.source_url == (
        "https://raw.githubusercontent.com/OI-wiki/OI-wiki/master/docs/ds/stack.md"
    )
    assert stack.meta["adapter"] == "oiwiki"
    assert stack.meta["path"] == "ds/stack.md"
    assert stack.meta["page_url"] == "https://oi-wiki.org/ds/stack/"
    assert stack.description == "OI Wiki · 数据结构"
    # 页面 URL 归一：index 页不带尾部 /stack 歧义
    idx = cands[0]
    assert idx.meta["page_url"] == "https://oi-wiki.org/ds/"


def test_search_multi_token_subject_unions_boards():
    """学科名「数据结构与算法」按连接词切分后并集多个板块"""
    async def go():
        adapter, http = _adapter(_mkdocs_handler())
        try:
            return await adapter.search("数据结构与算法")
        finally:
            await http.close()

    cands = _run(go())
    paths = {c.meta["path"] for c in cands}
    assert {"ds/stack.md", "ds/binary-heap.md", "basic/complexity.md",
            "basic/enumerate.md"} <= paths


def test_search_single_keyword_matches_nested_page():
    """单个词「线性方程组」直接命中嵌套子板块页面"""
    async def go():
        adapter, http = _adapter(_mkdocs_handler())
        try:
            return await adapter.search("线性方程组")
        finally:
            await http.close()

    cands = _run(go())
    assert [c.title for c in cands] == ["线性方程组"]
    assert cands[0].meta["page_url"] == "https://oi-wiki.org/math/linear-algebra/linear-equations/"


def test_search_empty_and_unrelated_query():
    async def go(query):
        adapter, http = _adapter(_mkdocs_handler())
        try:
            return await adapter.search(query)
        finally:
            await http.close()

    assert _run(go("")) == []
    assert _run(go("书法艺术")) == []


def test_search_network_fail_returns_empty():
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    async def go():
        adapter, http = _adapter(handler)
        try:
            return await adapter.search("数据结构")
        finally:
            await http.close()

    assert _run(go()) == []


def test_fetch_converts_oiwiki_markdown_to_kb_md():
    """正文转 KB Markdown：admonition 收敛、代码页标签行删除、相对图片补全"""
    async def go():
        adapter, http = _adapter(_mkdocs_handler())
        try:
            cand = CollectCandidate(
                title="栈",
                source_url=("https://raw.githubusercontent.com/OI-wiki/OI-wiki/"
                            "master/docs/ds/stack.md"),
                meta={"path": "ds/stack.md"},
            )
            return await adapter.fetch(cand)
        finally:
            await http.close()

    md = _run(go())
    assert "## 引入" in md and "## 基本操作" in md
    # admonition 头与内容标签消失，代码正文保留
    assert "???+ note" not in md and '=== "C++"' not in md
    assert "int st[N];" in md and "st = []" in md
    # 相对图片补全为 raw 目录绝对地址
    assert "master/docs/ds/images/stack.svg" in md
    # 公式原样保留
    assert "$O(1)$" in md


def test_fetch_fail_returns_empty_string():
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("mock timeout")

    async def go():
        adapter, http = _adapter(handler)
        try:
            cand = CollectCandidate(title="栈", source_url="https://example.org/x.md")
            return await adapter.fetch(cand)
        finally:
            await http.close()

    assert _run(go()) == ""
