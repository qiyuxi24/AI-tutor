# 项目记忆

> 只留**跨会话稳定的决策与约束**。命令/坑/文档路由见根 `AGENTS.md`（已注入，不重复）；历程见 `docs/项目历程_决策与效果记录.md`。
> 最近整理：2026-09-22（第十九次：压缩去重，删已完成项的过程细节）。

## 项目概述 / 用户偏好
- TutorAgent：知识图谱驱动的自适应导学 Agent；Vue3+Vite+D3+Pinia+EP / FastAPI+SQLite+Jinja2+LLM(OpenAI 兼容)。对话/Agent = MiniMax-M3；嵌入固定阿里 text-embedding-v4（独立 key/base）。
- 比赛材料冻结（2026-09-12 起）：`docs/比赛/` 不主动读/改/同步，仅用户点名才碰。
- 用户偏好：渐进式改进、清单式推进；重视代码安全与架构一致性。**写代码必加载 `ponytail`**（写文档/讨论豁免）——机制是压缩 diff，固定成本 ~2K token/任务，对外**不得宣称"省 token / 更稳定"**；债务标记 = 代码内 `ponytail:` 注释。

## 仓库 / 环境 / 部署
- `origin` = `qiyuxi24/AI-tutor`；`github-desktop-zhuzixuan2007` = 同学仓库（**只读，push 被拒**），其提交已在 main。远程另有 `zhuaqu` 分支（同学侧），本地 `pr/1` 为其镜像。
- **并发写入是常态**：提交必须路径限定（禁 `git add -A`），内容与预期不符先查 mtime；**整理/审计类任务不擅自 commit**。
- ⚠️ 推 GitHub：直连 443 被阻断 → `git -c http.proxy=http://127.0.0.1:7897 push origin main`。装包前 `$env:NO_PROXY="*"`（注册表 `ProxyEnable=1` 残留 → pip `ProxyError`，httpx 也读注册表）。
- 远程服务器：`ssh wojtek@100.90.96.111`（Tailscale，免密）；TutorAgent 运行于 `http://100.90.96.111:8080`（容器 `ai-tutor`，目录 `/home/wojtek/ai-tutor`）；一键部署 `deploy/deploy.ps1`（tar→scp→远端构建→轮询 healthy，tar 含 .env，非 rsync 语义）。Ubuntu 26.04 / i7-13700 / 30G；sudo 密码 = 登录密码（`echo pw | sudo -S -k`）。
- `start.ps1` 后端必须独立控制台（uvicorn 重启 `CTRL_C_EVENT` 会打死共享脚本）；清残留 `taskkill /T /F`；部署镜像 = 工作区快照，须 `docker compose up -d --build`。

## 运维后台（独立服务，2026-09-19 落地）
- 前端 `frontend-admin/` :5174、后端 `backend-admin/` :8001（管理入口**不与用户端同入口**）。
- 账号独立：`admin_users` / `admin_audit_logs` 同存 `data/knowledge/knowledge.db`；初始超管 `admin`/`admin123`（`ADMIN_DEFAULT_PASSWORD` 可覆盖），JWT 用 `ADMIN_SECRET_KEY`。
- 后端约定：纯 `sqlite3`（无 ORM）+ dataclass/os.getenv 配置（**不用 pydantic-settings**）+ `bcrypt` + 同步 `def` 路由；业务更新与审计日志**同一事务**提交。
- 权限：管理员 CRUD 需 `require_super_admin()` + 三条自锁（禁自我禁用/降级/删除 + 必须留至少一个启用超管）。
- **删号顺序铁律**：`delete_user_rows()`（同事务不 commit）→ 审计 → `conn.commit()` → `purge_user_storage()`（对话/agent_runs 行 + 三处用户目录）。单删 `DELETE /users/{id}`；批量 `POST /users/batch-delete`（≤100、去重、`not_found` 回填）。**两者都不要求填确认词**。
- 测试 `backend-admin/tests/test_admin_api.py`（37 例）：`cd backend-admin && ..\backend\venv\Scripts\python.exe -m pytest tests -q`。

