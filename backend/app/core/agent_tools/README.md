# agent_tools —— 工具系统（「工具」这件事的唯一目录）

> **一句话**：全部与「LLM 调工具」有关的代码都在本目录 —— 机制（注册 / 分发 / 说明生成）、
> 定义（11 个原生工具）、MCP 适配、SSRF 能力层。**别处不再有工具文件。**
>
> 定位：夹在「Agent 主循环」与「领域模块」之间的中介层。改工具系统只动本目录；
> 改业务只动领域模块。两侧互不感知。
>
> 沿革：2026-09-08 注册表收敛 → 09-15 拆包 + 说明文案单一来源 → 09-15 实现归位 →
> 09-15 **一工具一文件**。

---

## 0. 目录结构

```
app/core/agent_tools/
├── __init__.py      门面：组装（NATIVE_SPECS + mcp_tool_specs()），对外 API 稳定
├── registry.py      ① 注册表中间件 —— spec 契约 / register / KG_TOOLS / 提示词段落生成（零依赖）
├── dispatch.py      ② 调用中间件 —— 参数解析 → 查表 → 执行 handler → 异常隔离为文案
├── tools/           ③ **11 个原生工具，一个工具一个模块**（spec + handler 自包含）
│   ├── __init__.py      NATIVE_SPECS 汇总（唯一汇总点）
│   ├── README.md        工具目录索引 + 编写规范 → **加工具先看这份**
│   ├── add_knowledge_node.py / update_node_content.py / update_mastery.py
│   ├── add_edge.py / delete_node.py                （图谱 5 个）
│   ├── update_user_profile.py                      （画像 1 个）
│   ├── fetch_webpage.py / download_resource.py / rag_search.py   （资料 3 个）
│   └── quiz_generate.py / grade_answer.py          （教学检验 2 个）
├── mcp_host.py      ④ MCP 适配 —— tools/list → 同构 spec（schema 直通，失败降级为空）
├── net_guard.py     ⑤ SSRF 防护能力层 —— URL 类工具共用的安全边界（唯一实现）
└── README.md        本文（机制层）
```

配套文档：`tools/README.md`（工具怎么加、三条硬约定）·
`../llm/README.md`（LLM 原语）· `../../mcp_servers/README.md`（MCP 全景）。

---

## 1. 它解决三个问题

一次对话里，"工具"要同时出现在三个地方，且必须保持一致：

| 问题 | 产物 | 谁消费 |
|---|---|---|
| 模型怎么知道有哪些工具、参数长什么样？ | `KG_TOOLS`（OpenAI function-calling schema） | `agent/loop.py` 的 `_chat_once(..., tools=KG_TOOLS)` → LLM API |
| 模型请求的 `tool_call` 怎么落到代码上？ | `execute_kg_tool_async(tool_call, kg)` | `agent/loop.py` 主循环逐轮调用 |
| "什么时候该用哪个工具"怎么写进系统提示词？ | `TOOLS_PROMPT`（「工具调用指南」段落） | `chat_service._build_system_prompt()` → system prompt |

**核心设计**：这三份产物**全部由注册表生成**，新增/删除工具时自动跟随。
项目里不存在第四处需要手改的地方。

> 历史坑（2026-09-15 修）：第三个问题原先由 `chat_service` 手写一份说明（3991 字符），
> 与 spec 的 `description` 构成**双源**，实测已漂移 —— `WEB_SEARCH_ENABLED=false` 时
> `mcp__websearch__web_search` 根本不在 `KG_TOOLS` 里，提示词却仍教模型去调它。
> 现在：原生工具的"何时用"写在 `tools/*.py` 的 `GUIDANCE`，MCP 工具的写在它自己的
> description（server 侧维护）。

---

## 2. 五个角色，各管一段

| 文件 | 角色 | 它依赖 | 它**不**做 |
|---|---|---|---|
| `registry.py` | **注册表中间件**：定义 spec 契约、收注册、生成模型侧 schema 与提示词段落 | **零依赖**（纯 dict + 纯函数） | 不执行工具、不认识任何业务概念 |
| `dispatch.py` | **调用中间件**：解析参数 → 执行 handler → 异常隔离为文案 | stdlib（`asyncio`/`inspect`/`json`）+ `error_codes`（日志）+ `.registry`（查表） | 不知道注册了哪些工具、不做超时控制 |
| `tools/` | **工具定义（叶子）**：每个工具一个模块，`DESCRIPTION` / `PARAMETERS` / `GUIDANCE` / `handler` / `SPEC` | `.registry._spec` + 领域模块 | 不做分发、不拼提示词 |
| `mcp_host.py` | **MCP 适配**：连 server → `tools/list` → 同构 spec | `mcp` SDK + `config` + `error_codes` | 不定义工具、不碰注册表内部 |
| `net_guard.py` | **SSRF 能力层**：`is_blocked_url` | stdlib（`socket`/`ipaddress`） | 不认识任何工具 |
| `__init__.py` | **组装与门面**：`register(*NATIVE_SPECS)` + `register(*mcp_tool_specs())` | 上面五者 | 除组装外无逻辑 |

