# 项目记忆

> 只存**跨会话稳定的决策与约束**；细节一律指向代码 docstring 或 `docs/*.md`，避免双写失真。
> 整理：2026-09-12（第二次压缩：长调研段落收成结论 + 指向文档，旧日记蒸馏后删除）。

## 项目概述
- TutorAgent（原 AI-Tutor）：知识图谱驱动的自适应导学 Agent。前端 Vue3+Vite+D3+Pinia+EP；后端 FastAPI+SQLite+Jinja2+LLM(OpenAI 兼容)
- **模型**：对话/Agent 主模型 MiniMax-M3（api.minimaxi.com/v1）；嵌入固定阿里 text-embedding-v4（独立 key/base）
- **比赛材料冻结（2026-09-12 起）**：已归档到 `docs/比赛/`，暂停维护——不主动读取/更新/随代码同步，**仅用户点名才碰**。

## 硬约束（改代码前必看）
- **venv Python = `backend/venv/Scripts/python.exe`（3.13.9）必须用它**（系统 3.11 的 fastapi 过旧，导不进 chat_service）；`uvicorn` 必须 `--workers 1`（EventBus 用户队列 / 进程内定时 GC 依赖单进程）
- **`.env` 唯一真值 = 根目录 `.env`**（`config.py` 读 `parents[3]/.env`），模板唯一 `.env.example`；LLM 三段：`LLM_API_KEY`(空则回退 DASHSCOPE) / `LLM_BASE_URL`+`MODEL_NAME` / `DASHSCOPE_API_KEY`+`EMBED_BASE_URL`(钉阿里)
- **嵌入唯一出口 = `core/llm/embed.py::embed_texts`**；`kb_manager._embed` / `rag/manager._embed` 是薄委托，**方法名必须保留**（测试类级 `monkeypatch.setattr(KbManager,"_embed",…)`、`eval_rag.py` 实例属性遮蔽是既有遮罩缝）
- **新增 LLM 调用走 `core/llm/fallback.py::_chat_create`**（候选链+重试内建）；真打 LLM 仅 2 处：`agent_loop._chat_once`、`call_llm`
- **`nodes.id` 是全局 TEXT PRIMARY KEY**（非 per-user），生成必须查全局唯一（`_node_id_exists_globally`）
- **用户隔离**：图谱/画像/RAG/记录按 `user_id` 分区；`KnowledgeGraph(user_id)` 每次新建、用毕 `close()`，不跨协程共享
- **本仓库存在并发写入**（另一会话可能同时改同一文件）：报错与文件内容对不上时，先查 mtime/Get-FileHash 取同一快照再改

## 架构要点
- **对话 = Agent Loop**：`core/agent_loop.py::run_agent_loop`（纯编排 LLM↔KG_TOOLS 多轮，max_rounds=5/工具超时 60s/temp 0.3）；`call_llm` 仅纯文本
- **运行记录 = 唯一事实源**：`core/agent_run_store.py`（SQLite `agent_runs`，WAL）；传 user_id 自动落库；evidence JSON（200k 截断）；事件全带 **run_id**；分层保留 `prune(30,180)`；治理 API `/agent/runs`（**stats 路由须在 `{run_id}` 前**）/DELETE
- **模型回退链**：`_LLM_CANDIDATES` + `_should_fallback`（401/402/403/429/5xx/超时/断连触发；**400/404 不触发**）；降级只 warning，全败抛 RuntimeError
- **工具唯一注册表**：`core/agent_tools.py::_TOOL_SPECS`（8 工具），`KG_TOOLS`/`execute_kg_tool` 自动生成；重型实现放 `rag_tool.py`/`web_tool.py`
- **提示词组装点**：`chat_service._build_system_prompt()`；错误码 `core/error_codes.py`，异常 `[E-XXX]` 走 `log_error()`
- **主题切换**：`html.dark` + CSS 变量 + EP dark css-vars；`theme.js` 自实现 useDark；`isDark` 是 computed

