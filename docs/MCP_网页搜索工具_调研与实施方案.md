# MCP 网页搜索工具 · 调研与实施方案

> 调研日期：2026-09-12　|　调研人：AI Agent（含本机实测数据）　|　状态：**待拍板后实施**
> 一句话结论：**用官方 MCP Python SDK v2 写一个薄 MCP server（~200 行），搜索能力复用开源库 `ddgs`（MIT，零 API key）；传输 stdio + Streamable HTTP 双支持；TutorAgent 侧新增 `core/mcp/` 宿主层把它并入现有工具注册表。**
> 关键实测：本机当前网络下，**`ddgs` 走必应后端可正常返回中文结果（1~2 秒/次）**，DuckDuckGo / Brave / Google 全部不可达。

---

## 0. 结论速览（TL;DR）

| 维度 | 结论 |
|---|---|
| 协议 | MCP **2026-07-28** 版规范（当前最新），用官方 `mcp` Python SDK **v2.2.0**（MIT，2026-09-07 发布） |
| 交付物 ① | 独立可跑的 MCP server：`backend/mcp_servers/web_search/`，暴露 1 个工具 `web_search`，stdio / Streamable HTTP 双传输，可被 Claude Desktop / Cursor / 本仓库复用 |
| 交付物 ② | TutorAgent 内的 **MCP 宿主层** `core/mcp/`：动态连接 MCP server、发现工具、注入 `_TOOL_SPECS`，本轮先接 web_search，未来任何 MCP server 即插即用 |
| 搜索后端 | 默认 `ddgs`（MIT，无需 key）；预留 `SearXNG`（AGPL，自托管）与商业 API（博查）适配位 |
| 不做什么 | 不引入 Tavily/Exa/Brave 等需外网 key 的商业后端为默认；不改动现有 8 个原生工具的语义；不动 `fetch_webpage` |
| 风险最高点 | ① `mcp` v2 依赖与项目现状的解析兼容（新增 `httpx2` / `sse-starlette>=3` / `opentelemetry-api`）② in-memory MCP 会话在 `asyncio.to_thread` 线程中的可用性 —— 两者都需 **阶段 0 PoC 先验证** |

---

## 1. 需求确认

用户原话：**「做一下网页搜索的 mcp 工具，使用标准的 mcp 协议。尽量使用开源，先给我写一份调研文档。再开始做」**

拆解出 3 个可选理解，本文档采用的解释标注为 ✅：

| # | 理解 | 说明 | 采用 |
|---|---|---|---|
| A | **自研一个 MCP server** 提供网页搜索工具，并让 TutorAgent 作为 MCP 客户端接入 | 既产出可独立分发的开源 MCP server，又让产品具备联网搜索能力 | ✅ |
| B | 只做 MCP server（交给外部宿主用），不接本项目 | 少一半工作量，但项目自身仍不能联网搜索 | 作为阶段 1 中间态 |
| C | 把网页搜索写成普通工具塞进 `agent_tools._TOOL_SPECS`（不走 MCP） | 最省事，但**不满足「标准 MCP 协议」**要求 | ❌ |

**范围界定**
- 做：MCP server（标准协议、双传输、开源无 key 后端）+ TutorAgent 宿主层接入 + 测试与错误处理。
- 不做（本轮）：搜索结果的 rerank/摘要生成（用 LLM 二次加工会增加 token 与延迟，先不做）；把图谱/RAG 等已有工具全部改造成 MCP 导出（留作后续，宿主层设计已兼容）。

---

## 2. MCP 协议现状（2026-09 视角）

### 2.1 版本演进

| 规范版本 | 里程碑 |
|---|---|
| 2024-11-05 | 首版（stdio + HTTP+SSE） |
| 2025-03-26 | 引入 **Streamable HTTP** 取代 HTTP+SSE |
| 2025-06-18 | 补齐 OAuth / 结构化输出等 |
| 2025-11-25 | 上一稳定版 |
| **2026-07-28** | **发布以来最大修订**：无状态核心、MRTR（多轮往返请求）、扩展框架、授权加固、缓存与可观测 |

