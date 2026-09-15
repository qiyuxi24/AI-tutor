# AGENTS.md — TutorAgent 开发参考手册

> 用途：给 **AI 编码 Agent 与开发者**写代码/改架构前的查阅文档。
> 对应两本权威资料：① 本文件（内部契约 + 架构速查）；② FastAPI 自带的 `/docs`（全部 REST 端点与请求/响应 schema，**唯一端点级权威，本文件不重复细节**）。
> 快速校验：端点行为以代码为准，本文件是索引不是副本；改架构前必读 §3 耦合清单。

---

## 0. 常用命令 / 运行环境

| 事项 | 命令 / 路径 |
|---|---|
| 后端 venv Python | `backend/venv/Scripts/python.exe`（**必须用它**；系统 Python 3.11 的 fastapi 过旧会导入失败） |
| 启动后端 | `backend/venv/Scripts/python.exe -m uvicorn app.main:app --port 8000`（cwd=backend） |
| **硬约束** | uvicorn **必须 `--workers 1`**（EventBus 用户队列 / 进程内定时 GC 依赖单进程；多 worker 会各自持有事件总线与队列） |
| 启动前端 | cwd=frontend：`npm run dev`（Vite，默认 5173） |
| 测试 | `backend/venv/Scripts/python.exe -m pytest backend/tests -q -m "not llm_api"`（离线用例 558，cwd=项目根；`llm_api`=真实付费 API 测试需 pytest-asyncio，单独跑） |
| 配置真值 | 根目录 `.env`（唯一）；`backend/app/core/config.py` 读取 |
| LLM 三段配置 | `LLM_API_KEY`+`LLM_BASE_URL`+`MODEL_NAME`（对话主模型，默认 **MiniMax-M3**）；`DASHSCOPE_API_KEY`+`EMBED_BASE_URL`（嵌入固定阿里 text-embedding-v4） |

---

## 1. 架构总览（以 Agent Loop 为核心）

```
frontend (Vue3+Vite+Pinia+EP+D3)
   │  fetch/EventSource（/chat/stream）
   ▼
api/v1/chat.py ──► services/chat_service.py ──编排──► core/agent_loop.run_agent_loop()
                                                        │              │
                     ┌──────────────────────────────────┘              │
                     ▼                                                  ▼
        core/llm/(LLM 原语包)  +  agent_tools.py(工具)  core/agent_run_store.py(唯一事实源落库)
           chat_create/call_llm/embed_client                 ▲  token_estimator.py(读历史)
           KG_TOOLS / execute_kg_tool(_async)               │
           (rag/web 重型实现在 rag_tool/web_tool)           │
                     │                              │
                     ▼                              ▼
        core/event_bus.py(publish)   data/agent_runs/agent_runs.db(每用户一行/run)
                     │
                     ▼
        SSE 流回前端（thinking/tool_*/text_delta/agent_* 均带 run_id）
```

