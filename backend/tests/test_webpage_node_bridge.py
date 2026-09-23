"""`fetch_webpage` 抓到的网页 → 图谱节点（origin="web"）回归（2026-09-22）。

被测三层：
1. `knowledge_writer.create_node_from_webpage`：确定性 ID / 幂等 / 跨用户不撞全局主键 /
   正文与来源标注落盘 / 超长截断；
2. `fetch_webpage` 端到端：抓取返回文本**保持既有纯文本语义**，同时把网页存档为图谱节点；
3. 失败隔离：图谱写库炸 / 正文抽取失败 → 工具返回不受影响、不建半成品节点。

全离线：`httpx.Client` 换成假客户端（不碰真网），图谱用 tmp_path（不碰真 data/ 目录）。
"""
import hashlib

import pytest

from app.core import knowledge_writer as kw
from app.core import open_source
from app.core.agent_tools.tools import fetch_webpage as fw
from app.core.knowledge_graph import KnowledgeGraph

USER = 1
URL = "https://example.com/lesson/1"
BODY = "二分查找在有序数组中每次折半"
PAGE_HTML = (
    "<html><head><title>二分查找入门</title></head><body>"
    "<nav>导航噪声</nav><h1>二分查找</h1>"
    f"<p>{BODY}。</p><ul><li>时间复杂度 O(log n)</li></ul>"
    "<footer>版权所有</footer></body></html>"
)


def _web_node_id(user_id: int = USER, url: str = URL) -> str:
    return f"web_{user_id}_{hashlib.sha1(url.encode('utf-8')).hexdigest()[:12]}"


def _md(kg: KnowledgeGraph, node_id: str) -> str:
    return (kg.nodes_dir / f"{node_id}.md").read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _open_test_domain(monkeypatch):
    """夹具域名列为开放来源：默认策略是 fail-closed，未登记域名不会被存档（见 core/open_source.py）"""
    monkeypatch.setitem(open_source.SITE_LEVELS, "example.com", "L0")


@pytest.fixture
def kg(tmp_path):
    g = KnowledgeGraph(user_id=USER, data_dir=tmp_path)
    with g._conn:  # nodes.user_id 是外键，先备 users 行
        g._conn.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash)"
            " VALUES (?, 'w', 'x')", (USER,))
    yield g
    g.close()


class _FakeResp:
    status_code = 200
    headers = {"content-type": "text/html; charset=utf-8"}

    def __init__(self, text: str):
        self.text = text


class _FakeClient:
    """httpx.Client 替身：只实现 fetch_webpage 用到的那一小块"""

    def __init__(self, *a, **kw):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url):
        return _FakeResp(PAGE_HTML)


@pytest.fixture
def offline_http(monkeypatch):
    """让 fetch_webpage 拿到固定页面（不碰真网）"""
    monkeypatch.setattr(fw.httpx, "Client", _FakeClient)


# ── 1. 写路径：create_node_from_webpage ──────────────────────

def test_archive_creates_node_and_md(kg):
    """正常存档：确定性 ID、字段齐、MD 带来源标注"""
    msg = kw.create_node_from_webpage(kg, URL, title="二分查找入门",
                                      content="要点：折半查找")

    nid = _web_node_id()
    node = kg.get_node(nid)
    assert node is not None
    assert node["name"] == "二分查找入门"
    assert node["summary"] == f"来源：{URL}"
    assert node["tags"] == ["网页", "example.com"]
    assert node["added_by"] == "ai"
    assert nid in msg

    md = _md(kg, nid)
    assert md.startswith("# 二分查找入门")
    assert "> 由 AI 联网抓取" in md
    assert "折半查找" in md


def test_archive_keeps_full_markdown_verbatim(kg):
    """正文已是完整文档（以 # 开头）→ 原样落盘、不再套模板（既有契约）"""
    doc = "# 二分查找\n\n> 网页原文\n\n正文"
    kw.create_node_from_webpage(kg, URL, title="二分查找入门", content=doc)

    assert _md(kg, _web_node_id()) == doc


