"""B3.2 web_page 适配器（全离线）：

- 正文抽取漏斗：trafilatura 成功 / 主用失败 → readability 回退 / 双失败 → None
- 站点授权映射 + 白名单（个人 L0~L2 / 商用 L0，默认 L2）
- 用户给定 URL → 候选 → 下载抽取（httpx.MockTransport，SSRF 校验注入放行以免 DNS）

[需网络] 真网页端到端（白名单站点 1 条）另行手动联调。
"""
import asyncio

import httpx
import trafilatura

from app.core.collector.adapters import get_adapter
from app.core.collector.adapters import web_page as wp
from app.core.collector.adapters.web_page import (
    WebPageAdapter,
    extract_main_text,
    is_allowed,
    license_for_url,
)
from app.core.collector.http import CollectorHttp
from app.core.collector.types import CollectCandidate

# 含导航/脚本/页脚噪声的正文页（真实形态简化）
PAGE_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head><title>二叉树</title><script>var noise = "ad";</script></head>
<body>
<nav>首页 | 目录 | 关于我们</nav>
<article>
<h1>二叉树</h1>
<p>二叉树是每个节点最多有两个子节点的树结构。</p>
<h2>遍历方式</h2>
<p>常见遍历包括先序、中序、后序三种。</p>
</article>
<footer>版权所有 © 2026</footer>
</body>
</html>"""

_BODY_TEXT = "二叉树是每个节点最多有两个子节点的树结构"


def _run(coro):
    return asyncio.run(coro)


def _adapter(handler):
    http = CollectorHttp(retries=0, transport=httpx.MockTransport(handler))
    return WebPageAdapter(http=http, guard=lambda url: None), http


def _raise(*a, **kw):
    raise RuntimeError("mock extraction failure")


# ── 1. 抽取成功（主用 trafilatura） ──────────────

def test_extract_success_returns_body_text():
    text = extract_main_text(PAGE_HTML)
    assert text is not None
    assert _BODY_TEXT in text
    assert "先序、中序、后序" in text


def test_extract_success_drops_nav_script_footer_noise():
    text = extract_main_text(PAGE_HTML)
    assert "<p>" not in text and "var noise" not in text
    assert "版权所有" not in text


def test_extract_empty_html_returns_none():
    assert extract_main_text("") is None
    assert extract_main_text("   \n  ") is None


# ── 2. 主用失败 → readability 回退 ───────────────

def test_extract_falls_back_to_readability(monkeypatch):
    monkeypatch.setattr(trafilatura, "extract", lambda *a, **kw: None)
    text = extract_main_text(PAGE_HTML)
    assert text is not None
    assert _BODY_TEXT in text
    # 回退路径出口同样是纯文本（readability 产 HTML 已剥标签）
    assert "<" not in text and ">" not in text


def test_extract_returns_none_when_fallback_yields_nothing(monkeypatch):
    """主用 None + 后备拿不到正文（产空）→ None"""
    import readability

    class _EmptyDoc:
        def __init__(self, *a, **kw):
            pass

        def summary(self):
            return ""

    monkeypatch.setattr(trafilatura, "extract", lambda *a, **kw: None)
    monkeypatch.setattr(readability, "Document", _EmptyDoc)
    assert extract_main_text(PAGE_HTML) is None


# ── 3. 双失败 → None ────────────────────────────

def test_extract_returns_none_when_both_raise(monkeypatch):
    """主用抛异常 + 后备抛异常 → None（不向上抛，上层按空内容弃用）"""
    import readability

    monkeypatch.setattr(trafilatura, "extract", lambda *a, **kw: _raise())
    monkeypatch.setattr(readability, "Document", lambda *a, **kw: _raise())
    assert extract_main_text(PAGE_HTML) is None


# ── 4. 站点授权映射 / 白名单 ────────────────────

def test_license_default_l2_for_unlisted_site():
    assert license_for_url("https://www.example.com/a/b") == "L2"
    assert license_for_url("") == "L2"


def test_license_mapping_matches_suffix_and_subdomain():
    assert license_for_url("https://openstax.org/books/calculus") == "L0"
    assert license_for_url("https://cn.smartedu.cn/course/1") == "L1"
    # 后缀防误伤：非该域名后缀不命中
    assert license_for_url("https://notopenstax.org/x") == "L2"


def test_whitelist_follows_mode_rules():
    assert is_allowed("https://www.example.com/a", "personal") is True   # L2 个人可采
    assert is_allowed("https://www.example.com/a", "commercial") is False  # 商用仅 L0
    assert is_allowed("https://openstax.org/x", "commercial") is True


def test_l3_site_refused_even_in_personal_mode(monkeypatch):
    monkeypatch.setitem(wp._SITE_LICENSE, "paywall.com", "L3")
    assert license_for_url("https://paywall.com/doc") == "L3"
    assert is_allowed("https://paywall.com/doc", "personal") is False
    adapter = WebPageAdapter(guard=lambda url: None)
    assert adapter.candidate_from_url("https://paywall.com/doc") is None


# ── 5. 注册表 / 候选构造 ────────────────────────

def test_registered_instance_and_lazy_http():
    adapter = get_adapter("web_page")
    assert isinstance(adapter, WebPageAdapter)
    assert adapter.name == "web_page"
    assert adapter.license_level == "L2"
    assert adapter._http is None  # 惰性：注册实例不建 HTTP 客户端


def test_search_has_no_discovery_channel():
    """web_page 不做站内遍历发现候选（§8 不逆向对抗），候选由用户给 URL"""
    adapter = WebPageAdapter(guard=lambda url: None)
    assert _run(adapter.search("数据结构")) == []


def test_candidate_from_url_defaults_title_to_url():
    adapter = WebPageAdapter(guard=lambda url: None)
    cand = adapter.candidate_from_url("https://www.example.com/lesson/1")
    assert cand is not None
    assert cand.source == "web_page"
    assert cand.license_level == "L2"
    assert cand.title == "https://www.example.com/lesson/1"
    assert cand.meta["adapter"] == "web_page"


def test_candidate_from_url_rejects_blocked_and_empty():
    adapter = WebPageAdapter(guard=lambda url: "拒绝访问内网/回环地址: localhost")
    assert adapter.candidate_from_url("http://localhost:8000/x") is None
    assert adapter.candidate_from_url("") is None
    assert adapter.candidate_from_url("   ") is None


# ── 6. 下载 + 抽取（MockTransport，离线） ───────

def test_fetch_downloads_and_extracts_markdown():
    async def go():
        adapter, http = _adapter(lambda request: httpx.Response(200, text=PAGE_HTML))
        try:
            cand = CollectCandidate(title="二叉树", source_url="https://example.org/tree")
            return await adapter.fetch(cand)
        finally:
            await http.close()

    md = _run(go())
    assert _BODY_TEXT in md
    assert "<p>" not in md


def test_fetch_timeout_returns_empty_string():
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("mock timeout")

    async def go():
        adapter, http = _adapter(handler)
        try:
            cand = CollectCandidate(title="t", source_url="https://example.org/x")
            return await adapter.fetch(cand)
        finally:
            await http.close()

    assert _run(go()) == ""


def test_fetch_both_extractors_fail_returns_empty_string(monkeypatch):
    import readability

    monkeypatch.setattr(trafilatura, "extract", lambda *a, **kw: _raise())
    monkeypatch.setattr(readability, "Document", lambda *a, **kw: _raise())

    async def go():
        adapter, http = _adapter(lambda request: httpx.Response(200, text=PAGE_HTML))
        try:
            cand = CollectCandidate(title="t", source_url="https://example.org/x")
            return await adapter.fetch(cand)
        finally:
            await http.close()

    assert _run(go()) == ""  # 双失败即弃


def test_fetch_refuses_l3_site(monkeypatch):
    monkeypatch.setitem(wp._SITE_LICENSE, "paywall.com", "L3")

    async def go():
        adapter, http = _adapter(lambda request: httpx.Response(200, text=PAGE_HTML))
        try:
            cand = CollectCandidate(title="t", source_url="https://paywall.com/x")
            return await adapter.fetch(cand)
        finally:
            await http.close()

    assert _run(go()) == ""


def test_fetch_refuses_ssrf_blocked_url():
    """默认 guard = net_guard（内网/回环拒绝），无需真实 DNS 即命中 host 规则"""
    async def go():
        http = CollectorHttp(retries=0, transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text=PAGE_HTML)))
        adapter = WebPageAdapter(http=http)  # 不注入 guard，走真实 net_guard
        try:
            cand = CollectCandidate(title="t", source_url="http://127.0.0.1:8000/x")
            return await adapter.fetch(cand)
        finally:
            await http.close()

    assert _run(go()) == ""
