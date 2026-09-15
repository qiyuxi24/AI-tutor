# 项目记忆

> 只留**跨会话稳定的决策与约束**。技术细节 → 代码 docstring 与 `docs/*.md`；
> **历程/取舍/实测/踩坑档案** → `docs/项目历程_决策与效果记录.md`。
> 上次整理：2026-09-13（第七次压缩：合并冗余 + 并入上下文工程 P0 收口）。

## 项目概述
- TutorAgent（原 AI-Tutor）：知识图谱驱动的自适应导学 Agent。前端 Vue3+Vite+D3+Pinia+EP；后端 FastAPI+SQLite+Jinja2+LLM(OpenAI 兼容)。
- **模型**：对话/Agent = MiniMax-M3；嵌入固定阿里 text-embedding-v4（独立 key/base）。
- **比赛材料冻结（2026-09-12 起）**：`docs/比赛/` 不主动读/改/同步，仅用户点名才碰。

## 硬约束（改代码前必看）
- **venv Python = `backend/venv/Scripts/python.exe` 必须用它**；uvicorn 必须 `--workers 1`（EventBus / 进程内 GC 依赖单进程）。
- **`.env` 唯一真值 = 根目录 `.env`**（`config.py` 读 `parents[3]/.env`）；三段：`LLM_API_KEY`(空则回退 DASHSCOPE) / `LLM_BASE_URL`+`MODEL_NAME` / `DASHSCOPE_API_KEY`+`EMBED_BASE_URL`。
- **嵌入唯一出口 = `core/llm/embed.py::embed_texts`**；`kb_manager._embed` / `rag/manager._embed` 是薄委托，**方法名必须保留**（类级 monkeypatch 与 `eval_rag.py` 实例遮蔽的缝）。
- **LLM 响应 JSON 提取唯一出口 = `core/llm/json_extract.py::extract_json(raw, kind)`**（`kind`="object"|"array"；三策略 + 字符串感知括号配平 + **未转义引号兜底修复**；空数组 `[]` 合法，判空须 `is None`）。三处重复实现已删，仅留同名薄委托。
- **`call_llm(system_prompt, messages, max_tokens=2000, thinking=True)`**：**M3 的思考与正文共享同一个 max_tokens 预算** → 批量结构化抽取（图谱生成等）必须 `thinking=False`（→ `thinking.type=disabled`，仅 M3；`reasoning_split` 只管响应格式），长 JSON 输出必须显式调大 max_tokens，否则两种失败交替出现（正文为空 / 被硬截断）。`response_format={"type":"json_object"}` 实测被端点接受但**不强制**（仍包 ```json 围栏），别指望它。
- **新增 LLM 调用走 `core/llm/fallback.py::_chat_create`**（400/404/422 **不触发**回退）；真打 LLM 仅 2 处：`agent_loop._chat_once`、`call_llm`。
- **`nodes.id` 是全局 TEXT 主键**（非 per-user）；`nodes.user_id` 有外键 → 脚本/测试建节点前须 `INSERT OR IGNORE INTO users`。
- **用户隔离**：图谱/画像/RAG/记录按 user_id 分区；`KnowledgeGraph(user_id)` 每次新建、用毕 `close()`。
- **仓库存在并发写入**：报错与文件内容对不上时先查 mtime/Get-FileHash 取同一快照再改。
- **代理残留陷阱**：Windows 注册表 `ProxyEnable=1`+`127.0.0.1:7890` 常在代理退出后残留 → `pip` 报 `ProxyError`。**装包前先 `$env:NO_PROXY="*"`**；清理仅 2 处：① 注册表 `ProxyEnable` 置 0（保留 `ProxyServer`）② `git config --global --unset http.proxy / https.proxy`。**httpx 也读注册表**，勿假设"只有 pip 受影响"。
- **`start.ps1` 后端必须独立控制台**（`-WindowStyle Hidden` + stderr → `logs/uvicorn-dev.log`，刻意不加 `-NoNewWindow`）：uvicorn 重启 worker 用 `os.kill(pid, CTRL_C_EVENT)`，该信号**广播给同一控制台所有进程** → 共享控制台时改一次 `app/` 代码就打死启动脚本并连带关前端。**判别：日志无 `[WARN] 后端进程已退出` = 脚本被信号打死**。清残留必须 `taskkill /T /F`。
- **部署与共享**：镜像 = **工作区快照**，只有 `docker compose up -d --build` 才同步代码；判断"是否最新"看 **build 是否全 CACHED**。已修三坑：镜像旧 / `environment` 白名单漂移（→ `env_file: .env`）/ 根 `data/prompts/*.j2` 漏拷（health 与首页仍 200，一发消息 `TemplateNotFound` 500）。局域网放行**取决于网络类别**（Docker 自带规则只有 `Public`；本机已有 `TutorAgent LAN 8080`）；**本机 curl 自己 IP 恒 200 证明不了外部可达**，只能另一台设备实测。

## 架构要点
- **对话 = Agent Loop**：`core/agent_loop.py::run_agent_loop`（纯编排 LLM↔KG_TOOLS，max_rounds=5 / 工具超时 60s / temp 0.3）；`call_llm` 仅纯文本。三分法：`agent_context`（上下文唯一写入点）/ `context_guard`（发送前预算守卫）/ `agent_events`（run_id 注入 + per-user 路由）。
- **运行记录 = 唯一事实源**：`core/agent_run_store.py`；传 user_id 自动落库；事件全带 **run_id**；`prune(30,180)`；`/agent/runs`（**stats 路由须在 `{run_id}` 前**）。
- **工具唯一注册表**：`core/agent_tools.py::_TOOL_SPECS`（10 工具 = 9 原生 + 1 MCP `mcp__websearch__web_search`）→ `KG_TOOLS`/`execute_kg_tool` 自动生成；重型实现放 `rag_tool.py` / `web_tool.py` / `download_tool.py`。**fetch_webpage=只读网页正文；download_resource=下载文档/电子书入知识库落 `AI 下载[/{学科}]`**。
- **MCP 宿主层** `core/mcp_host.py`（in-memory 传输 + schema 直通 + 同步桥 + 失败降级为空）；新增 server = `_SERVERS` 加 `(前缀, 模块路径)`；`WEB_SEARCH_ENABLED=false` 关闭。**搜索后端 = Bing RSS 主 + ddgs 兜底**（ddgs 9.16 已删 `bing`）。
- **提示词组装点** `chat_service._build_system_prompt()`（空图谱追加 `EMPTY_GRAPH_PROMPT`）；错误码 `core/error_codes.py`，异常 `[E-XXX]` 走 `log_error()`。
- **上下文工程（三件套 + P0 已收口）**：`agent_context`（协议）/ `context_guard`（发送前裁最旧，**system prompt 自身超预算改 warning 点名**）/ `token_estimator`（三层预估）/ `agent_run_store.prune`。**P0 三项全完成（2026-09-13）**：① `call_llm(max_tokens=, thinking=)` 参数化 —— 且查明图谱生成的**真正主因不是截断，而是思考吃满共享的输出预算**（实测 8 块里 3 块思考写 2.6~2.9 万字符、8000 token 全烧光、正文 0 字符 = 日志里的 `E-LLM-006`；成功块也贴着 6237/8000）② `finish_reason=="length"` 截断检测 ③ **图谱注入预算化** —— `build_graph_context(kg, detailed, max_chars, focus_node_id)` 硬上限 `GRAPH_INJECT_MAX_CHARS=12000` 字符，降级阶梯「省节点摘要 → 限量节点（当前教学节点邻域优先）」+ 尾部显式"展示范围"说明（实测 300 节点 44,713→7,418 token）。**仍未做**：`slice_graph` 选片（缺"当前学科/板块"信号，待 G2）、recursive 模式的 `knowledge_graph_framework` 块未设上限（与 summary 信息重叠，删块优先于加上限）、Tool Result Clearing、历史分层摘要、前缀稳定化。详见 `docs/上下文工程_调研与差距审计.md` §11。

## 知识图谱（核心资产）
- **存储**：SQLite（nodes/edges）+ 节点 MD（`data/knowledge/nodes/{user_id}/`）；subject（tags 派生）→ board 两级；`graph_middleware.slice_graph` 按需切片；`SUBJECT_UNCLASSIFIED="未分类"`。**边方向语义：`A → B` = A 是 B 的前置**。
- **参照系契约（唯一参照 `docs/知识图谱_参照系契约.md`，已升 v2）**：三问 ①能讲什么(L1) ②现在在哪(L2) ③依据什么(L3)；现状 **L1 基本落地**（体量有上限，仅剩表述形式）/ L2 半 / L3 未；**未满足 AC-L1-3、AC-L2-1 前不得宣称"参照系"**。裁决链：运行时行为 > 契约 > AGENTS.md > 其他文档。
- **掌握度四档** `mastery_bucket()`（WEAK 30 / MASTERED 70，全库唯一实现，前后端常量互相注释指向）。
- **先修推断** `core/prerequisite.py`：多准则投票（分母固定 TOTAL_WEIGHT=11.5、单旋钮 threshold=0.30）；写库入口 `apply_candidates`、API 默认 dry-run；merged micro F1 0.941。
- **检索扩跳** `rag_manager.search(hops=)` → `_expand_prerequisites`（只反向补前置，`score = 初检最高 × 0.6^depth`，**默认 0 零回归**）；A/B 实测命中 25%→62.5%、graph_dependent 30%→80%。
- **归属判定** `core/kg_taxonomy.py`（规则优先 + LLM 兜底，失败保持未分类；`add_knowledge_node` 支持模型自报，**显式优先**）；**刻意不在 `add_node` 挂钩子**（污染存储层 + 测试夹具会真打 LLM）。**写路径仍薄弱**（4 套并行写实现）。
- **图谱生成** `kb/graph_generator.py`：subject / section **共用 `_generate(kg, subject, file_ids, board)`**（差异仅"是否记板块名"；文件夹统一由 `_resolve_files` 展开）；嵌入粗筛 0.78 + LLM 二次确认（确认调用同样 `thinking=False`）；抽块调用 **`thinking=False`** + `GRAPH_MAX_TOKENS=8000` + `GRAPH_CHUNK_CHARS=3000`（**必须成对调**）+ `GRAPH_JSON_RETRIES=1`（非法 JSON 重发一次，实测同一块重发即成功）；`failed_chunks` 随返回值回传。多资料综合维护仅调研。

## RAG 体系（四层）
- 图谱 rag(`core/rag/`) + 上传 kb(`core/kb/`) + 混合检索 `hybrid_search/` + 对话注入（`rag_pipeline/`）。
- **rag_pipeline**：`RagSource` 协议 + 两 Source + 纯规则 router + 跨源融合；**gather 必须 `return_exceptions=True`**；单源 8s 超时、异常静默返空。
- **kb 混合检索**：向量 + whoosh BM25 → 宽召回各 30 → RRF → top_k=5；坑：mock 弱向量下 RRF 拖累 Recall@1（真实 embedding 待复跑定夺）。
- **parsers**：text / pdf / docx / pptx / image-ocr / 电子书（epub+fb2 零依赖）；**OCR 统一走 `parsers/ocr.py` 能力层**（`have_ocr`/`ocr_lines`/`render_pdf_page`，pdf 与 image 共用）；**PDF 是逐页探针 + 页级 OCR 路由**（只对扫描页 OCR），输出带页标记 `<<<PAGE n>>>`（契约在 `parsers/base.py`，分块器剥离并记 `chunk["page"]`）；`parse_document(..., verbose=True)` 回传 `ParseResult`（meta：pages/via/scanned_pages/ocr_pages/ocr_missing/garbage_ratio）；whoosh 无中文分析器（自定义 CJK bigram，**单字查询天然不命中**，不引 jieba）。
- **Agentic RAG**：`rag_search`（source graph|kb|all，`hops` 0~3）；**图谱 RAG 未接双检索**（whoosh node_id NUMERIC 与图谱 TEXT id 冲突）。
- **⚠️ 嵌入 API 欠费**：`text-embedding-v4` 返 `Arrearage` → 向量检索静默降级为 BM25/空。

## 用户画像（`core/profile/` 分层包）
- schema + store（原子写 `os.replace` + 旧 MD 迁移）+ markdown + manager（`UserProfile` 门面）；数据 `data/profiles/{user_id}.json`。
- **外部只 import `UserProfile` 与 `get_usage_mode(user_id)`**；`/profile` PATCH 走 `model_dump(exclude_unset=True)`；`get_summary()` 空画像返 `""`；`get()` 是纯读（无写副作用）。

## 其他模块 / 测试
- **quiz**：`core/quiz/` + API 5 端点，依据 = 教材 KB 混合检索，错误码 E-QUIZ-001~005。
- `collector/quiz_splitter.py`（整卷→逐题，零 LLM）可用 + 9 测试，但**零引用、未接入上传链路**（债见 `TODO_Collector.md` #2）。
- 测试三层：单元(mock) → 集成 → 离线评测；`pytest -m "not llm_api"` = **630 passed, 7 deselected**（2026-09-13）。
- `.gitignore` 已排除 `data/`、`reports/`、`docs/比赛/官方材料/`、`.workbuddy/ppt_workspace/`。

## 规范与偏好
- **README 规范 v3 = `docs/README_编写规范.md`（唯一依据，写前先读）**：主语言 SSOT = 中文，英文为派生镜像（顶部 `<!-- base: ... -->`，结构同构）；CI 双语校验未实现。
- 喜欢渐进式改进、清单式推进；重视代码安全与架构一致性；倾向复用成熟组件；**整理/审计类任务不擅自 commit**。
- **ponytail skill = 写代码必加载**：触发看**产出物**（产出代码必须先 use_skill；写文档/讨论/问答豁免）；核心 = 七阶决策阶梯（YAGNI→复用→标准库→原生→已装依赖→一行→最少代码）。
- **Git 纪律**：不主动 commit/push；确需提交按**文件族**拆（跨文件按时间拆会留 import 失败中间态）；**单文件跨主题按依赖方向排序**（先被调用方）；测试与其修复必须同一提交；提交前跑离线全量测试。

## 文档索引
- **历程与决策**：`docs/项目历程_决策与效果记录.md`（2026-08-27 ~ 09-13 全景）
- **上下文工程**：`docs/上下文工程_调研与差距审计.md`（现状审计 + 业界/学术调研 + 图谱生成三段式 + §10/§11 两轮实施记录）
- **知识图谱**：`docs/知识图谱_参照系契约.md`（唯一参照）、`_模块结构与封装调研.md`、`_论文与开源实现调研.md`、`_P0实现方案与核心思路.md`、`_P1扩跳与AB对照实验.md`、`_多资料综合维护调研.md`
- **Agent Loop / RAG / MCP**：`docs/AgentLoop_*.md`、`docs/RAG_*.md`、`docs/RAG_视觉解析策略_调研与实施方案.md`、`docs/MCP_网页搜索工具_调研与实施方案.md`
- **部署 / 运维三层**：`deploy/README.md`（首次部署）→ `docs/Docker_学习路径与工程化部署.md`（入门）→ `docs/运维_生产上线与日常运营指南.md`（上线后运营）
- 参赛材料（**已冻结**）：`docs/比赛/`；其他：`docs/README_编写规范.md`、`docs/QUIZ_出题逻辑调研.md`、`docs/学习进度仪表盘设计.md`、`docs/教育资料采集模块_设计讨论.md`、`docs/国创技术报告.md`
- 开发索引（AI Agent 用）：根 `AGENTS.md`
