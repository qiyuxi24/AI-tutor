# 项目记忆

> 只留**跨会话稳定的决策与约束**。命令/坑/文档路由等契约 → 根 `AGENTS.md`（已随每次会话注入，**不在此重复**）；历程/取舍/踩坑档案 → `docs/项目历程_决策与效果记录.md`。
> 最近整理：2026-09-15（第十三次：删去与 AGENTS.md 重复项、压缩篇幅）。

## 项目概述
- TutorAgent（原 AI-Tutor）：知识图谱驱动的自适应导学 Agent。前端 Vue3+Vite+D3+Pinia+EP；后端 FastAPI+SQLite+Jinja2+LLM(OpenAI 兼容)。
- **模型**：对话/Agent = MiniMax-M3；嵌入固定阿里 text-embedding-v4（独立 key/base）。
- **比赛材料冻结（2026-09-12 起）**：`docs/比赛/` 不主动读/改/同步，仅用户点名才碰。

## 协作 / 仓库 / 环境
- `origin` = `qiyuxi24/AI-tutor`；`github-desktop-zhuzixuan2007` = 同学（子轩 朱）仓库（**只读，push 被拒**），其提交（含 `8d2230e` 出题模块大改）已全部在 main。
- **并发写入是常态**：提交必须路径限定，内容与预期不符先查 mtime；**整理/审计类任务不擅自 commit**；确需提交按文件族拆、测试与修复同提交、提交前跑离线全量。
- `start.ps1` 后端必须独立控制台（uvicorn 重启用 `CTRL_C_EVENT` 广播会打死共享控制台脚本）；清残留 `taskkill /T /F`。部署：镜像 = 工作区快照，必须 `docker compose up -d --build`。

## 硬约束（AGENTS.md 未覆盖的）
- **嵌入两条线（2026-09-15 收口）**：检索侧 `llm/embed.py::embed_texts`（async、无兜底）；语义去重侧 `kb/embedder.py::ApiEmbedder`（同步 + hash 兜底，服务 `graph_generator` / `prerequisite` / `api/v1/knowledge.py`）。共用 `EMBEDDING_MODEL`/`EMBED_BATCH_SIZE`/`MAX_EMBED_CHARS` 与失败语义（条数不齐 → 整批 `[]`）。**不合并调用路径**：同步桥会在 FastAPI 里跑新事件循环 → AsyncOpenAI 单例报 "Event loop is closed"。
- ⚠️ **`agent_runs` 落盘目录 ≠ compose 挂载（2026-09-15 发现，未修）**：实际 = `backend/app/data/agent_runs`（`store._DEFAULT_DB_DIR = parents[2]`），compose 挂 `./backend/data:/app/backend/data` → **容器内不在卷里，重建即丢审计记录**（L3 回源失守）。修法二选一：改 compose，或路径改回 `backend/data`（需迁移，`backend/data/agent_runs` 有 9/13 旧库残留）。
- **新建节点唯一出口** `KnowledgeGraph.create_node_with_content(node_data, content, origin)`（2026-09-15 收口）：4 条写路径（`knowledge_writer` / `graph_generator` / 手动 API / decompose）全走它，MD 模板唯一来源 = `knowledge_graph.ORIGIN_NOTES`；调用方**禁**自己写节点 MD（全库只剩 `knowledge_graph.py` 两处）。守卫 `tests/test_node_write_paths.py`（逐条断言 origin）。
- **同名并轨**：`knowledge_writer._find_same_name` —— ID 不同但中文名相同（同学科或未归档）→ 改走更新模式，不建重复节点（实证：`harmony_dev_intro`/`harmonyos_intro` 同名并存）。只做精确同名；嵌入语义去重未做（热路径成本 + 嵌入欠费）。
- **SSE 事件白名单**：`chat_service._consume_agent_events` 是 if/elif **无 else**，新事件不同步透传就静默丢弃。

## 架构要点
- **对话 = Agent Loop**：`core/agent/loop.py::run_agent_loop`（纯编排；max_rounds=5 / 工具超时 60s / temp 0.3）。配套：context（消息唯一写入点）/ guard（发送前预算守卫）/ events（run_id + per-user 路由）/ store（运行记录唯一事实源）/ estimator（发送前预估，落 `token_estimate`，**不参与预算决策**）。
- **工具系统 = 一个目录** `core/agent_tools/`：`registry.py` 契约（零 import）/ `dispatch.py` 调用（不认识具体工具）/ `tools/`（**一工具一文件**，五段 `DESCRIPTION`/`PARAMETERS`/`GUIDANCE`/`handler`/`SPEC`）/ `net_guard.py` SSRF / `mcp_host.py` MCP。文案 SSOT = `chat_service.TOOL_CAPABILITY_PROMPT`（`tests/test_tools_registry.py` 锁死）。**禁令**：领域模块禁 import `agent_tools`（单向：中间件→工具→领域）。
- **⚠️ 异步入口**：agent_loop 用 `execute_kg_tool_async`；同步 `execute_kg_tool` **无法执行协程 handler**（工作线程无运行 loop）。
- MCP 搜索后端 = **Bing RSS 主 + ddgs 兜底**（"无结果" ≠ "失败"）。
- `/knowledge/events` 必须 `subscribe(user_id=)`；`_cleanup_stale_queues` 靠 `_active_subs` 保护长连接；`publish(user_id=X)` 只在 X 队列已存在时投递；`/agent/runs` 的 stats 路由须在 `{run_id}` 前。

