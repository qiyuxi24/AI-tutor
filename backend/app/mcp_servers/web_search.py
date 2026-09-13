"""网页搜索 MCP server —— 标准 MCP 协议，可独立运行也可被本项目内嵌（2026-09-12）。

独立运行（stdio，挂到 Claude Desktop / Cursor 等任意 MCP 宿主）：
    cd backend && venv/Scripts/python.exe -m app.mcp_servers.web_search
远程运行（Streamable HTTP，供容器 / 跨进程宿主调用）：
    cd backend && venv/Scripts/python.exe -m app.mcp_servers.web_search \
        --transport streamable-http --port 8100
本项目内则由 core/mcp_host.py 以 **in-memory** 方式连接（同进程、零端口，仍走协议层）。

搜索后端（全开源、零 API key）：
- 默认 ddgs（MIT）。实测（2026-09-12，本机网络）：'auto' 与 'bing' 可用，返回中文结果
  1~2 秒；但显式多后端组合（如 'bing,mojeek'）会因单个后端失败而**整体抛异常**，
  故降级链由本模块自己串（auto → bing）。
- 环境变量 SEARXNG_URL 配了则优先用自建 SearXNG（AGPL；需在其 settings.yml 中
  开启 JSON 格式：search.formats 加 json）。
"""

import sys

from mcp.server import MCPServer

from app.core.config import settings

DEFAULT_MAX_RESULTS = 5
MAX_RESULTS_LIMIT = 10
TIMEOUT_SECONDS = 10  # ponytail: 需按网络调优时再提为配置项
_DDGS_BACKENDS = ("auto", "bing")  # 降级链，第一个有结果的胜出

mcp = MCPServer("web-search")


def _search_ddgs(query: str, max_results: int) -> list[dict]:
    """ddgs 搜索（MIT）。逐后端降级，全败才报错。"""
    from ddgs import DDGS

    errors = []
    for backend in _DDGS_BACKENDS:
        try:
            rows = DDGS(timeout=TIMEOUT_SECONDS).text(
                query, backend=backend, max_results=max_results
            )
            if rows:
                return rows
            errors.append(f"{backend}: 无结果")
        except Exception as e:  # 单个后端失败不该拖垮整次搜索
            errors.append(f"{backend}: {e}")
    raise RuntimeError("；".join(errors))


def _search_searxng(query: str, max_results: int) -> list[dict]:
    """自建 SearXNG JSON API（AGPL）。返回结构统一为 ddgs 的 title/href/body。"""
    import httpx

    resp = httpx.get(
        f"{settings.searxng_url.rstrip('/')}/search",
        params={"q": query, "format": "json", "language": "zh-CN"},
        timeout=TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    rows = resp.json().get("results", [])[:max_results]
    return [
        {"title": r.get("title", ""), "href": r.get("url", ""), "body": r.get("content", "")}
        for r in rows
    ]


def _format(query: str, rows: list[dict]) -> str:
    lines = [f"搜索「{query}」共 {len(rows)} 条结果："]
    for i, r in enumerate(rows, 1):
        body = " ".join((r.get("body") or "").split())[:300]
        lines.append(
            f"[{i}] {(r.get('title') or '').strip()}\n"
            f"    链接: {r.get('href', '')}\n"
            f"    摘要: {body}"
        )
    lines.append("\n（回答时请标注来源链接，便于学生核查。）")
    return "\n".join(lines)


@mcp.tool()
def web_search(query: str, max_results: int = DEFAULT_MAX_RESULTS) -> str:
    """在互联网上搜索网页，返回标题、链接与摘要。

    当需要最新信息、外部资料、或本地知识库/图谱中没有的内容时使用。
    返回结果含来源链接，回答时应标注出处。
    """
    query = (query or "").strip()
    if not query:
        return "搜索失败：关键词为空。"
    try:
        limit = max(1, min(int(max_results or DEFAULT_MAX_RESULTS), MAX_RESULTS_LIMIT))
    except (TypeError, ValueError):
        limit = DEFAULT_MAX_RESULTS

    try:
        rows = (
            _search_searxng(query, limit)
            if settings.searxng_url
            else _search_ddgs(query, limit)
        )
    except Exception as e:  # 工具返回友好文本（本项目约定：不把异常抛给模型）
        return f"搜索失败: {e}。请基于已有知识回答，或稍后再试。"

    if not rows:
        return f"未搜索到与「{query}」相关的结果，请换个关键词或基于已有知识回答。"
    return _format(query, rows)


def main() -> None:
    argv = sys.argv[1:]
    transport = argv[argv.index("--transport") + 1] if "--transport" in argv else "stdio"
    kwargs = {}
    if transport == "streamable-http":
        kwargs = {
            "host": "127.0.0.1",
            "port": int(argv[argv.index("--port") + 1]) if "--port" in argv else 8100,
        }
    mcp.run(transport=transport, **kwargs)


if __name__ == "__main__":
    main()
