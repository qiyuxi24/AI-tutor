# 项目记忆

> 只留**跨会话稳定的决策与约束**，细节一律指向代码 docstring 或 `docs/*.md`。
> 整理：2026-09-12（第四次压缩，解决注入超限）。

## 项目概述
- TutorAgent（原 AI-Tutor）：知识图谱驱动的自适应导学 Agent。前端 Vue3+Vite+D3+Pinia+EP；后端 FastAPI+SQLite+Jinja2+LLM(OpenAI 兼容)
- **模型**：主对话/Agent = MiniMax-M3；嵌入固定阿里 text-embedding-v4（独立 key/base）
- **比赛材料冻结（2026-09-12 起）**：`docs/比赛/` 不主动读取/更新/随代码同步，仅用户点名才碰

## 硬约束（改代码前必看）
- **venv Python = `backend/venv/Scripts/python.exe` 必须用它**；uvicorn 必须 `--workers 1`（EventBus / 进程内 GC 依赖单进程）
- **`.env` 唯一真值 = 根目录 `.env`**（`config.py` 读 `parents[3]/.env`）；LLM 三段：`LLM_API_KEY`(空则回退 DASHSCOPE) / `LLM_BASE_URL`+`MODEL_NAME` / `DASHSCOPE_API_KEY`+`EMBED_BASE_URL`
- **嵌入唯一出口 = `core/llm/embed.py::embed_texts`**；`kb_manager._embed` / `rag/manager._embed` 是薄委托，**方法名必须保留**（测试类级 monkeypatch + `eval_rag.py` 实例遮蔽的遮罩缝）
- **新增 LLM 调用走 `core/llm/fallback.py::_chat_create`**；真打 LLM 仅 2 处：`agent_loop._chat_once`、`call_llm`
- **`nodes.id` 是全局 TEXT 主键**（非 per-user）；`nodes.user_id` 有外键 → 脚本/测试建节点前须 `INSERT OR IGNORE INTO users`
- **用户隔离**：图谱/画像/RAG/记录按 user_id 分区；`KnowledgeGraph(user_id)` 每次新建、用毕 `close()`
- **仓库存在并发写入**：报错与文件内容对不上时先查 mtime/Get-FileHash 取同一快照再改
- **本机代理残留陷阱（2026-09-13 查明，同日复现两次）**：Windows 系统代理 `127.0.0.1:7890` 常处于 `ProxyEnable=1` 但**代理程序未运行**（关机前开代理忘了关）→ `pip install` 报 `ProxyError: Cannot connect to proxy`（pip 配置/环境变量里都没有代理，来源是注册表，urllib3 Windows 下读 `getproxies_registry()`）。**装包前先 `$env:NO_PROXY="*"`**（或启动代理软件）；httpx 不读注册表代理，**应用自身联网不受影响**。
  **清理点只有 2 处**：① 注册表 `HKCU:\...\Internet Settings` 的 `ProxyEnable` 置 0（保留 `ProxyServer` 值，下次开代理免填）+ 补发 `InternetSetOption`(39/37) 通知刷新；② `git config --global --unset http.proxy / https.proxy`（git 不读系统代理，必须单独清）。其余层（WinHTTP / 环境变量 / pip config / npm）默认干净
- **`start.ps1` 的后端必须在独立控制台**（**刻意不加** `-NoNewWindow`，用 `-WindowStyle Hidden` + stderr 重定向到 `logs/uvicorn-dev.log`）：uvicorn 在 Windows 用 `os.kill(worker_pid, CTRL_C_EVENT)` 重启 worker（`supervisors/basereload.py:91`），而 **`CTRL_C_EVENT` 会广播给同一控制台的所有进程** → 共享控制台时，改一次 `app/` 下代码触发热重载就会打死启动脚本并连带关掉前端（症状「提示启动完成、服务却莫名全关」）。**判别技巧：日志里没有 `[WARN] 后端进程已退出` 那行 = 脚本是被信号打死的，不是检测到子进程退出**。配套两条：`--reload-dir app`（缺 watchfiles 时 StatReload 会 `rglob` 整个 backend）、清理残留必须 `taskkill /T /F` 杀整棵进程树（只杀父 PID 会留下 `spawn_main` 孤儿继续占 8000）
- **Docker 部署（2026-09-13 查明并修复）**：镜像 = **工作区快照**，只有 `docker compose up -d --build` 才同步代码，光 `up -d` 永远跑旧版（本次实测镜像停在 8/31、落后 8 个提交）。两个已修的真坑：① **根 `data/prompts/*.j2` 必须由 Dockerfile 单独 `COPY`**（`prompt_loader.PROMPT_DIR` = 项目根/data/prompts，不在 backend/ 下）；漏掉时 `/api/health` 与首页仍 200，但一发消息就 `TemplateNotFound` 500 —— 判别法：用「只 401 不 404」的新端点探针 + `docker exec` 直接跑 `get_system_prompt()`；② `docker-compose.yml` 的 `environment` 原是**硬编码 8 项白名单**，而 config.py 有 20+ 字段且 `.dockerignore` 排除了 `.env` → 新配置静默失效，已加 `env_file: .env`（优先级仍低于 environment）
- **Windows 局域网共享本机服务（校园网场景）**：Docker Desktop 安装时留下的 `com.docker.backend.exe` 入站规则（`Inbound/Allow/Profile=Public/LocalPort=Any`）**已天然放行所有容器端口发布** → 同学直接访问 `http://<WLAN_IP>:8080`，**不需要加防火墙规则、不需要 UAC**；dev 模式（vite 5173）没有对应规则，才需要提权放行。运行时数据全在挂载卷（`data/{knowledge,conversations,profiles}` + `backend/data/*`），只有 `data/prompts` 属于「随镜像走」的配置

