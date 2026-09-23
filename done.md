# TutorAgent 已完成模块归档（DONE）

> 用途：`TODO.md` / `TODO_Collector.md` 的**完成侧归档**。待办留在原文件，做完的**搬**到这里（不是复制）。
>
> **维护约定**
> 1. **只追加，不重写**：新完成项追加到对应分节末尾；无对应分节就在文件末尾新建 `## <模块名>`。整文件重写既白烧 token，也有覆盖他人改动的风险。
> 2. 本文件是**历史台账**，不是契约；实现细节以代码 docstring 与 `docs/*.md` 为准。
> 3. 迁出时保留日期与关键证据（文件 / 测试名），便于回溯"当时验证到哪一步"。
>
> 来源：`TODO.md`（2026-09-13 迁出 P0/P1 已完成项 + 原「✅ 已完成（归档）」节）、`TODO_Collector.md`（B1/B2 已完成项）。
> 上次整理：2026-09-13

---

## 阶段就绪度评估快照（2026-09-08 复核）

**结论：工程化底子充足；比赛材料侧缺 演示数据 + 阿里 embedding 额度。**

| 维度 | 等级 | 关键证据 |
|---|---|---|
| 架构/分层 | A | rag_pipeline / kb / hybrid_search / quiz / collector 去耦合（注册表+策略）；Agent Loop 编排/事件/落库/工具四层分离 + 工具注册表 `_TOOL_SPECS` |
| 自动化测试 | A | 离线零网络集全绿（当时 491 passed；2026-09-13 已 580 passed, 7 deselected）；real_api 7 passed 单独报告 |
| 部署能力 | A- | Docker 多阶段 + healthcheck + 数据卷 + nginx SSE 反代 |
| 健壮性 | A- | LLM 重试(指数退避+抖动) / 错误码 / 单源超时隔离 / SSRF 防护 / Agent Loop 超时+轮次护栏 / reasoning_split 防思考污染 |
| 文档同步 | A- | README 规范 v3 全面重写 + `README.en.md` 镜像 + AGENTS.md 开发索引 + CONTRIBUTING 双语 |
| 运维/CI | A | 结构化日志轮转 + 聊天限流 + lifespan + `.github` CI 全绿 |
| 致命阻塞 | — | MiniMax-M3 对话/流式/工具真网全通；残余依赖仅 阿里 embedding 额度（卡 真实向量灌库 / 评测 两条） |

---

## 1. P0 收尾

### 1.1 文档同步 + 改名 TutorAgent（2026-09-05）
- README.md 全面重写（去"无 Docker"过时说法；补知识库/出题/采集/混合检索；标题改 TutorAgent）
- `frontend/index.html` `<title>` → TutorAgent + `lang="zh-CN"`
- LoginView / ActivityBar / OnboardingGuide / style.css 等 "AI Tutor" 文案 → TutorAgent
- `backend/app/main.py` `FastAPI(title="TutorAgent API")`；start.ps1 / install.ps1 横幅；deploy/README、error_codes.md、`docs/比赛/AI-Tutor_项目概述.md` 标题与文案
- **刻意保留**：logger 名 `ai-tutor`（30 处）与 localStorage key `ai_tutor_*` 属内部标识，改名会破坏登录态且零用户价值

### 1.2 真实 LLM 链路验证（2026-09-06 ~ 09-08）
- MiniMax-M3 chat / 流式 / 工具调用真网全通（`scripts/test_minimax.py` 自检 + `scripts/smoke_agent_loop.py` 冒烟）
- Agent Loop 真网 7/7 passed（L1 工具选择 / L2 参数提取 / L3 结果利用 / L4 协议合规 / token / trace）→ `reports/real_api_test_report.md`
- `reasoning_split` 防思考污染实测（`tests/test_minimax_thinking.py` 7 例）

### 1.3 图谱重复节点处理（2026-09-08）
- `create_node_from_ai` 遇已存在 ID 自动转更新模式：补全字段（summary/tags/difficulty 等）+ 追加 MD 内容 + 补建前置边，不再 ValueError 中断 Agent 循环