### 2.2 对「写一个搜索 server」真正有影响的点

| 变化 | 对我们的影响 |
|---|---|
| **传输**：stdio / Streamable HTTP 为标准，SSE 已废弃 | server 默认给 **stdio**（宿主通用），另开 `--transport http` 给远程部署；**不实现 SSE** |
| **无状态核心**：2026-07-28 转向无状态，协议版本变成「逐请求信封」 | 只影响 HTTP 部署形态（反代/多副本更简单），**stdio 形态不受影响**，我们主要走 stdio |
| **握手版本 ≠ 最新版本**：`LATEST_PROTOCOL_VERSION` = `2026-07-28` 且无法被 initialize 协商 | 我们**不手写握手**，全部交给 SDK，避免踩版本协商坑 |
| **工具定义不变**：`tools/list` 返回 `name/description/inputSchema` | 现有 `_TOOL_SPECS` 的 JSON Schema 结构与 MCP `inputSchema` **同构**（代码里早有 `ponytail:` 备注），迁移零成本 |

### 2.3 官方 Python SDK v2（`mcp` 2.2.0）关键事实

- 许可 **MIT**，Python ≥ 3.10（本项目 venv 3.13.9 ✅），`pip install "mcp[cli]"`
- **v2 是大重构**：`FastMCP` → **`MCPServer`**（`from mcp.server import MCPServer`）；官方 v1 线（1.30.0）仍在维护，但新项目应直接上 v2
- **Client 单参数推断 transport**（v2 亮点）：

| 传入 | transport | 适用 |
|---|---|---|
| `Client("http://host/mcp")` | Streamable HTTP | 远程/容器部署 |
| `Client(StdioServerParameters(...))` | stdio 子进程 | 桌面宿主通用形态 |
| `Client(mcp_server_obj)` | **in-memory（进程内）** | **测试 / 嵌入，仍是真实协议层** |

- **依赖变化（升级风险来源）**：新增必需 `httpx2>=2.5.0`、`opentelemetry-api>=1.28.0`、`mcp-types`、`sse-starlette>=3.0.0`；`pydantic>=2.12`、`anyio>=4.9`；**不再依赖 `httpx`**
- 注意：SDK 的 HTTP 层是 `httpx2`，与项目现有 `httpx` 并存；两者异常类型不同（`except httpx.ConnectError` **不会**捕获 `httpx2` 的错）——只在我们自己写 MCP 客户端异常分支时需要注意

---

## 3. 开源生态调研

### 3.1 现成开源 MCP 搜索 server 盘点

| 项目 | 许可 | 搜索后端 | 需 key | 传输 | 国内可用性 | 结论 |
|---|---|---|---|---|---|---|
| **`ddgs` 库自带 `ddgs mcp`** | MIT | DuckDuckGo / Bing / Brave / Google / Mojeek / Wikipedia 等 | ❌ 不需要 | stdio | ✅（走 bing 后端） | **最接近可用**，见 3.4 |
| nickclyde/duckduckgo-mcp-server | MIT | DuckDuckGo 专用 | ❌ | stdio | ❌ DDG 域名不可达 | 排除 |
| zhsama/duckduckgo-mcp-server-py | MIT | DuckDuckGo 专用 | ❌ | stdio + HTTP | ❌ 同上 | 排除 |
| jae-jae/searxng-mul-mcp 等 SearXNG 系 | MIT/Apache | **自建 SearXNG 实例** | ❌（自建） | stdio + HTTP | ✅（可配 bing/baidu/360） | **备选方案**，需先部署 SearXNG |
| brave-search-mcp-server（官方） | MIT | Brave Search API | ✅ | stdio | ❌ 域名不可达 | 排除（可留适配位） |
| tavily-mcp / exa-mcp-server / firecrawl-mcp | MIT | 商业 API | ✅ 付费 | stdio/HTTP | 需代理 | 不用作默认，留可选适配位 |
| mcp-server-fetch（Anthropic 官方） | MIT | 无（只抓取 url） | ❌ | stdio | ✅ | 与项目已有 `fetch_webpage` 重复，不引入 |

