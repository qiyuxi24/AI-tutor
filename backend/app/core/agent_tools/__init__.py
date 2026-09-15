"""Agent 工具系统门面 —— 对外 API 稳定（模块可重构，这些名字不变）。

「工具」这件事全部在本目录，别处不再有工具文件：

    registry.py   ① 注册表中间件：spec 契约 / 注册 / `KG_TOOLS` / 提示词段落生成（零依赖）
    dispatch.py   ② 调用中间件：参数解析 → 错误映射 → 同步/异步双入口执行
    tools/        ③ 12 个原生工具，**一个工具一个模块**（spec + handler 自包含）→ tools/README.md
    mcp_host.py   ④ MCP 适配器：从 MCP server 的 `tools/list` 取回同构 spec
    net_guard.py  SSRF 防护能力层（URL 类工具共用，唯一实现）
    README.md     机制说明（改架构前必读）

启动时组装：原生工具（`tools.NATIVE_SPECS`）+ MCP 工具（`mcp_tool_specs()`）。
MCP 未启用 / 连接失败时 `mcp_tool_specs()` 返回 []，原生工具行为完全不变。

新增工具：在 `tools/` 下复制任一模块改五处（DESCRIPTION / PARAMETERS / GUIDANCE /
handler / SPEC），再在 `tools/__init__.py` 加一行 —— 模型侧 schema、执行侧分发、
提示词段落三处自动生效，**不需要改其他任何文件**。
"""

from .dispatch import execute_kg_tool, execute_kg_tool_async, tool_timeout_secs
from .mcp_host import mcp_tool_specs
from .registry import (  # noqa: F401  ← 私有名保留：测试用它们检查/注入注册表
    KG_TOOLS,
    _spec,
    _TOOL_BY_NAME,
    _TOOL_SPECS,
    all_specs,
    build_tools_prompt,
    register,
)
from .tools import NATIVE_SPECS

register(*NATIVE_SPECS)
register(*mcp_tool_specs())

# 提示词侧「工具调用指南」段落（由注册表生成：MCP 开启/关闭自动跟随，不再手写第二份）
TOOLS_PROMPT = build_tools_prompt()
