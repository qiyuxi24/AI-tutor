# 项目记忆

> 只留**跨会话稳定的决策与约束**。命令/坑/文档路由等契约 → 根 `AGENTS.md`（已随每次会话注入，**不在此重复**）；历程/取舍/踩坑档案 → `docs/项目历程_决策与效果记录.md`。
> 最近整理：2026-09-19（第十四次：并入远程服务器部署通道，压缩篇幅）。

## 项目概述
- TutorAgent（原 AI-Tutor）：知识图谱驱动的自适应导学 Agent。前端 Vue3+Vite+D3+Pinia+EP；后端 FastAPI+SQLite+Jinja2+LLM(OpenAI 兼容)。
- **模型**：对话/Agent = MiniMax-M3；嵌入固定阿里 text-embedding-v4（独立 key/base）。
- **比赛材料冻结（2026-09-12 起）**：`docs/比赛/` 不主动读/改/同步，仅用户点名才碰。

## 远程服务器 wojteksever（2026-09-19 打通）
- `ssh wojtek@100.90.96.111`（Tailscale 网段，**已装本机公钥，免密直连**）；Ubuntu 26.04 / i7-13700 24 线程 / 30G 内存 / 354G 空闲盘 / Docker 29.1.3+Compose 2.40.3。
- **TutorAgent 已部署运行：`http://100.90.96.111:8080`**（容器 `ai-tutor`，healthy）。部署目录 `/home/wojtek/ai-tutor`。
- 一键部署：`deploy/deploy.ps1`（tar 打包→scp→远端解包→nohup 后台构建→轮询 healthy）。**tar 含 .env（远端配置与本地强制一致）**；本地删的文件远端不删（非 rsync 语义）。细节见 `.codebuddy/memory/2026-09-19.md`。
- 远端 Docker 已配镜像加速（`/etc/docker/daemon.json`：daocloud + 1ms.run；直连 DockerHub 被重置）。sudo 密码=登录密码，无免密（每条 `echo pw | sudo -S -k`）。
- Windows OpenSSH 9.5p2 **支持 `SSH_ASKPASS`**，`.cmd` 可直接当 askpass（`SSH_ASKPASS_REQUIRE=force`）→ 非交互密码登录的通用解法。

## 协作 / 仓库 / 环境
- `origin` = `qiyuxi24/AI-tutor`；`github-desktop-zhuzixuan2007` = 同学（子轩 朱）仓库（**只读，push 被拒**），其提交（含 `8d2230e` 出题模块大改）已全部在 main。
- **并发写入是常态**：提交必须路径限定，内容与预期不符先查 mtime；**整理/审计类任务不擅自 commit**；确需提交按文件族拆、测试与修复同提交、提交前跑离线全量。
- ⚠️ **推 GitHub**：直连 443 被阻断；代理 `127.0.0.1:7897` 隧道时好时坏 → 推送前先验证代理；`~/.ssh/id_ed25519` 已存在但 GitHub 未配 key。修好后：`git -c http.proxy=http://127.0.0.1:7897 push origin main`。
- 装包前 `$env:NO_PROXY="*"`（注册表残留 `127.0.0.1:7890` → pip `ProxyError`）。
- `start.ps1` 后端必须独立控制台（uvicorn 重启用 `CTRL_C_EVENT` 广播会打死共享控制台脚本）；清残留 `taskkill /T /F`。部署：镜像 = 工作区快照，必须 `docker compose up -d --build`。

