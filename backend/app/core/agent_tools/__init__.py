"""Agent 工具系统门面 —— 对外 API 稳定（模块可重构，这些名字不变）。

「工具」这件事全部在本目录，别处不再有工具文件：

    registry.py   ① 注册表中间件：spec 契约 / 注册 / `KG_TOOLS` / 提示词段落生成（零依赖）
    dispatch.py   ② 调用中间件：参数解析 → 错误映射 → 同步/异步双入口执行
    tools/        ③ 15 个原生工具，**一个工具一个模块**（spec + handler 自包含）→ tools/README.md
    mcp_host.py   ④ MCP 适配器：从 MCP server 的 `tools/list` 取回同构 spec
    README.md     机制说明（改架构前必读）

SSRF 防护（`is_blocked_url`）**不在本目录**：2026-10-26 上移到 `core/net_guard.py` ——
它被采集适配器 / KB 导入 / `/llm/models` 共用，放在工具包内会让「谁 import 谁」成环。

组装：原生工具（`tools.NATIVE_SPECS`，导入即注册）+ MCP 工具（`mcp_tool_specs()`，
**首次需要 `KG_TOOLS` / `TOOLS_PROMPT` / `all_specs()` 时才注册**）。
MCP 未启用 / 连接失败时 `mcp_tool_specs()` 返回 []，原生工具行为完全不变。

为什么 MCP 那半是惰性的（2026-10-26，P2-4）：`mcp_tool_specs()` 会 import
`app.mcp_servers.web_search` → 连带 `mcp` SDK（实测 1.27s）。外部宿主的只读网关
（`gateway.py`）只读 `registry.all_specs()` 里的 3 个原生工具，不该为它付这笔钱；
主系统（`agent/loop.py` / `chat_service.py`）在导入时就取 `KG_TOOLS` / `TOOLS_PROMPT`，
所以启动行为与原来完全一致。

新增工具：在 `tools/` 下复制任一模块改五处（DESCRIPTION / PARAMETERS / GUIDANCE /
handler / SPEC），再在 `tools/__init__.py` 加一行 —— 模型侧 schema、执行侧分发、
提示词段落三处自动生效，**不需要改其他任何文件**。
"""

from .dispatch import execute_kg_tool, execute_kg_tool_async, tool_timeout_secs
from .mcp_host import mcp_tool_specs
from .registry import (  # noqa: F401  ← 私有名保留：测试用它们检查/注入注册表
    _spec,
    _TOOL_BY_NAME,
    _TOOL_SPECS,
    build_tools_prompt,
    register,
)
from .registry import all_specs as _registry_all_specs
from .tools import NATIVE_SPECS

register(*NATIVE_SPECS)

_mcp_registered = False


def _ensure_mcp_tools() -> None:
    """把 MCP 工具并入注册表（幂等；首次需要完整工具集时执行）。"""
    global _mcp_registered
    if _mcp_registered:
        return
    _mcp_registered = True
    register(*mcp_tool_specs())


def all_specs() -> list[dict]:
    """注册表只读视图（含 MCP 工具）—— 与 `registry.all_specs()` 同语义。"""
    _ensure_mcp_tools()
    return _registry_all_specs()


def __getattr__(name: str):
    """`KG_TOOLS` / `TOOLS_PROMPT` 惰性产出（见模块 docstring「为什么 MCP 那半是惰性的」）。

    两者都由注册表派生，取它们就意味着「要发给模型的完整工具集」→ 此刻才连 MCP。
    取到后落成真属性，后续访问零开销；`chat_service` / `agent/loop` 的顶层 from-import
    仍会立刻触发，所以主系统启动时机不变。
    """
    if name == "KG_TOOLS":
        _ensure_mcp_tools()
        from .registry import KG_TOOLS as kg_tools
        globals()["KG_TOOLS"] = kg_tools
        return kg_tools
    if name == "TOOLS_PROMPT":
        _ensure_mcp_tools()
        prompt = build_tools_prompt()
        globals()["TOOLS_PROMPT"] = prompt
        return prompt
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