---

## 3. 一次工具调用的完整生命周期

```
① 定义          tools/<工具名>.py: DESCRIPTION / PARAMETERS / GUIDANCE / handler / SPEC
② 注册          tools/__init__.py: NATIVE_SPECS = [<工具名>.SPEC, ...]
                __init__.py: register(*NATIVE_SPECS) / register(*mcp_tool_specs())
                    ├─→ registry._TOOL_SPECS    全量列表（诊断/测试用）
                    ├─→ registry._TOOL_BY_NAME  执行侧查表（dispatch 用）
                    ├─→ registry.KG_TOOLS       模型侧 schema（不含 handler/guidance/timeout_secs）
                    └─→ registry.build_tools_prompt() → TOOLS_PROMPT（提示词段落）

③ 发给模型      agent/loop.py: _chat_once(ctx.messages, tools=KG_TOOLS)
④ 模型回请求    resp.choices[0].message.tool_calls = [{id, function:{name, arguments}}]
⑤ 落地执行      agent/loop.py._execute_tool → dispatch.execute_kg_tool_async
                    ├─ registry._resolve_spec(tool_call)   查表；未注册 → "未知工具: X"
                    ├─ _decode_args(tool_call, name)        JSON → dict；坏 JSON → "操作失败: …"
                    ├─ 协程 handler → 直接 await
                    │  同步 handler → asyncio.to_thread(_run_sync_handler)
                    └─ 任何异常 → _tool_error(...) → 文案（不向上抛）
                      （同步 handler 内部若需 async，再用工具模块自己的 _run_async 桥，见 tools/README §6）
⑥ 回填上下文    agent/context.append_tool_result(tc.id, content) → 下一轮 LLM 看得见
```

**超时不在本目录**：`agent/loop.py` 用 `asyncio.wait_for(..., timeout=tool_timeout_secs(tc, 60))` 包住第 ⑤ 步 ——
这样超时能同时产出 `tool_result` 事件与证据步骤（可观测性归 loop，机制归中间件）。

---

## 4. 解耦边界

```
        agent/loop.py ─┐                    ┌─ chat_service.py
 (KG_TOOLS / 分发 / 超时)│                    │(TOOLS_PROMPT)
                        ▼                    ▼
        ┌──────────────────────────────────────────────┐
        │  core/agent_tools/                           │
        │  registry  →  dispatch                        │
        │      ↑          ↑                             │
        │   tools/ ───────┘ (只注册，不被 dispatch 感知) │
        │      │  └→ net_guard (SSRF，URL 类工具共用)    │
        │  mcp_host.py ──→ register()                   │
        └───────┬──────────────────────────────────────┘
                │ 单向：tools → 领域模块
                ▼
   core/knowledge_writer.py · core/quiz/ · core/profile/ · core/rag_pipeline/ · core/kb/
   （领域模块**不知道**工具系统的存在）
```

### 实测证据

| 检查 | 命令 | 结果 |
|---|---|---|
| `registry.py` 是否零依赖 | `Select-String '^\s*(import\|from )' registry.py` | **无匹配**（纯 dict + 纯函数） |
| `dispatch.py` 依赖面 | 同上 | 仅 5 行：`asyncio` / `inspect` / `json` / `error_codes` / `.registry` |
| 领域模块是否反向依赖 | 搜 `app/core/**`（排除本包）的 `agent_tools` / `_TOOL_SPECS` | **0 处** |
| 谁正向依赖本包（生产代码） | 搜 `from app.core.agent_tools import` | 仅 2 处：`agent/loop.py`、`chat_service.py` |
| 中间件能否脱离具体工具工作 | 注册**假 spec**（`t_echo` / `t_boom`）后调用 | 正常调用 / 未知工具 / 坏 JSON / 业务错 四条路径全部按约定返回，提示词段落自动含该假工具 |

