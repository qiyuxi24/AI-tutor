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