### 1.1 目录速览
| 路径 | 职责 |
|---|---|
| `backend/app/core/agent_loop.py` | **Agent 主循环（纯编排）**：LLM↔KG_TOOLS 多轮串联、护栏、证据收集、事件发布、落库触发 |
| `backend/app/core/agent_context.py` | 一次 run 的对话上下文缓冲：API 消息序列唯一持有/写入点（快照/回填协议约束单点；Batch2 窗口裁剪扩展位） |
| `backend/app/core/agent_events.py` | 事件发射中间件：run_id 注入 + per-user 路由（agent_loop 对 event_bus 的唯一入口） |
| `backend/app/core/context_guard.py` | 发送前预算守卫：run_agent_loop 入口一次性丢最旧历史（chat_service 调用） |
| `backend/app/core/llm/` | **LLM 原语包**（自旧 llm_client.py 拆）：clients（client/embed_client/备用单例）、embed（**embed_texts 嵌入唯一出口**，kb/rag 共用）、messages、thinking（MiniMax 适配）、retry、fallback（chat_create 唯一出口）、call（call_llm） |
| `backend/app/core/agent_tools.py` | **工具系统唯一注册表**：11 个原生工具 spec + 薄壳 handler + `KG_TOOLS` / `execute_kg_tool(_async)` 分发；末尾追加 MCP 工具 |
| `backend/app/core/quiz/chat_quiz.py` | **对话内出题（P0）**：后台异步出题 → 入库(`source="chat"`) → 推 `quiz_ready`；判分后答对确定性抬升 mastery |
| `backend/app/core/mcp_host.py` | **MCP 宿主层**：连 MCP server → `tools/list` → 生成同构 spec 并入注册表（schema 直通不重复维护）；in-memory 传输 + 同步桥 + 失败降级为空 |
| `backend/app/mcp_servers/web_search.py` | **网页搜索 MCP server**（标准协议，可独立运行）：工具 `web_search`，后端 ddgs（默认）/ SearXNG；stdio + Streamable HTTP |
| `backend/app/core/rag_tool.py` / `web_tool.py` / `download_tool.py` | 工具重型实现（MCP 风格纯函数）：RAG 检索 `rag_search` / 网页抓取 `fetch_webpage`（只读） / 资源下载入库 `download_resource`（SSRF 复用 web_tool） |
| `backend/app/core/agent_run_store.py` | agent_runs 表（运行记录**唯一事实源**，写/查/清理/统计） |
| `backend/app/core/event_bus.py` | 进程内 per-user 发布订阅 → SSE |
| `backend/app/core/token_counter.py` | `TokenUsage` / `count_messages_tokens` / `extract_usage`（token 计量唯一事实） |
| `backend/app/core/token_estimator.py` | 发送前预估（历史=agent_runs） |
| `backend/app/core/profile/` | 用户画像分层包：schema(结构/字段权重) + store(原子写/旧MD迁移) + markdown(渲染/解析) + manager(门面)。外部只 import `UserProfile` 与 `get_usage_mode(user_id)` |
| `backend/app/services/chat_service.py` | 对话编排：提示词组装 + 调 run_agent_loop + 后台图谱分析 |
| `backend/app/core/knowledge_graph.py` | 图谱存储（SQLite + 节点 MD 文件），`KnowledgeGraph(user_id)` 实例级隔离 |
| `backend/app/core/kg_taxonomy.py` | **建节点时的学科/板块自动判定**（规则优先 + LLM 兜底，失败保持未分类；`assign_taxonomy` async / `assign_taxonomy_sync` 同步桥） |
| `backend/app/core/rag_pipeline/`、`kb/`、`rag/`、`hybrid_search/`、`quiz/` | RAG / 知识库 / 图谱索引 / 出题 |
| `backend/app/core/collector/` | 采集链路：`adapters/`（数据源）+ `pipeline_ingest.py`（切章入库）+ `chapterizer.py`；其中 `quiz_splitter.py`（试卷整卷文本 → 逐题，纯函数零 LLM）**已就绪但尚未接入上传链路** |
| `backend/app/api/v1/` | FastAPI 路由层（纯 HTTP 薄壳） |
| `backend/app/models/schemas.py` | Pydantic 请求/响应模型 |

---

## 2. FastAPI REST 端点总表（前缀 `/api/v1`）

> 认证：除 `POST /auth/register`、`/auth/login` 外全部需 `Authorization: Bearer <JWT>`；`user_id` 一律由 `Depends(get_current_user)` 从 token 解析，**不要**在 body/query 传 user_id。
> 端点级 schema/参数细节：运行后端后开 `http://localhost:8000/docs`。

### 2.1 对话（loop 前端入口，最核心）
| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/chat` | 一次性回复（旧兼容；内部走 run_agent_loop，非流式） |
| POST | `/chat/stream` | **主对话端点**：SSE 流，事件协议见 §4.4 |

### 2.2 Agent 运行记录（只读/治理）
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/agent/runs?limit=&offset=` | 本人运行列表（不含 evidence），分页 total |
| GET | `/agent/runs/stats` | 概览：条数/成功率/LLM 调用/累计 token/证据体积 |
| GET | `/agent/runs/{run_id}` | 单次详情（含 evidence 证据序列，越权返回 404） |
| DELETE | `/agent/runs/{run_id}` | 删除单条 |
| DELETE | `/agent/runs` | 清空本人全部 |