### 1.4 工程卫生（2026-09-08）
- 清理 3 个 `.bak` 残留文件（index.json.bak / 5.md.bak / ForceGraph.vue.bak）

---

## 2. P1 工程化补强

### 2.1 MiniMax-M3 推理内容（`ϩ` thinking）适配（2026-09-07）
- 现象：M3 默认把思考以 `ϩ…ϩ` 标签裹进 `message.content`（chat 与流式增量均是）→ ① 前端当正文显示；② 会话持久化 + 多轮回填累积思考白烧 token
- 方案（候选 B + A 双保险）：请求统一带 `extra_body={"reasoning_split": True}`（思考拆到 reasoning_details）+ `_strip_think_tags` 剥离兜底
- 覆盖点：`call_llm`（判分/出题/图谱分析不受污染）、`agent_loop._chat_once`（`_assistant_snapshot` 回填 reasoning_details 保思维链连续）
- 前端零改动（reasoningSplit 已在后端剥离）；旧 `call_llm_stream` 于 2026-09-08 作为死代码删除

### 2.2 其余工程化项（除注明外均 2026-09-08 完成）
- **lifespan**：3 处 `on_event` 统一收敛为 `@asynccontextmanager lifespan`，DeprecationWarning 清零
- **聊天限流**：`RateLimiter` 泛化（`LoginRateLimiter` 保留别名）+ `chat_rate_limiter`（20 次/60 秒）覆盖 `/chat` 与 `/chat/stream`，超限 429 + `Retry-After`
- **结构化日志**：`RotatingFileHandler` 写 `logs/tutor.log`（10 MB 轮转 × 5 备份）+ 控制台双输出；`LOG_LEVEL` / `LOG_DIR` / `LOG_MAX_BYTES` / `LOG_BACKUP_COUNT` 可配
- **.github CI + 测试收敛**：workflow run 加 `-m "not llm_api"`；顺手修 `token_counter` 根因 bug（退化路径对空 content 强给 1 token，改 `... if content else 0`）
- **节点掌握度手动调整**：NodeDetail 加 0-100 range 滑块 → `PUT /knowledge/node/{id}/mastery`
- **图谱知识一键导出**：`GET /knowledge/export?subject=` 返回合并 Markdown（节点列表 + 依赖关系），`Content-Disposition: attachment`
- **节点内容 Markdown 分屏编辑**（2026-09-10）：NodeDetail 编辑模式改双栏（左源码 / 右实时预览），复用 `frontend/src/utils/markdown.js`，窄屏 <760px 自动堆叠；零新依赖、单文件改动
- **空图谱行动顺序（提示词层 MPV）**（2026-09-13）：图谱为空时注入 `chat_service.EMPTY_GRAPH_PROMPT` —— 先 `add_knowledge_node` 建图谱 → 再 `add_edge`/`update_mastery`；并禁止向学生断言"你的图谱是空的"（防模型个人数据幻觉）

---

## 3. 地基与核心
- create_node 全局 ID 冲突修复（`_node_id_exists_globally`）
- LLM 请求重试机制（`_with_retry` 指数退避+抖动，只重试瞬时错误）
- 前端 toast 替代 alert（`utils/feedback.js`，源码已 0 处 alert）
- CORS 配置化（`CORS_ALLOW_ORIGINS` env → config.py）
- Docker/Nginx 生产部署（Dockerfile + compose + healthcheck + .dockerignore）
- 全局异常处理器 + 标准化错误码（含 E-LLM-00x / E-QUIZ / E-COLL / E-WEB-FETCH）

## 4. 知识图谱与对话
- 图谱 CRUD + D3 力导向可视化 + 搜索跳转 + 右键菜单
- 科技树化：掌握度四色着色 + 图例 + 学习路径高亮 + 薄弱点脉冲（ForceGraph）
- 拓扑排序学习路径推荐（Kahn）+ 仪表盘联动聚焦
- 学习进度仪表盘（`/knowledge/stats?subject`：掌握 / 学习中 / 未学 / 时长）
- 学科切换（`nodes.subject` 字段 + 前端学科选择器 + stats by_subject）
- 两阶段流式对话（SSE 文本 + 后台 function calling）+ 四种教学模式 —— **2026-09-06 起已统一收敛为 `run_agent_loop` 单一 Agent Loop SSE（见 AGENTS.md §3.4）**
- 用户画像 v2（JSON 结构化 + usage_mode + get_completeness）
- 事件总线解耦图谱更新；JWT 认证 + 限流

