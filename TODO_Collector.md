# TutorAgent 教育资源采集模块（Collector）— 实现 TODO

> 设计依据：docs/教育资料采集模块_设计讨论.md（决策 #1-25 + §11 里程碑）
> 创建：2026-09-05
> 铁律（用户）：**去耦合、独立测试（尽量）、复用已有组件**
> 标记约定：
> - `[需API]` 必须真实网络/外部服务才能验收（DASHSCOPE key / 维基联网），其余一律 mock 可测
> - `[新文件]` / `[改]` / `[复用]`
> - 每项含「测试」落点；标注的验收均指本地 mock 或离线可跑

---

## Batch 1 — V1 最小闭环（L0 知识主体：Wikipedia/Wikibooks）

> 目标：选学科 → 候选 → 勾选 → 下载 → 入库 → 对话引用 → 状态可查，3 步内弱网演示可走通。
> 首科：数据结构与算法（决策 #24）。次科：线性代数 / K12 数学（公式少的先上）。

### B1.1 采集核心包骨架 [新文件] `backend/app/core/collector/`
- [x] `__init__.py`：导出模块符号 + `collector_manager` 单例（B1.3 manager.py 已建立并导出，占位已补全）
- [x] `types.py`：`CollectCandidate`（title/source_url/license_level/description/size_bytes/meta）、`CollectTask`（id/user_id/subject/status/cursor/processed_count/cancel_event/is_cancelled/source/created_at）
- [x] `store.py`：采集记录表（每用户 `data/collector/{user_id}/collector.db`，仿 quiz_store 单文件 SQLite）
  - 表 `resources`：`source_url UNIQUE / content_hash / license_level / subject / mode / status / file_node_id / quiz_id / times_referenced / fetched_at`
  - 表 `tasks`：`id / user_id / subject / source / status / cursor TEXT / processed_count / total_count / error / created_at / finished_at`
  - 测试：`backend/tests/test_collector_store.py`（增删查、UNIQUE 去重、cursor 读写）
- [x] `http.py`：统一采集 HTTP 客户端 —— **User-Agent 必设** + `asyncio.Semaphore` 限并发（决策 #25），超时/重试各一；**不引 aiolimiter**
  - 测试：`test_collector_http.py`（mock httpx：UA 头存在、并发上限生效、超时降级返回空）
- [x] `subjects.py`：加载 `data/collector/subjects.json`（学段树 + 种子词 + 锚点 + resource 配比画像），提供 `find_subject()` / `seed_queries(subject)`
  - 测试：`test_collector_subjects.py`（JSON 结构校验、字段缺失兜底）

### B1.2 SourceAdapter 注册表 [新文件] `backend/app/core/collector/adapters/`（仿 `kb/parsers`）
- [x] `base.py`：`BaseAdapter`（name/license_level）`search(query)->list[Candidate]`、`fetch(candidate)->bytes|str`；`Registry` 注册表按名路由
  - 测试：`test_collector_adapters_registry.py`（注册/同名覆盖/注销/类型校验/内置注册/L0）
- [x] `wikipedia.py` [新文件]：MediaWiki API（zh.wikipedia.org）：`search` 按种子词搜 + 可选 `categorymembers` 递归发现；`fetch` 拉条目纯文本转 MD（**LaTeX 公式占位保留，不强行转**）
  - 测试：mock API 响应（`test_wikipedia_adapter.py`：分类递归翻页 continue 游标、UA、空结果、fetch 转 MD）；`[需API]` 真网全链路联调已验（见 Batch 1 待 API 验证清单 ①）
- [x] `wikibooks.py` [新文件]：同栈复用 wikipedia adapter 逻辑（换 host + project），URL 换参即可
  - 测试：mock 响应复用（`test_wikibooks_adapter.py`）
- [x] `__init__.py`：注册内置 adapter