## 上下文工程（2026-09-15 落地）
- 配额 SSOT = `docs/上下文工程_预算框架.md`（**B=48K** / 九段 S1–S9 / 让位顺序 历史→检索→图谱→画像 / 不变量 B-1~B-7）；**活清单 = 根 `TODO_Context.md`**。
- **已落地**：`LLM_CTX_BUDGET=48000` + `guard.OUTPUT_RESERVE=3000`；固定段 40%×B 告警 + 45%×B **强制减半重建图谱注入**（落点必须在组装侧 `chat_service._build_system_prompt`——`guard` 只能裁 messages）；S6/S7 按源**独立截断 ≤3K** + query 双端放置；S8 **分层保留**（省略说明+规则要点 / 首条 user 锚点 / 最近 3 轮，两档降级）；**Tool Result Clearing**；`build_graph_context(max_chars)`；`prune(30,180)`。
- **四个介入点**：① `_build_system_prompt`（固定段）→ ② `guard.trim_history_to_budget`（S8）→ ③ `context.AgentContext.clear_old_tool_results`（loop 内每轮，按"工具批次"分组保留最近 2 批）→ ④ `graph_analyzer.build_graph_context`（S4 上限）。
- **未做**：P2-① 运行时分段记账、P2-② 实测拐点回填配额、slice_graph 选片；待决策 D-1（S6/S7 top_k 与去重）、D-3（loop max_tokens 是否提 3000）、D-4（是否硬失败）；P1-④ query 双端放置的 A/B（现按论文落地）。
- ⚠️ **跨请求历史里没有 tool 消息**：`ChatMessage` 只有 role/content，`llm/messages.build_api_messages` 也只透传这两个字段 → 任何"改历史 tool 消息"的做法都是**死代码或造非法序列**（真机报 400 `tool result's tool id() not found (2013)`）。tool 回填只发生在单次 run 内。
- ⚠️ **Jinja2 占位符必须 `{{ }}`**：单花括号是字面文本、**不报错** → 图谱/画像静默不注入（曾被误判为模型幻觉）；守卫 `tests/test_prompt_loader.py`（断言"值被注入"，不是"占位符名字出现"）。

## core 目录状态（2026-09-15 审计）
- **已分包**：`agent/`、`llm/`、`agent_tools/`（含 `tools/`）、`profile/`、`quiz/`、`kb/`、`rag/`、`rag_pipeline/`、`hybrid_search/`、`collector/`；顶层仍平铺 15 个文件（分包 = 未做的可选规整，改动面 30+）。
- **未接线（有实现+有测试+零生产引用）**：`collector/pipeline_ingest.py` + `chapterizer.py`、`collector/quiz_splitter.py`（已登记 `TODO.md`）。**重复实现（刻意分离）**：`rag/vector_store.py` vs `kb/doc_vector_store.py`。
- 已删真死文件：`core/kb/parser.py`（parsers 包迁移的兼容薄壳，全库零引用）。

## 知识图谱
- **存储** SQLite（nodes/edges）+ 节点 MD（`data/knowledge/nodes/{user_id}/`）；`graph_middleware.slice_graph` 按需切片；`SUBJECT_UNCLASSIFIED="未分类"`。**边方向：`A → B` = A 是 B 的前置**。
- **参照系契约（唯一参照 `docs/知识图谱_参照系契约.md` v3）**：三问 ①能讲什么(L1) ②现在在哪(L2) ③依据什么(L3)；现状 L1 基本落地 / L2 半 / L3 未；**未满足 AC-L1-3、AC-L2-1 前不得宣称"参照系"**。裁决链：运行时行为 > 契约 > AGENTS.md > 其他文档。
- **掌握度四档** `mastery_bucket()`（WEAK 30 / MASTERED 70，全库唯一实现）；**唯一主信号 = 出题判分**（`grade_answer` 确定性 +20；`update_mastery` 仅限 3 种硬证据，实测模型从不主动调）。出题必须**跨调用去重**（`avoid_questions`）；触发类提示词必须写"不要做什么"（只写正向会被更强的教学原则盖过）。
- **先修推断** `core/prerequisite.py`（多准则投票阈值 0.30；`apply_candidates` 写库、API 默认 dry-run；merged micro F1 0.941）；**检索扩跳** `rag_manager.search(hops=)` 只反向补前置（`最高 × 0.6^depth`，默认 0 零回归）；**归属判定** `kg_taxonomy`（规则优先 + LLM 兜底）。
- **图谱生成** `kb/graph_generator.py`：嵌入粗筛 0.78 + LLM 二次确认；抽块 `thinking=False` + `GRAPH_MAX_TOKENS=8000` + `GRAPH_CHUNK_CHARS=3000`（**必须成对调**）+ `GRAPH_JSON_RETRIES=1`。
- **两条建节点通道**：主通道 = loop 内工具 `add_knowledge_node`；副通道 = 回复后 `_analyze_and_apply`（confidence ≥0.9 自动落库，其余进 `ai_suggestions.json`）。