### 4.1 后续增强（均已落地）
- 归属判定 `core/kg_taxonomy.py`（规则优先 + LLM 兜底，失败保持未分类）
- 先修推断 `core/prerequisite.py`（多准则投票，merged micro F1 0.941）
- 检索扩跳 `rag_manager.search(hops=)` → `_expand_prerequisites`（只反向补前置，A/B 实测命中 25%→62.5%）
- 掌握度四档唯一实现 `mastery_bucket()`；参照系契约 `docs/知识图谱_参照系契约.md`

### 4.2 知识图谱从未注入提示词 —— 影响面极大的静默 bug（2026-09-14 修复）
- 根因：`data/prompts/system_prompt_common.j2` 的占位符写成了**单花括号** `{knowledge_graph_summary}`
  / `{user_profile}`。Jinja2 只认 `{{ }}`，单括号是**字面文本**，渲染时原样输出；
  全库无任何 `.replace()` 兜底 → **adaptive / free_talk 模式下 AI 完全看不到知识图谱与学生画像**。
  （recursive 模式正常，它用的是 `{{ knowledge_graph_framework }}`。）
- **这是此前被误判为"模型个人数据幻觉"的真因** —— AI 说"你的图谱可能是空的"不是幻觉，
  它确实看不到图谱（修复后 57 个节点 id 命中 57）。
- 更坑的是 `tests/test_prompt_loader.py` 曾把这个 bug 当成"预期行为"锁死
  （断言占位符名字出现）→ 已改为**断言值被注入 + 字面占位符不残留 + 模板源码不含单花括号**三条守卫。
  **教训：断言"占位符名字出现"毫无意义。**
- 诊断脚本：`backend/scripts/probe_graph_prompt.py`（节点 id 命中数应为节点总数）
- 代价：注入后系统提示词 ~3k → **14.4k tokens**（57 节点 / 图谱摘要 22141 字符）；
  `LLM_CTX_BUDGET=32000` 下留给历史 ≈ 15.6k，待决策项见 `TODO.md`

## 5. RAG / 知识库
- 知识库目录树（递归多级 + 上传/删除/检索/上下文选择）
- 解析器注册表去耦合（text 30+ / pdf / docx / pptx / image-OCR / legacy / 电子书 epub+fb2，可选依赖降级）
- 混合检索：向量 + whoosh BM25 宽召回各 30 → RRF 融合（含 fuse 加权对照）
- chunk path 溯源 + 父级扩展（1500 字符预算 / 8 块上限）
- rag_pipeline 去耦合：RagSource 协议 + 路由（问候/过短跳过）+ 跨源融合 + 单源隔离（8s 超时 + `return_exceptions=True`）
- Agentic RAG：`rag_search` 工具（source graph/kb/all，hops 0~3）
- graph_generator：书籍自动生成图谱 + 语义去重合并
- 商用模式 L2 版权过滤（`allowed_node_ids` 白名单，零迁移）
- 嵌入唯一出口 `core/llm/embed.py::embed_texts`（`kb_manager._embed` / `rag_manager._embed` 收敛为薄委托）

## 6. AI 出题
- quiz 模块（schema / generator / grader / quality / store）+ API 5 端点
- QuizView 前端（配置 → 作答 → 客观题规则判分 / 简答 LLM 判分 → 解析反馈）

### 6.1 出题数量不足修复 —— 分批 + 补题 + 批次隔离（2026-09-14）
- 根因：思考型模型的 `max_tokens` **同时约束思考 + 正文** → 一次要 10 道题时 JSON 被硬截断，
  而抢救策略只能挖出已闭合的对象 → 请求 5 道只拿回 2 道。