> 最后一条是最硬的证据：`dispatch` 只认 spec 契约里的 `handler` 字段，
> **完全不需要知道项目里有哪些工具**。范例：`tests/test_chat_quiz.py` 用
> `monkeypatch.setitem(_TOOL_BY_NAME, "probe_async", ...)` 注入三个探针工具，
> 验同步 / 异步 / 异常三条分发路径。

### 三条禁令

1. **领域模块禁止 import `agent_tools`**。工具由 `tools/` 引用领域模块
   （方向：中间件 → 工具 → 领域，不能反过来）。`mcp_host.py` 同理：它只提供
   `mcp_tool_specs()`，由本包 `__init__` 主动调用；它自己不知道注册表存在。
2. **`registry.py` 禁止 import 任何 `app.*` 模块**。它是纯契约层，一旦引入依赖，
   `dispatch` 的单元测试就会被拖着加载整条链（配置、DB、网络）。
3. **`agent/loop.py` 禁止 import 具体工具或 `tools/`**。它只允许认三个名字：
   `KG_TOOLS` / `execute_kg_tool_async` / `tool_timeout_secs`。

### 一个无法消除的耦合（如实记录）

Python 包机制：`import app.core.agent_tools.registry` 会**先执行包的 `__init__.py`** →
触发组装（含 MCP 连接尝试）。所以"独立测试 registry"只能做到
「模块自身零依赖 + 用假 spec 驱动」，做不到"避开组装"。
要彻底隔离就得把组装移出包（代价：每个入口显式调一次 `init_tools()`，并失去"import 即就绪"的便利）。
当前规模（12 工具）不值得。

---

## 5. 不变量（改本目录必须守住）

| # | 不变量 | 违反的后果 |
|---|---|---|
| 1 | **永不抛异常**：所有失败回填成给模型看的文本（`操作失败: …` / `权限不足: …` / `工具执行出错: …` / `搜索失败: …`） | 异常穿透会打断整轮 Agent Loop |
| 2 | **双入口**：协程 handler 只有 `execute_kg_tool_async` 能跑；同步入口会明确报"请改用 async" | 静默走错路径 / 工具内 `create_task` 失败 |
| 3 | **模型侧 schema 只有三字段**（`name`/`description`/`parameters`） | 内部字段（`handler`/`guidance`/`timeout_secs`）泄漏给模型 = 白烧 token 且可能被模型当参数填 |
| 4 | **重名注册直接 `ValueError`** | 后注册的静默覆盖前一个，运行时才发现 |
| 5 | **原生工具必须写 `guidance`** | 提示词段落回落 `description` = 悄悄退回双源，重新埋下漂移 |
| 6 | **超时不在本目录**（由 `agent/loop.py` 用 `tool_timeout_secs()` 包住） | 超时变得不可观测（无事件/无证据步骤） |
| 7 | **MCP 工具无 `guidance` → 回落其 `description`** | 若给 MCP 工具在本目录硬写说明，又变回"宿主维护第二份" |
| 8 | **提示词只引用已注册的工具名** | 模型会去调不存在的工具（这就是 2026-09-15 修的那个 bug） |
| 9 | **URL 类工具必须过 `net_guard.is_blocked_url`** | SSRF：模型可诱导服务端请求内网地址 |
| 10 | **一个工具一个模块，模块名 = 工具名** | 破坏后"找工具"又要跨文件搜索（这就是 09-15 拆分的动机） |

其中 3、5、8 与"每工具只被记录一次"由 `tests/test_tools_registry.py` 自动看守。

---

## 6. 新增一个工具

### 原生工具（1 个新文件 + 1 行注册 + 3 份产物自动生成）

1. 在 `tools/` 下**复制任一模块**，改五处：`DESCRIPTION` / `PARAMETERS` / `GUIDANCE` /
   `handler` / `SPEC` 里的工具名。文件命名 = 工具名。
2. 在 `tools/__init__.py` 加 `from . import <工具名>` + `NATIVE_SPECS` 加一行。
3. 若工具会改图谱 → 确认 `added_by` 权限体系与图谱分析是否需同步。

**不需要**改：`KG_TOOLS`、分发逻辑、提示词。**不要再手写第四份说明。**
编写规范、三条硬约定、检查清单 → `tools/README.md`。

### MCP 工具（本目录零改动）