## 知识图谱（核心资产）
- **存储**：SQLite（nodes/edges）+ 节点 MD（`data/knowledge/nodes/{user_id}/*.md`）；学科(subject)→板块(board) 两级，`graph_middleware.slice_graph` 按需切片；`SUBJECT_UNCLASSIFIED="未分类"`（合成分组，不入学科列表）
- **前端一次只渲染一个学科**（2026-09-12，防无限生长）：`GraphSubjectBar`+`GraphBoardSidebar` 由 HomeView `.graph-nav` 统一布局；未选学科时 `fetchGraph` **清空画布、绝不拉全量**，`ensureSubjectSelected()` 自动选第一个；`fetchSubjects()` 取 `/knowledge/stats` 的 `by_subject`（`subjects` 剔除「未分类」，`subjectSummaries` 带 `unclassified` 标记）；两级栏=一张圆角卡片+右缘抽屉把手（width→0）+拖拽调宽（140~340px，`localStorage.graphPanelWidth`）
- **参照系契约（唯一参照 = `docs/知识图谱_参照系契约.md`）**：图谱 = 每次 LLM 调用的参照系；三问判据 ①能讲什么(L1)②现在在哪(L2)③依据什么(L3)。**现状 L1 部分 / L2 半 / L3 未**，缺口 G1~G5。裁决链：运行时行为 > 契约 > AGENTS.md > 其他文档；**未满足 AC-L1-3、AC-L2-1 前不得宣称"参照系"，只能写"全量框架约束"**
- **结构调研（`docs/知识图谱_模块结构与封装调研.md`）**：读路径封装良好，**写路径是薄弱面**——写节点 4 套并行实现（`knowledge_writer` / `graph_generator._write_to_graph` / `knowledge.py::create_node` / `decompose`），**`board` 只有 generator 写 → 其他路径建的节点永不进板块栏**；另 5 处绕过 `KnowledgeGraph` 直读写节点 MD。P1 待修：`KnowledgeView.vue` 绕过 store、SSE `/knowledge/events` 漏传 user_id、边字段三套命名
- **图谱生成**：`kb/graph_generator.py`；复用 tags 建模学科；嵌入粗筛(0.78)+LLM 二次确认+降级(0.90) 去重
- **论文/开源调研（`docs/知识图谱_论文与开源实现调研.md`）**：三用法 A 检索索引(GraphRAG 系) / **B 教学参照系(= 本项目)** / C Agent 记忆(Zep/Mem0)；GraphRAG 不能直接搬；**最大缺口=先修关系无量化** → 对策 arXiv:2509.05393（无监督 10 准则投票）+ MOOCCubeX 评测集；掌握度 ↔ 知识追踪（pyKT/NTKT，不采纳 DKT/AKT）；抑幻觉引 NAACL 2024
- **P0 已落地（2026-09-12，详见 `docs/知识图谱_P0实现方案与核心思路.md`）**：① 按学科导出改用 `kg.node_subject()`；顺手修掉中文学科名导致 `Content-Disposition` latin-1 崩溃→500（改 RFC 5987）。② 掌握度分档收敛为**后端唯一真值** `graph_middleware.mastery_bucket()`（`MASTERY_WEAK=30`/`MASTERY_MASTERED=70`，四档；`weak_count` 新增，`weak_points` 口径改 0~29；前端四档补齐并互相注释指向）。③ **先修推断 = `core/prerequisite.py`**：六准则加权投票（权重见代码，C1 正文引用最高 3.5），**分母固定 `TOTAL_WEIGHT=11.5`，只需 threshold 一个旋钮（默认 0.30）**，缺失信号弃权；结构约束=跨跳过滤+保 DAG+入度≤5；写库唯一入口 `apply_candidates`；接口 `POST /knowledge/prerequisite/infer`（默认 dry-run 出证据）。④ 评测 `scripts/eval_prerequisite.py` + `eval_data/prereq_sample.json`：三档口径 baseline/added/merged + 阈值扫描（merged micro 0.889/1.000；阈值 0.2~0.4 平台期）。**未做**：接入 `graph_generator` 生成链路、MOOCCubeX 实数据、前端复核 UI
- **多资料（多教材）维护调研（`docs/知识图谱_多资料综合维护调研.md`，仅调研未实现）**：五件事 `溯源→对齐→补全→冲突→增量`，**溯源是前提**；落地顺序 L0 溯源(`sources`: doc_id/chunk_id/span) → L1 跨源对齐(+`merge_decisions` 可回滚) → L2 冲突队列(复用 prerequisite 多准则骨架) → L3 按资料增量 → L4 覆盖度；对口参考 arXiv:2305.04567（**该文无 P/R/F1，别照抄其评测**）、KARMA 2502.06472、LightRAG 2410.05779。**前提：先收敛 4 套写路径**；`update_node_content` 覆盖语义与"合并"语义冲突