- 修复：`QUIZ_BATCH_SIZE=4` 分批并发（`_split_counts` / `_types_for_batch`）+ 不足补题
  （`QUIZ_MAX_TOPUP_ROUNDS=2`）+ **批次隔离**（`asyncio.gather(return_exceptions=True)`，
  一批炸掉不再拖垮整次出题；`_generate_batch` 内部再自加一次重试）。
- 真机验证：请求 1 / 3 / 5 / 10 全部达标；`scripts/smoke_quiz_generate.py --top-k`
- 观测能力：`call_llm` 日志带 `finish=`（区分撞顶 / 模型自停）、过滤原因从 debug 提到 info
- 已知不可解：思考量随机波动 3~5 倍（1606~4916 token），撞顶与"模型自己停"两种失败都压不掉，
  靠重试兜住（~10~20% 单次失败率）；延迟 ≈ 11ms × completion_tokens

### 6.2 对话内出题 P0（2026-09-14）
- `quiz_generate` + `grade_answer` 两个 Agent 工具，把"即学即测"闭环接进对话
- `core/quiz/chat_quiz.py`：后台异步出题（单题固有延迟数秒~40s，不能同步等）→ 入库（`source="chat"`）
  → 推 `quiz_ready` 事件 → 前端追加题目消息 → 学生作答 → 规则判分 → **答对自动 +20 掌握度**
- 题目依据 = 刚学的图谱节点正文（`seed_materials`）+ KB 检索，解决"学生没上传教材就退化成通用常识出题"
- 支撑改造：工具层支持**协程 handler** + 单工具超时覆盖（`execute_kg_tool_async` / `tool_timeout_secs`）；
  `/knowledge/events` 改为**按用户订阅**（原来全局队列，收不到 per-user 事件且互相广播）
- 真机验证：`scripts/smoke_chat_quiz.py` 全链路通过（出题 4.3~6.2s，答对 0→20 / 答错 20→20）
- 同节点跨调用去重：`QuizStore.asked_questions(node_id)` → `generate_quiz(avoid_questions=…)`
  → 注入提示 + `filter_questions(avoid_texts=…)` 强制排除（判分已是掌握度主信号，不去重就能靠重答刷分）

### 6.3 掌握度更新：以出题判分为主信号（①，2026-09-14）
- **实测发现 `update_mastery` 从未被 AI 调用过**（12 次 agent 运行，0 次）；
  诊断脚本 `backend/scripts/probe_agent_runs.py --summary`
- 根因："当用户正确回答/理解后，适当调整 mastery"是**不可执行的软约束** ——
  苏格拉底教学里"学生在回答我"是每轮常态，模型分不清"答对一个小问题"与"掌握了知识点"
- 提示词改造（`chat_service.TOOL_CAPABILITY_PROMPT`）：新增「掌握度由谁更新」+「学生说懂了 → **出题，不要再追问**（铁律）」；
  `update_mastery` 收敛为**仅限 3 种硬证据**（说来就很熟→70 / 完全没学过→0 / 主动纠正→+10）；
  删除旧的"根据回复质量打分（0=未掌握, 1-25=入门…）"档位
- **关键教训**：第一版只写"学生表示理解 → 出题"**无效** —— 真机模拟"我完全搞懂了"，
  模型仍继续苏格拉底追问、不调工具。提成**铁律级** + 明确"❌ 错误做法：继续反问"后才生效
  → 改触发类提示词**必须写清"不要做什么"**，否则会被更强的既有原则盖过
- 验证手段：`scripts/smoke_chat_quiz.py --simulate "学生发言"`（真机跑一轮，打印实际工具调用序列）

## 7. 资源采集 Collector

