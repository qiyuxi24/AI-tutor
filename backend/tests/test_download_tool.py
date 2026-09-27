"""资源下载工具 download_tool 离线测试。

覆盖：SSRF 拒绝 / HTTP 状态 / 体积上限（预检 + 流式）/ 格式白名单 /
文件名推断（Content-Disposition 与 content-type）/ 入库落点与目录 /
工具注册与分发。全程打桩 httpx 与入库函数：不发真实网络请求、不碰 embedding。
"""
from types import SimpleNamespace

import httpx
import pytest

from app.core import agent_tools
from app.core import open_source
from app.core.agent_tools.tools import download_resource as download_tool


# ────────────────────────────────────────────
#  打桩：httpx 客户端 + 入库函数（保持测试离线）
# ────────────────────────────────────────────

class _FakeResponse:
    def __init__(self, status: int, headers: dict, body: bytes):
        self.status_code = status
        self.headers = httpx.Headers(headers)
        self._body = body

    def iter_bytes(self, chunk_size: int = 65536):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i:i + chunk_size]


class _FakeStream:
    def __init__(self, resp: _FakeResponse):
        self._resp = resp

    def __enter__(self):
        return self._resp

    def __exit__(self, *exc):
        return False


class _FakeClient:
    def __init__(self, status: int, headers: dict, body: bytes, exc=None):
        self._status, self._headers, self._body, self._exc = status, headers, body, exc

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def stream(self, method: str, url: str):
        if self._exc is not None:
            raise self._exc
        return _FakeStream(_FakeResponse(self._status, self._headers, self._body))


def _install(monkeypatch, *, status: int = 200, headers: dict | None = None,
             body: bytes = b"", exc=None, allow_ssrf: bool = True):
    """打入假 httpx 命名空间；默认同时放行 SSRF 校验（避免测试依赖 DNS）。"""
    if allow_ssrf:
        monkeypatch.setattr(download_tool, "is_blocked_url", lambda url: None)
    # 夹具域名列为开放来源：默认策略是 fail-closed，未登记域名会先被拦掉，测不到下载链路本身
    for host in ("example.com", "e.com", "gutenberg.org"):
        monkeypatch.setitem(open_source.SITE_LEVELS, host, "L0")
    monkeypatch.setattr(download_tool, "httpx", SimpleNamespace(
        Client=lambda **kw: _FakeClient(status, headers or {}, body, exc),
        Headers=httpx.Headers,
        HTTPError=httpx.HTTPError,
        TimeoutException=httpx.TimeoutException,
    ))


def _install_save(monkeypatch) -> dict:
    """替掉入库函数，返回可断言的记录字典。"""
    saved: dict = {}

    async def _fake_save(user_id, filename, content, subject=""):
        saved.update(user_id=user_id, filename=filename, content=content, subject=subject)
        return 42

    monkeypatch.setattr(download_tool, "_save_to_kb", _fake_save)
    return saved


# ────────────────────────────────────────────
#  安全 / 失败路径
# ────────────────────────────────────────────

def test_rejects_internal_url():
    """SSRF：内网/回环地址直接拒绝，不发起下载。"""
    out = download_tool.download_resource("http://127.0.0.1:8080/book.epub", user_id=1)
    assert "无法下载" in out


def test_rejects_non_http_scheme():
    """非 http/https 协议拒绝（file:// 等）。"""
    out = download_tool.download_resource("file:///etc/passwd", user_id=1)
    assert "无法下载" in out


def test_http_error_status(monkeypatch):
    _install(monkeypatch, status=404, body=b"")
    out = download_tool.download_resource("https://example.com/a.epub", user_id=1)
    assert "404" in out


def test_content_length_over_limit(monkeypatch):
    """content-length 预检超限：不读 body 直接拒绝。"""
    _install(monkeypatch, headers={"content-length": str(60 * 1024 * 1024)},
             body=b"x" * 100)
    out = download_tool.download_resource("https://example.com/big.epub", user_id=1,
                                          max_mb=50)
    assert "过大" in out


def test_stream_over_limit(monkeypatch):
    """无 content-length 时按流式累计中止。"""
    _install(monkeypatch, body=b"x" * (1024 * 1024 + 1))
    out = download_tool.download_resource("https://example.com/big.epub", user_id=1,
                                          max_mb=1)
    assert "上限" in out


def test_timeout(monkeypatch):
    _install(monkeypatch, exc=httpx.TimeoutException("boom"))
    out = download_tool.download_resource("https://example.com/a.epub", user_id=1)
    assert "下载超时" in out


def test_unsupported_format_not_saved(monkeypatch):
    """格式不受支持：明确提示，且不调用入库。"""
    saved = _install_save(monkeypatch)
    _install(monkeypatch, headers={"content-type": "application/octet-stream"},
             body=b"x" * 3000)
    out = download_tool.download_resource("https://example.com/setup.exe", user_id=1)
    assert "格式无法入库" in out
    assert saved == {}