### 3.2 搜索后端对比（含本机实测）

**本机实测（2026-09-12，Windows / 当前网络，`curl --max-time 8`）**

| 目标 | HTTP | 判定 |
|---|---|---|
| `https://www.bing.com/` | 302（→ cn.bing.com） | ✅ 可用 |
| `https://cn.bing.com/` | 200 | ✅ 可用 |
| `https://www.baidu.com/` | 200 | ✅ 可用 |
| `https://www.sogou.com/web?query=test` | 200 | ✅ 可用 |
| `https://www.so.com/s?q=test` | 200 | ✅ 可用 |
| `https://api.bochaai.com/`（博查，商业） | 404（域名通） | ✅ 可达，需 key |
| `https://www.mojeek.com/search?q=test` | 403（拦 curl UA） | ⚠️ 站点通，库调用返回 0 条 |
| `https://duckduckgo.com/`、`html.duckduckgo.com` | 000 | ❌ 不可达 |
| `https://search.brave.com/`、`www.google.com`、`startpage.com`、`search.yahoo.com`、`zh.wikipedia.org` | 000 | ❌ 不可达 |

**`ddgs` 9.16.0 实战实测**（临时目录安装，未污染项目 venv）

```
DDGS().text('神经网络 入门', backend='bing', max_results=3) → 3 条中文结果（知乎/CSDN），约 1~2s
DDGS().text('Python 教程',   backend='auto', max_results=2) → 2 条，1.9s
DDGS().text('Python 教程',   backend='mojeek',   …)        → DDGSException: No results found（0.3s）
DDGS().text('Python 教程',   backend='bing,mojeek', …)     → DDGSException: No results found（1.1s）
```

**从中得到两条硬约束**：
1. **默认后端定 `auto` 或 `bing`，不要写多后端组合** —— `bing,mojeek` 会因其中一个失败而**整体抛异常**，fallback 必须由我们自己写循环实现；
2. DDG/Google/Brave 在此网络不可用，**任何以 DDG 为唯一后端的开源 server 都不能直接采用**。

**后端选型建议**

| 后端 | 许可/成本 | 优点 | 缺点 | 定位 |
|---|---|---|---|---|
| **ddgs（auto/bing）** | MIT / 免费 | 零 key、零部署、中文结果可用（已实测） | 靠抓 HTML，**上游改版可能失效**；无 SLA | **默认** |
| **SearXNG（自建）** | AGPL / 自托管 | 可聚合 bing+baidu+360，结果稳定可控，隐私 | 需 Docker 部署与维护；需开 `format: json` | 备选（一行配置切换） |
| 博查 BochaAI | 商业 / 需 key 有额度 | 国内稳定，专为 AI 优化，返回清洗过的摘要 | 需付费/额度；非开源 | 可选适配位 |
| Brave / Tavily / Exa | 商业 | 质量高 | 域名不可达 + 付费 | 不接 |

### 3.3 为什么自研 server，而不是直接 `pip install ddgs[mcp]`

`ddgs` 9.16 自带 `ddgs mcp`（stdio，工具：`search_text`/`search_images`/`search_news`/`search_videos`/`search_books`/`extract_content`），**能直接跑**。不直接采用的理由：

1. **工具集过大**：一次暴露 6 个工具（图片/视频/图书）会把无关工具灌给模型，挤占上下文、诱发误调用；我们只要 1 个 `web_search`。
2. **返回格式不可控**：教学场景需要「标题 + 链接 + 摘要 + 字数上限 + 出处标注」的稳定文本，且要能塞进 `agent_runs.evidence` 回放。
3. **后端策略不可控**：实测已证明多后端组合会整体失败，需要我们自己写「auto → bing → 其他」的重试与代理配置。
4. **复用项目既有体系**：错误码（`E-WEB-*`）、日志（`log_error`）、SSRF 校验（未来 `web_fetch`）、超时护栏都已在仓库内，自研 server 可直接复用，避免另起一套。
5. **可开源、可演进的交付物**：一个 ~200 行、依赖干净的 server，比包装别人的 server 更适合作为项目产出/比赛材料。