## RAG 体系（四层）
- 四层：图谱 rag(`core/rag/`) + 上传 kb(`core/kb/`) + 混合检索 `hybrid_search/` + 对话注入
- **rag_pipeline 去耦合**：RagSource 协议 + Graph/Kb 两 Source + 纯规则 router（问候/过短跳过）+ 跨源融合；**gather 必须 `return_exceptions=True`**；单源 8s 超时/异常隔离，失败静默返空
- **kb 混合检索**：向量 + whoosh BM25 → 宽召回各 30 → RRF → top_k=5；chunk 带 path 溯源 + 父级扩展（1500 字符 / 8 块）。**已知坑：mock 弱向量下 RRF 拖累 Recall@1（0.766 vs 加权 0.818 vs BM25 0.964），真实 embedding 待重跑**
- **parsers 解析包**：text(30+) / pdf(PyMuPDF+扫描 OCR 回退) / docx / pptx / image-ocr(RapidOCR) / legacy；可选依赖探测降级；whoosh 无中文分析器 → 自定义 `RegexTokenizer(连续CJK)`+`NgramFilter(2,2)`+英文 StemmingAnalyzer（**不引 jieba**）；批量 `upsert_bulk`+`begin_deferred/flush`（256 文档 180s→4.7s）
- **向量维度保护**：两个 VectorStore.search 维度不符跳过+warning（不抛 500），BM25 兜底
- **Agentic RAG**：`rag_search` 工具（source graph|kb|all，top_k 1~5）；`_run_async` 解决同步里跑 async；**图谱 RAG 未接双检索**（whoosh `node_id` NUMERIC 与图谱 TEXT id 冲突，暂缓）

## 用户画像（`core/profile/` 分层包，2026-09-12 拆分）
- `schema.py`(结构 / `FIELD_WEIGHTS` 兼白名单 / `merge_patch`) + `store.py`(`ProfileStore` **原子写 `os.replace`** + 旧 MD 迁移) + `markdown.py`(渲染 + 旧版解析) + `manager.py`(`UserProfile` 门面)；数据 `data/profiles/{user_id}.json`（旧 MD → `.md.bak`）
- **外部只 import `UserProfile` 与 `get_usage_mode(user_id)`**（usage_mode **唯一入口**，异常回退 personal；rag_tool / chat_service 均走它）；API GET/PUT/PATCH `/profile` + POST/DELETE `/profile/notes/{id}`，**PATCH 走 `model_dump(exclude_unset=True)`，只覆盖显式提交字段**
- `get_summary()` 空画像返 `""`（不注入空模板）；`get()` 纯读不落盘；`get_completeness`（weight=0 的 usage_mode 不计入）；`update_user_profile` 工具即 `add_note`

## 出题 / 采集
- **quiz**（借鉴 OpenMAIC）：`core/quiz/`（schema/generator/grader/quality/quiz_store）+ API 5 端点；依据=教材 KB 混合检索；错误码 E-QUIZ-001~005
- `collector/quiz_splitter.py`（试卷整卷→逐题，纯函数零 LLM）已可用 + 9 测试，但**零引用、未接入上传链路**（债见 `TODO_Collector.md` #2，入口形态三选一待定）

## 测试 / 环境
- 三层：单元(mock) → 集成(`test_integration_kb.py`) → 离线评测(`scripts/eval_rag.py --embed mock|api`)；`pytest -m "not llm_api"` = **533 passed, 7 deselected（收集 540）**（2026-09-12 实测）
- 知识库栈：PyMuPDF / python-docx / python-pptx / RapidOCR + numpy + whoosh(BM25) + python-multipart