在 `mcp_host.py` 的 `_SERVERS` 加 `(前缀, 模块路径)` 即可。工具名自动加 `mcp__<server>__<tool>`
前缀，schema 直通 server 的 `inputSchema`，提示词段落回落到其 `description`
（**"何时用"写在该 MCP server 的 tool docstring 里**，宿主不重复维护）。
完整步骤见 `../../mcp_servers/README.md`。

---

## 7. 测试与守卫

```bash
cd backend
venv/Scripts/python.exe -m pytest tests/test_tools_registry.py -q          # 一致性守卫 7 例
venv/Scripts/python.exe -m pytest tests/test_chat_quiz.py -q               # 分发三条路径 + quiz 工具
venv/Scripts/python.exe -m pytest tests/test_download_tool.py tests/test_rag_tool.py -q
venv/Scripts/python.exe -m pytest tests/test_mcp_web_search.py -q          # mcp_host + server
venv/Scripts/python.exe -m pytest tests/test_agent_loop.py -q              # loop 侧超时/护栏
venv/Scripts/python.exe -m pytest tests/ -q -m "not llm_api"               # 离线全量
```

`tests/test_tools_registry.py` 看守的 7 条：原生工具必有 `guidance` / `KG_TOOLS` 与注册表一一对应且不泄漏内部字段 /
提示词引用的工具名 ⊆ 注册名 / **MCP 关闭时段落不含 `mcp__`** / 每工具只被记录一次 / 跨工具策略段仍在 / 重名注册报错。

**独立测中间件的办法**（不需要真工具、真图谱、网络）：

```python
from types import SimpleNamespace
from app.core.agent_tools.registry import register
from app.core.agent_tools.dispatch import execute_kg_tool

register({"name": "t_echo", "description": "回显", "parameters": {},
          "handler": lambda a, kg: f"echo:{a['x']}", "guidance": "仅测试", "timeout_secs": None})
tc = SimpleNamespace(function=SimpleNamespace(name="t_echo", arguments='{"x":1}'))
assert execute_kg_tool(tc, None) == "echo:1"
```

---

## 8. 已知边界与刻意不做

| 项 | 现状 | 说明 |
|---|---|---|
| Skills / 渐进披露 | **不做** | 12 个工具的规模下，全量 schema + 一段提示词足够；渐进披露收益不抵成本（YAGNI）。工具数上量再议 |
| 插件热加载 | **不做** | 注册表在 import 时组装一次（`_specs_cache` 缓存 MCP 结果）。运行期改工具集 = 重启进程 |
| 工具并行执行 | **未做** | 同一轮的多个 `tool_calls` 目前逐个 `await`（顺序执行）；如需并行，改 `agent/loop.py` |
| 工具级权限模型 | **未做** | 权限在 handler 内部（`KnowledgeGraph` 的 `added_by` 校验），中间件不做 ACL |
| `guidance` 的语义分层 | **未做** | `description`（模型侧）与 `guidance`（提示词侧）靠人工分工，未强制"不重复" |
| 工具按领域合并文件 | **刻意不做** | 合并会让"该放哪个文件"每次都需重新论证；一工具一文件的规则不需要判断力。见 `tools/README.md` §3 |
| 同步异步桥 | **4 份拷贝** | `tools/rag_search` / `tools/download_resource` / `mcp_host` / `kg_taxonomy` 各持单例池（刻意互不干扰），见 `tools/README.md` §6 |
| 图谱 RAG 双检索 | **不在本目录** | `rag_search` 内部未接混合检索（见 `core/hybrid_search/`） |

---

## 9. 相关位置

| 位置 | 关系 |
|---|---|
| `tools/README.md` | **加/改工具的第一入口**：一个工具一个模块的规范与检查清单 |
| `core/agent/loop.py` | 唯一调用方（主循环、超时、事件、证据） |
| `services/chat_service.py` | 提示词组装：`TOOL_CAPABILITY_PROMPT = TOOLS_PROMPT + TOOL_POLICY_PROMPT`（跨工具策略：掌握度主信号 / "学生说懂了就出题"铁律 / 权限限制） |
| `mcp_host.py` · `app/mcp_servers/` | MCP 宿主与 server 本体 |
| `core/knowledge_writer.py` | AI 写图谱层（`add_knowledge_node` 的落点）。**不属工具层**：被本目录与 `chat_service` 共用 |
| `core/quiz/chat_quiz.py` | `quiz_generate` / `grade_answer` 的落点 |
| `tests/test_tools_registry.py` | 一致性守卫 |
