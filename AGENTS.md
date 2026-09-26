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
- **`nodes.id` 是全局 TEXT 主键**（非 per-user）；脚本/测试建节点前先 `INSERT OR IGNORE INTO users`。
- **用户隔离**：图谱/画像/RAG/记录全按 `user_id` 分区；`KnowledgeGraph(user_id)` 每次新建、用毕 `close()`，别跨协程共享实例。
- **记录型库（`agent_runs` / `llm_usage` / `agent_debug_logs`）统一走 `core/records.py`**：连接 / 建表 / 补列 / 过期清理的唯一实现，三张表分属两个库（`agent_runs.db`、`debug_log.db`）。**加表 = 写自己的 schema + 调 `records.connect` + 在 `main.py::_prune_once` 的 `_JOBS` 加一行**，别另抄一套连接代码。**加列 = 写进该模块的补列清单**（如 `store._COLUMN_MIGRATIONS`，由 `records.ensure_columns` 就地补）：`CREATE TABLE IF NOT EXISTS` 既不会给老表加字段、也不会自动接线（`token_estimate` 就是这么加的；回归 `test_legacy_db_gets_token_estimate_column`）。
- **🔴 Jinja2 占位符必须双花括号**（单花括号是字面文本，**不报错**）：`data/prompts/system_prompt_common.j2` 曾把 `{knowledge_graph_summary}` / `{user_profile}` 写错 → adaptive/free_talk 下 AI **完全看不到图谱与画像**，曾被误判为"模型幻觉"，排查成本极高。改模板/加载器后**断言"值被注入"**，而不是"占位符名字出现"；诊断脚本 `backend/scripts/probe_graph_prompt.py`；回归 `backend/tests/test_prompt_loader.py`。
- **loop 默认值**：`max_rounds=5` / 单工具超时 `60s` / 循环内 `temperature=0.3` / 工具调用总次数 `12` / run 墙钟 `180s` / 同参数重复 `≤2` / 连续失败熔断 `3`（改默认值 = 改 `core/agent/loop.py` 常量 + 同步 `run_agent_loop` 签名、测试与文档）。**`max_rounds` 挡不住模型用相同参数反复重试**（`Tool Result Clearing` 还会诱发它），那一类由 `loop._LoopGuard` 拦。
- **排查"输出无用数据"**：调试日志 `core/agent/debug_log.py`（控制台 + SQLite `data/agent_debug/debug_log.db`，`recent(run_id=)` 回看，保留 14 天）+ 终止原因 `stop_reason`（`natural`/`max_rounds`/`time_budget`/`call_budget`/`fail_circuit`/`error`，同一取值出现在 `AGENT_DONE` 事件与 `agent_runs` 证据的 `loop_stop`）。安全边界规则在 `core/agent/loop_guard.py`。
- **日志**：唯一配置在 `core/logging_setup.py`，由 `app/__init__.py` **导入即初始化**（所以 `python scripts/xxx.py` 直跑也有日志——曾整段配置写在 `main.py`，脚本直跑时 INFO 全丢）；落点 `backend/logs/tutor.log`，`LOG_DIR` 是相对路径时按 **backend 目录**解析（不随 CWD 漂移；曾同时写出项目根与 backend 两份 `tutor.log`）。排查三入口：`tutor.log`（全局）/ `GET /agent/logs?run_id=`（agent 流水）/ `GET /agent/usage`（token 账）。**加日志只记"判定了什么、放弃了什么、为什么"**，不要记每次成功的流水。
- **git 与并发**：不主动 commit/push；本仓库**常有多个 AI 会话并发写** → 提交必须**路径限定**（禁 `git add -A` / `commit -a`），内容与预期不符先查 mtime。
- **代理残留**：注册表 `ProxyEnable=1` 残留在代理退出后会让 pip 报 `ProxyError`（httpx 也读注册表）；装包前 `$env:NO_PROXY="*"`；推 GitHub 前 `$env:HTTPS_PROXY="http://127.0.0.1:7897"`。

