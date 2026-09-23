"""「只采开源来源」接线回归 —— 四条按 URL 取内容的路径（2026-09-23）。

判定本身（来源表 / 页面许可声明 / fail-closed）由 `test_open_source.py` 覆盖；
**本文件只测接线**，即每条路径上「非开放来源」是否真的被拦在留存之前：

1. `web_archive.fetch_and_archive`：非开放来源**不发请求**就被跳过；
2. `web_archive.archive_html`：页面声明了开放许可 → 放行；无声明 → 不建节点；
3. `fetch_webpage` 端到端：非开放来源 → 返回文本不变、图谱**零节点**（只读不留）；
4. `download_resource`：非开放来源 → 不下载、不入库，回填友好文案；
5. 总开关 `OPEN_SOURCE_ONLY=False` → 回到旧行为。

全离线：httpx 全部打桩、图谱用 tmp_path，不碰真网与真实 data/。
"""
from types import SimpleNamespace

import pytest

from app.core import open_source
from app.core.agent_tools import web_archive as wa
from app.core.agent_tools.tools import download_resource as download_tool
from app.core.agent_tools.tools import fetch_webpage as fw
from app.core.knowledge_graph import KnowledgeGraph

USER = 1
# 未登记域名：fail-closed → 判定为 L3（不可留存）
UNKNOWN = "https://random-blog.xyz/post/1"
BODY = "二分查找在有序数组中每次折半"
PAGE_PLAIN = (
    "<html><head><title>未知来源</title></head><body>"
    f"<h1>二分查找</h1><p>{BODY}。</p><p>时间复杂度为对数级。</p></body></html>"
)
PAGE_CC = (
    "<html><head><title>开放教程</title></head><body>"
    f"<h1>二分查找</h1><p>{BODY}。</p><p>时间复杂度为对数级。</p>"
    '<footer><a rel="license" href="https://creativecommons.org/licenses/by-sa/4.0/">'
    "CC BY-SA 4.0</a></footer></body></html>"
)


@pytest.fixture
def kg(tmp_path):
    g = KnowledgeGraph(user_id=USER, data_dir=tmp_path)
    with g._conn:  # nodes.user_id 是外键，先备 users 行
        g._conn.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash)"
            " VALUES (?, 'o', 'x')", (USER,))
    yield g
    g.close()


class _FakeResp:
    def __init__(self, text):
        self.status_code = 200
        self.text = text
        self.headers = {"content-type": "text/html; charset=utf-8"}


class _FakeClient:
    """httpx.Client 替身：只实现被测路径用到的那一小块"""

    def __init__(self, *a, **kw):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url):
        raise AssertionError(f"不该发起请求: {url}")


def _client_returning(text):
    class _C(_FakeClient):
        def get(self, url):
            return _FakeResp(text)

    return _C


# ── 1. 存档：URL 预筛（不发请求）──────────────────────────────

def test_fetch_and_archive_skips_non_open_without_request(kg, monkeypatch):
    monkeypatch.setattr(wa, "is_blocked_url", lambda url: None)
    monkeypatch.setattr(wa.httpx, "Client", _FakeClient)

    assert wa.fetch_and_archive(kg, UNKNOWN) is None
    assert kg.nodes == []


def test_archive_search_results_skips_non_open_without_request(kg, monkeypatch):
    """联网搜索的结果页同样在发请求前就被拦掉"""
    monkeypatch.setattr(wa, "is_blocked_url", lambda url: None)
    monkeypatch.setattr(wa.httpx, "Client", _FakeClient)
    text = f"搜索「二分查找」共 1 条结果：\n[1] t\n    链接: {UNKNOWN}\n    摘要: …\n"

    assert wa.archive_search_results(text, kg) == 0
    assert kg.nodes == []


# ── 2. 存档：页面许可声明复核 ─────────────────────────────────

def test_archive_html_allows_page_declaring_open_license(kg):
    """未登记域名，但页面明确声明开放许可 → 放行（不靠人肉维护白名单）"""
    msg = wa.archive_html(kg, UNKNOWN, PAGE_CC)

    assert msg and "已存档" in msg
    assert len(kg.nodes) == 1


def test_archive_html_skips_page_without_declaration(kg):
    assert wa.archive_html(kg, UNKNOWN, PAGE_PLAIN) is None
    assert kg.nodes == []


# ── 3. fetch_webpage：只读不改、但不留存 ──────────────────────

def test_fetch_webpage_returns_text_but_archives_nothing(kg, monkeypatch):
    monkeypatch.setattr(fw, "is_blocked_url", lambda url: None)
    monkeypatch.setattr(fw.httpx, "Client", _client_returning(PAGE_PLAIN))

    text = fw.fetch_webpage(UNKNOWN, max_chars=3000, kg=kg)

    assert BODY in text      # 既有「只读查看」语义不变（非开源仍可看）
    assert kg.nodes == []    # 但零留存（不算采集）


def test_fetch_webpage_archives_page_declaring_open_license(kg, monkeypatch):
    monkeypatch.setattr(fw, "is_blocked_url", lambda url: None)
    monkeypatch.setattr(fw.httpx, "Client", _client_returning(PAGE_CC))

    fw.fetch_webpage(UNKNOWN, max_chars=3000, kg=kg)

    assert len(kg.nodes) == 1


# ── 4. download_resource：fail-closed ─────────────────────────

def test_download_resource_refuses_non_open(monkeypatch):
    monkeypatch.setattr(download_tool, "is_blocked_url", lambda url: None)
    monkeypatch.setattr(download_tool, "httpx", SimpleNamespace(
        Client=_FakeClient, HTTPError=Exception, TimeoutException=Exception))
    saved: dict = {}
    monkeypatch.setattr(download_tool, "_save_to_kb",
                        lambda *a, **kw: saved.update(called=True))

    out = download_tool.download_resource(UNKNOWN, user_id=USER)

    assert "非开放许可" in out
    assert saved == {}       # 既没下载也没入库


# ── 5. 总开关关闭 → 旧行为 ────────────────────────────────────

def test_switch_off_restores_old_archive_behavior(kg, monkeypatch):
    monkeypatch.setattr(open_source, "OPEN_SOURCE_ONLY", False)
    monkeypatch.setattr(wa, "is_blocked_url", lambda url: None)
    monkeypatch.setattr(wa.httpx, "Client", _client_returning(PAGE_PLAIN))

    assert wa.fetch_and_archive(kg, UNKNOWN) is not None
    assert len(kg.nodes) == 1