### 2.3 知识图谱
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/knowledge/events` | SSE 全局事件（旧端点；新前端走 /chat/stream） |
| GET | `/knowledge/graph?subject=&board=` | 图谱数据（按需切片：全量 / 整学科 / 学科+板块）。`subject=未分类`（常量 `graph_middleware.SUBJECT_UNCLASSIFIED`）返回无学科归属节点，前端收藏栏一次只渲染一个学科 |
| GET | `/knowledge/subjects` / `/knowledge/boards?subject=` | 学科 / 板块两级分组 |
| GET | `/knowledge/node-ids` | 全部节点 ID |
| GET/PUT/DELETE | `/knowledge/node/{node_id}` | 节点 CRUD |
| PUT | `/knowledge/node/{node_id}/info` / `/mastery` | 节点信息 / 掌握度 |
| POST | `/knowledge/node` | 创建节点（未指定 `tags` 学科/`board` 板块时自动判定归属，见 `core/kg_taxonomy.py`） |
| POST/PUT/DELETE | `/knowledge/edge...` | 边 CRUD（含 `/edge/{edge_id}`） |
| POST | `/knowledge/ai/edit` | AI 建议批量应用 |
| POST | `/knowledge/decompose` | 问题拆解 |
| GET | `/knowledge/learning-path` / `/next-to-learn` / `/stats` | 学习路径 / 推荐 / 统计 |

### 2.4 用户画像
| 方法 | 路径 |
|---|---|
| GET/PUT/PATCH | `/profile`（PATCH=局部更新 `data`；PUT=`content`+`op` 兼容旧） |
| POST | `/profile/notes`（AI 教学笔记追加） |
| DELETE | `/profile/notes/{note_id}` |

### 2.5 会话持久化（前端本地记录同步）
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/conversations` / `/conversations/{conv_id}` | 列表 / 详情 |
| POST | `/conversations` / `/conversations/sync` | 保存 / 批量同步 |
| DELETE | `/conversations/{conv_id}` | 删除 |