> 顺带记录：**阶段 1 之前可先用 `ddgs mcp` 现成 server 验证宿主链路**（零代码），确认 MCP 通路没问题后再替换为自研 server——这是最省的最短验证路径。

---

## 4. 系统设计

### 4.1 三个接入布局候选

| 方案 | 形态 | 优点 | 缺点 | 结论 |
|---|---|---|---|---|
| **A1 in-memory** | 同进程 `Client(mcp_server_obj)` | 零子进程、零端口、零生命周期管理；**仍走真实协议层**（官方明确） | 依赖 SDK 在同一/多事件循环下的行为；server 与宿主耦合在同一进程 | **首选**（阶段 0 PoC 验证） |
| A2 stdio 子进程 | 常驻子进程 + 长连接 session | 与桌面宿主形态一致，进程隔离 | 需管理生命周期、崩溃重启、Windows 编码、环境变量白名单；在 `to_thread` 线程里维护长连接较绕 | 回退方案 |
| A3 Streamable HTTP | server 独立服务（本地/容器） | 解耦、可复用、可远程 | 需端口/服务管理；本机场景属过度设计 | 供**外部宿主/容器部署**用，非项目内首选 |

### 4.2 推荐架构

```
TutorAgent 进程
├─ core/agent_tools.py  (_TOOL_SPECS 仍是唯一注册入口)
│     └─ 动态并入 MCP 工具：mcp__websearch__web_search
│            ▲
├─ core/mcp/
│     ├─ client.py     MCP 会话管理（in-memory 优先，可切 stdio/http）
│     ├─ loader.py     读取配置 → 连接 server → list_tools → 生成 spec（schema 直通）
│     └─ sync_bridge.py 同步↔异步桥（复用 rag_tool._run_async 同款模式）
│            ▲
└─ mcp_servers/web_search/   ← 独立可跑的标准 MCP server
      ├─ __main__.py   入口：python -m mcp_servers.web_search [--transport stdio|http]
      ├─ server.py     MCPServer + @mcp.tool() web_search
      └─ backends.py   后端适配（ddgs 默认 / searxng 可选）
```

**关键设计取舍**
- **工具注册表面不变**：`_TOOL_SPECS` 仍是唯一注册入口，MCP 工具以「动态 spec」形式并入（`KG_TOOLS` / `execute_kg_tool` 自动生效），**不改现有 8 个工具**。
- **命名防冲突**：MCP 工具命名为 `mcp__websearch__web_search`（业界 `mcp__<server>__<tool>` 惯例，一眼看出来源），避免与原生工具重名。
- **失败隔离**：MCP 连接失败 → 该 server 的工具不注入（只 warning），agent 循环照常跑；工具调用失败 → 按现有约定回填「操作失败: …」文案，不让循环炸。

### 4.3 MCP server 工具定义

```jsonc
// tools/list → web_search
{
  "name": "web_search",
  "description": "在互联网上搜索网页，返回标题、链接与摘要，用于获取实时/外部信息。当需要最新资料、新闻、或本地知识库中没有的内容时使用。",
  "inputSchema": {
    "type": "object",
    "properties": {
      "query":       {"type": "string",  "description": "搜索关键词，尽量具体"},
      "max_results": {"type": "integer", "description": "返回条数 1~10（默认 5）"},
      "backend":     {"type": "string",  "description": "搜索后端，默认 auto；可选 bing / searxng"},
      "timelimit":   {"type": "string",  "enum": ["d","w","m","y"], "description": "时间范围，可选"}
    },
    "required": ["query"]
  }
}
```

**返回**：友好文本（与项目既有约定一致，工具不抛异常给模型）：