### 7.1 Batch 1 — V1 最小闭环（B1.1-B1.8，2026-09-05）
- **B1.1 核心包骨架**：`collector/{__init__,types,store,http,subjects}.py` + `test_collector_{store,http,subjects}.py`
- **B1.2 SourceAdapter 注册表**：`adapters/{base,wikipedia,wikibooks}.py`；**维基真网联调通过**（`search("栈")` 10 候选；`fetch`「堆栈」→ MD 6677 字符 / 361 行，无残留 `{{}}`/`<ref>`/`Category`）
- **B1.3 manager**：并行发现候选 + mode 过滤 + 去重 / 断点续传（cursor + processed_count）/ 协作式取消（`asyncio.Event`）/ `_ingest` 复用 parsers + `kb.ensure_folder` + `upload_and_index` + content_hash 去重 —— `test_collector_manager.py` 44 passed
- **B1.4 采集 API**：`/collector/search|tasks|tasks/{id}|tasks/{id}/cancel|stats`（contract 对齐前端 `api/collector.js`）+ 错误码 E-COLL-001/002 —— 16 用例
- **B1.5 版权双模式设置**：`preferences.usage_mode`（FIELD_WEIGHTS 白名单 + 旧 JSON 自动补默认）
- **B1.6 离线种子**：`data/collector/subjects.json` + `seed/dsa/` 40 词条 + `scripts/seed_collector.py`（`--embed mock|api`，幂等跳过）
- **B1.7 前端**：`CollectorView.vue` + `api/collector.js` + SettingsView「资源与版权」双模式单选 + ActivityBar `resources` 入口
- **B1.8 商用过滤接入**：`RagContext.mode` + `kb_manager.allowed_node_ids()`（commercial 排除 `自动采集/L2` 子树，全库场景转显式白名单）+ `KbRagSource` 应用 + 对话侧注入 mode

### 7.2 Batch 2 — 切片整理（2026-09-05）
- **B2.1 OI-wiki 适配器**：`adapters/oiwiki.py` 按站点内置目录抓正文 → 清理 `=== "C++"` 代码页签 / `???+` admonition / 相对图片后入库；`test_oiwiki_adapter.py` 17 passed（真网联调仍待做，见 `TODO.md`）
- **B2.2 规则切章**：`collector/chapterizer.py`（`第X章` / `^\d+[.、]`，无目录兜底整段，不做 LLM 知识卡片）—— 13 passed
- **B2.3 入库文本下限**：`MIN_PARSE_TEXT_LEN = 200` + `IMAGE_EXTS`，解析文本 <200 字符拒绝入库 —— `test_low_text_reject.py` 3 用例
- **B2.4 `doc_chunks.embedding` 可空**：BM25-only 支持 + `search` 只对非空向量行算相似度 + 幂等迁移 `scripts/migrate_embedding_nullable.py` —— `test_bm25_only_index.py`
- **B2.5 切章入库管线**：`collector/pipeline_ingest.py::ingest_book_chapters`（`VECTORIZE_MIN_CHARS = CHUNK_SIZE(500)` 决定是否向量化，残章跳过，`_safe_name` 净化）—— 6 用例

### 7.3 只采开源来源（2026-09-23）
- 背景：五条「按 URL 取内容」的路径里只有采集模块有授权判定，**AI 联网三条（搜索存档 / `fetch_webpage` / `download_resource`）零判定** —— 非开源内容会被落成图谱节点或进 KB 被 `rag_search` 检索。
- 唯一判定出口 **`core/open_source.py`**（纯 stdlib 叶子模块，零跨包依赖，放 core 根以避开 `collector ↔ agent_tools` 包级环）：开放来源表 + 关闭来源表（盗版分发 / 付费墙 / 明确禁止）+ 页面许可声明识别（CC 链接、`rel=license`、CC0/公有领域、SPDX 风格文本）；出口**直接复用既有 `L0/L1/L2/L3`**（`types.py` 里 L3 本就是「版权不明」），不新造第二套分级；**fail-closed：判不出来一律 L3 不可采**。
- 三个接线点（各 2~6 行，全部加法）：
  - `collector/adapters/web_page.py`：来源表改为 import `open_source.SITE_LEVELS`（保留 `_SITE_LICENSE` 同名别名以兼容既有测试 patch）；**未登记站点默认 L2 → L3**；
  - `agent_tools/web_archive.py`：`fetch_and_archive` 按 URL 预筛（非开放来源**不发请求**）+ `archive_html` 按页面许可声明复核 → 一次覆盖「联网搜索存档」与「`fetch_webpage` 存档」，**`mcp_host.py` / `tools/fetch_webpage.py` 零改动**；
  - `agent_tools/tools/download_resource.py`：下载前 `is_open(url)`，非开放来源不下载不入库（fail-closed）。
