# mcp_servers —— MCP 模块（server 本体 + 宿主接线）

> **一句话**：本目录是 **MCP server 本体**（标准协议、可脱离本项目独立运行）；
> 宿主编排在 `core/agent_tools/mcp_host.py`（把 server 的工具并进 agent 工具表）。
> 两者合起来才是完整的「MCP 模块」。
>
> 当前 1 个 server：`web_search`（联网搜索）→ 暴露给模型的工具名
> `mcp__websearch__web_search`。
>
> 沿革：2026-09-12 PoC 引进；09-15 宿主随工具系统归位到 `core/agent_tools/`。

---

## 1. 两张皮，各自独立

```
  ┌──── 本目录（server 本体，标准 MCP 协议，零项目依赖假设）────┐
  │  web_search.py : MCPServer("web-search") + @mcp.tool()    │
  │  可 stdio 独立运行 → 挂 Claude Desktop / Cursor 等任意宿主 │
  │  可 streamable-http 运行 → 容器 / 跨进程宿主               │
  └───────────────────────┬───────────────────────────────────┘
                          │ ① stdio / HTTP（外部宿主）
                          │ ② in-memory（本项目内嵌，同进程零端口，仍走完整协议层）
                          ▼
  ┌──── core/agent_tools/mcp_host.py（宿主层）──────────────────┐
  │  _SERVERS = (("mcp__websearch__", "app.mcp_servers.web_search"),) │
  │  连 server → tools/list → 转同构 spec → register()          │
  │  执行侧：call_tool → 文本回填给模型                          │
  └────────────────────────────────────────────────────────────┘
```

**为什么是 in-memory**：同进程、零子进程、零端口，但仍经过完整 MCP 协议层 ——
既拿到了"协议一致性"（换成远程 server 时行为不变），又不用管进程生命周期。
实测（2026-09-12 PoC）：in-memory 会话在 `asyncio.to_thread` 线程 + `asyncio.run`
新事件循环下能正常 `list_tools` / `call_tool`，所以执行侧**不需要常驻连接**。

---

## 2. server 侧：搜索后端降级链

```
settings.searxng_url 配了？ ──是──→ SearXNG（自建，JSON API）
        │否
        ▼
  Bing RSS（主后端，std httpx + stdlib XML 解析，零 API key）
        │ 失败 / 无结果
        ▼
  ddgs（兜底，MIT）
        │ 全败
        ▼
  抛异常 → 工具返回「搜索失败: <各级原因>」
```

| 后端 | 状态 | 说明 |
|---|---|---|
| **Bing RSS** | **主** | `cn.bing.com/search?format=rss`（失败依次退 `www.bing.com`）。实测 0.5s 返 10 条，中英文均可 |
| ddgs | 兜底 | 其 `bing` 后端在 9.16 已移除；其余引擎（google/duckduckgo/yahoo…）国内实测全部超时（32s 才抛错）→ 只作海外/带代理环境的兜底 |
| SearXNG | 按需 | 配 `SEARXNG_URL` 才启用。需在其 `settings.yml` 的 `search.formats` 加 `json` |

**坑**：ddgs 传显式多后端（如 `'bing,mojeek'`）时，**单个后端失败会整体抛异常** ——
所以降级链由 `_search_web` 自己串，`_DDGS_BACKENDS` 保持单后端 `("auto",)`。

**"无结果"与"失败"语义不同**：全部后端正常但都没结果 → 返回友好提示；
有后端报错且最终无结果 → 抛异常并汇总各级原因（否则排查时看不出为什么搜不到）。

---

## 3. 宿主侧：`core/agent_tools/mcp_host.py`

