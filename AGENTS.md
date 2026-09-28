# AGENTS.md — TutorAgent 给 AI Agent 的操作手册

> **定位**（依 [agents.md](https://agents.md/) 规范）：本文件是**给 agent 的操作手册**，不是项目百科。
> 只写三类：① **可直接执行的命令** ② **不写就会做错的硬约束/坑** ③ **文档路由**。
> 架构说明、模块职责、端点 schema **刻意不写在这里** —— 权威依次为：代码 `docstring` → 各包 `README.md` → `docs/*.md` → 运行后的 `http://localhost:8000/docs`。
> 为什么这么瘦：AGENTS.md 能把 agent 的运行时间中位降低 **~28.6%**、输出 token 降低 **~16.6%**（arXiv 2601.20404），但**仓库概览型内容不提升成功率、反而让成本 +>20%**（ETH arXiv 2602.11988）→ 只留可执行信息。取舍经过见 `docs/工程实践/AI编码Skill_ponytail实测与通用Skill选型.md`。
> 冲突裁决：用户对话中的显式指令 > 本文件。

---

## 0. 命令与环境（照抄，别自己猜）

| 事项 | 命令 / 路径 |
|---|---|
| 后端 Python | `backend/venv/Scripts/python.exe`（**必须用它**；系统 Python 的 fastapi 过旧会导入失败） |
| 一键启动 | 根目录：`.\start.ps1`（自动探测：有 Docker 走容器，否则本机开发模式；`-Local` / `-Docker` 强制，`start.cmd` 双击同一入口） |
| 启动后端 | cwd=`backend`：`venv\Scripts\python.exe -m uvicorn app.main:app --port 8000 --workers 1` |
| 启动前端 | cwd=`frontend`：`npm run dev`（Vite 5173；`/api` 已代理到 8000，SSE 走 ws） |
| 跑测试 | cwd=项目根：`backend/venv/Scripts/python.exe -m pytest backend/tests -q -m "not llm_api"` |
| 真实 API 测试 | 加 `-m llm_api`（真实付费 key，需 pytest-asyncio，单独跑） |
| 配置真值 | 根目录 `.env`（唯一，`core/config.py` 读 `parents[3]/.env`） |
| LLM/嵌入配置 | 对话：`LLM_API_KEY` + `LLM_BASE_URL` + `MODEL_NAME`（默认 **MiniMax-M3**）；嵌入：`DASHSCOPE_API_KEY` + `EMBED_BASE_URL`（固定阿里 text-embedding-v4） |

## 1. 硬约束（违反必出事）

- **uvicorn 必须 `--workers 1`**：EventBus 用户队列与进程内定时 GC 依赖单进程，多 worker 各自持有总线 → 事件必丢。
- **`.env` 只有根目录一份**，不要在 `backend/` 下另建。
- **`nodes.id` 是全局 TEXT 主键**（非 per-user）；脚本/测试建节点前先 `INSERT OR IGNORE INTO users`。**给 `knowledge.db` 加表**（`node_aliases`/`mastery_events`/`doc_node_marks` 同族）= `knowledge_graph.py::_create_tables` 建表 + `_auto_migrate` 补索引，**并同步三处**：`backend-admin/app/core/db.py::delete_user_rows` 的删号清单（该连接**不开** FK 级联，漏一行就是永久脏数据）、`scripts/inspect_graph_quality.py` 的体检口径、`AGENTS.md §5` 路由。**给 `knowledge.db` 加列**（如 `nodes.sources`、`nodes.subject`）= 写进 `_auto_migrate` 的 `ALTER TABLE` 清单：`CREATE TABLE IF NOT EXISTS` 不给老表加字段（与 `records.py` 那条同理）。
- **用户隔离**：图谱/画像/RAG/记录全按 `user_id` 分区；`KnowledgeGraph(user_id)` 每次新建、用毕 `close()`，别跨协程共享实例。
- **记录型库（`agent_runs` / `llm_usage` / `agent_debug_logs`）统一走 `core/records.py`**：连接 / 建表 / 补列 / 过期清理的唯一实现，三张表分属两个库（`agent_runs.db`、`debug_log.db`）。**加表 = 写自己的 schema + 调 `records.connect` + 在 `main.py::_prune_once` 的 `_JOBS` 加一行**，别另抄一套连接代码。**加列 = 写进该模块的补列清单**（如 `store._COLUMN_MIGRATIONS`，由 `records.ensure_columns` 就地补）：`CREATE TABLE IF NOT EXISTS` 既不会给老表加字段、也不会自动接线（`token_estimate` 就是这么加的；回归 `test_legacy_db_gets_token_estimate_column`）。
- **🔴 Jinja2 占位符必须双花括号**（单花括号是字面文本，**不报错**）：`data/prompts/system_prompt_common.j2` 曾把 `{knowledge_graph_summary}` / `{user_profile}` 写错 → AI **完全看不到图谱与画像**，曾被误判为"模型幻觉"，排查成本极高。改模板/加载器后**断言"值被注入"**，而不是"占位符名字出现"；诊断脚本 `backend/scripts/probe_graph_prompt.py`；回归 `backend/tests/test_prompt_loader.py`。
- **loop 默认值**：`max_rounds=5` / 单工具超时 `60s` / 循环内 `temperature=0.3` / 工具调用总次数 `12` / run 墙钟 `180s` / 同参数重复 `≤2` / 连续失败熔断 `3`（改默认值 = 改 `core/agent/loop.py` 常量 + 同步 `run_agent_loop` 签名、测试与文档）。**`max_rounds` 挡不住模型用相同参数反复重试**（`Tool Result Clearing` 还会诱发它），那一类由 `loop._LoopGuard` 拦。
- **排查"输出无用数据"**：调试日志 `core/agent/debug_log.py`（控制台 + SQLite `data/agent_debug/debug_log.db`，`recent(run_id=)` 回看，保留 14 天）+ 终止原因 `stop_reason`（`natural`/`max_rounds`/`time_budget`/`call_budget`/`fail_circuit`/`error`，同一取值出现在 `AGENT_DONE` 事件与 `agent_runs` 证据的 `loop_stop`）。安全边界规则在 `core/agent/loop_guard.py`。
- **日志**：唯一配置在 `core/logging_setup.py`，由 `app/__init__.py` **导入即初始化**（所以 `python scripts/xxx.py` 直跑也有日志——曾整段配置写在 `main.py`，脚本直跑时 INFO 全丢）；落点 `backend/logs/tutor.log`，`LOG_DIR` 是相对路径时按 **backend 目录**解析（不随 CWD 漂移；曾同时写出项目根与 backend 两份 `tutor.log`）。排查三入口：`tutor.log`（全局）/ `GET /agent/logs?run_id=`（agent 流水）/ `GET /agent/usage`（token 账）。**加日志只记"判定了什么、放弃了什么、为什么"**，不要记每次成功的流水。
- **git 与并发**：不主动 commit/push；本仓库**常有多个 AI 会话并发写** → 提交必须**路径限定**（禁 `git add -A` / `commit -a`），内容与预期不符先查 mtime。
- **代理残留**：注册表 `ProxyEnable=1` 残留在代理退出后会让 pip 报 `ProxyError`（httpx 也读注册表）；装包前 `$env:NO_PROXY="*"`；推 GitHub 前 `$env:HTTPS_PROXY="http://127.0.0.1:7897"`。

## 2. 项目特有约定（非标准实践，照做）

- **单一事实源 / 唯一出口**：LLM 原语在 `core/llm/`（**检索侧**嵌入 `embed.py::embed_texts` —— 语义去重侧另走 `kb/embedder.py::ApiEmbedder`，共用其常量与失败语义；JSON 提取 `json_extract.py::extract_json`；`chat_create` 是唯一带 fallback 的出口；真打 LLM 仅 `agent/loop._chat_once` 与 `call_llm`）；运行记录 `core/agent/store.py`（**唯一写入方 = loop**）；工具注册表 `core/agent_tools/registry.py`；token 计量 `core/token_counter.py`；**节点来源溯源 = `KnowledgeGraph.add_sources`**（并入去重、绝不覆盖），配套 `doc_node_marks` = 「资料 → 该资料产出/影响的节点」**账本**（`evidence.kind` 区分 `new`/`hit`）+ **增补队列**（`status` 区分 `pending`/`filled`）—— 两职责**有意共用一表**（同资料、同批节点、同生命周期）；唯一写入口 `mark_doc_nodes`/`set_mark_status`，状态**单向** `pending`→`filled`。**图谱 `nodes`/`edges` 的存储布局与检索通路说明** = `docs/知识图谱/知识图谱_数据结构与检索通路_评审与改良方案.md`（表定义仍以 `knowledge_graph.py::_create_tables` 为唯一真值）；**图谱只保留最小节点与关系边** —— 主题层（`themes`/`node_themes`、聚合下钻）已于 2026-09-27 整体下线**并删表**，勿再引入任何第二级结构或聚合大节点。
- **新建节点只有一个出口** `KnowledgeGraph.create_node_with_content(node_data, content, origin)`：4 条写路径（Agent 工具写层 `knowledge_writer` / 书籍建图 `graph_generator` / 手动 API / 问题拆解）全部走它，MD 模板唯一来源 = `knowledge_graph.ORIGIN_NOTES`。**禁止**在调用方自己 `open(kg.nodes_dir/...)` 写节点 MD；新增写路径时守住 `tests/test_node_write_paths.py`（逐条断言 origin，加了新路径而没复用模板就会红）。**同名并轨也在这一层**（2026-09-20）：命中同名（`normalize_node_name` + 同用户同学科，判重唯一实现 = `KnowledgeGraph.find_node_by_name`）则不新建、只并入正文，**返回实际落点 ID** —— 调用方必须用返回值建边/回执，别再自己写一份同名比较。建图语义去重状态 `dedup_status`（`ok`/`degraded` hash 兜底/`unavailable` 欠费）随 aggregate 带出。
- **节点小节化（2026-09-27）= 纯文件层，不加任何表**：一个节点可以是**文件夹** `nodes/{uid}/{node_id}/`（`manifest.json` 路由 + 若干**平行**小节 MD，**没有主 MD**；老节点仍是单 `{node_id}.md`，不迁移）。读写唯一出口 = `KnowledgeGraph` 的 `manifest_path`/`has_sections`/`read_manifest`/`list_sections`/`read_section`/`create_section`/`write_section`/`set_section_status`/`delete_section`/`add_quiz_ref`（manifest 原子写 + 实例锁）。**小节不是图谱节点**：不进 `nodes`/`edges`、不参与判重建边与图渲染（图渲染/检索/统计只看 SQLite，零文件 I/O）。删节点/删学科/删号必须**连目录整删**（`remove_node`/`remove_subject`/`backend-admin::purge_user_storage` 已接，新增删除路径要跟上）。生成管线 = `kb/section_generator.py`（两阶段、逐节纯 Markdown 直出、单节失败不拖垮整批）；**建图阶段② 也走它**（`graph_generator._fill_sections`：新概念直接小节化；命中**老单 MD 节点**才走 `_fill_legacy` 单篇追加），小节化后由 `KnowledgeGraph.set_content_status` 把骨架翻 `filled`。**读节点正文一律走 `node_content_text`（小节优先）**——图谱注入 / RAG / 出题 / 导出 / 体检共用它，直读 `{node_id}.md` 会拿到骨架占位（"待完善..."）。体检口径 = `inspect_graph_quality.section_metrics`；设计见 `docs/知识图谱/知识图谱_节点小节化_设计与实现方案.md`。
- **LLM 调用边界**：所有对话走 `run_agent_loop`（带 KG_TOOLS）；一次性文本/JSON（出题/判分/图谱生成）走 `call_llm`（不带工具）。
- **M3 三段坑**：思考与正文**共享** `max_tokens` 预算 → 批量结构化抽取必须 `thinking=False`，长 JSON 显式调大 max_tokens（否则"空正文 / 硬截断"交替出现）。
- **加工具 = 1 个新模块 + 1 行注册 + 3 份产物自动生成**：在 `core/agent_tools/tools/` 下**复制任一模块**（一工具一文件），改 `DESCRIPTION` / `PARAMETERS` / `GUIDANCE` / `handler` / `SPEC` 五处，再在 `tools/__init__.py` 的 `NATIVE_SPECS` 加一行；`KG_TOOLS`、执行分发、提示词「工具调用指南」全自动生效，**不要再手写第四份说明**（曾双源漂移：MCP 关闭后提示词仍教模型调不存在的工具）。编写规范/检查清单见 `core/agent_tools/tools/README.md`，一致性由 `backend/tests/test_tools_registry.py` 锁死。**MCP 工具不用改本仓库任何文件**：`core/agent_tools/mcp_host.py::_SERVERS` 加 `(前缀, 模块路径)` 即入注册表。
- **工具 handler**：同步 `(args, kg) -> str` 或 `async def`；只有当工具内需要 `asyncio.create_task` 起后台任务时才用 async（如 `quiz_generate`）——**不要为了"想 await"改协程再 `asyncio.run`**（AsyncOpenAI 单例跨事件循环会报 "Event loop is closed"）。业务错误回填 `"操作失败: …"`、权限拒绝回填 `"权限不足: …"`（`ValueError`/`PermissionError` 分级在 `dispatch` 统一兜底）。
- **SSE 事件改动三处同步**：`core/agent/events.py` 发射 → `chat_service._consume_agent_events`（**白名单 if/elif，无 else，不加就静默丢弃**）→ `frontend/src/api/index.js` 回调分发；运行类事件**必带 run_id**。**请求内事件（thinking/text_delta/tool_*/agent_*）只能走 `run_agent_loop(event_queue=...)` 的请求私有队列，禁止经 `event_bus.publish`** —— per-user 队列是对话流与常驻长连接 `/knowledge/events` 的**共享队列**，两个消费者会互相抢事件（2026-09-26 故障：长连接抢走 text_delta → 前端空流 → 表现为"对话没反应"，后端 agent_runs 却完整）；该队列只留 `graph_updated`/`quiz_ready`/`error` 三类通知。**两条对话路径都要传**（`process_message_stream` 与 `process_message`），回归 `tests/test_event_bus.py`。
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
实测与选型依据：`docs/工程实践/AI编码Skill_ponytail实测与通用Skill选型.md`（结论：ponytail 省的是 **diff**，不是 token，回本阈值 ≈ 不省则要多写 >2K token 的代码）。

## 4. 改架构前必读的坑（每条一句，细节在链接里）

1. **异步双入口**：loop 调 `execute_kg_tool_async`（协程 handler 直接 await）；同步入口 `execute_kg_tool` 供测试/非 async 调用方，**不能执行协程 handler**（工作线程里没有运行中的事件循环）。
2. **messages 双形**：`_build_api_messages` / `estimate_single_call` 兼容 dict 与 Pydantic，`agent/estimator._predict_completion_features` 只读 dict。
3. **token 指标冗余**：`context_tokens` 可由 `token_usage` 推导，向后兼容保留。
4. **双记录并存**：conversations 消息内嵌 thinking/tools（前端在消费）vs `agent_runs.evidence`；回源要知道两处，未来以 agent_runs 为回放源。
5. **`/knowledge/events` 两个陷阱**：必须 `subscribe(user_id=...)`（全局队列收不到 per-user 事件）；`_cleanup_stale_queues` 会清空闲队列 → 已加 `_active_subs` 保护，否则长连接**永久收不到事件**；`publish(user_id=X)` 只在 X 队列已存在时投递。
6. **分层保留**：`store.prune(full_days=30, strip_days=180)`，启动 + 每日 GC 自动执行。
7. **已收敛的历史遗留（别引回来）**：`core→api` 反向依赖、私有符号跨模块、llm_client 上帝模块、工具注册 3 处分散（均 2026-09-08/09-15 处理）。
8. 想知道"为什么这么设计"：`docs/项目历程_决策与效果记录.md` + `.codebuddy/memory/MEMORY.md`。

## 5. 文档路由（要查什么去哪）

| 要查什么 | 去哪 |
|---|---|
| 端点 / 请求响应 schema | 起后端后 `http://localhost:8000/docs`（**唯一端点级权威**） |
| 整体架构与快速上手 | 根 `README.md` |
| **Agent 内核逐模块职责 / 时序 / 改码坑** | `backend/app/core/agent/README.md` |
| **工具系统（注册表 / 分发 / 不变量）改码指南** | `backend/app/core/agent_tools/README.md` |
| **加/改一个工具（一工具一文件、三条硬约定、检查清单）** | `backend/app/core/agent_tools/tools/README.md` |
| **LLM 原语包（客户端 / 思考 / 回退 / 嵌入 / JSON 提取）** | `backend/app/core/llm/README.md` |
| **MCP 模块（server 本体 + 宿主接线 / 搜索后端降级链）** | `backend/app/mcp_servers/README.md` |
| 模块级最新契约 | 对应模块 `*.py` 的 **docstring**（代码优先于文档） |
| Agent Loop 设计决策 / 业界调研 | `docs/AgentLoop/AgentLoop_重构设计讨论.md`、`docs/AgentLoop/AgentLoop_业界调研与学习路线.md` |
| 上下文工程（预算 / 裁剪 / 预估） | `docs/上下文工程/上下文工程_调研与差距审计.md`、`docs/上下文工程/上下文工程_预算框架.md`、`TODO_Context.md` |
| **Prompt 缓存命中率（成本 / 剪链点）** | `docs/上下文工程/上下文工程_Prompt缓存命中率_调研与优化方案.md`（MiniMax 缓存机制 + **实测机理 §1.5** + 剪链点清单 + 改造清单）；复跑证据 = `backend/scripts/probe_prompt_cache.py`；观测面板 = 运维后台「用量与缓存命中」 |
| 知识图谱（**唯一参照 = 参照系契约**） | `docs/知识图谱/知识图谱_参照系契约.md` + `docs/知识图谱/知识图谱_P0实现方案与核心思路` / `docs/知识图谱/知识图谱_P1扩跳与AB对照实验` / `docs/知识图谱/知识图谱_多资料综合维护调研` |
| 已归档的过期文档（快照类，只作历史出处） | `docs/归档/<模块>/`（现有 `docs/归档/知识图谱/知识图谱_模块结构与封装调研.md`）；引用归档文档时用归档路径，别再写回 `docs/<模块>/` |
| 图谱质量体检 / 存量同名合并 | `backend/scripts/inspect_graph_quality.py`（`--user N` 单用户、`--fix-dupes [--apply]` 合并，默认只读）；指标口径与验收基线见 `TODO_Graph_Quality.md` §0.1 |
| **图谱数据结构（schema / 索引 / 检索通路）** | `docs/知识图谱/知识图谱_数据结构与检索通路_评审与改良方案.md`（15 项问题分级 + KG-D1~D15 改造清单）；**设计说明 / 选型理由 / 业界对比 / 准确率口径** → `docs/知识图谱/知识图谱_数据结构设计说明与业界对比.md`；表定义仍以 `knowledge_graph.py::_create_tables` 为准 |
| **节点小节化（文件夹 + manifest.json + 平行小节 MD）** | 设计 SSOT = `docs/知识图谱/知识图谱_节点小节化_设计与实现方案.md`；读写出口 = `knowledge_graph.py` 的 manifest/section 方法；生成管线 = `kb/section_generator.py`；体检 = `inspect_graph_quality.py::section_metrics`。**已作废**：主题层 `themes`/`node_themes`（2026-09-27 连表带数据删除，原两份设计文档同删） |
| RAG / 文档解析 / 检索 / 出题 | `docs/RAG/RAG_*.md`、`docs/教学模块/QUIZ_出题逻辑调研.md` |
| **试卷归档（拆题 / 入库 / 知识点关联）** | `docs/教学模块/试卷归档_调研与实施方案.md`（解析引擎选型不重复，引用 `docs/RAG/RAG_视觉解析策略_调研与实施方案.md`） |
| MCP 网页搜索 | `docs/RAG/MCP_网页搜索工具_调研与实施方案.md` |
| 采集模块 | `docs/教育资料采集/教育资料采集模块_设计讨论.md` + `TODO_Collector.md` |
| 部署与运维 | `deploy/README.md` → `docs/运维部署/Docker_学习路径与工程化部署.md` → `docs/运维部署/运维_生产上线与日常运营指南.md` |
| AI 编码 skill 的效果实测与选型 | `docs/工程实践/AI编码Skill_ponytail实测与通用Skill选型.md` |
| 历程 / 决策 / 踩坑总表 | `docs/项目历程_决策与效果记录.md` |
| 实现细节 / 评测数字 / 运维后台 / 部署 / 比赛结论（记忆蒸馏归档） | `docs/知识沉淀_跨会话决策归档.md` |
| 跨会话决策与硬约束（AI 会话必读） | `.codebuddy/memory/MEMORY.md` |
| 未完成项 | `TODO.md`、`TODO_Collector.md`、`done.md`（完成侧归档） |

## 6. 提交与测试纪律

- 测试口径写进回复/PR：`pytest backend/tests -q -m "not llm_api"` → 期望 **1164 passed, 7 deselected**（2026-09-27 实测；另一会话改动多时先重跑确认）。
- 提交按**文件族**拆；单文件跨主题按**依赖方向**排序（先被调用方）；测试与其修复同一提交；不混无关改动。
- 与其他 AI 会话共存：提交前 `git status` 看清别人 WIP，**路径限定 add**。