## 架构要点
- **对话 = Agent Loop**：`core/agent_loop.py::run_agent_loop`（纯编排 LLM↔KG_TOOLS，max_rounds=5/工具超时 60s/temp 0.3）；`call_llm` 仅纯文本
- **运行记录 = 唯一事实源**：`core/agent_run_store.py`；传 user_id 自动落库；事件全带 **run_id**；`prune(30,180)`；`/agent/runs`（**stats 路由须在 `{run_id}` 前**）
- **模型回退链**：`_should_fallback`（401/402/403/429/5xx/超时/断连触发；**400/404 不触发**）；全败抛 RuntimeError
- **工具唯一注册表**：`core/agent_tools.py::_TOOL_SPECS`（9 工具 = 8 原生 + 1 MCP `mcp__websearch__web_search`）→ `KG_TOOLS`/`execute_kg_tool` 自动生成；重型实现放 `rag_tool.py`/`web_tool.py`
- **MCP 宿主层**：`core/mcp_host.py`（in-memory + schema 直通 + 同步桥 + 降级）；新增 server = `_SERVERS` 加 `(前缀, 模块路径)`；`WEB_SEARCH_ENABLED=false` 关闭；SearXNG **有意保持预留**（ddgs 实测可用；官方部署含 valkey 两容器，且本机网络对常规 UA 不友好，自建未必更稳 —— 启用路径见 `docs/MCP_网页搜索工具_调研与实施方案.md` §9.5）
- **提示词组装点** `chat_service._build_system_prompt()`；错误码 `core/error_codes.py`，异常 `[E-XXX]` 走 `log_error()`

## 知识图谱（核心资产）
- **存储**：SQLite（nodes/edges）+ 节点 MD（`data/knowledge/nodes/{user_id}/`）；subject→board 两级；`graph_middleware.slice_graph` 按需切片；`SUBJECT_UNCLASSIFIED="未分类"`
- **学科由 tags 派生**（`kg.node_subject()` = 第一个非难度标签）；`board` 是独立列；前端一次只渲染一个学科
- **先修边方向语义**：`A → B (prerequisite)` = A 是 B 的前置（先学 A）；`get_prerequisites` 曾方向反（返后继），2026-09-12 已修 + 回归测试
- **写路径薄弱（部分收敛）**：4 套并行写实现；`core/kg_taxonomy.py` 已在 3 条路径 `add_node` 前统一补 subject/board（规则优先+LLM 兜底）；`add_knowledge_node` 工具另支持模型自报 `subject`/`board`（**显式优先**：subject 置 tags 首位、board 直落，缺的才自动判定）；**刻意不在 `add_node` 挂钩子**（会污染存储层、测试夹具会真打 LLM）。收口顺序见结构调研文档
- **参照系契约（唯一参照 `docs/知识图谱_参照系契约.md`）**：三问 ①能讲什么(L1)②现在在哪(L2)③依据什么(L3)；现状 L1 部分 / L2 半 / L3 未；**未满足 AC-L1-3、AC-L2-1 前不得宣称"参照系"**。裁决链：运行时行为 > 契约 > AGENTS.md > 其他文档
- **已落地 P0**：学科导出用 `node_subject()`；掌握度四档 `mastery_bucket()`（WEAK 30 / MASTERED 70）；`core/prerequisite.py` 多准则投票（**分母固定 TOTAL_WEIGHT=11.5，单旋钮 threshold=0.30**；写库入口 `apply_candidates`；API 默认 dry-run；merged micro F1 0.941）
- **已落地 P1（2026-09-12）**：检索扩跳 `rag_manager.search(hops=)` → `_expand_prerequisites`（只反向补前置，`score = 初检最高 × 0.6^depth`，**默认 0 零回归**；全链路 `rag_search.hops` → `RagContext.metadata["graph_hops"]`）；A/B 对照 `scripts/eval_graph_injection.py`（自包含图谱，测试用户 9902 不碰真实数据）实测命中 **25%→62.5%**、graph_dependent **30%→80%**、token +6%
- **图谱生成** `kb/graph_generator.py`（嵌入粗筛 0.78 + LLM 二次确认）；**多资料维护仅调研**（溯源为先，前提先收敛写路径）