```
[1] 神经网络小白入门：从0看懂基础神经网络算法
    来源: https://zhuanlan.zhihu.com/p/1954953443883067103
    摘要: …
[2] …
（共 5 条，后端: bing，耗时 1.8s）
```

**配置项（环境变量，`.env` 统一）**

| 变量 | 默认 | 说明 |
|---|---|---|
| `WEB_SEARCH_ENABLED` | `true` | 总开关（关则不注入 MCP 工具） |
| `WEB_SEARCH_BACKEND` | `auto` | `auto` / `bing` / `searxng` |
| `WEB_SEARCH_MAX_RESULTS` | `5` | 默认返回条数 |
| `WEB_SEARCH_TIMEOUT` | `10` | 单次搜索超时（秒） |
| `WEB_SEARCH_PROXY` | 空 | 可选代理（`http://` / `socks5://`），透传给 ddgs |
| `SEARXNG_URL` | 空 | 配了才启用 searxng 后端 |

### 4.4 TutorAgent 侧接入点（改动清单）

| 文件 | 改动 | 量级 |
|---|---|---|
| `core/mcp/client.py`、`loader.py`、`sync_bridge.py` | 新增：MCP 宿主层 | ~180 行 |
| `core/agent_tools.py` | 启动时把 MCP 工具并入 `_TOOL_SPECS`；`execute_kg_tool` 分发到 MCP 分支 | ~25 行 |
| `services/chat_service.py` | `TOOL_CAPABILITY_PROMPT` 补第 9 条（何时主动搜索） | ~5 行 |
| `core/error_codes.py` | 新增 `E-MCP-001`（连接失败）/ `E-MCP-002`（工具调用失败） | ~3 行 |
| `requirements.txt` | 加 `mcp>=2,<3`、`ddgs>=9.16` | 2 行 |
| `core/config.py` + `.env.example` | 新增上表配置项 | ~10 行 |

**约定沿用**（不新增机制）：
- 工具调用结果自动进 `agent_runs.evidence` → 前端可回源（零额外开发）
- 事件流沿用 `tool_start` / `tool_result`（MCP 工具同样带 `run_id`）
- 超时护栏沿用 agent_loop 的 `tool_timeout_secs`

### 4.5 与现有 `fetch_webpage` 的关系

搜索 vs 抓取是**互补**的两个动作：搜索给「有哪些页面」，抓取给「页面里写了什么」。
- 本轮：只加 `web_search`，**不动** `fetch_webpage`（模型可先搜后抓，天然形成闭环）。
- 阶段 3（可选）：把 `fetch_webpage`（含 SSRF 校验）也作为 `web_fetch` 工具导出到 MCP server，使 server 独立使用时同样具备「搜索 + 抓取」闭环。

---

## 5. 实施计划

| 阶段 | 内容 | 验收标准（可验证） | 估时 |
|---|---|---|---|
| **0. PoC 排雷** | ① `pip install --dry-run mcp ddgs` 检查依赖解析；② 写 10 行脚本验证 `Client(mcp_obj)` in-memory 在 `asyncio.to_thread` 线程 + `asyncio.run` 新事件循环下能正常 `list_tools/call_tool` | 两个 PoC 都通过 → 走 A1；任一失败 → 退 A2/A3（方案已备） | 0.5 天 |
| **1. MCP server 独立交付** | `mcp_servers/web_search/`：`web_search` 工具 + ddgs 后端（auto→bing 自建 fallback）+ searxng 适配位 + stdio/http 双传输 + README（含 Claude Desktop 配置示例） | ① `python -m mcp_servers.web_search` 能被 SDK client / MCP Inspector 列出工具并成功调用；② 真实搜索返回中英文结果各一次；③ 后端全挂时返回友好文案不抛异常 | 1 天 |
| **2. 接入 TutorAgent** | `core/mcp/` 宿主层 + 工具动态注入 + 提示词 + 配置 + 错误码 | ① 对话里问「帮我查一下 XX 最新进展」，agent 自动调用 `mcp__websearch__web_search` 并引用链接；② 行为可回放（`GET /agent/runs/{run_id}` 有该工具 evidence）；③ MCP 关掉后行为与今天完全一致 | 1~1.5 天 |
| **3. 测试与加固（可选）** | 单测（mock 后端，不联网）+ 联网测试单独标记 + （可选）SearXNG 入 docker-compose + 搜索质量评测脚本 | `pytest -m "not llm_api"` 全绿；联网测试单独 marker 可跳过 | 1 天 |

