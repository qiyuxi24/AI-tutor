"""网页存档能力层测试（2026-09-22）—— 全离线：假 httpx / 假抽取 / 假搜索结果文本。

覆盖四层：
1. 搜索结果文本 → 结果页 URL（去重 / 截断 / 关掉开关）；
2. `archive_html`：HTML → Markdown → 图谱节点；抽不出正文时不建节点；
3. `fetch_and_archive`：SSRF 拒绝不发请求、非 200 / 非 HTML 静默跳过、正常下载后存档；
4. 宿主接线：`web_search` 成功后调度后台存档；`kg=None`（既有测试/调用方）零副作用。
"""
from types import SimpleNamespace

import pytest

from app.core import knowledge_writer as kw
from app.core import open_source
from app.core.agent_tools import mcp_host
from app.core.agent_tools import web_archive as wa
from app.core.knowledge_graph import KnowledgeGraph

USER = 1
BODY = "二分查找在有序数组中每次折半"
PAGE_HTML = (
    "<html><head><title>二分查找入门</title></head><body><h1>二分查找</h1>"
    f"<p>{BODY}。</p></body></html>"
)
SEARCH_TEXT = (
    "搜索「数据结构」共 3 条结果：\n"
    "[1] 标题A\n    链接: https://a.example/x\n    摘要: …\n"
    "[2] 标题B\n    链接: https://b.example/y。\n    摘要: …\n"
    "[3] 重复A\n    链接: https://a.example/x\n    摘要: …\n"
)


@pytest.fixture(autouse=True)
def _open_test_domains(monkeypatch):
    """夹具域名列为开放来源（`core/open_source.py` 只采开源、默认 fail-closed）。

    否则未登记的夹具域名会先被存档判定拦掉，本文件就测不到「存档机制本身」。
    判定逻辑自身见 `test_open_source.py`、接线见 `test_open_source_paths.py`。
    """
    for host in ("example.com", "a.example", "b.example"):
        monkeypatch.setitem(open_source.SITE_LEVELS, host, "L0")


@pytest.fixture
def kg(tmp_path):
    g = KnowledgeGraph(user_id=USER, data_dir=tmp_path)
    with g._conn:  # nodes.user_id 是外键，先备 users 行
        g._conn.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash)"
            " VALUES (?, 'w', 'x')", (USER,))
    yield g
    g.close()


# ── 1. 搜索结果文本 → URL ────────────────────────────────────

def test_page_urls_dedupe_and_limit():
    """按出现顺序去重，最多取 settings.web_archive_max_pages 个；句末标点被裁掉"""
    assert wa.page_urls_from_search_text(SEARCH_TEXT, limit=2) == [
        "https://a.example/x", "https://b.example/y"]


def test_page_urls_limit_zero_disables(monkeypatch):
    """上限 0 = 关闭该行为（配置里的开关）"""
    monkeypatch.setattr(wa.settings, "web_archive_max_pages", 0)
    assert wa.page_urls_from_search_text(SEARCH_TEXT) == []


def test_page_urls_ignores_text_without_url():
    assert wa.page_urls_from_search_text("未搜索到相关结果", limit=2) == []


# ── 2. archive_html ─────────────────────────────────────────

def test_archive_html_creates_node(kg):
    msg = wa.archive_html(kg, "https://example.com/a", PAGE_HTML)

    assert msg and "已存档为图谱节点" in msg
    nid = kw._web_node_id(USER, "https://example.com/a")
    md = (kg.nodes_dir / f"{nid}.md").read_text(encoding="utf-8")
    assert BODY in md
    assert kg.get_node(nid)["name"] == "二分查找入门"  # 取的是页面 <title>


def test_archive_html_returns_none_when_extract_fails(kg, monkeypatch):
    """正文抽不出来 → 不建节点、不抛异常（返回 None）"""
    import app.core.collector.adapters.web_page as wp

    monkeypatch.setattr(wp, "extract_main_text", lambda html: None)

    assert wa.archive_html(kg, "https://example.com/a", PAGE_HTML) is None
    assert kg.nodes == []


def test_archive_html_noop_without_kg(kg):
    assert wa.archive_html(None, "https://example.com/a", PAGE_HTML) is None
    assert kg.nodes == []


# ── 3. fetch_and_archive ───────────────────────────────────

class _FakeResp:
    def __init__(self, text=PAGE_HTML, ctype="text/html; charset=utf-8", status=200):
        self.text = text
        self.status_code = status
        self.headers = {"content-type": ctype}


class _FakeClient:
    """httpx.Client 替身：只实现 fetch_and_archive 用到的那一小块"""

    resp = _FakeResp()
    calls: list[str] = []

    def __init__(self, *a, **kw):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url):
        type(self).calls.append(url)
        return type(self).resp