## RAG 体系（四层）
- 图谱 rag(`core/rag/`) + 上传 kb(`core/kb/`) + 混合检索 `hybrid_search/` + 对话注入 `rag_pipeline/`（`RagSource` 协议 + 纯规则 router；**gather 必须 `return_exceptions=True`**；单源 8s 超时、异常静默返空）。
- **kb 混合检索**：向量 + whoosh BM25 宽召回各 30 → RRF → top_k=5（mock 弱向量下 RRF 拖累 Recall@1，待真实 embedding 复跑定夺）。**parsers**：text/pdf/docx/pptx/image-ocr/电子书；**OCR 统一走 `parsers/ocr.py`**；PDF 逐页探针 + 页级路由，页标记 `<<<PAGE n>>>` 契约在 `parsers/base.py`；whoosh 无中文分析器（自定义 CJK bigram，**单字查询不命中**，不引 jieba）。
- **Agentic RAG**：`rag_search`（source graph|kb|all，`hops` 0~3）；**图谱 RAG 未接双检索**（whoosh `node_id` NUMERIC 与图谱 TEXT id 冲突）。⚠️ **嵌入 API 欠费**时 `text-embedding-v4` 返 `Arrearage` → 向量检索静默降级为 BM25/空。

## 其他模块 / 测试 / 规范
- 画像 `core/profile/` 分层包；数据 `data/profiles/{user_id}.json`；**外部只 import `UserProfile` 与 `get_usage_mode(user_id)`**。quiz：`core/quiz/` + API 5 端点；对话内出题走 agent 工具（`quiz_generate` 后台异步 → `quiz_ready` → `grade_answer`；`_INFLIGHT` 必须在 `create_task` 前占位）。
- 测试三层：单元(mock) → 集成 → 离线评测；离线全量口径见 AGENTS.md §6。**有需要就直接用真实 API 测试（用户授权，不担心消耗 token）**：`pytest -m llm_api` + `backend/scripts/smoke_*.py` / `probe_*.py`（**脚本须 `$env:PYTHONPATH="."` 从 backend 跑**）；离线 mock 通过 ≠ 真机通过。
- **README 规范 v3 = `docs/README_编写规范.md`（唯一依据）**：中文 SSOT，英文派生镜像；CI 双语校验未实现。
- 用户偏好：渐进式改进、清单式推进；重视代码安全与架构一致性；倾向复用成熟组件。
- **ponytail = 写代码必加载**（触发看产出物；写文档/讨论豁免）：七阶阶梯（需要吗→已有吗→标准库→原生→已装依赖→一行→最少代码）。机制是**压缩 diff**，固定加载成本 **2,005 token/任务**，回本阈值 ≈ 不省则要多写 >2K token 的代码 → 小改动净亏，收益在维护面（少改文件、不擅自扩大范围）。**对外口径：不得宣称"省 token / 更稳定"**（arXiv 2503.20197：>90% 缺陷是"缺失检查"，无"过度设计"类别）。债务标记 = 代码内 `ponytail:` 注释。

## 文档索引
- 上下文工程：`docs/上下文工程_调研与差距审计.md` + `_预算框架.md`（SSOT）+ `TODO_Context.md`（活清单）。
- 知识图谱：`docs/知识图谱_参照系契约.md`（唯一参照）+ `_模块结构与封装调研` / `_P0实现方案与核心思路` / `_P1扩跳与AB对照实验` / `_论文与开源实现调研` / `_多资料综合维护调研`。
- 模块内解说：`core/agent/README.md`、`core/agent_tools/README.md`、`core/agent_tools/tools/README.md`、`core/llm/README.md`、`app/mcp_servers/README.md`；`docs/AgentLoop_*.md`、`docs/RAG_*.md`、`docs/MCP_网页搜索工具_*.md`。
- 历程与决策：`docs/项目历程_决策与效果记录.md`；AI 编码 skill 实测：`docs/AI编码Skill_ponytail实测与通用Skill选型.md`；多人协作：`docs/Git_多人协作_工作流调研与学习路径.md`。
- 部署运维：`deploy/README.md` → `docs/Docker_*.md` → `docs/运维_*.md`；参赛材料 `docs/比赛/`（**冻结**）；开发索引 根 `AGENTS.md`。