### B1.3 采集服务/任务编排 [新文件] `backend/app/core/collector/manager.py`
- [x] `search(subject, mode)`：遍历适配器并行发现候选 → 按 mode 过滤 license_level → 去重（source_url）→ 补全 candidate.source
- [x] `start_task(user, subject, mode, candidates)`：建任务 + **断点续传**：写入 `store`，循环内每处理完一个候选更新 `cursor + processed_count`（决策 #21）；重启 `resume_pending()` 扫描未完成任务从 cursor 续跑
- [x] **协作式取消**（决策 #21）：任务持 `asyncio.Event`（manager `_events`）；执行循环每轮边界检查取消，收尾标记 cancelled；对外暴露 `cancel(task_id)`
- [x] `_ingest(candidate)`：fetch → **复用 parsers 解析**（parse_document，注册表已含 md/txt）→ `kb.ensure_folder(["自动采集", f"L{level}", subject])` + `upload_and_index()` 入库（目录自动建）；**入库前 content_hash 去重**（命中 → duplicate 状态，省 embedding，决策 #19）
- [x] 商用模式过滤辅助（决策 #23）：**已在 B1.8 落于 `kb_manager.allowed_node_ids()`**（含 test_pipeline_commercial_filter.py 单测），对话检索侧直接经 user_profile mode 注入，manager 无需复制/委托（无调用方 = 不写）
- [x] 测试：`backend/tests/test_collector_manager.py`（mock adapters + fake embed + 临时目录全离线）：搜索过滤/去重/单源异常隔离、任务建/自动完成、run_task 入「自动采集/L0/{学科}」目录断言、hash 去重（同内容不同 URL 只入一份）、单候选失败隔离、取消（运行前/运行中）、resume_pending 续跑 —— **44 passed 全绿**

### B1.4 采集 API [新文件] `backend/app/api/v1/collector.py`
- [x] `POST /collector/search`（subject/mode → 候选列表；manager.search 注入 Depends(get_manager)，测试可 override）
- [x] `POST /collector/tasks`（建任务，商用模式 L2 兜底 422）；`GET /collector/tasks/{id}`（进度含 cursor/total）；`POST /collector/tasks/{id}/cancel`
- [x] `GET /collector/stats`（覆盖率：学段→学科 已采/缺口，§4.3；**契约对齐前端 api/collector.js**：coverage 为数组、task status pending/processing 翻译 queued/running、subject 级 collected 份数 + boards_total/boards_collected 恒 0 待 B1.6 板块数据）
- [x] `[改] backend/app/main.py`：`include_router(collector_router, prefix="/api/v1")`（复用现有模式，一行）
- [x] 测试：`tests/test_api_collector.py`（16 用例，fastapi TestClient + mock manager + 临时 store，全部离线）
- [x] `[改] backend/app/core/error_codes.py`：新增 E-COLL-001 任务不存在 / E-COLL-002 任务创建失败（去掉了 manager 不会上抛的 search 错误码）

### B1.5 版权双模式：设置存取 [改]
- [x] `[改] backend/app/core/user_profile.py`：`FIELD_WEIGHTS` 与 `default_profile` 的 `preferences` 增加 `usage_mode: str = "personal"`（**坑：update_field 受白名单限制，不加进 weights 会更新失败**）
- [x] `[改] backend/app/models/schemas.py`：`ProfilePreferences` 加 `usage_mode`
- [x] `[改] backend/app/core/user_profile.py` 迁移：读旧 JSON 无该字段自动补默认，零破坏
- [x] 测试：`test_user_profile_usage_mode.py`（set/get/默认值/白名单更新）