**测试策略**
- **单元**：用官方推荐的 **in-memory `Client(server)`** 断言 `tools/list` 与 `tools/call`（不联网、不起进程）；
- **集成**：mock 掉 `backends.py` 的 HTTP 层，断言结果格式化/截断/异常兜底；
- **联网**：新增 marker（如 `search_api`），默认跳过，与现有 `llm_api` 同级。

**回滚**：`WEB_SEARCH_ENABLED=false` 即回到今天的行为（MCP 层完全不加载），不存在数据迁移。

---

## 6. 风险与待验证项

| # | 风险 | 影响 | 应对 |
|---|---|---|---|
| R1 | `mcp` v2 依赖解析（新增 `httpx2`、`sse-starlette>=3`、`opentelemetry-api`、`mcp-types`）与项目锁版本冲突。**注意 `requirements.txt` 与实际环境已漂移**（写 `httpx==0.27.0`/`fastapi==0.111.0`，实际 `0.28.1`/`0.136.1`） | 装不上或装完破坏现有环境 | 阶段 0 先 `--dry-run`；先对齐 requirements 与实际环境；必要时用 v1 线（`mcp>=1.28,<2`） |
| R2 | in-memory 会话在「`to_thread` 线程 + 新事件循环」中是否可用（agent 工具当前就在这种环境执行） | 决定用 A1 还是 A2 | 阶段 0 专项 PoC（10 行脚本即可证伪） |
| R3 | `ddgs` 依赖上游 HTML 结构，**bing 改版即失效** | 搜索能力突然不可用 | ① 自建 fallback 链（auto→bing→searxng）；② 保留 SearXNG 切换；③ 失败返回友好文案，不让循环炸 |
| R4 | 搜索质量（中文长尾/时效性）无法保证 | 教学体验 | 先可用优先；后续引入评测（沿用 `scripts/eval_*.py` 风格）再决定是否接 SearXNG/博查 |
| R5 | Windows 子进程 / stdio 编码坑（项目已踩过 GBK 问题） | A2 方案乱码 | 若走 A2：显式 UTF-8 + `PYTHONIOENCODING`；A1 无此问题 |
| R6 | MCP 工具注入破坏现有 8 工具行为 | 回归 | 工具注入失败只 warning；`pytest -m "not llm_api"` 作为回归门禁 |

---

## 7. 参考资料（均为本次实际查阅）

| 主题 | 来源 |
|---|---|
| MCP 规范 2026-07-28 | `https://modelcontextprotocol.io/specification/2026-07-28`（含 `github.com/modelcontextprotocol/modelcontextprotocol` Releases） |
| MCP Python SDK v2 | PyPI `mcp` 2.2.0；文档 `https://py.sdk.modelcontextprotocol.io/`（`client/transports/index.md`、`migration/index.md`） |
| `ddgs`（原 duckduckgo-search） | PyPI `ddgs` 9.16.0；源码 `github.com/deedy5/ddgs`（MIT，含 `ddgs mcp` / `ddgs api`） |
| 现成搜索类 MCP server 盘点 | mcp.directory / awesome-mcp.tools / lobehub MCP 市场 |
| SearXNG 自建与 JSON API | SearXNG 官方文档 + 国内部署实践（需在 `settings.yml` 开启 `format: json`） |

---

## 8. 待拍板的 3 个决策点