### 2.6 RAG / 知识库（上传文档）
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/rag/search` / `/rag/stats`，POST `/rag/index` | 全量索引重建等 |
| GET | `/kb/tree` | 目录树 |
| POST | `/kb/folder` / `/kb/upload` | 建文件夹 / 上传文件 |
| DELETE | `/kb/node/{node_id}` | 删除文件/夹 |
| GET | `/kb/search?q=` | 混合检索（向量+BM25 RRF） |
| POST | `/kb/context` | 注入知识库上下文字段 |
| GET | `/kb/stats`，POST `/kb/graph/generate` | 统计 / 生成图谱 |

### 2.7 出题（quiz）
`POST /quiz/generate`、`GET /quiz/questions`、`GET /quiz/questions/{id}`、`POST /quiz/{id}/grade`、`GET /quiz/stats`

**对话内出题（P0，2026-09-14）** 走 agent 工具而非 REST：`quiz_generate`（后台异步出 1 道客观题）
→ `quiz_ready` SSE 事件 → 学生作答 → `grade_answer`（规则判分，答对自动 +20 掌握度）。
详见 `core/quiz/chat_quiz.py`；端到端冒烟 `scripts/smoke_chat_quiz.py`。

**掌握度更新的唯一主信号 = 出题判分（①，2026-09-14）**：
- 实测 `update_mastery` 工具**从未被 AI 调用过**（12 次运行 0 次）—— "当用户正确回答/理解后适当调整"
  是不可执行的软约束（苏格拉底教学里"学生在回答我"是每轮常态，模型分不清答对小题 vs 掌握知识点）。
- 已改造：掌握度由 `grade_answer` **确定性**更新（答对 +20）；`update_mastery` 收敛为仅限 3 种硬证据。
- **提示词坑**：只写"学生表示理解 → 出题"**无效**，模型会继续苏格拉底追问而不调工具；
  必须提成**铁律级**并写明"**不要**用追问代替出题"（对立表述）。
  → 触发类提示词必须写清"不要做什么"，否则会被更强的既有教学原则盖过。
- 出题必须**跨调用去重**（`generate_quiz(avoid_questions=…)`）：否则学生重答同一道题就能刷掌握度。
- 验证：`scripts/smoke_chat_quiz.py --simulate "学生发言"`（打印真实工具调用序列）。

### 2.8 资料采集（collector）
`POST /collector/search`、`POST /collector/tasks`、`GET /collector/tasks/{id}`、`POST /collector/tasks/{id}/cancel`、`GET /collector/stats`

### 2.9 认证
`POST /auth/register`、`POST /auth/login`、`GET /auth/me`

---

## 3. Agent Loop 内部契约（改架构前必读）

### 3.1 核心函数 `run_agent_loop`

```python
# core/agent_loop.py
async def run_agent_loop(
    system_prompt: str,          # 含图谱/RAG/工具能力说明（chat_service 组装）
    messages: list,              # 对话历史（dict 或 Pydantic ChatMessage 均可）
    *,
    kg,                          # KnowledgeGraph 实例（绑定 user_id）
    max_rounds: int = 5,         # 工具执行轮上限；到限仍要工具→强制收尾(空 tools 再问一次)
    tool_timeout_secs: int = 60, # 单工具超时
    temperature: float = 0.3,    # 循环内统一低温
    user_id: int | None = None,  # None=纯测试(不推事件不落库)；传值=默认可观测
    db_dir=None,                 # 测试注入临时目录用
) -> AgentRunResult
```

`AgentRunResult` 字段：`text`（最终面向用户文本）、`rounds`（[{round,tool,args_head,ok,duration_ms,result_head}]）、`evidence`（证据级步骤序列：thinking 全文/完整 tool arguments/tool 返回）、`total_llm_calls`、`context_tokens`（旧，prompt 累加）、`token_usage`（完整明细，优先用）、`estimated_prompt_tokens`。

### 3.2 一次运行的完整链路（事件↔记录唯一 run_id）
```
run_agent_loop(user_id=uid)
  ├─ 生成 run_id（uuid hex）
  ├─ 事件实时推 EventBus（均带 run_id）→ SSE 前端
  └─ 结束（正常 status=ok / 异常 status=error）自动落 agent_runs 表
       → 前端凭 run_id 可从 GET /agent/runs/{run_id} 回源全证据