## 硬约束（AGENTS.md 未覆盖的）
- **嵌入两条线（2026-09-15 收口）**：检索侧 `llm/embed.py::embed_texts`（async、无兜底）；语义去重侧 `kb/embedder.py::ApiEmbedder`（同步 + hash 兜底，服务 `graph_generator` / `prerequisite` / `api/v1/knowledge.py`）。共用 `EMBEDDING_MODEL`/`EMBED_BATCH_SIZE`/`MAX_EMBED_CHARS` 与失败语义。**不合并调用路径**：同步桥会在 FastAPI 里跑新事件循环 → AsyncOpenAI 单例报 "Event loop is closed"。
- ⚠️ **`agent_runs` 落盘目录 ≠ compose 挂载（未修）**：实际 = `backend/app/data/agent_runs`（`store._DEFAULT_DB_DIR = parents[2]`），compose 挂 `./backend/data` → 容器内不在卷里，重建即丢审计记录。修法：改 compose 或路径改回 `backend/data`（需迁移，旧库有 9/13 残留）。
- **新建节点唯一出口** `KnowledgeGraph.create_node_with_content(node_data, content, origin)`：4 条写路径全走它，MD 模板唯一来源 = `knowledge_graph.ORIGIN_NOTES`；调用方**禁**自己写节点 MD。守卫 `tests/test_node_write_paths.py`。
- **同名并轨**：`knowledge_writer._find_same_name` —— ID 不同但中文名相同 → 走更新模式不建重复节点（只做精确同名；语义去重未做）。
- **SSE 事件白名单**：`chat_service._consume_agent_events` 是 if/elif **无 else**，新事件不同步透传就静默丢弃。

## 架构要点
- **对话 = Agent Loop**：`core/agent/loop.py::run_agent_loop`（纯编排；max_rounds=5 / 工具超时 60s / temp 0.3）。配套：context / guard / events（run_id + per-user 路由）/ store（运行记录唯一事实源）/ estimator（预估不参与决策）。
- **工具系统 = 一个目录** `core/agent_tools/`：`registry.py` 契约（零 import）/ `dispatch.py` / `tools/`（一工具一文件五段式）/ `net_guard.py` / `mcp_host.py`。文案 SSOT = `chat_service.TOOL_CAPABILITY_PROMPT`（`tests/test_tools_registry.py` 锁死）。**禁令**：领域模块禁 import `agent_tools`。
- **⚠️ 异步入口**：agent_loop 用 `execute_kg_tool_async`；同步 `execute_kg_tool` 无法执行协程 handler（工作线程无运行 loop）。
- MCP 搜索后端 = Bing RSS 主 + ddgs 兜底（"无结果" ≠ "失败"）。
- `/knowledge/events` 必须 `subscribe(user_id=)`；`_cleanup_stale_queues` 靠 `_active_subs` 保护；`/agent/runs` 的 stats 路由须在 `{run_id}` 前。

## 上下文工程（2026-09-15 落地）
- 配额 SSOT = `docs/上下文工程_预算框架.md`（**B=48K** / S1–S9 / 让位顺序 历史→检索→图谱→画像）；**活清单 = 根 `TODO_Context.md`**。
- 已落地：`LLM_CTX_BUDGET=48000` + `OUTPUT_RESERVE=3000`；固定段 40%×B 告警 + 45%×B 强制减半重建图谱注入（落点在 `chat_service._build_system_prompt`）；S6/S7 按源独立截断 ≤3K + query 双端放置；S8 分层保留；Tool Result Clearing；`prune(30,180)`。
- 四个介入点：① `_build_system_prompt` → ② `guard.trim_history_to_budget` → ③ `context.clear_old_tool_results` → ④ `graph_analyzer.build_graph_context`。
- ⚠️ **跨请求历史无 tool 消息**：`ChatMessage` 只有 role/content → 任何"改历史 tool 消息"都是死代码或造非法序列（真机 400 `tool result's tool id() not found`）。
- ⚠️ **Jinja2 占位符必须 `{{ }}`**：单花括号是字面文本不报错 → 图谱/画像静默不注入；守卫 `tests/test_prompt_loader.py`。

## core 目录状态（2026-09-15 审计）
- 已分包：`agent/`、`llm/`、`agent_tools/`（含 `tools/`）、`profile/`、`quiz/`、`kb/`、`rag/`、`rag_pipeline/`、`hybrid_search/`、`collector/`；顶层仍平铺 15 个文件（可选规整，改动面 30+）。
- 未接线（有实现+测试+零生产引用）：`collector/pipeline_ingest.py` + `chapterizer.py`、`collector/quiz_splitter.py`（已登记 `TODO.md`）。