def test_empty_body(monkeypatch):
    _install(monkeypatch, body=b"")
    out = download_tool.download_resource("https://example.com/a.epub", user_id=1)
    assert "内容为空" in out


# ────────────────────────────────────────────
#  文件名推断
# ────────────────────────────────────────────

def test_resolve_filename_prefers_content_disposition():
    h = httpx.Headers({"content-disposition": 'attachment; filename="pg1342.epub"',
                       "content-type": "application/octet-stream"})
    assert download_tool._resolve_filename("https://e.com/download?id=7", h) == "pg1342.epub"


def test_resolve_filename_uses_content_type_without_ext():
    h = httpx.Headers({"content-type": "application/epub+zip"})
    assert download_tool._resolve_filename("https://e.com/get?id=7", h) == "get.epub"


def test_resolve_filename_strips_path_separators():
    h = httpx.Headers({"content-type": "text/plain"})
    name = download_tool._resolve_filename("https://e.com/a/..%2Fevil.txt", h)
    assert "/" not in name and "\\" not in name


# ────────────────────────────────────────────
#  成功路径
# ────────────────────────────────────────────

def test_download_saves_to_kb(monkeypatch):
    """成功：按 title 命名 + 学科参数透传 + 返回节点 ID 与目录。"""
    saved = _install_save(monkeypatch)
    body = b"epub-bytes" * 500
    _install(monkeypatch, headers={"content-type": "application/epub+zip"},
             body=body)

    out = download_tool.download_resource(
        "https://www.gutenberg.org/cache/epub/1342/pg1342.epub",
        title="傲慢与偏见", subject="英语", user_id=7)

    assert saved["filename"] == "傲慢与偏见.epub"
    assert saved["content"] == body
    assert saved["subject"] == "英语"
    assert saved["user_id"] == 7
    assert "42" in out and "AI 下载/英语" in out


def test_download_without_title_keeps_url_filename(monkeypatch):
    saved = _install_save(monkeypatch)
    _install(monkeypatch, headers={"content-type": "application/epub+zip"}, body=b"x" * 500)

    download_tool.download_resource("https://e.com/books/pg1342.epub", user_id=7)
    assert saved["filename"] == "pg1342.epub"
    assert "AI 下载" in download_tool.download_resource(
        "https://e.com/books/pg1342.epub", user_id=7)


def test_missing_user_id_not_saved(monkeypatch):
    saved = _install_save(monkeypatch)
    _install(monkeypatch, headers={"content-type": "application/epub+zip"}, body=b"x" * 500)
    out = download_tool.download_resource("https://e.com/a.epub", user_id=None)
    assert "无法确定用户上下文" in out
    assert saved == {}


def test_save_value_error_reported(monkeypatch):
    """入库业务错（解析文本过短等）→ 友好回填，不抛异常。"""
    async def _boom(*a, **kw):
        raise ValueError("文档可提取文本过短")

    monkeypatch.setattr(download_tool, "_save_to_kb", _boom)
    _install(monkeypatch, headers={"content-type": "application/epub+zip"}, body=b"x" * 500)

    out = download_tool.download_resource("https://e.com/a.epub", user_id=1)
    assert "入库失败" in out and "文本过短" in out


# ────────────────────────────────────────────
#  工具注册与分发
# ────────────────────────────────────────────

def test_tool_registered_and_dispatched(monkeypatch):
    names = [s["name"] for s in agent_tools._TOOL_SPECS]
    assert "download_resource" in names
    assert "download_resource" in [t["function"]["name"] for t in agent_tools.KG_TOOLS]

    called: dict = {}
    # handler 在 tools/download_resource.py 里解析 download_resource 模块全局名，故 patch 到该模块
    monkeypatch.setattr(download_tool, "download_resource",
                        lambda url, **kw: called.update(url=url, **kw) or "已下载")

    tc = SimpleNamespace(function=SimpleNamespace(
        name="download_resource",
        arguments='{"url": "https://e.com/a.epub", "title": "书"}'))
    out = agent_tools.execute_kg_tool(tc, SimpleNamespace(user_id=3))

    assert out == "已下载"
    assert called == {"url": "https://e.com/a.epub", "title": "书",
                      "subject": "", "user_id": 3}


@pytest.mark.parametrize("bad_args", ['{"title": "无 URL"}', "not-json"])
def test_dispatch_bad_args(monkeypatch, bad_args):
    """缺 url / 参数非 JSON：回填错误文本，不抛异常。"""
    tc = SimpleNamespace(function=SimpleNamespace(name="download_resource",
                                                  arguments=bad_args))
    out = agent_tools.execute_kg_tool(tc, SimpleNamespace(user_id=1))
    assert out  # 有内容即视为已捕获（具体文案由 key/JSON 错误决定）