def test_fetch_and_archive_blocked_url_never_requests(kg, monkeypatch):
    """SSRF 拒绝：不发任何请求，也不建节点"""
    monkeypatch.setattr(wa, "is_blocked_url", lambda url: "拒绝访问内网/回环地址")

    def _boom(*a, **kw):
        raise AssertionError("被拒的 URL 不该发请求")

    monkeypatch.setattr(wa.httpx, "Client", _boom)

    assert wa.fetch_and_archive(kg, "http://localhost:8000/x") is None
    assert kg.nodes == []


def test_fetch_and_archive_downloads_and_saves(kg, monkeypatch):
    monkeypatch.setattr(wa, "is_blocked_url", lambda url: None)
    _FakeClient.calls = []
    monkeypatch.setattr(wa.httpx, "Client", _FakeClient)

    msg = wa.fetch_and_archive(kg, "https://example.com/a")

    assert _FakeClient.calls == ["https://example.com/a"]
    assert msg and "已存档为图谱节点" in msg


@pytest.mark.parametrize("resp", [
    _FakeResp(ctype="application/pdf"),          # 不是网页
    _FakeResp(status=404),                        # 非 200
])
def test_fetch_and_archive_silently_skips(kg, monkeypatch, resp):
    monkeypatch.setattr(wa, "is_blocked_url", lambda url: None)
    monkeypatch.setattr(wa.httpx, "Client", lambda *a, **kw: _ClientOf(resp))

    assert wa.fetch_and_archive(kg, "https://example.com/a") is None
    assert kg.nodes == []


class _ClientOf(_FakeClient):
    resp = None

    def __init__(self, resp, *a, **kw):
        type(self).resp = resp


# ── 4. 调度与宿主接线 ─────────────────────────────────────────

def test_archive_search_results_saves_each_url(kg, monkeypatch):
    saved: list[str] = []
    monkeypatch.setattr(wa, "fetch_and_archive",
                        lambda g, url, **kw: saved.append(url) or "ok")

    assert wa.archive_search_results(SEARCH_TEXT, kg) == 2
    assert saved == ["https://a.example/x", "https://b.example/y"]


def test_schedule_archive_noop_cases(monkeypatch):
    """无 user_id / 上限为 0 时不启线程"""
    assert wa.schedule_archive_from_search(SEARCH_TEXT, None) is None
    monkeypatch.setattr(wa.settings, "web_archive_max_pages", 0)
    assert wa.schedule_archive_from_search(SEARCH_TEXT, USER) is None


def test_schedule_archive_runs_in_background(kg, monkeypatch):
    """真起线程：结果页逐个存档（测试里注入 kg_factory 与假 downloader）"""
    saved: list[str] = []
    monkeypatch.setattr(wa, "fetch_and_archive",
                        lambda g, url, **kw: saved.append(url) or "ok")

    t = wa.schedule_archive_from_search(SEARCH_TEXT, USER, kg_factory=lambda uid: kg)
    assert t is not None
    t.join(10)

    assert saved == ["https://a.example/x", "https://b.example/y"]


def _fake_mcp_call(text: str):
    async def _call(server, tool_name, args):
        return SimpleNamespace(content=[SimpleNamespace(text=text)], is_error=False)
    return _call


def test_mcp_search_schedules_archive(monkeypatch):
    """联网搜索成功后调度存档（用 kg.user_id 另建实例在后台干）"""
    seen: list = []
    monkeypatch.setattr(mcp_host, "_call_tool", _fake_mcp_call(SEARCH_TEXT))
    monkeypatch.setattr(wa, "schedule_archive_from_search",
                        lambda text, uid, **kw: seen.append((text, uid)))

    out = mcp_host.run_mcp_tool("mcp__websearch__web_search", "web_search",
                                {"server": None}, {"query": "x"},
                                kg=SimpleNamespace(user_id=7))

    assert "https://a.example/x" in out          # 返回文本给模型的部分不变
    assert seen == [(SEARCH_TEXT.strip(), 7)]    # 宿主取回的文本已 strip


def test_mcp_without_kg_does_not_archive(monkeypatch):
    """kg=None（既有调用方/测试路径）→ 纯查询语义，不触碰存档"""
    seen: list = []
    monkeypatch.setattr(mcp_host, "_call_tool", _fake_mcp_call(SEARCH_TEXT))
    monkeypatch.setattr(wa, "schedule_archive_from_search",
                        lambda text, uid, **kw: seen.append((text, uid)))

    out = mcp_host.run_mcp_tool("mcp__websearch__web_search", "web_search",
                                {"server": None}, {"query": "x"})

    assert "https://a.example/x" in out
    assert seen == []


def test_mcp_other_server_not_archived(monkeypatch):
    """只有 websearch 前缀触发存档；其它 MCP 工具不碰图谱"""
    seen: list = []
    monkeypatch.setattr(mcp_host, "_call_tool", _fake_mcp_call(SEARCH_TEXT))
    monkeypatch.setattr(wa, "schedule_archive_from_search",
                        lambda text, uid, **kw: seen.append((text, uid)))

    mcp_host.run_mcp_tool("mcp__other__foo", "foo", {"server": None}, {},
                          kg=SimpleNamespace(user_id=7))

    assert seen == []