| 事项 | 做法 |
|---|---|
| 注册入口 | `mcp_tool_specs()` —— 由 `agent_tools/__init__.py` 在 import 时调用 |
| 工具命名 | 前缀 + server 内名 = `mcp__websearch__web_search`（业界 `mcp__<server>__<tool>` 惯例） |
| schema | **直通** `tools/list` 的 `inputSchema`，宿主不维护第二份（server 改了自动跟随） |
| 说明文案 | 无 `guidance` → 提示词段落回落该工具的 `description`。**"何时用"写在 server 的 tool docstring 里** |
| 降级 | 未装 `mcp` 包 / `WEB_SEARCH_ENABLED=false` / 连接失败 → 返回 `[]`，**不阻断启动**，原生工具行为不变 |
| 缓存 | `_specs_cache` 缓存首次结果（首次调用建连，之后不再连）；`_SERVERS` 改动需重启进程 |
| 执行 | 同步桥 `_run_async` + `call_tool`；失败返回「搜索失败: …」文本（符合工具层"永不抛异常"约定） |

**边界**：宿主只产 spec，**不知道注册表的内部结构**（只管调 `register()`）；
注册表也**不引用任何具体 server**（只按 `_SERVERS` 里的模块路径惰性 import）。
详见 `core/agent_tools/README.md` §4「三条禁令」。

---

## 4. 新增一个 MCP server

1. 在本目录写 `xxx.py`：`mcp = MCPServer("xxx")` + `@mcp.tool()` 的 `def tool_name(...) -> str`。
   **在 tool 的 docstring 里写清"何时用 / 何时别用"** —— 这就是模型侧看到的说明，
   也是提示词回落的来源（宿主不重复维护）。
2. 在 `core/agent_tools/mcp_host.py` 的 `_SERVERS` 加一项 `("mcp__xxx__", "app.mcp_servers.xxx")`。
3. **不需要改**注册表、分发、提示词 —— 三份产物自动跟随。
4. 加离线测试（参考 `tests/test_mcp_web_search.py`）：降级为空的两种情形 +
   spec 与 `KG_TOOLS` 的对应关系。

---

## 5. 配置

| 环境变量 | 作用 |
|---|---|
| `WEB_SEARCH_ENABLED` | 总开关。`false` → `mcp_tool_specs()` 返 `[]`，`KG_TOOLS` 里没有 `mcp__*`，提示词段落也不含它 |
| `SEARXNG_URL` | 配了则 server 优先走自建 SearXNG |

---

## 6. 运行与测试

```bash
cd backend

# 独立运行（stdio，挂到任意 MCP 宿主）
venv/Scripts/python.exe -m app.mcp_servers.web_search

# 远程运行（Streamable HTTP）
venv/Scripts/python.exe -m app.mcp_servers.web_search --transport streamable-http --port 8100

# 测试
venv/Scripts/python.exe -m pytest tests/test_mcp_web_search.py -q
venv/Scripts/python.exe -m pytest tests/ -q -m "not llm_api"
```

---

## 7. 已知边界

| 项 | 现状 | 说明 |
|---|---|---|
| 远程 server | **未接线** | `_SERVERS` 目前只挂 in-memory 本地 server。接远程只需把 `_server()` 改成返回 URL 字符串 |
| 常驻连接 | **不做** | 每次调用建一次会话（in-memory 成本≈0）。远程 server 高延迟时再议连接池 |
| 工具名冲突 | 靠前缀规避 | `mcp__<server>__` 前缀保证与原生工具不撞名；重名会被 `register()` 直接 `ValueError` |
| 权限 | **未做** | MCP 工具与原生工具共用同一套 handler 契约，没有 ACL（见 `agent_tools/README.md` §8） |

---

## 8. 相关文档

| 位置 | 关系 |
|---|---|
| `../core/agent_tools/mcp_host.py` | 宿主层实现（本模块的另一半） |
| `../core/agent_tools/README.md` | 工具机制层：注册表 / 分发 / 不变量 |
| `docs/MCP_网页搜索工具_调研与实施方案.md` | 选型与实施方案（为什么 MCP、为什么 Bing RSS） |
| `../requirements.txt` | `mcp` 依赖声明 |