- 严格度取舍：非开源网页**仍可只读查看**（`fetch_webpage` 返回文本逐字不变），只是**不留存**；要连"看"都拦需另改 `tools/fetch_webpage.py`（本次未做）。
- 总开关 `open_source.OPEN_SOURCE_ONLY=False` → 回到旧行为（刻意内联，**不新增 `config.py` 字段**、不动 `collector/types.py`）。
- **口径变更（非回归）**：`test_web_page_adapter.py` 5 处（L2→L3 / 未登记构造不出候选）、`test_collector_adapters_registry.py` 1 处（web_page 兜底等级 L2→L3）。
- 夹具适配：`test_web_archive.py` / `test_webpage_node_bridge.py` 加 autouse fixture、`test_download_tool.py` 扩 `_install()` —— 把夹具域名列入开放来源，否则 fail-closed 会先拦掉，测不到各自机制本身。
- 测试：`test_open_source.py` 25 例（判定）+ `test_open_source_paths.py` 6 例（接线）；离线全量 **859 passed, 7 deselected**（新增 31 例，既有 828 例全绿、无转红）。
- 方案 / 禁区 / 未做项（robots、留痕、搜索侧过滤）：`docs/采集与联网查阅_只采开源来源_计划.md`。

### 7.4 正文预览 + 维基正文修复（2026-09-23）
- **知识库正文预览**（用户："那些 md 看不到内容"）：`GET /kb/node/{node_id}/text` + `kb_manager.get_document_text`（读正文唯一出口）+ `KbTextPreviewDialog.vue`；`KbPanel` 文件节点「查看正文」按钮与**双击文件**两个入口。落点较原方案 §4 改为知识库侧直读（同样覆盖手动上传文件），详见 `docs/采集_网页正文Markdown预览_方案.md` §12。
- **`wikitext_to_md` 修「字有问题」**（真网《贪心算法》原文 `'''贪心算法'''（{{langx|en|greedy algorithm}}）` 被整段丢模板 → 入库成「贪心算法（）」）：内容型模板（`lang*` / `transl` / `nowrap` / `nobr` / `ipa` / `langue` / `rtl-lang`）改取**末个非空参数**，维护型（`{{Expand}}`/`{{NoteTA}}`…）仍整体丢弃；顺带清 `__NOTOC__` 等魔术字、HTML 注释/标签（`<code>` → 行内代码）、图片链接（`[[File:]]`/`[[文件:]]` 整条丢）、空列表项与游离 `[[`/`]]`。
- 真网前后对比（只读核对）：《贪心算法》`（）` → `（greedy algorithm）`；《树 (数据结构)》`（）` → `（tree）` 且 9471→8376 字（图片说明等噪声被清）；《数据结构与算法术语列表》`__NOTOC__` 消失。
- 测试：`test_wikipedia_adapter.py` 新增 4 例（内容型模板保留 / 维护型仍丢 / 魔术字与 HTML 残渣 / 图片链接整条丢）。
- **简繁统一**（用户确认「字有问题」指简繁混排）：源页面普遍简繁混写，MediaWiki **只在渲染时**按变体转换，`prop=revisions`（wikitext）路径绕过了它（实测 `action=parse&prop=wikitext&variant=zh-cn` 对正文无效）。方案：加依赖 **`zhconv`**（纯 Python 转换表，MIT），在 `wikitext_to_md` 出口统一为大陆简体（`_VARIANT_TARGET = "zh-cn"`，比 `zh-hans` 多一层地区词：軟體→软件、雷射→激光）；未装则原样返回不阻断采集。
  - 已排除的两条替代路径（实测）：`prop=extracts&explaintext&variant=zh-cn` 简繁完美且模板被服务端展开（「（英语：tree）」），但**丢列表符号、公式烂掉**（《快速排序》72 处 `{\displaystyle…}` 残骸）；渲染态 HTML 同理伤公式。
  - 真网验证（只读）：三页重转后 `zhconv.convert(md) == md`（**待转换字符 0**）；库内旧文本《贪心算法》有 **824** 字符需转换（正是截图里的混排量）、《树》21、《术语列表》3；公式 `$…$` 与标题层级（40/12/7 行）不受影响。