## RAG 体系（四层）
- 图谱 rag(`core/rag/`) + 上传 kb(`core/kb/`) + 混合检索 `hybrid_search/` + 对话注入
- **rag_pipeline**：RagSource 协议 + 两 Source + 纯规则 router + 跨源融合；**gather 必须 `return_exceptions=True`**；单源 8s 超时、异常静默返空
- **kb 混合检索**：向量 + whoosh BM25 → 宽召回各 30 → RRF → top_k=5；坑：mock 弱向量下 RRF 拖累 Recall@1（真实 embedding 待重跑）
- **parsers**：text / pdf(PyMuPDF+OCR 回退) / docx / pptx / image-ocr / 电子书（epub+fb2 零依赖）；whoosh 无中文分析器（自定义 CJK bigram，不引 jieba）
- **Agentic RAG**：`rag_search`（source graph|kb|all，`hops` 0~3）；**图谱 RAG 未接双检索**（whoosh node_id NUMERIC 与图谱 TEXT id 冲突）
- **⚠️ 嵌入 API 欠费**：`text-embedding-v4` 返 `Arrearage` → 向量检索静默降级为 BM25/空，待充值或换 key

## 用户画像（`core/profile/` 分层包）
- schema + store（原子写 `os.replace` + 旧 MD 迁移）+ markdown + manager（`UserProfile` 门面）；数据 `data/profiles/{user_id}.json`
- **外部只 import `UserProfile` 与 `get_usage_mode(user_id)`**；`/profile` PATCH 走 `model_dump(exclude_unset=True)`；`get_summary()` 空画像返 `""`

## 其他模块 / 测试
- **quiz**：`core/quiz/` + API 5 端点，依据 = 教材 KB 混合检索，错误码 E-QUIZ-001~005
- `collector/quiz_splitter.py`（整卷→逐题，零 LLM）已可用 + 9 测试，但**零引用、未接入上传链路**（债见 `TODO_Collector.md` #2）
- 测试三层：单元(mock) → 集成 → 离线评测；`pytest -m "not llm_api"` = **557 passed, 7 deselected**（2026-09-13，装 tiktoken 后 2 个原条件跳过用例恢复）
- `.gitignore` 已排除 `data/`、`reports/`、`docs/比赛/官方材料/`、`.workbuddy/ppt_workspace/`

## 规范与偏好
- **README 规范 v3 = `docs/README_编写规范.md`（唯一依据，写前先读）**：主语言 SSOT = 中文，英文为派生镜像（顶部 `<!-- base: ... -->`，两侧结构同构）；CI 双语校验未实现
- 喜欢渐进式改进、清单式推进；重视代码安全与架构一致性；倾向复用成熟组件；**整理/审计类任务不擅自 commit**
- **ponytail skill = 写代码必加载**：触发看**产出物**（产出代码必须第一步 use_skill；写文档/讨论/问答豁免）；核心 = 七阶决策阶梯（YAGNI→复用→标准库→原生→已装依赖→一行→最少代码）
- **Git 纪律**：不主动 commit/push；确需提交按**文件族**拆（跨文件按时间拆会留 import 失败中间态）；提交前跑离线全量测试

## 文档索引
- 参赛材料（**已冻结**）：`docs/比赛/`
- 知识图谱：`docs/知识图谱_参照系契约.md`、`_模块结构与封装调研.md`、`_论文与开源实现调研.md`、`_P0实现方案与核心思路.md`、`_P1扩跳与AB对照实验.md`、`_多资料综合维护调研.md`
- Agent Loop / RAG / MCP：`docs/AgentLoop_*.md`、`docs/RAG_*.md`、`docs/MCP_网页搜索工具_调研与实施方案.md`
- README / 贡献指南：`docs/README_编写规范.md`、`README.md`、`CONTRIBUTING.md`
- 其他：`docs/QUIZ_出题逻辑调研.md`、`docs/学习进度仪表盘设计.md`、`docs/教育资料采集模块_设计讨论.md`、`docs/Docker_学习路径与工程化部署.md`、`docs/国创技术报告.md`
- 开发索引（AI Agent 用）：根 `AGENTS.md`