1. **搜索后端**：只用 `ddgs`（零配置、即刻可用，但依赖上游）／还是同时把 **SearXNG** 加进 `docker-compose.yml`（更稳，需要额外服务）？
2. **交付范围**：阶段 1 只交付「可独立使用的 MCP server」，还是直接连阶段 2 的「接入 TutorAgent」一起做完？
3. **依赖引入**：是否同意给 `backend/requirements.txt` 增加 `mcp>=2,<3` 与 `ddgs>=9.16` 两个依赖（会带来 `httpx2` 等传递依赖）？

---

## 9. 实施记录（2026-09-12 完成）

拍板结果：**ddgs 默认 + 预留 SearXNG 适配位** / **MCP server + 接入一次做完** / **同意加依赖并对齐漂移**。

### 9.1 阶段 0 排雷结论（均通过）

| 排雷项 | 结果 |
|---|---|
| `mcp>=2,<3` + `ddgs>=9.16` 依赖解析 | 通过，且是**纯新增不升级现有包**（`fastapi`/`pydantic`/`anyio`/`starlette` 均未变动） |
| 装依赖后回归 | 543 → **555 passed, 7 deselected**（新增 12 个 MCP 测试，其余零回归） |
| in-memory 会话在 `asyncio.to_thread` 线程 + `asyncio.run` 新事件循环下可用性 | **通过**（同 loop / worker 线程 / 连续多次 / 多线程四种姿势全通）→ 确定走 in-memory，无需子进程与端口 |

### 9.2 实际落地（与本文第 4~5 节计划的差异）

| 计划 | 实际 | 原因 |
|---|---|---|
| `mcp_servers/web_search/`（server.py + backends.py + `__main__.py`） | **单文件** `app/mcp_servers/web_search.py` | 后端适配与工具本体合计约 130 行，拆包只增加跳转成本（YAGNI） |
| `core/mcp/`（client + loader + sync_bridge 三文件） | **单文件** `core/mcp_host.py` | 同样理由；同步桥复用 `kg_taxonomy` 既定约定（各持单例池） |
| 6 个配置项 | **2 个**：`WEB_SEARCH_ENABLED`、`SEARXNG_URL` | 后端选择由 `SEARXNG_URL` 是否为空决定（省一个 `BACKEND` 项）；`proxy`/`timeout`/`max_results` 尚无真实调优需求，先做模块常量 |
| 新增错误码 `E-MCP-001/002` | **仅 `E-WEB-004`**（网页搜索失败） | 用户可见语义只有"搜索失败"，MCP 是内部实现手段，不该外泄到错误码体系 |
| 宿主侧手写工具 spec | **schema/描述直接取自 `tools/list`** | 避免宿主重复维护一份必然漂移的 JSON Schema |

改动清单：新增 `core/mcp_host.py`、`mcp_servers/{__init__,web_search}.py`、`tests/test_mcp_web_search.py`；修改 `agent_tools.py`（+5 行并入）、`config.py`、`error_codes.py`、`chat_service.py`（提示词第 9 条）、`requirements.txt`、`.env.example`、`AGENTS.md`。

### 9.3 验收状态

| 验收项 | 状态 |
|---|---|
| MCP server 能被标准客户端列出工具并调用 | **通过**（in-memory 与 **stdio 子进程**两种方式均实测成功） |
| 真实搜索返回结果 | **通过**（`Python 列表推导式 教程` → 5 条中文结果，含链接与摘要） |
| 工具并入后模型侧/执行侧零改动、原生工具不受影响 | **通过**（`rag_search` 等仍正常） |
| 关闭开关回到原生 8 工具 | **通过**（`WEB_SEARCH_ENABLED=false` → 不返回 MCP spec） |
| 失败降级不中断循环 | **通过**（后端异常 → `搜索失败: …`；server 加载失败 → 空列表 + 日志） |
| 对话中自动调用（AC2） | **通过**（真实对话：问「Python 3.14 有哪些新特性」→ 模型 thinking 判定"这是外部实时信息，应该搜索" → 第 0 轮调 `mcp__websearch__web_search`（2.76s，5 条结果）→ 第 1 轮自动接 `fetch_webpage` 抓官方文档正文 → 回答带来源链接。**搜索→抓取闭环自然发生**，无需额外编排） |