### B1.6 离线种子数据 [新文件] `backend/data/collector/seed/` ✅ 2026-09-05
- [x] 数据结构与算法：预置 30-50 个 L0 词条 Markdown（维基摘要级，手工/一次性脚本生成后入库）→ `backend/data/collector/seed/dsa/`（40 词条，每篇含定义/核心概念/复杂度/示例/参考来源，与 `subjects.py` 同数据根）
- [x] `backend/data/collector/subjects.json`：首期两学科（数据结构与算法 + 线性代数）种子词/锚点/配比画像；schema 对齐 B1.1 subjects.py 加载器 —— `{"version","stages":[{"id","name","subjects":[{"id","name","seeds":[...],"anchors":[{type:wiki_category|wiki_page,title,url}],"profile":{knowledge,quiz}}]}]}`（线性代数暂无 seed 目录，脚本自动跳过不报错）
- [x] 弱网演示脚本：`backend/scripts/seed_collector.py`（一键把 seed 灌入 KB，离线可跑，勿需网络；`--embed mock` 哈希嵌入零网络、`--embed api` 可选，`--user`/`--subject` 可选，同名词条幂等跳过；目录 `自动采集/L0/{学科}/`）

### B1.7 前端：资源采集页 [新文件] ✅ 2026-09-05
- [x] `frontend/src/api/collector.js`（仿 kb.js 风格：search/createTask/getTask/cancelTask/getStats）
- [x] `frontend/src/views/CollectorView.vue`：学科选择 → 候选列表（license 徽标/预览/勾选）→ 采集进度（轮询 GET task，含取消按钮）→ 覆盖率概览
- [x] `[改] frontend/src/views/HomeView.vue` + `components/ActivityBar.vue`：新增 `resources` 入口与 viewMode 分支（照抄 quiz 接入方式）
- [x] `[改] frontend/src/views/SettingsView.vue`：「资源与版权」分组，双模式单选 → 调 profile 接口存 `preferences.usage_mode`
- [x] 样式：沿用现有 CSS 变量体系（scoped），不引入新 UI 库

### B1.8 商用模式检索过滤接入中间件 [改]（决策 #23）✅ 2026-09-05
- [x] `[改] backend/app/core/rag_pipeline/types.py`：`RagContext` 加 `mode: str = "personal"`（默认 personal，老调用点零改动）
- [x] `[改] backend/app/core/kb/kb_manager.py`：新增 `allowed_node_ids(user_id, mode)` —— commercial 时把 `自动采集/L2` 整棵子树排除出文件白名单（全库场景也转显式白名单，杜绝 None=全库漏过滤）；无 L2 内容返回 None 保持原路径零开销。**B1.3 的 collector manager 落地时直接委托本方法，不重复实现**
- [x] `[改] backend/app/core/rag_pipeline/sources.py`：`KbRagSource.retrieve` 在 commercial 时应用 `allowed_node_ids()`（白名单为空 → 返回空，不触发 search）
- [x] `[改] backend/app/services/chat_service.py` + `core/llm_client.py`（`rag_search` 工具）：构造 RagContext 注入 mode（读 `UserProfile.get_usage_mode()`，缺省 personal —— B1.5 前画像无该字段也零破坏）
- [x] 测试：`test_pipeline_commercial_filter.py`（personal 不受影响 / commercial 目录范围与全库均不含 L2 / 纯 L2 范围返回空不检索 / allowed_node_ids 单测）+ `test_collector_manager.py::test_commercial_whitelist_excludes_ingested_l2`（B1.3×B1.8 目录命名契约：manager 入库的 L0 在白名单、L2 被排除）；203 passed 全绿

**Batch 1 验收（离线优先）**：seed 灌库 → 对话引用 → 3 步演示；取消/断点续传 mock 测试过；商用过滤单测过。`[需API]` 仅联调 1-2 条。

