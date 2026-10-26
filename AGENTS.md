# AGENTS.md — AI Tutor 给 AI Agent 的操作手册

> **定位**（依 [agents.md](https://agents.md/) 规范）：本文件是**给 agent 的操作手册**，不是项目百科。
> 只写三类：① **可直接执行的命令** ② **不写就会做错的硬约束/坑** ③ **文档路由**。
> 架构说明、模块职责、端点 schema **刻意不写在这里** —— 权威依次为：代码 `docstring` → 各包 `README.md` → `docs/*.md` → 运行后的 `http://localhost:8000/docs`。
> 为什么这么瘦：AGENTS.md 能把 agent 的运行时间中位降低 **~28.6%**、输出 token 降低 **~16.6%**（arXiv 2601.20404），但**仓库概览型内容不提升成功率、反而让成本 +>20%**（ETH arXiv 2602.11988）→ 只留可执行信息。取舍经过见 `docs/归档/工程实践/AI编码Skill_ponytail实测与通用Skill选型.md`。
> 冲突裁决：用户对话中的显式指令 > 本文件。

---

## 0. 命令与环境（照抄，别自己猜）

| 事项 | 命令 / 路径 |
|---|---|
| 后端 Python | `backend/venv/Scripts/python.exe`（**必须用它**；系统 Python 的 fastapi 过旧会导入失败） |
| 一键启动 | 根目录：`.\start.ps1`（自动探测：有 Docker 走容器，否则本机开发模式；`-Local` / `-Docker` 强制，`start.cmd` 双击同一入口） |
| 启动后端 | cwd=`backend`：`venv\Scripts\python.exe -m uvicorn app.main:app --port 8000 --workers 1` |
| 启动前端 | cwd=`frontend`：`npm run dev`（Vite 5173；`/api` 已代理到 8000，SSE 走 ws） |
| 跑测试 | cwd=项目根：`backend/venv/Scripts/python.exe -m pytest backend/tests -q -m "not llm_api"`。⚠️ **若报 `PermissionError: …Temp…pytest-of-<user>`**：那是宿主沙箱对 `mkdir(mode=0o700)` 的处理缺陷（pytest 的 basetemp 正好用它，`:158`）→ 用兜底 `backend/venv/Scripts/python.exe .dsh_local/run_pytest.py backend/tests -q -m "not llm_api" -p no:cacheprovider --basetemp=.dsh_local/pytest_tmp`（脚本机制见 `docs/架构/重构实施计划_可执行清单.md` §1.4；会话切「完全权限」后不再需要）。**运维后台套件必须另起一次调用**（两边包名都叫 `app`，合跑必 `ModuleNotFoundError`）：同上换 `backend-admin/tests` 且用 `backend/venv` 的 python（`backend-admin/venv` 未装 pytest） |
| 真实 API 测试 | 加 `-m llm_api`（真实付费 key，需 pytest-asyncio，单独跑） |
| 配置真值 | 根目录 `.env`（唯一，`core/config.py` 读 `parents[3]/.env`） |
| 版本 | 真源 **两处**：`backend/app/__init__.py::__version__`（`main.py` 的 `FastAPI(version=…)` 读它）+ `frontend/package.json`。发版 = 改这两处 → `CHANGELOG.md` 顶部加一节 → `git tag -a vX.Y.Z`（**别在别处再写版本号**） |
| LLM/嵌入配置 | 对话：**每用户**（设置页「模型」自配，存 `data/llm/{uid}/models.json`（A1b 迁移后单根 = `<repo>/data`；该目录**已在 `.gitignore` 里逐个列出**，否则明文 Key 会被 `git add` 收进历史），Key 明文只在服务端）→ 主→备自动降级；未配置则回落根 `.env` 的 `LLM_API_KEY` + `LLM_BASE_URL` + `MODEL_NAME`（默认 **MiniMax-M3**）= **系统默认档**。嵌入固定：`DASHSCOPE_API_KEY` + `EMBED_BASE_URL`（阿里 text-embedding-v4，**不随用户模型切换**——换嵌入要重建索引） |

| B 站登录态（视频导入用） | 唯一来源 = **`data/bili/cookies.txt`**（Netscape 格式，由「扫码登录 B 站」写入，实现 `core/kb/bili/login.py`；**已在 `.gitignore`，删文件 = 退出登录**）→ yt-dlp 的 `cookiefile` 与平台字幕接口共用同一份；`BILI_COOKIE` / `BILI_COOKIE_FILE` 可覆盖。`BILI_COOKIES_FROM_BROWSER` **已弃用**：Edge/Chrome 127+ 有 App-Bound 加密，外部程序必然解不开（实测 Edge 156 → `Failed to decrypt with DPAPI`）。**别新建第二处凭证文件** |

## 1. 硬约束（违反必出事）

- **uvicorn 必须 `--workers 1`**：EventBus 用户队列与进程内定时 GC 依赖单进程，多 worker 各自持有总线 → 事件必丢。
- **`.env` 只有根目录一份**，不要在 `backend/` 下另建。
- **`nodes.id` 是全局 TEXT 主键**（非 per-user）；脚本/测试建节点前先 `INSERT OR IGNORE INTO users`。**给 `knowledge.db` 加表**（`node_aliases`/`mastery_events`/`doc_node_marks` 同族）= `core/kg/schema.py`（`_SchemaMixin._create_tables` 建表 + `_auto_migrate` 补索引），**并同步两处**：`scripts/inspect_graph_quality.py` 的体检口径、`AGENTS.md §5` 路由。**删号清单不需要手工同步**（2026-10-09 起由 schema 派生：`backend-admin/app/core/db.py` 遍历 `sqlite_master` + `PRAGMA table_info`，带 `user_id` 列的表一律 `DELETE ... WHERE user_id = ?`，`users` 走 `id`；回归 `backend-admin/tests/test_admin_api.py`）—— 别再写第二份手写清单。**给 `knowledge.db` 加列**（如 `nodes.sources`、`nodes.subject`）= 写进 `core/kg/schema.py::_auto_migrate` 的 `ALTER TABLE` 清单：`CREATE TABLE IF NOT EXISTS` 不给老表加字段（与 `records.py` 那条同理）。
- **用户隔离**：图谱/画像/RAG/记录全按 `user_id` 分区；`KnowledgeGraph(user_id)` 每次新建、用毕 `close()`，别跨协程共享实例。
- **记录型库（`agent_runs` / `llm_usage` / `agent_debug_logs`）统一走 `core/records.py`**：连接 / 建表 / 补列 / 过期清理的唯一实现，三张表分属两个库（`agent_runs.db`、`debug_log.db`）。**加表 = 写自己的 schema + 调 `records.connect` + 在 `main.py::_prune_once` 的 `_JOBS` 加一行**，别另抄一套连接代码。**加列 = 写进该模块的补列清单**（如 `store._COLUMN_MIGRATIONS`，由 `records.ensure_columns` 就地补）：`CREATE TABLE IF NOT EXISTS` 既不会给老表加字段、也不会自动接线（`token_estimate` 就是这么加的；回归 `test_legacy_db_gets_token_estimate_column`）。
- **🔴 Jinja2 占位符必须双花括号**（单花括号是字面文本，**不报错**）：`data/prompts/system_prompt_common.j2` 曾把 `{knowledge_graph_summary}` / `{user_profile}` 写错 → AI **完全看不到图谱与画像**，曾被误判为"模型幻觉"，排查成本极高。改模板/加载器后**断言"值被注入"**，而不是"占位符名字出现"；诊断脚本 `backend/scripts/probe_graph_prompt.py`；回归 `backend/tests/test_prompt_loader.py`。
- **loop 默认值**：`max_rounds=5` / 单工具超时 `60s` / 循环内 `temperature=0.3` / 工具调用总次数 `12` / run 墙钟 `180s` / 同参数重复 `≤2` / 连续失败熔断 `3`（改默认值 = 改 `core/agent/loop.py` 常量 + 同步 `run_agent_loop` 签名、测试与文档）。**`max_rounds` 挡不住模型用相同参数反复重试**（`Tool Result Clearing` 还会诱发它），那一类由 `loop._LoopGuard` 拦。
- **排查"输出无用数据"**：调试日志 `core/agent/debug_log.py`（控制台 + SQLite `data/agent_debug/debug_log.db`，`recent(run_id=)` 回看，保留 14 天）+ 终止原因 `stop_reason`（`natural`/`max_rounds`/`time_budget`/`call_budget`/`fail_circuit`/`error`，同一取值出现在 `AGENT_DONE` 事件与 `agent_runs` 证据的 `loop_stop`）。安全边界规则在 `core/agent/loop_guard.py`。
- **日志**：唯一配置在 `core/logging_setup.py`，由 `app/__init__.py` **导入即初始化**（所以 `python scripts/xxx.py` 直跑也有日志——曾整段配置写在 `main.py`，脚本直跑时 INFO 全丢）；落点 `backend/logs/tutor.log`，`LOG_DIR` 是相对路径时按 **backend 目录**解析（不随 CWD 漂移；曾同时写出项目根与 backend 两份 `tutor.log`）。排查三入口：`tutor.log`（全局）/ `GET /agent/logs?run_id=`（agent 流水）/ `GET /agent/usage`（token 账）。**加日志只记"判定了什么、放弃了什么、为什么"**，不要记每次成功的流水。
- **git 与并发**：不主动 commit/push；本仓库**常有多个 AI 会话并发写** → 提交必须**路径限定**（禁 `git add -A` / `commit -a`），内容与预期不符先查 mtime。
- **代理残留**：注册表 `ProxyEnable=1` 残留在代理退出后会让 pip 报 `ProxyError`（httpx 也读注册表）；装包前 `$env:NO_PROXY="*"`；推 GitHub 前 `$env:HTTPS_PROXY="http://127.0.0.1:7897"`。

## 2. 项目特有约定（非标准实践，照做）

- **单一事实源 / 唯一出口**：LLM 原语在 `core/llm/`（**检索侧**嵌入 `embed.py::embed_texts` —— 语义去重侧另走 `kb/embedder.py::ApiEmbedder`，共用其常量与失败语义；JSON 提取 `json_extract.py::extract_json`；`chat_create` 是唯一带 fallback 的出口（`stream.py::chat_create_stream` = 其流式变体，正文逐 chunk 回调、返回同形响应，候选链/重试仍唯一在 `chat_create` —— 别在别处另建 client；`loop._chat_once(on_delta=...)` 非空即走它）；真打 LLM 仅 `agent/loop._chat_once` 与 `call_llm`）；运行记录 `core/agent/store.py`（**唯一写入方 = loop**）；工具注册表 `core/agent_tools/registry.py`；token 计量 `core/token_counter.py`；**节点来源溯源 = `KnowledgeGraph.add_sources`**（并入去重、绝不覆盖），配套 `doc_node_marks` = 「资料 → 该资料产出/影响的节点」**账本**（`evidence.kind` 区分 `new`/`hit`）+ **增补队列**（`status` 区分 `pending`/`filled`）—— 两职责**有意共用一表**（同资料、同批节点、同生命周期）；唯一写入口 `mark_doc_nodes`/`set_mark_status`，状态**单向** `pending`→`filled`。**图谱 `nodes`/`edges` 的存储布局与检索通路说明** = `docs/知识图谱/知识图谱_数据结构与检索通路_评审与改良方案.md`（表定义仍以 `core/kg/schema.py::_create_tables` 为唯一真值）；**图谱只保留最小节点与关系边** —— 主题层（`themes`/`node_themes`、聚合下钻）已于 2026-09-27 整体下线**并删表**，勿再引入任何第二级结构或聚合大节点。
- **新建节点只有一个出口** `KnowledgeGraph.create_node_with_content(node_data, content, origin)`：4 条写路径（Agent 工具写层 `knowledge_writer` / 书籍建图 `graph_generator` / 手动 API / 问题拆解）全部走它，MD 模板唯一来源 = `core/kg/_common.py` 的 `ORIGIN_NOTES`。**禁止**在调用方自己 `open(kg.nodes_dir/...)` 写节点 MD；新增写路径时守住 `tests/test_node_write_paths.py`（逐条断言 origin，加了新路径而没复用模板就会红）。**同名并轨也在这一层**（2026-09-20）：命中同名（`normalize_node_name` + 同用户同学科，判重唯一实现 = `KnowledgeGraph.find_node_by_name`）则不新建、只并入正文，**返回实际落点 ID** —— 调用方必须用返回值建边/回执，别再自己写一份同名比较。建图语义去重状态 `dedup_status`（`ok`/`degraded` hash 兜底/`unavailable` 欠费）随 aggregate 带出。
- **图谱节点「创建/改文即入索引」只有一处收口 = `core/rag/node_indexer.py`**（2026-10-11，P0-1）：`KnowledgeGraph` 的四个写出口（`create_node_with_content` / `update_node_content` / `create_section` / `write_section`）只调 `mark_dirty`（纯内存、零 IO），由各调用方在合适时机 `flush`（异步）/ `sync_flush`（同步工具）—— **不要在写路径里各调一次 `rag_manager.index_node`**（原先全仓库只有后台图谱分析一个调用点 → 四条写路径建完都不入索引，S6 按需检索长期接近全空）。新增第 5 条写路径时**不用改本文件**（出口已挂），只需保证调用了 flush。**删除侧另有一处收口**（2026-10-11）：`remove_node` / `remove_subject` 内部调 `_purge_rag_index_for_nodes` → `rag_manager.delete_node_index`（原先只有 API 层两处显式清，Agent `delete_node` 工具与 AI 建议采纳走 kg 直删会留索引 → 已删知识点仍被 S6 检索注入）；新增删除路径同样**不用改调用方**。回归 `backend/tests/test_node_index_on_create.py`、`backend/tests/test_kg_rag_purge.py`（并要求 `tests/conftest.py::_isolate_rag_index` 隔离真实 `data/rag`）。
- **节点小节化（2026-09-27）= 纯文件层，不加任何表**：一个节点可以是**文件夹** `nodes/{uid}/{node_id}/`（`manifest.json` 路由 + 若干**平行**小节 MD，**没有主 MD**；老节点仍是单 `{node_id}.md`，不迁移）。读写唯一出口 = `KnowledgeGraph` 的 `manifest_path`/`has_sections`/`read_manifest`/`list_sections`/`read_section`/`create_section`/`write_section`/`set_section_status`/`delete_section`/`add_quiz_ref`（manifest 原子写 + 实例锁）。**小节不是图谱节点**：不进 `nodes`/`edges`、不参与判重建边与图渲染（图渲染/检索/统计只看 SQLite，零文件 I/O）。删节点/删学科/删号必须**连目录整删**（`remove_node`/`remove_subject`/`backend-admin::purge_user_storage` 已接，新增删除路径要跟上）。生成管线 = `kb/section_generator.py`（两阶段、逐节纯 Markdown 直出、单节失败不拖垮整批）；**建图阶段② 也走它**（`graph_generator._fill_sections`：新概念直接小节化；命中**老单 MD 节点**才走 `_fill_legacy` 单篇追加），小节化后由 `KnowledgeGraph.set_content_status` 把骨架翻 `filled`。**读节点正文一律走 `node_content_text`（小节优先）**——图谱注入 / RAG / 出题 / 导出 / 体检共用它，直读 `{node_id}.md` 会拿到骨架占位（"待完善..."）。体检口径 = `inspect_graph_quality.section_metrics`；设计见 `docs/知识图谱/知识图谱_节点小节化_设计与实现方案.md`。
- **LLM 调用边界**：所有对话走 `run_agent_loop`（带 KG_TOOLS）；一次性文本/JSON（出题/判分/图谱生成）走 `call_llm`（不带工具）。
- **M3 三段坑**：思考与正文**共享** `max_tokens` 预算 → 批量结构化抽取必须 `thinking=False`，长 JSON 显式调大 max_tokens（否则"空正文 / 硬截断"交替出现）。
- **加工具 = 1 个新模块 + 1 行注册 + 3 份产物自动生成**：在 `core/agent_tools/tools/` 下**复制任一模块**（一工具一文件），改 `DESCRIPTION` / `PARAMETERS` / `GUIDANCE` / `handler` / `SPEC` 五处，再在 `tools/__init__.py` 的 `NATIVE_SPECS` 加一行；`KG_TOOLS`、执行分发、提示词「工具调用指南」全自动生效，**不要再手写第四份说明**（曾双源漂移：MCP 关闭后提示词仍教模型调不存在的工具）。编写规范/检查清单见 `core/agent_tools/tools/README.md`，一致性由 `backend/tests/test_tools_registry.py` 锁死。**MCP 工具不用改本仓库任何文件**：`core/agent_tools/mcp_host.py::_SERVERS` 加 `(前缀, 模块路径)` 即入注册表。
- **工具 handler**：同步 `(args, kg) -> str` 或 `async def`；只有当工具内需要 `asyncio.create_task` 起后台任务时才用 async（如 `quiz_generate`）——**不要为了"想 await"改协程再 `asyncio.run`**（AsyncOpenAI 单例跨事件循环会报 "Event loop is closed"）。业务错误回填 `"操作失败: …"`、权限拒绝回填 `"权限不足: …"`（`ValueError`/`PermissionError` 分级在 `dispatch` 统一兜底）。
- **SSE 事件改动三处同步**：`core/agent/events.py` 发射 → `chat_service._consume_agent_events`（**白名单 if/elif，无 else，不加就静默丢弃**）→ `frontend/src/api/index.js` 回调分发；运行类事件**必带 run_id**。**请求内事件（thinking/text_delta/tool_*/agent_*）只能走 `run_agent_loop(event_queue=...)` 的请求私有队列，禁止经 `event_bus`（`broadcast`/`notify`）** —— per-user 队列是对话流与常驻长连接 `/knowledge/events` 的**共享队列**，两个消费者会互相抢事件（2026-09-26 故障：长连接抢走 text_delta → 前端空流 → 表现为"对话没反应"，后端 agent_runs 却完整）；该队列只留 `graph_updated`/`quiz_ready`/`error` 三类通知。**两条对话路径都要传**（`process_message_stream` 与 `process_message`），回归 `tests/test_event_bus.py`。
- **通知类事件只有两个投递口（A2-1b，`publish` 已删除）**：全局刷新走 `broadcast(type, data=None)`；发给他一人走 `notify(user_id, type, data=None)`。**`notify` 先落库再推**（`core/notices.py` 的 `pending_notices`，与 agent_runs 同库）：推成功即标记已投递（与重连补拉互斥，不重复渲染），无订阅者则留库 → 客户端重连经 `GET /knowledge/notices/pending` 补拉。**别在调用方另写"推丢了就算"的分支，也别为通知另建一张表**；新增按 user_id 分区的行必须同步 `backend-admin` 删号清单与 `main.py::_prune_once` 的 `_JOBS`。
- **开发态自动刷新（改代码 → 页面自动同步，2026-09-27）**：前端改动走 **Vite HMR**（内置，无需接线）；后端改动靠 `chatStore.connectSSE` 的**断线重连信号** —— uvicorn `--reload` 重启必掐断 SSE → `onopen` 发 `backend-reloaded` 事件 → `HomeView` 重挂当前子视图（`:key="viewEpoch"`）+ `refreshGraph()`，**仅 `import.meta.env.DEV` 生效**（生产不受网络抖动影响）。⚠️ 新增子视图若数据由组件自己 `onMounted` 拉取，**必须挂 `:key="viewEpoch"`**，否则改后端后该页数据不刷新。事件名常量 `chatStore.BACKEND_RELOADED_EVENT` 是唯一来源。`start.ps1` 后端另加 `--reload-dir data/prompts`（不在 `app/` 下，漏了改 `.j2` 不重载）。
- **掌握度更新的唯一主信号 = 出题判分**：`grade_answer` 答对确定性 +20；`update_mastery` 只认 3 种硬证据（实测模型**从不主动调用**它）。**手动改掌握度的入口已全部删除（2026-09-28）：节点详情滑块 + `PUT /knowledge/node/{id}/mastery` 端点 + `chatStore.updateMastery`，勿重加**（`update_node_info` 的 mastery 写入通道本身保留，判分要用）。触发类提示词必须写成**铁律**并写明"**不要**用追问代替出题"（对立表述），否则会被更强的既有教学原则盖过；出题必须跨调用去重（`avoid_questions=`），否则重答同题可刷掌握度。
- **错误码**：`core/error_codes.py` 定义 `ErrorCode.*`；异常信息以 `[E-XXX]` 开头并走 `log_error()`。
- **请求结构**：`ChatRequest.messages = [{role, content}]`，另带可选 `current_node`/`kb_node_ids`（2026-09-26 起三种引导模式已合并为单一提示词，`mode` 字段已删，见 `data/prompts/system_prompt_common.j2`）；RAG 检索前读 `UserProfile.get_usage_mode()`。
- **知识库可见性 = 源自己证明，不是调用方传参**：`KbRagSource.should_query` 直接查"该用户有没有已索引资料"（`kb_manager.has_indexed_content`，只读探测、零副作用 —— 不许调 `_get_vec_store()`，那会给没用过知识库的用户凭空建库）。曾经它写成 `bool(ctx.kb)`，把"源是否启用"外包给调用方构造的 `ctx.kb`：chat 侧按前端 `kb_node_ids` 判空、rag_search 侧只在 `source=="kb"` 时构造，两处都错 → 上传资料对 LLM **整源不可见且不报任何错**（最难排查的一类）。**范围 ≠ 开关**（两个正交维度，别再让任一字段兼职）：`RagContext.kb` 只表达"在哪些文件里找"（None = 不限范围）；`RagContext.sources` 只表达"选哪些源"（None = 全部；`rag_search` 的 graph/kb 选择走它，不再靠把 kb 置空）。回归：`tests/test_kb_source_availability.py`、`tests/test_chat_kb_scope.py`、`tests/test_pipeline.py`（ctx.sources 段）、`tests/test_rag_tool.py`。
- **源分三类，别再混**：① `required=True`（现只有 `GraphStructureSource`）=「每次必须送达的上下文」，**不受** `router` 节流与 `ctx.sources` 影响（图谱结构是教学的唯一边界，学生说"你好"也得给）——kg 经 `ctx.metadata["kg"]` 传入，缺省返回空且**不得自查库**（否则测试会碰真库）；② 普通检索源（graph / kb）= 需要 query + 过节流 + 被 `ctx.sources` 选中；③ 图谱**结构**（有哪些知识点，保证可见）与图谱**片段**（相关正文，按需检索）是**两个**源。`chat_service._build_graph_summary` 经 pipeline 取结构（等价重构，注入文本逐字符不变，回归 `tests/test_graph_structure_source.py`）。
- **上传必须原子**：`kb_manager.upload_and_index` 里 `add_file` 先提交、`_index_document` 后跑，索引失败必须回滚删节点（回滚用 `except BaseException` —— 客户端断连的 `CancelledError` 绕过 `except Exception`，会留下"有正文、无索引"的孤儿：用户在知识库看得见、点得开，AI 永远检索不到）。**前端上传超时不再是 120s**（扫描版 PDF 逐页 OCR 是分钟级）。存量孤儿体检/重建 = `backend/scripts/reindex_kb.py`（`KbManager.reindex_file`：正文已在 `documents.extract_text`，不重新解析）。
- **原件（真预览用，2026-09-28）**：`data/kb/{uid}/files/{node_id}{ext}`，路径由 `kb_manager._raw_path` 从 (uid, node_id, `documents.file_type`) 完全推导 —— **不加表也不加列**；上传时与索引同批落盘（失败一起回滚），读出口 = `GET /kb/node/{id}/raw`（`kb_manager.get_raw_path`，前端 PDF/Word/Excel/图片走它，其余走 `/text` 的解析正文）。**新增任何删除路径都要跟着删原件**（`kb_manager.delete_node` 已接；删号由 `purge_user_storage` 整删 `data/kb/{uid}` 覆盖，不用动）。历史文件没原件 → `/raw` 404，前端静默回退正文。⚠️ 原件不设上限，扫描版 PDF 会原样占盘。
- **README 纪律**：根 README 依 `docs/工程实践/README_编写规范.md`（中文 SSOT，英文派生镜像）；本文件与 README 视角不同，**别互相复制**。

## 3. 写代码的纪律（已装 skill，按场景选一个）

| 场景 | 用哪个 skill | 要点 |
|---|---|---|
| 小改 / 纯样式 / 文案 | `ponytail` | 七阶阶梯压缩 diff；**不为指标造测试** |
| 中大型 / 数据层 / 安全·并发路径 | `test-driven-development` | 先写失败测试，再实现 |
| 修 bug | `systematic-debugging` | 先定位根因再动手，"症状补丁"算失败 |
| 宣称"做完了/修好了"之前 | `verification-before-completion` | 先跑命令、贴出输出，**证据先于断言** |
| 给整体改动做过度设计审查 | `ponytail-review`（另有 `-audit` / `-debt`） | 输出 `net: -N lines` |

**永不让步（任何 skill 都不得简化掉）**：输入校验、错误处理、安全、可访问性、用户明确要求的东西。
**外部 skill 与本文件冲突时，以本文件为准**（例：`brainstorming`/`writing-plans` 里的 "frequent commits" ✗ 本项目"不主动 commit"；`ponytail` 的"测试也 YAGNI" ✗ 中大型改动走 TDD）。
实测与选型依据：`docs/归档/工程实践/AI编码Skill_ponytail实测与通用Skill选型.md`（结论：ponytail 省的是 **diff**，不是 token，回本阈值 ≈ 不省则要多写 >2K token 的代码）。

## 4. 改架构前必读的坑（每条一句，细节在链接里）

1. **异步双入口**：loop 调 `execute_kg_tool_async`（协程 handler 直接 await）；同步入口 `execute_kg_tool` 供测试/非 async 调用方，**不能执行协程 handler**（工作线程里没有运行中的事件循环）。
2. **messages 双形**：`_build_api_messages` / `estimate_single_call` 兼容 dict 与 Pydantic，`agent/estimator._predict_completion_features` 只读 dict。
3. **token 指标冗余**：`context_tokens` 可由 `token_usage` 推导，向后兼容保留。
4. **双记录并存**：conversations 消息内嵌 thinking/tools（前端在消费）vs `agent_runs.evidence`；回源要知道两处，未来以 agent_runs 为回放源。
5. **`/knowledge/events` 两个陷阱**：必须 `subscribe(user_id=...)`（全局队列收不到 per-user 事件）；`_cleanup_stale_queues` 会清空闲队列 → 已加 `_active_subs` 保护，否则长连接**永久收不到事件**；`notify(X, ...)` 只在 X 队列已存在时投递（否则留库待补拉，断线期间的通知不会丢）。
6. **分层保留**：`store.prune(full_days=30, strip_days=180)`，启动 + 每日 GC 自动执行。
7. **已收敛的历史遗留（别引回来）**：`core→api` 反向依赖、私有符号跨模块、llm_client 上帝模块、工具注册 3 处分散（均 2026-09-08/09-15 处理）。
8. 想知道"为什么这么设计"：`docs/项目历程_决策与效果记录.md` + `.codebuddy/memory/MEMORY.md`。

## 5. 文档路由（要查什么去哪）

| 要查什么 | 去哪 |
|---|---|
| 端点 / 请求响应 schema | 起后端后 `http://localhost:8000/docs`（**唯一端点级权威**） |
| 整体架构与快速上手 | 根 `README.md` |
| **数据根 / 路径取径（唯一声明处）** | `backend/app/core/storage.py`（`DATA_ROOT` + 每域取径函数，保留一版旧路径回退）；一次性迁移 = `backend/scripts/migrate_data_root.py`（**默认 dry-run**，`--apply` 才动）；回归 `backend/tests/test_storage_paths.py` / `test_migrate_data_root.py` |
| **全项目架构问题清单与重构路线（P0~P2 / Phase A-B-C）** | `docs/架构/整体架构_问题清单与重构方案.md`（根因「主体性错层」另见 `TODO.md` §3）；**执行施工计划 / 分工与验收 / 任务 ID** → `docs/架构/重构实施计划_可执行清单.md` |
| **比赛材料里的数字 / 能力口径（唯一来源，别自己数）** | `docs/架构/演示口径与操作卡.md`（实测数字 + 「能演示」/「只能写不能演」的分界 + 禁止出现的说法 + 演示 SOP；原《可演示事实清单》《演示操作卡》《演示预演记录》《待拍板问题》已并入并移入 `docs/归档/架构/`） |
| **Agent 内核逐模块职责 / 时序 / 改码坑** | `backend/app/core/agent/README.md` |
| **工具系统（注册表 / 分发 / 不变量）改码指南** | `backend/app/core/agent_tools/README.md` |
| **加/改一个工具（一工具一文件、三条硬约定、检查清单）** | `backend/app/core/agent_tools/tools/README.md` |
| **LLM 原语包（客户端 / 思考 / 回退 / 嵌入 / JSON 提取 / 每用户模型）** | `backend/app/core/llm/README.md` |
| **用户自配模型（存储 / 状态机 / 降级档位 / 端点）** | 代码 = `backend/app/core/llm/user_models.py`（存储 + 状态机）+ `api/v1/llm_models.py`（端点）；候选链接入点 = `llm/fallback.py::chat_create(user_id=…)`；测试 = `tests/test_user_models.py` / `test_user_model_routing.py` / `test_llm_models_api.py` |
| **MCP 模块（server 本体 + 宿主接线 / 搜索后端降级链）** | `backend/app/mcp_servers/README.md` |
| 模块级最新契约 | 对应模块 `*.py` 的 **docstring**（代码优先于文档） |
| Agent Loop 设计决策 / 业界调研 | `docs/AgentLoop/AgentLoop_重构设计讨论.md`（§14 = 业界真 Agent 循环共识骨架；原《AgentLoop_业界调研与学习路线》已并入并移入 `docs/归档/AgentLoop/`） |
| 上下文工程（预算 / 裁剪 / 预估） | `docs/上下文工程/上下文工程_调研与差距审计.md`、`docs/上下文工程/上下文工程_预算框架.md`、待办见 `TODO.md` §5 |
| **Prompt 缓存命中率（成本 / 剪链点）** | `docs/上下文工程/上下文工程_Prompt缓存命中率_调研与优化方案.md`（MiniMax 缓存机制 + **实测机理 §1.5** + 剪链点清单 + 改造清单）；复跑证据 = `backend/scripts/probe_prompt_cache.py`；观测面板 = 运维后台「用量与缓存命中」 |
| 知识图谱（**唯一参照 = 参照系契约**） | `docs/知识图谱/知识图谱_参照系契约.md`（唯一参照）+ `docs/知识图谱/知识图谱_检索与注入_方法与现状.md`（检索/注入方法与现状）+ `docs/知识图谱/知识图谱_外部调研_论文开源与多源融合.md`（论文/开源/多源融合） |
| 已归档的过期文档（快照类，只作历史出处） | `docs/归档/<模块>/`（现有 `docs/归档/知识图谱/知识图谱_模块结构与封装调研.md`）；引用归档文档时用归档路径，别再写回 `docs/<模块>/` |
| 图谱质量体检 / 存量同名合并 | `backend/scripts/inspect_graph_quality.py`（`--user N` 单用户、`--fix-dupes [--apply]` 合并，默认只读）；指标口径与验收基线见 `TODO.md` §4（体检脚本已把基线口径固化在脚本里） |
| **图谱数据结构（schema / 索引 / 检索通路）** | `docs/知识图谱/知识图谱_数据结构与检索通路_评审与改良方案.md`（15 项问题分级 + KG-D1~D15 改造清单；**设计说明 / 选型理由 / 业界对比 / 准确率口径已并入同一份** §3~§7）；表定义仍以 `core/kg/schema.py::_create_tables` 为准 |
| **节点小节化（文件夹 + manifest.json + 平行小节 MD）** | 设计 SSOT = `docs/知识图谱/知识图谱_节点小节化_设计与实现方案.md`；读写出口 = `core/kg/sections.py` 的 manifest/section 方法；生成管线 = `kb/section_generator.py`；体检 = `inspect_graph_quality.py::section_metrics`。**已作废**：主题层 `themes`/`node_themes`（2026-09-27 连表带数据删除，原两份设计文档同删） |
| **知识图谱实现（拆分后落点）** | `backend/app/core/kg/`：`KnowledgeGraph` = 各分域 mixin 组合（`_common` 常量/helper、`core` 连接与护栏、`schema` 建表迁移、`nodes`、`edges`、`paths`、`content`、`sections`、`mastery`、`sources`）；`core/knowledge_graph.py` 仅转发层（`from app.core.knowledge_graph import KnowledgeGraph` 不变） |
| **图谱 RAG 索引（创建即入索引）与 S4 注入选片（2026-10-11）** | 索引收口 = `core/rag/node_indexer.py`（登记 + flush；挂点 = `KnowledgeGraph` 四个写出口）；S4 两段式选片 = `core/graph_analyzer.py::_render_focus_blocks`（`focus_ids`），接线 = `chat_service._build_system_prompt`/`_build_graph_summary`；问题与证据 = `docs/知识图谱/知识图谱_检索与注入_方法与现状.md` §4（诊断）、施工与验收 = 同文 §5（改进计划）；回归 = `tests/test_node_index_on_create.py`、`tests/test_graph_injection_query_aware.py` |
| RAG / 文档解析 / 检索 / 出题 | `docs/RAG/RAG_*.md`、`docs/教学模块/QUIZ_出题逻辑调研.md` |
| **试卷归档（拆题 / 入库 / 知识点关联）** | `docs/教学模块/试卷归档_调研与实施方案.md`（解析引擎选型不重复，引用 `docs/RAG/RAG_文档解析（含视觉与PDF）_调研与实施方案.md`） |
| MCP 网页搜索 | `docs/RAG/MCP_网页搜索工具_调研与实施方案.md` |
| 采集模块 | `docs/教育资料采集/教育资料采集模块_设计讨论.md` + 待办见 `TODO.md` §6 |
| 部署与运维 | `deploy/README.md` → `docs/运维部署/Docker_学习路径与工程化部署.md` → `docs/运维部署/运维_生产上线与日常运营指南.md`；线上实例 = `docs/运维部署/运维_线上实例手册.md`，**安全评估（P0/P1 发现与加固路线）** = `docs/运维部署/运维_线上实例安全评估报告_2026-10-19.md` |
| AI 编码 skill 的效果实测与选型 | `docs/归档/工程实践/AI编码Skill_ponytail实测与通用Skill选型.md` |
| 历程 / 决策 / 踩坑总表 | `docs/项目历程_决策与效果记录.md` |
| 实现细节 / 评测数字 / 运维后台 / 部署 / 比赛结论（记忆蒸馏归档） | `docs/知识沉淀_跨会话决策归档.md` |
| 跨会话决策与硬约束（AI 会话必读） | `.codebuddy/memory/MEMORY.md` |
| 未完成项（唯一待办总表） | `TODO.md`；完成侧归档 = `done.md`（只追加） |

## 6. 提交与测试纪律

- 测试口径写进回复/PR：`pytest backend/tests -q -m "not llm_api"` → 期望 **1654 collected / 7 deselected**（2026-10-12 复核；原 1590 为 2026-10-10 实测；B1 学习会话 / B2 教学策略 / B3 排序与阈值落地后净增 41 例）。历史：1549（2026-10-09）、1512 + 1 failed（2026-10-07，那条"他会话 WIP"已随 A2 关闭）。后台套件基线 = `backend-admin/tests` **41 passed**。
- 提交按**文件族**拆；单文件跨主题按**依赖方向**排序（先被调用方）；测试与其修复同一提交；不混无关改动。
- 与其他 AI 会话共存：提交前 `git status` 看清别人 WIP，**路径限定 add**。