### 9.4 遗留与观察

- `ddgs` 走 `auto` 后端时会在 stderr 打印若干行上游请求日志（低频，不影响功能）。**意外发现**：这些日志显示它实际能触达 `startpage` / `mojeek` / `yahoo` / `wikipedia` / `grokipedia`（均 200）—— 说明第 3.2 节用 `curl` 测出的"不可达"对这些站点只是**裸 curl 被 UA 拦截**，`ddgs` 依赖的 `primp`（Rust 客户端 + 浏览器指纹）可以正常访问。故实际可用后端比预期多，`auto` 的鲁棒性高于最初判断。
### 9.5 遗留项处置（2026-09-13）

| 遗留项 | 性质判定 | 处置 |
|---|---|---|
| `pandas>=2.2.0,<2.3.0`：声明了但零引用 | **僵尸依赖 + 潜在冲突** | **已删除**。全仓库 0 处 `pandas` 引用（含脚本/报告）；且 `pandas<2.3` 要求 `numpy<2.1`，全新安装时会把本地 numpy 2.5.2 降级 —— 同一份 requirements 在两台机器装出不同 numpy，属真实隐患 |
| `tiktoken>=0.7.0`：声明了但 venv 未装 | **功能静默降级** | **已安装**（0.14.0 + regex，纯新增）。未装时 `token_counter._get_encoding()` 退化为"1 token ≈ 3 字符"字符估算 —— 因有 ImportError 兜底、只打 warning，长期无人发现；且 2 个依赖它的测试被条件跳过。装后回归 555 → **557 passed** |
| SearXNG 适配位已就绪但未部署实例 | **不是缺陷，是设计选择** | **保持预留**（依据见下） |

**SearXNG 暂不部署的 4 条依据**：
1. 当前 ddgs 后端**已实测可用**（§9.3 真实对话链路通过，2.76s / 5 条结果），尚无失败或限流记录；
2. 官方部署方式比预期重：推荐从 GitHub 拉 `container/docker-compose.yml` + `.env.example`，模板含 **`searxng-core` + `searxng-valkey` 两个服务**（单用户场景缓存服务纯属负担），`settings.yml` 还需自行准备；
3. **本机网络对它不利**：§3.2 实测裸 `curl` 直连多数搜索站点返回 000（按 UA 拦截），而 ddgs 依赖的 `primp`（Rust + 浏览器指纹）能绕过。SearXNG 走 Python `requests` + 常规 UA，被拦概率更高 —— 自建**未必比 ddgs 更稳**；
4. 代价是常驻容器 + 需要维护的 `settings.yml`，而收益目前没有实测数据支持。

**真要启用时的路径**（本次未执行，留作可操作步骤）：

```bash
# 1) 拉官方模板（勿手写 compose，官方模板会更新）
mkdir -p ./searxng && cd ./searxng
curl -fsSL -O https://raw.githubusercontent.com/searxng/searxng/master/container/docker-compose.yml
curl -fsSL -O https://raw.githubusercontent.com/searxng/searxng/master/container/.env.example
cp -i .env.example .env        # 填 SEARXNG_SECRET（openssl rand -hex 32）等
docker compose up -d
# 2) 编辑 core-config/settings.yml：search.formats 加 json
#    （否则 /search?format=json 直接 403，宿主层会拿到 HTML 解析失败）
# 3) SEARXNG_URL 填容器网络地址（如 http://searxng-core:8080）——注意不是 localhost
# 4) 本机（非容器）开发则用 http://localhost:8888（官方 docker run 示例映射 8888:8080）
```

> 验证：`curl "http://localhost:8888/search?q=test&format=json"` 应返回 JSON。
> ⚠️ 受限网络下还需给容器配 `HTTP_PROXY/HTTPS_PROXY`（指向宿主机代理）并调整 `settings.yml` 的 `outgoing` 段 —— 否则容器内引擎同样连不出去。