## 硬约束 / 存储布局
- ⚠️ 主系统存储：图谱/用户 = 根 `data/knowledge/knowledge.db` + `data/knowledge/nodes/{uid}/`（`nodes.file_path` 是装饰性相对串，**不能 unlink**）；对话 = `data/conversations/conversations.db`（按 user_id 分区）；知识库/向量 = `backend/data/{kb,rag}/{uid}/`；agent_runs = `backend/data/agent_runs/agent_runs.db`；`backend/app/data/agent_debug/` 在挂载外。
- **嵌入两条线**：检索侧 `llm/embed.py::embed_texts`（async、无兜底）vs 语义去重侧 `kb/embedder.py::ApiEmbedder`（同步 + hash 兜底）。共用 `EMBEDDING_MODEL`/`EMBED_BATCH_SIZE`/`MAX_EMBED_CHARS` 与失败语义。**不合并调用路径**（同步桥在 FastAPI 里会跑新事件循环 → "Event loop is closed"）。
- ⚠️ 排查 agent 行为第一站 = `core/agent/debug_log.py`（控制台 + SQLite `backend/app/data/agent_debug/debug_log.db`，保留 14 天，`AGENT_DEBUG_LOG=0` 关闭）。`max_rounds` 只限"最多问模型几次"，防循环靠 `_LoopGuard`（总调用 12 / 墙钟 180s / 同参数重复 ≤2 / 连续失败 3）；`stop_reason` 在结果、`AGENT_DONE`、evidence `loop_stop` 三处一致。
- ⚠️ `/knowledge/events` 必须 `subscribe(user_id=)`；`_cleanup_stale_queues` 靠 `_active_subs` 保护；`/agent/runs` 的 stats 路由须在 `{run_id}` 前。

## 上下文工程（2026-09-15 落地）
- 配额 SSOT = `docs/上下文工程/上下文工程_预算框架.md`（**B=48K** / S1–S9 / 让位顺序 历史→检索→图谱→画像）；活清单 = 根 `TODO_Context.md`。
- 已落地：`LLM_CTX_BUDGET=48000` + `OUTPUT_RESERVE=3000`；固定段 40%×B 告警 + 45%×B 强制减半重建图谱注入；S6/S7 按源独立截断 ≤3K + query 双端放置；S8 分层保留；Tool Result Clearing；`prune(30,180)`。四个介入点：`_build_system_prompt` → `guard.trim_history_to_budget` → `context.clear_old_tool_results` → `graph_analyzer.build_graph_context`。
- ⚠️ **跨请求历史无 tool 消息** → 任何"改历史 tool 消息"都是死代码或造非法序列（真机 400）。
- **token 记账三层**：run 级 `agent_runs.token_usage`；调用级 `llm_usage`（`core/llm/usage.py`，与 agent_runs 同库）；调试流水 `debug_log.db`。`call_llm` 必须传 `kind=`，查询 `GET /api/v1/agent/usage`。

## 知识图谱 / RAG / 其他模块
- 图谱：SQLite（nodes/edges）+ 节点 MD；**边方向 `A → B` = A 是 B 的前置**；质量清单 = 根 `TODO_Graph_Quality.md`（GQ-1…GQ-13）。参照系契约唯一参照 `docs/知识图谱/知识图谱_参照系契约.md` v3（未满足 AC-L1-3、AC-L2-1 前不得宣称"参照系"）；裁决链：运行时行为 > 契约 > AGENTS.md > 其他文档。
  - 节点过浅（已修 09-21）：`build_section_tree`（中文「第X章」优先、MD 标题兜底）+ `collect_units`（递归切单元：下限 1500 / 上限 5000 / 软 6000 / 下钻 ≤4 层），正文 <400 字拒收（`_drop_shallow_nodes`）。⚠ 滑窗兜底禁走 `chunk_text`（会把 `# 注释`、折行误判成标题）。
  - 重名重复（已修 09-20 GQ-1~3）：判重唯一实现 `KnowledgeGraph.find_node_by_name`（`normalize_node_name` + 同用户同学科），写入层 `create_node_with_content` 命中即并轨并**返回实际落点 ID**（调用方必须用返回值建边/回执），`_write_to_graph` 用 `node_id_alias` 重映射边，`dedup_status`（ok/degraded/unavailable）随 aggregate 带出；体检/存量合并 = `backend/scripts/inspect_graph_quality.py`（`--user N` / `--fix-dupes [--apply]`）。