**Batch 1 待 API 验证清单**（2026-09-05 加；① 已验，② 依赖阿里 embedding 额度，③ 主模型已切 MiniMax-M3 不再卡聊天额度）
- [x] **B1.2 维基真网联调**：真网实测通过 —— `search("栈")` 返回 10 候选（标题/URL/snippet 正常），`fetch` 首条「堆栈」转 MD 6677 字符 / 361 行，无残留 `{{}}`/`<ref>`/`Category`。**已知小瑕疵（未修）**：wikitext_to_md 未清理 MediaWiki 地区词转换标记 `-{zh-cn:堆叠; zh-tw:堆棧;}-`（堆栈页可见）→ 已登记「遗留技术债」#1
- [ ] **B1.6 真实嵌入灌库**：`venv/Scripts/python.exe scripts/seed_collector.py --user <演示账号> --embed api`（阿里 text-embedding-v4 需非欠费），40 词条入库后 `stats` 正常，替换 mock 哈希向量（真向量召回质量是检索指标前提）
- [ ] **Batch 1 端到端对话引用**：灌库后对话中挂载「自动采集」目录提问 DSA 概念 → 回答带 KB 引用（先完成② 灌库，对话侧 MiniMax-M3 已可用）

---

## Batch 2 — 切片整理（OI-wiki + 整书规则切章 + BM25-only 支持）

### B2.1 OI-wiki 适配器 [新文件] `adapters/oiwiki.py`
- [x] 按站点内置目录清单（`/ds`、`/math` 等板块，源=mkdocs.yml 导航，含中文标题）抓正文 → 转 MD 入库（正文取仓库 raw .md，天然 MD，仅清理 `=== "C++"` 代码页签 / `???+` admonition / 相对图片后入库）
- [x] 测试 mock：`test_oiwiki_adapter.py`（导航分板块发现/多词学科并集/单页命中/无关词空/断网空/fetch 清理断言；注册表内置断言更新为 3 源）；17 passed 全绿
- [ ] **[需API] 真网联调**：`search("数据结构与算法")` 应返回 OI-wiki 数据结构/算法基础板块候选，fetch 首页正文入库无 MkDocs 残留语法（2026-09-05 已转 TODO.md P0 待议，暂缓）

### B2.2 整书/长文规则切章 [新文件] `backend/app/core/collector/chapterizer.py` ✅ 2026-09-05
- [x] 输入解析后长文本 + 目录锚点 → 按章节标题规则（`第X章`/`^\d+[.、]`）切块为独立文档
- [x] **不做 LLM 知识卡片**（决策 #22）：仅切章保留原文切片，总结由对话期 RAG 动态生成
- [x] 测试：`test_chapterizer.py`（中文多级编号、无目录兜底整段、越界防护）—— 13 passed 全绿

### B2.3 入库文本质量下限 [改] ✅ 2026-09-05
- [x] `[改] backend/app/core/kb/kb_manager.py`：模块级 `MIN_PARSE_TEXT_LEN = 200` + `IMAGE_EXTS` 常量；`upload_and_index` 解析文本 `< 200` 字符 → `logger.warning`「图片型/不可解析」+ `ValueError` 不入库（放公共入库入口，覆盖 PDF/图片/md 全格式，审查⑥边缘增强；图片分支单独报「未识别到足够文字」）
- [x] 测试：`backend/tests/test_low_text_reject.py`（3 用例：短文本拒绝且零落库 / 199 拒·200 收边界 / 空文档回归）
- [x] `[改] backend/tests/test_collector_manager.py`：mock 正文加长 >200（原 195 字符低于新下限被拒，仅测试数据适配，非逻辑回归）

### B2.4 doc_chunks.embedding 可空（BM25-only）[改]
- [x] `[改] backend/app/core/kb/doc_vector_store.py`：`embedding` 列改 NULL 允许；`search` 只对非空向量行算相似度
- [x] `[改] kb_manager._index_document`：支持 `vectorize=False`（短文/题目只写 whoosh，不 embedding）
- [x] 迁移：`backend/scripts/migrate_embedding_nullable.py`（幂等 ALTER，不破坏现网数据）
- [x] 测试：`test_bm25_only_index.py`（短文档只进 whoosh、向量检索不报错、混合检索仍工作）