## 文档规范（README / 贡献指南）
- **README 规范 v3 = `docs/README_编写规范.md`（唯一依据，写前先读）**：13 章门面模板 + 禁放清单 + 第 5 章评审证据场景 + **第 6 章多语言维护**（主语言 SSOT=中文；镜像 `README.en.md`/`CONTRIBUTING.en.md`；切换器紧跟 H1 且语言名用母语；可执行代码块/URL/徽章语法不译，架构图与目录树要译图中文字；镜像顶部 `<!-- base: README.md @ <commit> (日期) -->`；两侧章节数/表格行数/代码块必须同构）+ 第 7 章贡献指南模板 + 第 8 章检查清单
- 门面文档成对维护：`README.md`↔`README.en.md`、`CONTRIBUTING.md`↔`CONTRIBUTING.en.md`（2026-09-12 实测同构：README 11 H2/10 代码块/47 表格行；CONTRIBUTING 12/14/51）
- CI 双语文档结构校验脚本：规范 6.5 记为**可选加固，未实现**

## 用户偏好与规范
- 喜欢渐进式改进，创建清单后逐项推进；重视代码安全与架构一致性；倾向复用成熟组件；**纯整理/审计类任务不擅自 commit**（要他明确说）
- **ponytail skill = 写代码必加载**（`.codebuddy/skills/ponytail/`）。触发看**产出物**：产出代码必须第一步 use_skill；**写文档/讨论/翻译/问答豁免**。核心=七阶决策阶梯（YAGNI→复用已有→标准库→原生→已装依赖→一行→最少代码）。子 skill：`-review`/`-audit`/`-debt`/`-help`
- **Git 纪律**：不主动 commit/push；确需提交时按**文件族**拆（不按时间/主题跨文件拆，否则中间态 import 失败）

## 早期演进（已归档，仅追溯历史时看）
- 2025-06 ~ 2026-07：画像 v1(MD)→现 `core/profile/`；边 CRUD 由索引改 DB ID；图谱 Obsidian 极简视觉；语义关系四层验证 + AI 权限守卫 `_guard_human_content`/`_guard_human_edge`（caller="ai"，以 `knowledge_graph.py` 为准）；学习路径推荐四函数（`topological_sort` 等）；模式曾并为 adaptive+free_talk；`install.ps1`；README 重写(commit 3cd89a4)

## 文档索引（细节在此，勿在本文件重复）
| 主题 | 文档 |
|---|---|
| 参赛材料（**已冻结，不主动读取**） | `docs/比赛/`（总纲 COMPETITION.md · 规格与策略 · BP v1/v2 · OPC 专项 · PPT 脚本 · `bp_v2/` 分章 · `官方材料/` 不入库） |
| README / 贡献指南 | `docs/README_编写规范.md`(v3) · `README.md`/`README.en.md` · `CONTRIBUTING.md`/`CONTRIBUTING.en.md` |
| 图谱角色契约（唯一参照） | `docs/知识图谱_参照系契约.md` |
| 图谱结构 / 论文调研 | `docs/知识图谱_模块结构与封装调研.md`、`docs/知识图谱_论文与开源实现调研.md` |
| 图谱 P0 实现（技术+思路+踩坑） | `docs/知识图谱_P0实现方案与核心思路.md` |
| 多资料（多教材）综合维护调研 | `docs/知识图谱_多资料综合维护调研.md` |
| Agent Loop / RAG 调研 | `docs/AgentLoop_*.md`、`docs/RAG_*.md` |
| 同类项目 / 教育开源 | `docs/参考资料_同类Agent项目对标与学习路线.md`、`docs/教育类开源AI调研报告_家教教师教辅方向.md` |
| 出题 / 仪表盘 / 采集 / 部署 | `docs/QUIZ_出题逻辑调研.md`、`docs/OPENMAIC_借鉴方案.md`、`docs/学习进度仪表盘设计.md`、`docs/教育资料采集模块_设计讨论.md`、`docs/Docker_学习路径与工程化部署.md` |
| 技术报告 / 简历 | `docs/国创技术报告.md`、`docs/简历强化_改进方向与验收标准.md` |
| 开发索引（AI Agent 用） | 根 `AGENTS.md` |
