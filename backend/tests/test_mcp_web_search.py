"""网页搜索 MCP 工具测试（2026-09-12）—— 离线，mock 搜索后端，不打网络。

覆盖三层：
1. MCP server 本体（后端成功/失败/空结果/参数钳制）
2. MCP 协议层（in-memory Client 真实 tools/call 往返）
3. 宿主层接入（工具表并入、execute_kg_tool 路由、开关降级、加载失败降级）
"""
import json
from types import SimpleNamespace

from app.core import mcp_host
from app.mcp_servers import web_search as ws

TOOL = "mcp__websearch__web_search"


def _tool_call(name, args):
    return SimpleNamespace(function=SimpleNamespace(name=name, arguments=json.dumps(args)))


def _fake_rows(*_, **__):
    return [{"title": "标题", "href": "https://example.com/a", "body": "摘要内容"}]


# ── 1. MCP server 本体 ──

def test_search_success_formats_sources(monkeypatch):
    monkeypatch.setattr(ws, "_search_ddgs", _fake_rows)
    out = ws.web_search("关键词")
    assert "https://example.com/a" in out and "标题" in out and "摘要内容" in out


def test_backend_failure_returns_friendly_text(monkeypatch):
    def boom(query, max_results):
        raise RuntimeError("网络不可用")
    monkeypatch.setattr(ws, "_search_ddgs", boom)
    out = ws.web_search("关键词")
    assert out.startswith("搜索失败") and "网络不可用" in out


def test_empty_query_and_empty_result(monkeypatch):
    assert "关键词为空" in ws.web_search("   ")
    monkeypatch.setattr(ws, "_search_ddgs", lambda query, max_results: [])
    assert "未搜索到" in ws.web_search("关键词")


def test_max_results_clamped(monkeypatch):
    seen = {}

    def fake(query, max_results):
        seen["n"] = max_results
        return _fake_rows()

    monkeypatch.setattr(ws, "_search_ddgs", fake)
    ws.web_search("关键词", max_results=999)
    assert seen["n"] == ws.MAX_RESULTS_LIMIT


def test_ddgs_backend_falls_back_on_first_failure(monkeypatch):
    """降级链：首个后端抛异常时应继续试下一个（实测多后端组合会整体拉挂）。"""
    import ddgs

    calls = []

    class FakeDDGS:
        def __init__(self, **kwargs):
            pass

        def text(self, query, backend=None, max_results=None):
            calls.append(backend)
            if backend == "auto":
                raise RuntimeError("auto 挂了")
            return _fake_rows()

    monkeypatch.setattr(ddgs, "DDGS", FakeDDGS)
    rows = ws._search_ddgs("关键词", 3)
    assert calls == ["auto", "bing"] and rows


def test_searxng_backend_used_when_configured(monkeypatch):
    """配了 SEARXNG_URL 时走自建实例，不碰 ddgs。"""
    import httpx

    monkeypatch.setattr(ws.settings, "searxng_url", "http://searx.local:8888")

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"results": [{"title": "T", "url": "https://s.example", "content": "C"}]}

    monkeypatch.setattr(httpx, "get", lambda url, **kw: FakeResp())
    assert "https://s.example" in ws.web_search("关键词")


# ── 2. MCP 协议层（in-memory，真实往返）──

def test_protocol_call_via_in_memory_client(monkeypatch):
    monkeypatch.setattr(ws, "_search_ddgs", _fake_rows)
    result = mcp_host._run_async(mcp_host._call_tool(ws.mcp, "web_search", {"query": "x"}))
    assert "https://example.com/a" in result.content[0].text
    assert not result.is_error


def test_protocol_missing_required_arg_is_error():
    """缺必填参数应由 MCP schema 校验拦下（工具内不会拿到非法参数）。"""
    result = mcp_host._run_async(mcp_host._call_tool(ws.mcp, "web_search", {}))
    assert result.is_error


# ── 3. 宿主层接入 ──

def test_tool_merged_into_agent_tools():
    from app.core.agent_tools import KG_TOOLS

    spec = next(t for t in KG_TOOLS if t["function"]["name"] == TOOL)
    assert spec["function"]["parameters"]["required"] == ["query"]
    assert spec["function"]["description"]


def test_execute_kg_tool_routes_to_mcp(monkeypatch):
    from app.core.agent_tools import execute_kg_tool

    monkeypatch.setattr(ws, "_search_ddgs", _fake_rows)
    out = execute_kg_tool(_tool_call(TOOL, {"query": "x"}), None)
    assert "https://example.com/a" in out


def test_disabled_flag_hides_mcp_tools(monkeypatch):
    monkeypatch.setattr(mcp_host.settings, "web_search_enabled", False)
    monkeypatch.setattr(mcp_host, "_specs_cache", None)
    assert mcp_host.mcp_tool_specs() == []


def test_server_load_failure_degrades_to_empty(monkeypatch):
    monkeypatch.setattr(mcp_host, "_specs_cache", None)
    monkeypatch.setattr(mcp_host, "_SERVERS", (("mcp__broken__", "app.mcp_servers.nope"),))
    assert mcp_host.mcp_tool_specs() == []