### B2.5 切章入库管线 [新文件] `collector/pipeline_ingest.py` ✅ 2026-09-05
- [x] 采集大文件 → chapterizer 切章 → 逐章 `upload_and_index(vectorize=True 或 False 按长度)` → 目录层级 `自动采集/L0/{subject}/{书名}/{章节}`
  - `ingest_book_chapters(user_id, subject, book_title, text, toc=(), license_level="L0", kb=)`：无章节边界整书退化单文档入 `{学科}` 目录（对齐 B1.3 路径）；有章则建 `{书名}` 子目录逐章入库；vectorize 按 `VECTORIZE_MIN_CHARS = CHUNK_SIZE(500)` 判定；残章（< MIN_PARSE_TEXT_LEN=200）跳过不落库；文件名 `_safe_name` 净化 + 冲突加序号
- [x] 测试：`test_pipeline_ingest.py`（6 用例：逐章入书目录+长章向量化/短章 BM25-only、混合检索命中短章+纯向量不含短章、文件名净化、无边界退化单文档、超短/空安全、toc 精确切分）—— **238 passed 全绿**（9/5 实测）

**Batch 2 验收**：PDF 教材 → 切章 → 分章入库可检索；短文不向量化但 BM25 命中。

---

## Batch 3 — 题库导入 + web_page 漏斗 + 来源统计

### B3.1 结构化题库导入 [新文件] `adapters/dataset_quiz.py`
- [ ] 读取开放数据集 JSON（如 CMMLU）→ 映射 `Question` schema → `quiz_store.save_questions`（复用）
- [ ] subject/knowledge_point 来自数据集字段，填入库（`save_questions(subject=...)`）
- [ ] 测试：`test_dataset_quiz_adapter.py`（样例 JSON 映射、非法行跳过）
- [ ] **预研项：存储配额/TTL**（审查⑧，入库批量导入**之前**必须完成，防百万题撑爆磁盘）：`storage_quota` 配置 + 超限告警

### B3.2 通用网页正文抓取 [新文件] `adapters/web_page.py`
- [ ] 站点白名单（个人模式）+ `trafilatura` 主用 / `readability-lxml` 后备（决策 #5），双失败即弃
- [ ] `httpx` 下载 → 正文抽取 → 转 MD 入库（license 由站点映射表给定，默认 L2）
- [ ] 测试：`test_web_page_adapter.py`（mock HTML：抽取成功/失败回退/超时）

### B3.3 来源质量统计 [改]
- [ ] RAG 命中时 `times_referenced += 1`（KbRagSource.retrieve 命中即累计，异步落库，不阻塞）
- [ ] `GET /collector/stats/sources`：按来源统计引用次数排行
- [ ] 测试：`test_source_stats.py`

**Batch 3 验收**：开放题集灌入 quiz 可练；网页抓取 2-3 站可入库；来源排行可查。

---

## Batch 4 — 登录站 + CollectorRagSource + 本地模型（v1.5+）

### B4.1 Cookie 会话复用 [新文件] `collector/session.py`
- [ ] 浏览器辅助登录（复用 agent-browser/playwright）：弹窗登录一次 → Cookie 存用户级目录（隔离/不进日志）
- [ ] httpx 携带 Cookie 抓取；合法需登录站标注 L2；zlib 类不提供适配器（决策 #13）
- [ ] 测试：mock cookie 注入 + 会话请求

### B4.2 CollectorRagSource 评估 [改]
- [ ] `[改] rag_pipeline/sources.py` 新增 `CollectorRagSource`（name="collector"）+ `pipeline.register()` 一行
- [ ] 评估是否入对话引用（延迟/质量），默认仅在采集页使用（§6.8/#18）
- [ ] 测试：`test_collector_rag_source.py`