## 2. 项目特有约定（非标准实践，照做）

- **单一事实源 / 唯一出口**：LLM 原语在 `core/llm/`（**检索侧**嵌入 `embed.py::embed_texts` —— 语义去重侧另走 `kb/embedder.py::ApiEmbedder`，共用其常量与失败语义；JSON 提取 `json_extract.py::extract_json`；`chat_create` 是唯一带 fallback 的出口；真打 LLM 仅 `agent/loop._chat_once` 与 `call_llm`）；运行记录 `core/agent/store.py`（**唯一写入方 = loop**）；工具注册表 `core/agent_tools/registry.py`；token 计量 `core/token_counter.py`。**图谱 `nodes`/`edges` 的存储布局与检索通路说明** = `docs/知识图谱/知识图谱_数据结构与检索通路_评审与改良方案.md`（表定义仍以 `knowledge_graph.py::_create_tables` 为唯一真值）。
- **新建节点只有一个出口** `KnowledgeGraph.create_node_with_content(node_data, content, origin)`：4 条写路径（Agent 工具写层 `knowledge_writer` / 书籍建图 `graph_generator` / 手动 API / 问题拆解）全部走它，MD 模板唯一来源 = `knowledge_graph.ORIGIN_NOTES`。**禁止**在调用方自己 `open(kg.nodes_dir/...)` 写节点 MD；新增写路径时守住 `tests/test_node_write_paths.py`（逐条断言 origin，加了新路径而没复用模板就会红）。**同名并轨也在这一层**（2026-09-20）：命中同名（`normalize_node_name` + 同用户同学科，判重唯一实现 = `KnowledgeGraph.find_node_by_name`）则不新建、只并入正文，**返回实际落点 ID** —— 调用方必须用返回值建边/回执，别再自己写一份同名比较。建图语义去重状态 `dedup_status`（`ok`/`degraded` hash 兜底/`unavailable` 欠费）随 aggregate 带出。
- **LLM 调用边界**：所有对话走 `run_agent_loop`（带 KG_TOOLS）；一次性文本/JSON（出题/判分/图谱生成）走 `call_llm`（不带工具）。
- **M3 三段坑**：思考与正文**共享** `max_tokens` 预算 → 批量结构化抽取必须 `thinking=False`，长 JSON 显式调大 max_tokens（否则"空正文 / 硬截断"交替出现）。
- **加工具 = 1 个新模块 + 1 行注册 + 3 份产物自动生成**：在 `core/agent_tools/tools/` 下**复制任一模块**（一工具一文件），改 `DESCRIPTION` / `PARAMETERS` / `GUIDANCE` / `handler` / `SPEC` 五处，再在 `tools/__init__.py` 的 `NATIVE_SPECS` 加一行；`KG_TOOLS`、执行分发、提示词「工具调用指南」全自动生效，**不要再手写第四份说明**（曾双源漂移：MCP 关闭后提示词仍教模型调不存在的工具）。编写规范/检查清单见 `core/agent_tools/tools/README.md`，一致性由 `backend/tests/test_tools_registry.py` 锁死。**MCP 工具不用改本仓库任何文件**：`core/agent_tools/mcp_host.py::_SERVERS` 加 `(前缀, 模块路径)` 即入注册表。
- **工具 handler**：同步 `(args, kg) -> str` 或 `async def`；只有当工具内需要 `asyncio.create_task` 起后台任务时才用 async（如 `quiz_generate`）——**不要为了"想 await"改协程再 `asyncio.run`**（AsyncOpenAI 单例跨事件循环会报 "Event loop is closed"）。业务错误回填 `"操作失败: …"`、权限拒绝回填 `"权限不足: …"`（`ValueError`/`PermissionError` 分级在 `dispatch` 统一兜底）。
- **SSE 事件改动三处同步**：`core/agent/events.py` 发射 → `chat_service._consume_agent_events`（**白名单 if/elif，无 else，不加就静默丢弃**）→ `frontend/src/api/index.js` 回调分发；运行类事件**必带 run_id**。
- **掌握度更新的唯一主信号 = 出题判分**：`grade_answer` 答对确定性 +20；`update_mastery` 只认 3 种硬证据（实测模型**从不主动调用**它）。触发类提示词必须写成**铁律**并写明"**不要**用追问代替出题"（对立表述），否则会被更强的既有教学原则盖过；出题必须跨调用去重（`avoid_questions=`），否则重答同题可刷掌握度。
- **错误码**：`core/error_codes.py` 定义 `ErrorCode.*`；异常信息以 `[E-XXX]` 开头并走 `log_error()`。
- **请求结构**：`ChatRequest.messages = [{role, content}]`，另带 `mode`（adaptive/free_talk/recursive）与可选 `current_node`/`kb_node_ids`；RAG 检索前读 `UserProfile.get_usage_mode()`。
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
| 知识图谱（**唯一参照 = 参照系契约**） | `docs/知识图谱/知识图谱_参照系契约.md` + `docs/知识图谱/知识图谱_模块结构与封装调研` / `docs/知识图谱/知识图谱_P0实现方案与核心思路` / `docs/知识图谱/知识图谱_P1扩跳与AB对照实验` / `docs/知识图谱/知识图谱_多资料综合维护调研` |
| 图谱质量体检 / 存量同名合并 | `backend/scripts/inspect_graph_quality.py`（`--user N` 单用户、`--fix-dupes [--apply]` 合并，默认只读）；指标口径与验收基线见 `TODO_Graph_Quality.md` §0.1 |
| **图谱数据结构（schema / 索引 / 检索通路）** | `docs/知识图谱/知识图谱_数据结构与检索通路_评审与改良方案.md`（15 项问题分级 + KG-D1~D15 改造清单）；**设计说明 / 选型理由 / 业界对比 / 准确率口径** → `docs/知识图谱/知识图谱_数据结构设计说明与业界对比.md`；表定义仍以 `knowledge_graph.py::_create_tables` 为准 |
| RAG / 文档解析 / 检索 / 出题 | `docs/RAG/RAG_*.md`、`docs/教学模块/QUIZ_出题逻辑调研.md` |
| MCP 网页搜索 | `docs/RAG/MCP_网页搜索工具_调研与实施方案.md` |
| 采集模块 | `docs/教育资料采集/教育资料采集模块_设计讨论.md` + `TODO_Collector.md` |
| 部署与运维 | `deploy/README.md` → `docs/运维部署/Docker_学习路径与工程化部署.md` → `docs/运维部署/运维_生产上线与日常运营指南.md` |
| AI 编码 skill 的效果实测与选型 | `docs/工程实践/AI编码Skill_ponytail实测与通用Skill选型.md` |
| 历程 / 决策 / 踩坑总表 | `docs/项目历程_决策与效果记录.md` |
| 实现细节 / 评测数字 / 运维后台 / 部署 / 比赛结论（记忆蒸馏归档） | `docs/知识沉淀_跨会话决策归档.md` |
| 跨会话决策与硬约束（AI 会话必读） | `.codebuddy/memory/MEMORY.md` |
| 未完成项 | `TODO.md`、`TODO_Collector.md`、`done.md`（完成侧归档） |

## 6. 提交与测试纪律

- 测试口径写进回复/PR：`pytest backend/tests -q -m "not llm_api"` → 期望 **835 passed, 7 deselected**（2026-09-22 实测；另一会话改动多时先重跑确认）。
- 提交按**文件族**拆；单文件跨主题按**依赖方向**排序（先被调用方）；测试与其修复同一提交；不混无关改动。
- 与其他 AI 会话共存：提交前 `git status` 看清别人 WIP，**路径限定 add**。