```
**规则：调用方不需要也不应自己 save_trace / 落库**——传 user_id 即全自动。

### 3.3 LLM 原语使用边界
| 函数 | 场景 | 是否带工具 |
|---|---|---|
| `run_agent_loop` | **所有对话** | 带 KG_TOOLS |
| `call_llm(system_prompt, messages)` | 一次性文本/JSON：出题/判分/GraphAnalyzer/图谱生成 | 不带 |

### 3.4 SSE 事件协议（POST /chat/stream 响应体）
每事件一行 `data: {"type": "...", ...}`，末尾 `data: [DONE]`。除 graph_updated/error 外 **均带 run_id**。

| type | payload 关键字段 | 说明 |
|---|---|---|
| `agent_start` | `{max_rounds}` | 循环开始 |
| `thinking` | `{text}`(≤200字) | MiniMax reasoning_details，展示友好截断（evidence 里存全文） |
| `tool_start` | `{tool, args_head(≤200), round}` | 工具开始 |
| `tool_result` | `{tool, ok, summary(≤120), duration_ms, round}` | 工具结束 |
| `text_delta` | `{text}` | **最终回答**（前端按 token 流拼接；兼容旧 `{token}`） |
| `agent_done` | `{rounds, total_llm_calls}` | 循环结束 |
| `graph_updated` | — | 图谱变更通知刷新 |
| `error` | `{code, message, module, detail}` | 错误 |

**注意**：`chat_service._consume_agent_events` 是白名单 if/elif **无 else** —— 新增事件类型会被静默丢弃，需同步加透传。

**常驻长连接事件（非 /chat/stream）**：`GET /knowledge/events`（前端 `EventSource`，登录后一直连着）。
2026-09-14 起该端点改为 `subscribe(user_id=...)`（原来全局队列）。

| type | payload 关键字段 | 说明 |
|---|---|---|
| `graph_updated` | — | 图谱变更通知刷新（全局广播，每个用户队列都会收到） |
| `quiz_ready` | `{ok, node_id, subject, questions[], message?}` | **对话内出题完成**（后台任务约 4~40s 后到达）。`questions` 载荷**刻意不含 answer/analysis**（防作弊）；`ok=false` 时带 `message` |
| `error` | `{code, message, module, detail}` | 错误 |

> ⚠️ `publish(..., user_id=X)` 只在 X 的队列**已存在**时投递，否则静默丢弃 ——
> 后台任务推事件前，用户必须已连上 `/knowledge/events`（或刚跑过 /chat/stream）。

### 3.5 agent_runs 表与存储 API（core/agent_run_store.py）
表：`agent_runs`（run_id PK / user_id / status / started_at / ended_at / total_llm_calls / context_tokens / token_usage JSON / estimated_prompt_tokens / final_text / evidence JSON）。

| 函数 | 说明 |
|---|---|
| `save_run(run_dict, db_dir)` | upsert，**唯一写入方=agent_loop** |
| `list_runs(user_id, limit, offset)` / `get_run` / `count_runs` | 查询（分页/越权隔离） |
| `run_stats(user_id)` | 概览 |
| `recent_completion_tokens` | 供 token_estimator |
| `prune(full_days=30, strip_days=180)` | 分层保留：≤30 天全量；>30 天摘 evidence 留元数据；>180 天整行删（启动+每日 GC 自动执行） |
| `delete_run` / `delete_user_runs` | 治理 |

### 3.6 已知耦合与坑（改架构/重构时对照）
1. **~~core→api 反向依赖~~（2026-09-08 已解决）**：`create_node_from_ai`/`apply_suggestion`/`load_suggestions`/`save_suggestions` 四个纯业务函数已下沉到新模块 `core/knowledge_writer.py`（AI 写图谱层），`agent_tools._h_add_node` 与 `chat_service` 改从 core 导入；api 层原定义与 json/Path 无用 import 已删除。
2. **~~私有符号跨模块~~（2026-09-08 拆分已解决）**：原 `agent_loop` import `llm_client` 的 5 个下划线成员，已随 `core/llm/` 包化收敛为**公开契约**（`chat_create`/`build_api_messages`/`strip_think_tags`/`LLM_EXTRA_BODY`/`MODEL_NAME`）。agent_loop 用别名保持命名（`import chat_create as _chat_create`），不再触碰私有符号。
3. **~~llm_client 上帝模块~~（2026-09-08 已拆，718 行归零）**：按职责拆为 `core/llm/` 原语包（clients/thinking/messages/retry/fallback/call）+ `core/agent_tools.py`（注册表+分发）+ `core/rag_tool.py`/`core/web_tool.py`（重型实现）。文件已删，全库零残留引用。
4. **同步↔异步线程池（2026-09-14 起为"双入口"）**：agent_loop 调 `agent_tools.execute_kg_tool_async` ——
   **协程 handler 直接 await**，同步 handler 仍走 `asyncio.to_thread` → 其内部 `rag_tool.rag_search` 再用 `_run_async` + 线程池起新 loop（`_run_async` 随 rag_tool 私有）。
   同步入口 `execute_kg_tool` 保留给测试/非 async 调用方，**无法执行协程 handler**。
   为什么必须有异步入口：`asyncio.to_thread` 的工作线程里没有运行中的事件循环，工具内无法
   `create_task`（如后台出题）；也不能退回 `asyncio.run` —— `llm/clients.py` 的 AsyncOpenAI 单例
   在模块导入时创建，跨事件循环复用会报 "Event loop is closed"。
5. ~~工具注册 3 处分散~~（2026-09-08 已收敛）：`agent_tools._TOOL_SPECS` 注册表（name/description/parameters/handler）为唯一注册入口；`KG_TOOLS`（模型 tools 参数）与 `execute_kg_tool`（分发）均由注册表生成/查表驱动。`chat_service.TOOL_CAPABILITY_PROMPT` 保留为教学触发语义层（与注册表有意分离）。
   **2026-09-12 更新（MCP 已引入）**：`core/mcp_host.py` 在 import 时调用 `mcp_tool_specs()`，把 MCP `tools/list` 的 name/description/inputSchema 原样转成注册表 spec（含闭包 handler）后 `_TOOL_SPECS.extend(...)`。故：注册表仍是唯一入口，**执行侧与模型侧零改动**；MCP 工具名前缀 `mcp__<server>__<tool>`；未装 mcp 包 / 连接失败 / `WEB_SEARCH_ENABLED=false` 时返回 `[]` 静默降级（原生工具行为不变）。新增 MCP server = 在 `mcp_host._SERVERS` 加 `(前缀, 模块路径)`。
   **2026-09-14 更新（异步 handler）**：spec 新增可选 `timeout_secs`（单工具超时覆盖，None=用
   agent_loop 默认 60s）；handler 可以是 `async def`（由 `execute_kg_tool_async` 直接 await）。
   该字段**不出现在 `KG_TOOLS`**（模型侧 schema 不变）。

6. **`/knowledge/events` 用户队列的两个陷阱（2026-09-14 修）**：
   - 该端点原用 `subscribe()`（全局队列），收不到 `publish(user_id=...)` 的 per-user 事件 → 已改 `subscribe(user_id=...)`。
   - `event_bus._cleanup_stale_queues` 会按 TTL 清"空闲"队列，但长连接只在建立时调用一次
     `_get_user_queue`，不刷新访问时间 → 队列被清掉后订阅者仍在 await 那个已移除的对象，
     从此**永久收不到事件**。已加 `_active_subs` 计数保护（有活跃订阅者的队列不清理）。
7. **messages 双形（dict/Pydantic）兼容不全**：`_build_api_messages`/`estimate_single_call` 兼容两者，但 `token_estimator._predict_completion_features` 只读 dict。
8. **token 指标冗余**：`AgentRunResult`/`agent_runs` 表同时保留 `context_tokens` + `token_usage`（前者可由后者推导，向后兼容保留）。
9. **双记录并存**：conversations 消息内嵌 thinking/tools 字段（前端 MessageBubble 在消费）vs agent_runs.evidence —— 前端回源需知道两处；未来统一方向以 agent_runs 为回放源。
10. **🔴 Jinja2 占位符必须是双花括号（2026-09-14 修，排查成本极高）**：
   `data/prompts/system_prompt_common.j2` 曾把占位符写成**单花括号** `{knowledge_graph_summary}`
   / `{user_profile}` —— Jinja2 只认 `{{ }}`，单括号是**字面文本**，渲染时原样输出，
   **不报错、不告警**。后果：adaptive / free_talk 模式下 AI **完全看不到知识图谱与学生画像**
   （57 个节点 id 在 prompt 里命中 0 个），「框架约束」整段形同虚设。
   该 bug 曾被误判为"模型幻觉"（AI 说"你的图谱可能是空的"）并被 `EMPTY_GRAPH_PROMPT` 打补丁掩盖；
   甚至 `tests/test_prompt_loader.py` 把它当成"预期行为"锁死。
   → 改模板/加载器后，**必须断言"值被注入"，而不是"占位符名字出现"**。
   → 诊断：`backend/scripts/probe_graph_prompt.py`（节点 id 命中数应等于节点总数）。
   ⚠️ 注入后系统提示词约 14.4k tokens（57 节点），注意 `LLM_CTX_BUDGET`（默认 32000）留给历史的余量。

---

## 4. 工具系统：如何新增一个工具

**当前 12 个工具**：原生 11 个 —— `add_knowledge_node` / `update_node_content` / `update_mastery` / `add_edge` / `delete_node` / `update_user_profile` / `fetch_webpage`（只读网页正文）/ `download_resource`（下载文档/电子书入库知识库）/ `rag_search` / `quiz_generate`（对话内出题）/ `grade_answer`（判分）（其中 `add_knowledge_node` 支持模型自报 `subject`/`board`，缺的部分由 `core/kg_taxonomy.py` 自动判定）；**MCP 1 个** —— `mcp__websearch__web_search`（联网搜索，server 源码 `app/mcp_servers/web_search.py`，宿主层 `core/mcp_host.py`）。

**注册唯一入口**：`core/agent_tools.py` 的 `_TOOL_SPECS` 注册表（MCP/OpenAI function-calling 同构）。图谱/画像工具 handler 即薄壳在此；重型实现放领域模块（`web_tool.py` / `rag_tool.py`）被 handler 引用。

新增工具只需两步（旧版需改 3-4 处）：
1. 在 `_TOOL_SPECS` 加一条 spec：`name` / `description`（给模型看）/ `parameters`（JSON Schema，同 MCP inputSchema）/ `handler`，
   可选 `timeout_secs`（单工具超时覆盖）。
2. 写一个 handler `(args: dict, kg) -> str`（同步）或 `async def`：直接调 KnowledgeGraph / 领域函数
   （需要 RAG 检索等 async 能力的直接调 `rag_search()` 等既有封装，其内部已处理事件循环）。
   - **异步 handler 的场景**：需要在工具内 `asyncio.create_task` 起后台任务（如 `quiz_generate` 后台出题）。
     不要为了"想 await"而改成协程再 `asyncio.run` —— 见 §3.6 坑 4。
   - `KG_TOOLS`（模型 tools 参数）与 `execute_kg_tool(_async)`（执行分发）由注册表自动生成，无需再改。
   - **可选**：若该工具需要"何时主动调用"的教学语义，才在 `services/chat_service.py` `TOOL_CAPABILITY_PROMPT` 补一条触发规则。
3. 若工具会改动图谱 → 确认权限 `added_by` 体系与图谱分析是否需同步。

> 工具返回约定：给模型看的友好文本（非异常向上抛）；业务错误以"操作失败: …"、权限拒绝以"权限不足: …"回填 tool 消息，让模型自行应对。
> 执行异常分级：`ValueError`=业务错、`PermissionError`=权限不足、其余=意外错，由 `execute_kg_tool` / `execute_kg_tool_async` 共用 `_tool_error` 统一兜底 + `ErrorCode.LLM_TOOL_EXEC_FAILED` 记日志。

---

## 5. 开发规范速查

- **错误码**：`core/error_codes.py` 定义 `ErrorCode.*`；异常信息以 `[E-XXX]` 开头，统一走 `log_error()`。
- **用户隔离**：图谱/画像/RAG/记录全部按 `user_id` 分区；`KnowledgeGraph(user_id)` 每次新建，用毕 `close()`（别跨协程共享实例）。
- **模式过滤**：RAG 检索前读 `UserProfile.get_usage_mode()`（缺省 personal）。
- **消息结构**：ChatRequest.messages 为 `[{role, content}]`（Pydantic ChatMessage，见 schemas.py）；补 `mode`（adaptive/free_talk/recursive）与可选 `current_node`/`kb_node_ids`。
- **run_id 贯穿**：新增"运行相关"事件务必携带 run_id，便于前端回源。
- **前端接入 SSE**：`frontend/src/api/index.js` 的 streamChat 封装已处理全部事件回调（onThinking/onToolStart/…），新事件类型需在此加回调分发。
- **README 纪律**：根 README 遵循 `docs/README_编写规范.md`；本文件是开发索引，与 README 视角不同。

## 6. 深度设计文档索引
| 文档 | 内容 |
|---|---|
| `docs/AgentLoop_重构设计讨论.md` | loop 设计决策（路线 A、护栏、事件/记录整合） |
| `docs/AgentLoop_业界调研与学习路线.md` | 业界 Agent 模式调研 |
| `docs/token_consumption_prediction_research.md` | token 预估三层策略 |
| `docs/RAG_*.md`、`QUIZ_出题逻辑调研.md` | RAG/出题设计 |
| `docs/MCP_网页搜索工具_调研与实施方案.md` | MCP 网页搜索：协议/生态调研 + 实测数据 + 分期实施记录 |
| `docs/Docker_学习路径与工程化部署.md` | Docker 原理与学习路径（入门，非运维） |
| `docs/运维_生产上线与日常运营指南.md` | **上线后运营手册**：网关/SSE/证书、Linux 服务器运维、镜像治理、发布回滚备份、监控告警、排障表、真实事故复盘、上线检查清单 |
| `backend/app/core/*.py` docstring | 模块级最新契约（代码优先于文档） |