### B4.3 本地 embedding 正式启用 [改]
- [ ] 安装 sentence-transformers（可选依赖，`requirements-optional.txt` 或注释指引）
- [ ] `[改] app/core/llm/embed.py`（2026-09-12 起嵌入唯一出口，kb_manager/rag_manager 的 `_embed` 已收敛为薄委托）：加 `EMBED_MODE=local|api|auto`，auto 降级链 API→本地→hash（复用 `kb/embedder.py` 的 `get_embedder`）
- [ ] 大规模入库批量 embedding 性能验证；成瓶颈再评估 worker 进程（决策 #20）
- [ ] 测试：本地未装时优雅回退已有测试覆盖，补 `test_embed_mode_config.py`

---

## v2 — 高级（暂不排期）

- [ ] 权威目录按需查询（§6.4）：K12 教材章节/考核类大纲实时查官方源（静态目录先覆盖主干科目）
- [ ] 整书 LLM 智能切章（需真实 LLM key，`[需API]`）
- [ ] 全学科目录数据批量内置（14 门类 + 117 学科种子词自动化生成）
- [ ] 采集覆盖率前端雷达图细化（学段→学科→板块 drill-down）

---

## 里程碑与外部依赖（汇总）

| 批次 | 主要外部依赖 | 风险 |
|------|------------|------|
| B1 | Wikipedia API（联网）/ DASHSCOPE embedding（入库默认走 API） | 维基 UA/限速已内置处理；embedding 可在 .env 配 mock 降级 |
| B2 | 无（全部离线可测） | — |
| B3 | trafilatura/readability-lxml（新增 py 依赖）、开放数据集下载 | 数据集网络下载一次缓存本地 |
| B4 | playwright/agent-browser、sentence-transformers+torch | torch 体积大，仅可选启用 |

## 遗留技术债（2026-09-08 盘点，登记未修的已知小项）
- [x] **#1 维基地区词转换标记未清理**（2026-09-12 已修）：`wikitext_to_md` 新增 `_strip_variant()` —— `-{zh-cn:堆叠; zh-tw:堆棧;}-` 保留首选变体（`堆叠`）、`-{H|…}-` 转换规则定义整段丢弃、`-{A|B}-` 取前者（不切坏 `[[链接|文字]]`）；嵌套按 `while` 循环剥净。测试：`test_wikipedia_adapter.py::test_wikitext_variant_markup_stripped` 1 例。

- [ ] **#2 试卷拆分器 `quiz_splitter.py` 已就绪但零引用（未接入任何链路）**（2026-09-12 发现）：`collector/quiz_splitter.py`（整卷非结构化文本 → 逐题结构：题号/大题/选项/答案回填/解析，纯函数零 LLM）已可用且有 `tests/test_quiz_splitter.py` 9 例覆盖，但**全库零调用**（只在自身 `__main__` 自检里跑），属"能用但没人用"的库。**待决策（入口形态三选一）**：① `/kb/upload` 命中试卷形态后自动拆分；② 独立 `POST /quiz/import`（上传整卷 → 拆分 → 人工校对 → 入库）；③ 与 B3.1 `dataset_quiz` 合并做（同属"题库导入"，但形态不同：B3.1=结构化 JSON，本项=非结构化试卷文本，不要混在一支适配器里）。**接入时必须注意**：`quiz_store.save_questions` 收 `Question` **对象**而非 dict（须写 `Question(**q.to_question_dict())`）；DB `id` 自增，拆分产物的 `q1/q2` 重号入库无害。

## 已解决（原「待确认」，实现中定案，勿重复实现）
- [x] 商用过滤的 node 白名单用目录前缀匹配：确认 `collect_files` 语义复用方式 → **B1.8 落于 `kb_manager.allowed_node_ids()`**（见上，含单测）
- [x] 设置「usage_mode」是否需要独立 API vs 复用 PATCH /profile/{field} → **复用 PATCH /profile**（B1.5 落地，SettingsView 先 GET 后 PATCH 全量合并）