- 掌握度四档 `mastery_bucket()`（WEAK 30 / MASTERED 70）；**唯一主信号 = 出题判分**（`grade_answer` +20；`update_mastery` 仅限 3 种硬证据）；出题跨调用去重（`avoid_questions`）；触发类提示词必须写"不要做什么"。先修推断 `core/prerequisite.py`（多准则投票 0.30）；图谱生成 `kb/graph_generator.py`（嵌入粗筛 0.78 + LLM 复核，`thinking=False` 与 `GRAPH_MAX_TOKENS`/`GRAPH_UNIT_MAX_CHARS` **成对调**）。
- RAG 四层：图谱 rag(`core/rag/`) + 上传 kb(`core/kb/`) + `hybrid_search/` + 对话注入 `rag_pipeline/`（`RagSource` 协议 + 规则 router；**gather 必须 `return_exceptions=True`**；单源 8s 超时静默返空）。kb 混合检索：向量 + whoosh BM25 各 30 → RRF → top5；whoosh 自定义 CJK bigram（**单字查询不命中**，不引 jieba）；PDF 逐页探针 + `<<<PAGE n>>>` 契约在 `parsers/base.py`。图谱 RAG 未接双检索（whoosh `node_id` NUMERIC 与图谱 TEXT id 冲突）。
  - ⚠️ **嵌入 API 欠费已确认**（阿里 text-embedding-v4 返回 400 `Arrearage`，09-20 复测仍未充值）→ `ApiEmbedder.embed()` 返回空 → kb 混合检索 / RAG / 建图语义去重三处长期降级。
- 画像 `core/profile/`（外部只 import `UserProfile` 与 `get_usage_mode`）；quiz：API 5 端点 + agent 工具（`quiz_generate` 后台异步 → `quiz_ready` → `grade_answer`；`_INFLIGHT` 必须在 `create_task` 前占位）。
- core 已分包：`agent/ llm/ agent_tools/ profile/ quiz/ kb/ rag/ rag_pipeline/ hybrid_search/ collector/`；顶层仍平铺 15 个文件（可选规整，改动面 30+）。未接线（有实现+测试+零生产引用）：`collector/pipeline_ingest.py`+`chapterizer.py`、`collector/quiz_splitter.py`（见 `TODO.md`）。
- 测试：**真实 API 测试已授权**（`pytest -m llm_api` + `backend/scripts/smoke_*.py`，须 `$env:PYTHONPATH="."` 从 backend 跑）；离线 mock 通过 ≠ 真机通过。

## 比赛选题（AIC 第八届，2026-09-20 调研）
> 细节与数据在 `docs/比赛/AIC/AIC_深度调研_第八届规则与往届各赛道国奖分析.md` + `AIC_赛题全景评估_七大赛题与五大赛道.md`，此处只留结论。
- 六赛道 = 算法挑战/创新/应用/主题/产业命题/专项；**"算法创新赛"是赛道**（校赛→省赛/区域赛→总决赛），**"AI+学科交叉"是其中赛题5**；"算法主题赛道"才是"纯学科主题"路线。
- **定选赛题5 AI+学科交叉**（备选赛题7 OPC）。三条硬要求：锚定一门具体课（建议"数据结构"，否则"需求分析"直接 0-4 分）+ 真实试点/用户反馈（20 分）+ **材料禁出现校名/Logo/指导教师**。视频 3-5 分钟 ≤300MB，方案 PDF ≤10M。
- 盘子（第七届公示）：算法应用 2155 > 算法创新 1071（国一 151≈14%）> 算法挑战 530 > 主题赛 69；奖级固定约 15/25/30% → **换赛题不提高获奖率，卡点在省赛二等以上**；"交叉学科"子类仅 49（国一 6）→ 2026 独立设奖是窗口期。
- ⚠️ 旧口径作废：第六届"乘数公式"与"一镜到底演示"已被第八届《赛题规则汇总》(260506) 的每题独立百分制评分表取代。
- 参赛路径：陕西**未设省级赛区** → 西工大走**"区域赛"**；时间轴 = 校赛 10/10 前 → 区域赛报名缴费 10/15 20:00 → 省赛 11/01 前 → 总决赛 11 月中下旬（区域赛提交/答辩形式须问组委会 025-85778806）。
- 抓官方 PDF：`curl.exe` 取原始 HTML + `Select-String 'uploads/[^"<]*\.pdf'` → 中文名用 `[System.Uri]::EscapeDataString()` → venv 的 PyMuPDF `fitz` 逐页 `get_text()`。
