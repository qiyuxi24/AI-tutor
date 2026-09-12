# 项目记忆

## 项目概述
- TutorAgent（原 AI-Tutor）：知识图谱驱动的自适应导学 Agent
- **三赛同投/一体三用（2026-09-05 起，同代码投三赛；唯一参赛总纲=根目录 COMPETITION.md，已合并原三赛调研文档）**：①中国国际大学生创新大赛(2026)=教育部国家级，**已校赛报名**（高教主赛道·本科创意组方向）；②中国教育技术协会"AI+教育"创新应用技能大赛·大学生OPC创新创业AI Agent赛道（校内截止2026-10-15，材料最全；赛道由协会艺术设计专委会承办）；③全球校园人工智能算法精英大赛 AIC 第八届·算法创新赛道（赛题5 AI+学科交叉/6 AI+创新创业/7 AI+OPC创新创业对口；**每队≤3人**、校赛须10/10前、省赛报名截止**10-15 20:00**，与 AI+教育同日双截止）。差异打法：国创讲创新创业/商业、AI+教育讲五维+OPC点题、AIC 讲技术纵深+量化技术报告
- 前端：Vue 3 + Vite + D3 + Pinia + Element Plus（2026-08-27 引入）
- 后端：FastAPI + SQLite + Jinja2 + LLM（OpenAI 兼容）。**对话/Agent 主模型默认 MiniMax-M3**（国内站 api.minimaxi.com/v1）；**嵌入固定阿里 text-embedding-v4**（独立 key/base）
- 参赛主文档：COMPETITION.md（《TutorAgent 参赛总纲·三赛同投》，含注意事项总清单 E0-E8 与统一时间线）；工程清单：TODO.md；AI+教育规格核实：docs/比赛规格与参赛策略.md；对标分析：docs/标杆项目对标分析.md

## 用户偏好
- 喜欢渐进式改进，创建清单后逐项推进
- 重视代码安全和架构一致性
- 倾向于复用成熟开源组件，避免重复造轮子
- **README 写作规范（2026-09-07 固定，v2）**：本仓库所有 README 一律遵循 `docs/README_编写规范.md`（唯一依据，写前先读）。规范含 5 权威来源、8 条固定原则（禁放清单：TODO、比赛日程、变更日志、排障长文）、13 章固定模板（0 Badges→12 License）、第 5 章「评审/技术证据场景」（双模式：门面版 vs 证据版；ER1-ER6 派生不双写/断言可复核/mock 方法学自证/截图必配；证据版模板 E0-E7；证据版只能当"技术附件"不能当完整申报材料，素材源=docs/国创技术报告_校评支撑版.md）。当前根 README.md 即按此规范写的全景门面版（ASCII 架构图 + 两大闭环，无 TODO）。
- **强制编码准则：ponytail skill（写代码必加载）**（2026-09-05 安装，同日强化为强制规则）。项目级 skill 位于 `.codebuddy/skills/ponytail/`（完整克隆自 DietrichGebert/ponytail，MIT，127k stars），根目录 `SKILL.md` 是 CodeBuddy 识别入口。触发逻辑**不看关键词、看产出物**：凡产出物是代码（写/改 .py/.vue/.js 等、加功能、重构、修 bug、写测试/配置/脚本、设计实现、选库）必须第一步 use_skill 加载；写文档/纯讨论/翻译/问答豁免。所有编码任务须应用其「七阶决策阶梯」（YAGNI→复用已有→标准库→原生特性→已装依赖→一行→最少代码）。子 skill 按需动态加载（on-demand 专项命令，不随写代码强制加载）：`ponytail-review`（diff 过度设计审查）/`ponytail-audit`（全库审计）/`ponytail-debt`（ponytail: 注释债务台账）/`ponytail-help`（速查卡）已分别注册于 `.codebuddy/skills/<name>/SKILL.md`；`ponytail-gain`（纯 benchmark 宣传记分板）未装。GitHub 直连不稳，用 codeload zip 下载。