## 知识图谱
- 存储 SQLite（nodes/edges）+ 节点 MD（`data/knowledge/nodes/{user_id}/`）；**边方向：`A → B` = A 是 B 的前置**。
- 参照系契约唯一参照 `docs/知识图谱_参照系契约.md` v3（三问 L1/L2/L3；未满足 AC-L1-3、AC-L2-1 前不得宣称"参照系"）。裁决链：运行时行为 > 契约 > AGENTS.md > 其他文档。
- **掌握度四档** `mastery_bucket()`（WEAK 30 / MASTERED 70）；**唯一主信号 = 出题判分**（`grade_answer` +20；`update_mastery` 仅限 3 种硬证据）。出题必须跨调用去重（`avoid_questions`）；触发类提示词必须写"不要做什么"。
- 先修推断 `core/prerequisite.py`（多准则投票 0.30，merged micro F1 0.941）；检索扩跳 `rag_manager.search(hops=)` 只反向补前置。
- 图谱生成 `kb/graph_generator.py`：嵌入粗筛 0.78 + LLM 二次确认；`thinking=False` + `GRAPH_MAX_TOKENS=8000` + `GRAPH_CHUNK_CHARS=3000`（**成对调**）。

## RAG 体系（四层）
- 图谱 rag(`core/rag/`) + 上传 kb(`core/kb/`) + 混合检索 `hybrid_search/` + 对话注入 `rag_pipeline/`（`RagSource` 协议 + 纯规则 router；**gather 必须 `return_exceptions=True`**；单源 8s 超时静默返空）。
- kb 混合检索：向量 + whoosh BM25 宽召回各 30 → RRF → top_k=5。whoosh 自定义 CJK bigram（**单字查询不命中**，不引 jieba）；PDF 逐页探针 + `<<<PAGE n>>>` 契约在 `parsers/base.py`；OCR 统一走 `parsers/ocr.py`。
- 图谱 RAG 未接双检索（whoosh `node_id` NUMERIC 与图谱 TEXT id 冲突）。⚠️ 嵌入 API 欠费 → 向量检索静默降级 BM25/空。

## 其他模块 / 测试 / 规范
- 画像 `core/profile/`（外部只 import `UserProfile` 与 `get_usage_mode`）；quiz：API 5 端点 + agent 工具（`quiz_generate` 后台异步 → `quiz_ready` → `grade_answer`；`_INFLIGHT` 必须在 `create_task` 前占位）。
- 测试三层；离线全量口径见 AGENTS.md §6。**真实 API 测试已授权**：`pytest -m llm_api` + `backend/scripts/smoke_*.py`（须 `$env:PYTHONPATH="."` 从 backend 跑）；离线 mock 通过 ≠ 真机通过。
- **README 规范 v3 = `docs/README_编写规范.md`**（中文 SSOT，英文派生镜像）。
- 用户偏好：渐进式改进、清单式推进；重视代码安全与架构一致性；倾向复用成熟组件。
- **ponytail = 写代码必加载**（写文档/讨论豁免）。机制是**压缩 diff**，固定加载成本 ~2K token/任务，回本阈值 ≈ 不省则要多写 >2K token 的代码。**对外口径：不得宣称"省 token / 更稳定"**。债务标记 = 代码内 `ponytail:` 注释。

## 文档索引
- 上下文工程：`docs/上下文工程_调研与差距审计.md` + `_预算框架.md`（SSOT）+ `TODO_Context.md`（活清单）。
- 知识图谱：`docs/知识图谱_参照系契约.md`（唯一参照）+ 同前缀调研文档。
- 模块内解说：`core/agent/README.md`、`core/agent_tools/README.md`、`core/agent_tools/tools/README.md`、`core/llm/README.md`、`app/mcp_servers/README.md`；`docs/AgentLoop_*.md`、`docs/RAG_*.md`。
- 历程与决策：`docs/项目历程_决策与效果记录.md`；AI 编码 skill 实测：`docs/AI编码Skill_ponytail实测与通用Skill选型.md`。
- 部署运维：`deploy/README.md` + `deploy/deploy.ps1` → `docs/Docker_*.md` → `docs/运维_*.md`。