- 测试：`test_wikipedia_adapter.py` 共 17 例（简繁统一 / 公式不被转换 / zhconv 缺失降级 各 1 例）；离线全量 **874 passed, 7 deselected**。
- **正文外链可点**（用户问「网页里面的链接也能打开吧」）：实测库里正文 `](http` 计数为 **0** —— 维基外链 `[http://x 说明]` 原样入库只是方括号文字，网页存档走 trafilatura 也只留锚文本。修法两处：① `wikitext_to_md` 把 `[url 说明]` → `[说明](url)`（裸 URL、`[1]` 脚注不动，真网《计算机科学》20 / 《数据结构》4 / 《遗传算法》2 处全部转成链接、零残留）；② `KbTextPreviewDialog` 拦截正文里的 `<a>` 点击 → `window.open(..., '_blank', 'noopener,noreferrer')` —— 因为 **DOMPurify 默认放行 `href`/`rel` 但不放行 `target`**（实测当前版本），靠 `target=_blank` 无效；且不改共享的 `utils/markdown.js`（对话/图谱/画像三处共用）。全量 **875 passed**，`npm run build` 通过。
- **遗留**：已入库的 11 个采集文件仍是旧文本 —— 要看到修复效果需「删除 KB 文件 + 重置 `resources` 状态（indexed → pending）后重采」（`start_task` 对已 indexed 的 URL 会直接跳过，故不能在采集页重采；重采需重跑一次嵌入）。

## 8. 测试与评测
- 单元 + 集成 + collector 系列；**2026-09-13 离线集 580 passed, 7 deselected**（`-m "not llm_api"`）
- CMRC2018 离线评测集（mock：vector R@1 0.470 / bm25 0.964 / RRF 0.766 / fuse 0.818）
- whoosh 批量写入 38× 加速（180s → 4.7s）
- 向量检索维度校验（库内维度 ≠ 查询维度时跳过该行 + warning）—— `test_vector_store_dims.py` 2 例
- README 双语结构校验脚本：按要求**暂未实现**（规范 6.5 ④）

## 9. 项目 / 部署脚本 / 文档
- install.ps1 / start.ps1 一键安装启动；.env.example 模板；迁移脚本体系
- **README 门面同步 + 双语化 + 贡献指南**（2026-09-12）：① 数字/路径校准（测试口径 `533 passed, 7 deselected`，架构图 `llm_client.py` → `core/llm/`，目录树补 `agent_run_store.py` / `agent_tools.py`，功能表补"运行记录与证据回放""模型回退链"）；② `README.en.md` 英文镜像（`<!-- base -->` 注释，结构同构）；③ `CONTRIBUTING.md` + `CONTRIBUTING.en.md`；④ `docs/README_编写规范.md` 升 v3（新增多语言维护 / 贡献指南规范 / 检查清单）

## 10. 技术债已清偿
- `backend/test_data.py` 遗留失效脚本（2026-09-08 删除）
- 向量检索维度校验（2026-09-12，`test_vector_store_dims.py` 2 例）
- 维基地区词标记 `-{…}-` 未清理（2026-09-12，`wikitext_to_md` 增 `_strip_variant`）
- 两处 `_embed()` 合并统一门面（2026-09-12，`embed_texts` 唯一出口 + 部分返回整批作废，杜绝 chunk/向量错位）
- 商用过滤 node 白名单语义确认 → 落于 `kb_manager.allowed_node_ids()`
- 「usage_mode」存取方式确认 → 复用 `PATCH /profile`