## 关键架构决策
- Docker 部署（2026-08-31）：单容器双进程（Nginx:80 + Uvicorn:8000）。**硬约束**：uvicorn 必须 --workers 1（事件总线/限流进程内）；数据持久化挂载 data/{knowledge,conversations,profiles} + backend/data（rag/kb/quiz）；.dockerignore 排除 .env（load_dotenv override=True 陷阱）
- **.env 唯一真值 = 根目录 .env**（2026-09-06）：本地与 Docker compose 共源。backend/.env 与 backend/.env.example 已删除；`config.py` 读 `Path(__file__).parents[3]/.env`；唯一模板根 `.env.example`；compose environment 显式注入 LLM_API_KEY/EMBED_BASE_URL（容器内根 .env 不存在，依赖注入）。**LLM 三段配置**：LLM_API_KEY(对话 key，空则回退 DASHSCOPE_API_KEY)/LLM_BASE_URL+MODEL_NAME(默认 MiniMax-M3)/DASHSCOPE_API_KEY+EMBED_BASE_URL(嵌入钉阿里)。llm_client 拆 `client`(chat)+`embed_client`(嵌入)。**2026-09-12 起嵌入走唯一出口 `core/llm/embed.py::embed_texts`**（模型名/截断 6000 字/失败与"部分返回"兜底集中一处），`kb_manager._embed`、`rag/manager._embed` 只是薄委托——**方法名必须保留**：测试用类级 `monkeypatch.setattr(KbManager, "_embed", _fake_embed)`、`scripts/eval_rag.py` 用实例属性遮蔽，是既有遮罩缝。README/install.ps1 已同步
- **对话 = Agent Loop**（2026-09-06 Batch1）：Phase 1 流式文本 SSE（call_llm_stream，过渡）→ Phase 2 后台 `agent_loop.run_agent_loop`（`app/core/agent_loop.py` 纯编排：LLM↔KG_TOOLS 多轮真循环，max_rounds=5 / 单工具超时 60s / temp 0.3）；`call_llm` 仅纯文本（去 enable_tools），`call_llm_tools` 已删。GraphAnalyzer 独立调用暂保留。设计文档：docs/AgentLoop_重构设计讨论.md（决策 #1-5）+ 调研 docs/AgentLoop_业界调研与学习路线.md
- **Agent 运行记录整合（2026-09-08，事件↔记录唯一事实源 + 分层保留清理）**：新增 `app/core/agent_run_store.py`（SQLite `agent_runs` 表，WAL；save_run/list_runs/get_run/recent_completion_tokens）。`run_agent_loop` 传 user_id 即自动落库（ok/error 都落；db_dir 可注入测试）；**证据级 evidence JSON**（thinking 全文/完整 tool arguments+tool_call_id/完整 tool 返回/final_text，护栏 200k 截断）；所有 EventBus 事件 payload 带 **run_id**（前端可回源）。旧 jsonl save_trace 已删；chat_service 删 process_background_tools 死代码。token_estimator Phase2 历史读 agent_runs。**库目录迁移到 `backend/data/agent_runs`**（原根 data/agent_runs 废弃，随 Docker `./backend/data` 卷持久化，compose 无需改）。**分层保留清理**：`prune(full_days=30, strip_days=180)`——近 30 天完整保留(evidence 回放)→30~180 天摘 evidence 留元数据（token 统计/审计不受影响）→>180 天整行删；幂等全局按 started_at。触发=main.py on_event startup 跑一次 + 24h 后台任务（uvicorn workers=1 唯一实例）；shutdown cancel。**治理 API**：GET /agent/runs 分页(limit/offset+total)、GET /agent/runs/stats（total/ok/errors/llm_calls/累计 token/evidence_bytes；stats 路由须在 {run_id} 前）、DELETE /agent/runs/{run_id} 单删、DELETE /agent/runs 清空本人（越权隔离）。测试 464 过（test_agent_run_store 扩至 10 例：prune 分层/幂等/跨用户、delete 隔离、stats 分页）；唯一挂 test_token_counter none_content 系既有 tiktoken 退化偏差(8vs7)与本次无关。**待办**：conversations 内嵌 tools/thinking 字段去留（随前端可视化第二步一起定，届时摘除改 run_id 回源）、run 与会话无关联键（无 conversation_id 列，无法按会话清/溯源）
- **模型回退链（2026-09-08，静默降级）**：对话主模型是外部 SaaS 不可控，配额耗尽(402/403)/认证失败/持续异常会让对话瘫痪。config +3 可选字段 `fallback_llm_api_key/base_url/model_name`（env `FALLBACK_*` 三件套缺任一不启用；key 缺省回退 DASHSCOPE_API_KEY）；llm_client 模块级 `_LLM_CANDIDATES`（主→备用）+ `_should_fallback`（触发认证/401/402/403/429/5xx/超时/断连；**不触发 400/404 请求侧错误**）+ 公共 `_chat_create(**kwargs)`（每候选注入 model + _with_retry + 降级只 warning 日志，全败抛 RuntimeError）。**全后端真打 LLM 仅 2 处**收敛至此：`agent_loop._chat_once`（对话）与 `call_llm`（一次性文本）——新增任何 LLM 调用都应走 `_chat_create`。单级 fallback（YAGNI；多供应商=把候选表加长）。README 配置章 + .env.example 已记此层设计。测试 test_llm_fallback.py 3 例
- 知识图谱：SQLite + 节点 MD 文件（data/knowledge/nodes/{user_id}/*.md），每个用户独立隔离；**nodes.id 是全局 TEXT PRIMARY KEY**，生成 ID 必须查全局唯一（`_node_id_exists_globally`）
- 图谱分组两级：学科(subject) → 知识板块(board)，`graph_middleware.py` 按需切片（全量→学科→板块）
- 事件总线：chat_service 通过 EventBus 发布事件；错误码体系：标准化错误码 + 中英文消息
- 对话系统提示词组装点：`chat_service._build_system_prompt()`（async），RAG/知识库上下文在此注入
- 用户画像结构化 v2（2026-08-31）：`data/profiles/{user_id}.json`（basic/goals/knowledge_background/learning/preferences/ai_notes），旧 MD 自动迁移备份 .md.bak；API：GET/PUT/PATCH /profile + POST/DELETE /profile/notes/{id}；`update_user_profile` 工具改为 `add_note`；完整度 get_completeness（10 字段加权）
- 主题切换：`html.dark` class + CSS 变量 + EP 官方 dark css-vars；theme.js 自实现 useDark（dark/light/system，localStorage + prefers-color-scheme）；isDark 是 computed，模板勿再调用

## RAG 体系（核心资产，四层）
- 图谱 rag（app/core/rag/）+ 上传文件 kb（app/core/kb/）+ 混合检索 hybrid_search + 对话注入
- **rag_pipeline 去耦合**（2026-08-29）：RagSource 协议 + GraphRagSource/KbRagSource + 纯规则 router（问候/过短跳过）+ pipeline 跨源融合；**gather 必须 return_exceptions=True**；单源 8s 超时/异常隔离，失败静默返回空
- **kb 混合检索链路**：向量(text-embedding-v4 + SQLite) + whoosh BM25 → 宽召回各 30 → RRF 融合 → top_k=5；chunk 带 path 溯源（动态拼，零迁移）+ 父级扩展（PARENT_EXPAND_CHARS=1500 / PARENT_MAX_BLOCKS=8）；**已知坑：mock 弱向量下 RRF 拖累 Recall@1（0.766 vs 加权 0.818 vs BM25 0.964），真实 embedding 待重跑**
- **parsers 去耦合解析包**（注册表+策略）：text(30+种)/pdf(PyMuPDF+扫描OCR回退)/docx/pptx/image-ocr(RapidOCR)/legacy；可选依赖探测降级
- **Agentic RAG 完全体**（P3，2026-08-30）：`rag_search` 工具（query/source graph|kb|all/top_k 1~5），KG_TOOLS 7→8；`_run_async` helper 解决同步里跑 async（有循环→线程池新循环 asyncio.run，无→直接 run）
- **图谱 RAG 未接入双检索**（待推广 hybrid_search 到 rag_manager）。2026-09-12 评估：`hybrid_search/whoosh_index.py` 的 schema 把 `node_id` 定成 `NUMERIC`（KB int），图谱 node_id 是 TEXT → 复用需改 schema + 老索引迁移/双 schema 兼容，属功能级改动，暂缓
- **embedder 可插拔**：text-embedding-v4 API 优先 → sentence-transformers（torch 未装）→ numpy 哈希兜底
- **已知问题：DASHSCOPE qwen 聊天额度 exhausted（HTTP 403）→ 已切 MiniMax-M3 做对话主模型（embedding API 阿里仍可用）**；MiniMax M3 支持图/视频输入但 RAG 多模态暂未接入（待单独设计）

## 知识图谱生成 & 出题
- **graph_generator**（2026-08-29）：从学科书籍自动生成图谱（generate_subject_graph / generate_section_graph），复用 tags 建模学科（第一个非难度标签）；集成嵌入粗筛(0.78)+LLM 二次确认+降级(0.90)语义去重（合并"栈/堆栈"），`_write_to_graph` 返回 merged_nodes
- **quiz 出题模块**（2026-08-29，借鉴 OpenMAIC）：`app/core/quiz/`（schema/generator/grader/quality/quiz_store），API：POST /quiz/generate、GET /quiz/questions、POST /quiz/{id}/grade、GET /quiz/stats；依据=上传教材 KB 混合检索；错误码 E-QUIZ-001~005

## 测试与评测
- 三层体系：单元（90+ mock 用例）→ 集成（test_integration_kb.py 11 用例）→ 离线评测（CMRC2018 trial，scripts/eval_rag.py --embed mock|api）
- whoosh 批量写入：`upsert_bulk` + `begin_deferred/flush`，256 文档入库 180s→4.7s（38 倍）

## 知识库技术栈
- parsers（PyMuPDF/python-docx/python-pptx/RapidOCR）+ numpy + whoosh(BM25) + python-multipart
- whoosh 中文分词：无内置 ChineseAnalyzer，自定义 RegexTokenizer(连续CJK)+NgramFilter(2,2) bigram + 英文 StemmingAnalyzer
- 前端：Element Plus（el-tree 目录树 + el-upload 上传）
- **venv Python 位置**：`backend/venv/Scripts/python.exe`（3.13.9）。系统 Python 3.11 fastapi 过旧无法导入 chat_service，必须用 venv