def test_archive_is_idempotent(kg):
    """同一 URL 第二次抓取：跳过、不重写 MD（不覆盖、不追加）"""
    kw.create_node_from_webpage(kg, URL, title="第一次", content="第一次正文")
    before = _md(kg, _web_node_id())

    msg = kw.create_node_from_webpage(kg, URL, title="第二次", content="第二次正文")

    assert _md(kg, _web_node_id()) == before
    assert "未重复写入" in msg
    assert [n["id"] for n in kg.nodes] == [_web_node_id()]


def test_same_url_two_users_get_distinct_ids(tmp_path):
    """nodes.id 是全局主键 → 同 URL 不同用户必须落到不同 ID，不能互相撞"""
    g1 = KnowledgeGraph(user_id=1, data_dir=tmp_path)
    g2 = KnowledgeGraph(user_id=2, data_dir=tmp_path)
    with g1._conn:
        g1._conn.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash)"
            " VALUES (1, 'a', 'x'), (2, 'b', 'x')")
    try:
        kw.create_node_from_webpage(g1, URL, title="同一页", content="正文")
        kw.create_node_from_webpage(g2, URL, title="同一页", content="正文")

        assert g1.get_node(_web_node_id(1)) is not None
        assert g2.get_node(_web_node_id(2)) is not None
        assert _web_node_id(1) != _web_node_id(2)
    finally:
        g1.close()
        g2.close()


def test_archive_truncates_overlong_content(kg):
    """超长网页：正文截断到上限并注明（防节点 MD 撑到数 MB）"""
    kw.create_node_from_webpage(kg, URL, title="长文",
                                content="x" * (kw._WEB_NODE_MAX_CHARS + 500))

    md = _md(kg, _web_node_id())
    assert "网页正文过长，已截断" in md
    assert len(md) < kw._WEB_NODE_MAX_CHARS + 500


def test_archive_without_url_does_nothing(kg):
    """无 URL（幂等键缺失）→ 不建节点，只回文案"""
    msg = kw.create_node_from_webpage(kg, "  ", title="x", content="正文")

    assert "未存档" in msg
    assert kg.nodes == []


# ── 2. 端到端：fetch_webpage 抓取后存档 ──────────────────────

def test_fetch_archives_md_and_keeps_plain_text(offline_http, kg):
    """既有语义（返回纯文本）不变，同时把网页存档为图谱节点"""
    text = fw.fetch_webpage(URL, max_chars=3000, kg=kg)

    # 既有：返回剥离标签/噪声后的纯文本
    assert BODY in text
    assert "<p>" not in text and "导航噪声" not in text and "版权所有" not in text

    # 增量：同一网页已存档为图谱节点
    assert kg.get_node(_web_node_id()) is not None
    assert BODY in _md(kg, _web_node_id())


def test_fetch_without_kg_touches_nothing(offline_http, kg):
    """不传 kg（既有调用方 / 测试）→ 行为与改动前一致，不写图谱"""
    text = fw.fetch_webpage(URL, max_chars=3000)

    assert BODY in text
    assert kg.nodes == []


def test_fetch_survives_graph_write_failure(offline_http, kg, monkeypatch):
    """图谱写库抛异常 → 工具仍正常返回文本，且不留半成品节点"""
    def _boom(self, *a, **kw):
        raise RuntimeError("图谱写库炸了")

    monkeypatch.setattr(KnowledgeGraph, "create_node_with_content", _boom)

    text = fw.fetch_webpage(URL, max_chars=3000, kg=kg)

    assert BODY in text
    assert kg.get_node(_web_node_id()) is None


def test_fetch_skips_archive_when_extract_fails(offline_http, kg, monkeypatch):
    """正文抽取双失败（拿不到 Markdown）→ 不建节点，返回文本不受影响"""
    import app.core.collector.adapters.web_page as wp

    monkeypatch.setattr(wp, "extract_main_text", lambda html: None)

    text = fw.fetch_webpage(URL, max_chars=3000, kg=kg)

    assert BODY in text
    assert kg.nodes == []
