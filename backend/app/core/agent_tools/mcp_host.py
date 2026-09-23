"""MCP 宿主层：把 MCP server 的工具并入 agent 工具表（2026-09-12）。

职责边界：`registry._TOOL_SPECS` 仍是**唯一注册入口**，本模块只做三件事 ——
「连 MCP server → list_tools → 生成同构 spec（handler 为同步桥）」。连接失败或
未启用时返回 `[]`，原生工具不受影响（不阻断启动）。

连接形态：内置 web_search server 走 **in-memory** 传输（同进程、零子进程、零端口，
但仍经过完整 MCP 协议层）。换远程 server 只需把 `_server()` 改返回 URL 字符串。
实测（2026-09-12 PoC）：in-memory 会话在 `asyncio.to_thread` 线程 + `asyncio.run`
新事件循环下可正常 list_tools / call_tool，故执行侧无需常驻连接。

新增一个 MCP server 的成本 = 在 `_SERVERS` 加一项（工具名前缀 + server 工厂）。

存档副作用（2026-09-22）：`web_search` 返回成功后，后台线程把**结果页正文**抓下来转 Markdown，
存成知识图谱节点（可在图谱里双击查看）。理由：搜索只返回标题/链接/摘要，不存档等于"搜完没留下
东西"；实现与失败语义见 `web_archive.py`（静默、不阻塞本轮回答、单页失败不影响其它页）。
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

from app.core.config import settings
from app.core.error_codes import ErrorCode, log_error

logger = logging.getLogger("ai-tutor")

# 同步→异步桥：与 impl/rag._run_async / kg_taxonomy 同一约定（各持单例池互不干扰）
_POOL = ThreadPoolExecutor(max_workers=2)

# 已挂载的 MCP server：(工具名前缀, server 工厂)。前缀用业界 mcp__<server>__<tool> 惯例
_SERVERS = (("mcp__websearch__", "app.mcp_servers.web_search"),)

_specs_cache: list[dict] | None = None


def _run_async(coro):
    """在同步上下文里跑协程（当前线程无事件循环则直接 run，否则丢线程池）。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    return _POOL.submit(asyncio.run, coro).result()


def _load_server(dotted: str):
    """按 `app.mcp_servers.x` 路径取模块级 `mcp` 实例（惰性导入：未装 mcp 包时不炸）。"""
    import importlib

    return importlib.import_module(dotted).mcp


async def _list_tools(server):
    from mcp import Client

    async with Client(server) as client:
        return await client.list_tools()


async def _call_tool(server, name: str, args: dict):
    from mcp import Client

    async with Client(server) as client:
        return await client.call_tool(name, args)


def _make_handler(spec_name: str, tool_name: str, server_ref: dict):
    def _handler(args: dict, kg) -> str:
        return run_mcp_tool(spec_name, tool_name, server_ref, args, kg=kg)

    return _handler


def _schedule_search_archive(text: str, kg) -> None:
    """联网搜索成功后，后台把结果页正文存档为图谱节点（2026-09-22）。

    为什么在这里：搜索只返回标题/链接/摘要，**没有正文** —— 不存档就等于"搜了没留下东西"。
    存档不依赖模型是否再调 `fetch_webpage`；实现见 `web_archive.py`（失败静默、不阻塞本轮回答）。
    """
    from . import web_archive

    try:
        web_archive.schedule_archive_from_search(text, getattr(kg, "user_id", None))
    except Exception as e:  # 存档是副作用，绝不能影响搜索结果的返回
        logger.warning("结果页存档调度失败: %s", e)


def run_mcp_tool(spec_name: str, tool_name: str, server_ref: dict, args: dict,
                 kg=None) -> str:
    """同步调用 MCP 工具并取回文本（失败返回友好文本，符合本项目工具约定）。

    `kg` 仅用于"搜索后自动存档结果页"（None 时保持纯查询语义，既有调用方零改动）。
    """
    try:
        result = _run_async(_call_tool(server_ref["server"], tool_name, args))
    except Exception as e:
        log_error(ErrorCode.WEB_SEARCH_FAILED, detail=f"{spec_name}: {e}",
                  exception=e, context={"tool": tool_name})
        return f"搜索失败: {e}。请基于已有知识回答，或稍后再试。"

    text = "\n".join(
        getattr(item, "text", "") for item in getattr(result, "content", []) or []
    ).strip()
    if getattr(result, "is_error", False):
        log_error(ErrorCode.WEB_SEARCH_FAILED, detail=f"{spec_name}: {text}",
                  context={"tool": tool_name})
        return f"搜索失败: {text}"

    if kg is not None and spec_name.startswith("mcp__websearch__"):
        _schedule_search_archive(text, kg)
    return text or "搜索无结果。"


def mcp_tool_specs() -> list[dict]:
    """生成与 `_TOOL_SPECS` 同构的 spec 列表。

    schema 直接复用 MCP `tools/list` 的 inputSchema —— 宿主侧不重复维护一份必然
    漂移的 JSON Schema（server 改了自动跟随）。结果缓存，仅首次调用建连。
    """
    global _specs_cache
    if _specs_cache is not None:
        return _specs_cache

    if not settings.web_search_enabled:
        _specs_cache = []
        return _specs_cache

    specs: list[dict] = []
    for prefix, dotted in _SERVERS:
        try:
            server = _load_server(dotted)
            listed = _run_async(_list_tools(server))
        except Exception as e:  # 单个 server 失败不影响其它 server / 原生工具
            log_error(ErrorCode.WEB_SEARCH_FAILED, detail=f"MCP server 加载失败 {dotted}: {e}",
                      exception=e, context={"server": dotted})
            continue
        ref = {"server": server}
        for t in listed.tools:
            name = prefix + t.name
            specs.append({
                "name": name,
                "description": t.description or "",
                "parameters": t.input_schema,  # MCP 字段名 inputSchema 的 pydantic 侧属性
                "handler": _make_handler(name, t.name, ref),
            })
    _specs_cache = specs
    return _specs_cache
