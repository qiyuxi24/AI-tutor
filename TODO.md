# TutorAgent 备赛与工程化清单

> 定位：知识图谱驱动的自适应导学 Agent（知识图谱 + 上传知识库 RAG + AI 出题 + 资源采集）
> 赛道：**三赛同投**（国创 / AI+教育 OPC / AIC），总纲·时间线·注意事项见 `docs/比赛/COMPETITION.md`；本文件只列工程化待办
> 今日：2026-09-10（W3 第 3 天，距 10-15 双截止约 5 周）
>
> **⚠️ 清单状态按代码实测校准（2026-09-05 起随完成随勾），非按文档印象勾选。**

---

## 📋 工程化就绪度评估结论（2026-09-08 复核）

**结论：工程化底子充足；比赛材料侧缺 演示数据 + 阿里 embedding 额度（真实向量灌库/评测前置）。**

| 维度 | 等级 | 关键证据 |
|------|------|---------|
| 架构/分层 | A | rag_pipeline / kb / hybrid_search / quiz / collector 去耦合（注册表+策略）；Agent Loop 编排/事件/落库/工具四层分离 + 工具注册表 `_TOOL_SPECS` 收敛 |
| 自动化测试 | A | 离线零网络集（排除 real_api）**491 passed 全绿**；real_api 另 7 passed（reports/real_api_test_report.md） |
| 部署能力 | A- | Docker 多阶段 + healthcheck + 数据卷 + nginx SSE 反代已就绪 |
| 健壮性 | A- | LLM 重试(指数退避+抖动) / 错误码 / 单源超时隔离 / SSRF 防护 / Agent Loop 超时+轮次护栏 / reasoning_split 防思考污染 |
| 文档同步 | A- | ✅ README 规范 v2 全景重写（9/7）+ AGENTS.md 开发索引（9/8）+ 技术报告评审证据版 |
| 运维/CI | A | ✅ 结构化日志轮转 + 聊天限流 + lifespan（9/8）+ .github CI（9/8 补 `-m "not llm_api"` 后全绿 482） |
| 致命阻塞 | — | **MiniMax-M3 对话/流式/工具真网全通**（real_api 7/7）；残余依赖：阿里 embedding（text-embedding-v4）额度 → 仅卡 真实向量灌库/评测 两条 |

---

## P0 — 收尾就绪（约 2 周，演示不翻车 + 材料可信）

- [x] **文档同步 + 改名 TutorAgent 收尾**（2026-09-05 完成）
  - [x] README.md 全面重写（去"无 Docker"过时说法；补知识库/出题/采集/混合检索/211 测试；标题 TutorAgent）
  - [x] frontend/index.html `<title>` "frontend" → TutorAgent + `lang="zh-CN"`
  - [x] LoginView / ActivityBar / OnboardingGuide / style.css 等 "AI Tutor" 文案 → TutorAgent
  - [x] backend/app/main.py `FastAPI(title="TutorAgent API")`
  - [x] start.ps1 / install.ps1 横幅；deploy/README、error_codes.md、docs/比赛/AI-Tutor_项目概述.md 标题与文案
  - 说明：logger 名 `ai-tutor`（30 处）与 localStorage key `ai_tutor_*` 属内部标识，改名会破坏登录态且零用户价值，保留不改；docs/比赛/bp_v2 商业计划书系列品牌统一留 W5 材料期做
- [x] **真实 LLM 链路验证（对话/Agent 主链路）**（2026-09-06~09-08 完成）
  - ✅ MiniMax-M3 chat/流式/工具调用真网全通（scripts/test_minimax.py 自检 + smoke_agent_loop.py 冒烟）
  - ✅ Agent Loop 真网 7/7 passed（L1 工具选择/L2 参数提取/L3 结果利用/L4 协议合规/token/trace，详见 reports/real_api_test_report.md）
  - ✅ reasoning_split 防思考污染已实测（test_minimax_thinking.py 7 例）
- [ ] **真实向量链路验证（阿里 embedding 额度恢复后）**
  - [ ] `scripts/seed_collector.py --embed api` 真实向量灌库
  - [ ] `scripts/eval_rag.py --embed api` 真实重跑 → 定夺 RRF vs 加权融合（当前 mock 结论：加权 0.818 > RRF 0.766）
- [ ] **quiz 简答 LLM 判分端到端联调**（走 call_llm 纯文本，MiniMax key 可用即可验，不依赖 embedding）
- [ ] **OI-wiki 真网联调**（B2.1 收尾）：`search("数据结构与算法")` 应返回 ds/算法基础板块候选、fetch 首页正文入库无 MkDocs 残留语法 —— 普通联网即可验（无需 DASHSCOPE），**待议**（2026-09-05 暂缓，待用户定时间）
- [x] **图谱重复节点处理**（2026-09-08 完成）
  - `create_node_from_ai` 遇已存在 ID 自动转更新模式：补全字段（summary/tags/difficulty 等）+ 追加 MD 内容 + 补建前置边，不再 ValueError 中断 Agent 循环
- [ ] **演示数据 + 脚本**：填充 1 个完整学科图谱（15-20 节点 + prerequisite 边）；写 2-3 条预演对话路径
- [x] **工程卫生**（2026-09-08 完成）：清理 3 个 `.bak` 拘留文件（index.json.bak / 5.md.bak / ForceGraph.vue.bak）

## P1 — 工程化补强（约 1 周，评委可问项）

- [x] **MiniMax-M3 推理内容（`ϩ` 思考/thinking）适配 — 上下文工程**（2026-09-07 完成）
  - 现象：M3 默认把思考过程以 `ϩ…ϩ` 特殊标签裹进 `message.content`（chat 与流式增量均是）→ ① 前端当正文显示；② 会话持久化 + Agent Loop 多轮回填会累积思考、白白烧 token
  - 方案（候选 B + A 双保险）：请求统一带 `extra_body={"reasoning_split": True}`（思考拆到 reasoning_details，content 保持纯正文）+ `_strip_think_tags` 剥离兜底
    - `call_llm`：判分/出题/图谱分析不再受 thinking 污染
    - `agent_loop._chat_once`：带 reasoning_split；`_assistant_snapshot` 回填 reasoning_details 保思维链连续
    - 注：旧 `call_llm_stream` 过渡函数已于 2026-09-08 死代码清理删除（Agent Loop 全面收敛后无调用方），本文档不再引用
  - 测试：新增 `tests/test_minimax_thinking.py`（7 例）；9/8 全库 473 passed 零网络
  - 前端：无需改动（reasoningSplit 已在后端剥离，token 已达纯正文）
- [x] **FastAPI `on_event` → lifespan**（2026-09-08 完成）：3 处 `on_event` 统一收敛为 `@asynccontextmanager lifespan`，消除全部 DeprecationWarning
- [x] **聊天接口限流**（2026-09-08 完成）：`RateLimiter` 泛化（`LoginRateLimiter` 保留别名）+ `chat_rate_limiter`（20 次/60 秒）覆盖 `/chat` 和 `/chat/stream`，超限返回 429 + `Retry-After`
- [x] **结构化日志**（2026-09-08 完成）：`RotatingFileHandler` 写 `logs/tutor.log`（10 MB 轮转 × 5 备份）+ 控制台双输出；`LOG_LEVEL`/`LOG_DIR`/`LOG_MAX_BYTES`/`LOG_BACKUP_COUNT` 可配
- [x] **.github CI + 测试收敛**（2026-09-08 完成）：`.github/workflows/backend-tests.yml` 已存在。修复两处：
  - workflow run 加 `-m "not llm_api"` 排除 `test_agent_loop_real_api.py`（需真 key + pytest-asyncio/pytest-timeout，CI 无）；pytest.ini 已有 `llm_api` marker
  - 顺手修 `token_counter` 根因小 bug：退化路径对空 content 也强给 1 token（`max(1,0//3)=1`），与 tiktoken 路径空串=0 不一致 → 本地无 tiktoken 时 `none_content` 得 8 非 7；改为 `... if content else 0`。**CI 等价命令实测 482 passed 全绿**
- [x] **节点掌握度手动调整**（2026-09-08 完成）：NodeDetail 加 range 滑块（0-100），emit `update-mastery` → HomeView 调 `store.updateMastery()` → `PUT /knowledge/node/{id}/mastery`
- [x] **图谱知识一键导出**（2026-09-08 完成）：`GET /knowledge/export?subject=` 返回合并 Markdown（节点列表 + 依赖关系），`Content-Disposition: attachment` 触发下载
- [x] **节点内容 Markdown 分屏编辑**（2026-09-10 完成）：NodeDetail 编辑模式由单栏 textarea 改为双栏（左源码 / 右实时预览），复用 `frontend/src/utils/markdown.js` 既有渲染管线（marked + KaTeX + highlight.js + DOMPurify），窄屏 <760px 自动堆叠；零新依赖、单文件改动（`NodeDetail.vue`）
- [ ] **Prompt 笔记优化**：教学后主动 `update_node_content`/`add_knowledge_node` 记笔记（提示词层，未动）

## P2 — 功能路线图（比赛可选项 / 有真实 key 后）

- [ ] **RAG 精排（Cross-Encoder）**：hybrid_search 后接 rerank，量化收益（W4 可选，须先有真实 embedding 灌库）
- [ ] **HyDE / Query Expansion**：召回 +15-25%，LLM 失败回退普通检索
- [ ] **出题进阶**：参数化母题模板、题目知识关联回填图谱
- [ ] **学习大纲生成 / 主动引导式提问 / 自包含 HTML 复习卡导出**
- [ ] **"AI 同学"多角色讨论 / 白板图谱联动（SVG）**
- [ ] **LLM 服务商抽象层**（deepseek/GLM 切换）；KG_TOOLS → 抽象"动作层"
- [ ] **RAG 引用卡片拖拽窗口**（知识卡片白板，借鉴 OpenMAIC）

---

## ✅ 已完成（归档，勿重复实现）

### 地基与核心
- [x] create_node 全局 ID 冲突修复（`_node_id_exists_globally`）
- [x] LLM 请求重试机制（`_with_retry` 指数退避+抖动，只重试瞬时错误）
- [x] 前端 toast 替代 alert（`utils/feedback.js`，源码已 0 处 alert）
- [x] CORS 配置化（`CORS_ALLOW_ORIGINS` env → config.py）
- [x] Docker/Nginx 生产部署（Dockerfile + compose + healthcheck + .dockerignore）
- [x] 全局异常处理器 + 标准化错误码（含 E-LLM-00x / E-QUIZ / E-COLL / E-WEB-FETCH）

### 知识图谱与对话
- [x] 图谱 CRUD + D3 力导向可视化 + 搜索跳转 + 右键菜单
- [x] 科技树化：掌握度四色着色 + 图例 + 学习路径高亮 + 薄弱点脉冲（ForceGraph）
- [x] 拓扑排序学习路径推荐（Kahn）+ 仪表盘联动聚焦
- [x] 学习进度仪表盘（/knowledge/stats?subject，掌握/学习中/未学/时长）
- [x] **学科切换**（nodes.subject 字段 + 前端学科选择器 + stats by_subject）
- [x] 两阶段流式对话（SSE 文本 + 后台 function calling）+ 四种教学模式（**注**：2026-09-06 起已统一收敛为 `run_agent_loop` 单一 Agent Loop SSE，见 AGENTS.md §3.4）
- [x] 用户画像 v2（JSON 结构化 + usage_mode + get_completeness）
- [x] 事件总线解耦图谱更新；JWT 认证 + 限流

### RAG / 知识库
- [x] 知识库目录树（递归多级 + 上传/删除/检索/上下文选择）
- [x] 解析器注册表去耦合（text 30+/pdf/docx/pptx/image-OCR/legacy，可选依赖降级）
- [x] 混合检索：向量 + whoosh BM25 宽召回各 30 → **RRF 融合**（含 fuse 加权对照）
- [x] chunk path 溯源 + 父级扩展（1500 字符预算 / 8 块上限）
- [x] rag_pipeline 去耦合：RagSource 协议 + 路由（问候/过短跳过）+ 跨源融合 + 单源隔离
- [x] Agentic RAG：`rag_search` 工具（source graph/kb/all），KG_TOOLS 7→8
- [x] graph_generator：书籍自动生成图谱 + 语义去重合并
- [x] 商用模式 L2 版权过滤（allowed_node_ids 零迁移）

### AI 出题
- [x] quiz 模块（schema/generator/grader/quality/store）+ API 5 端点
- [x] QuizView 前端（配置→作答→客观题规则判分/简答 LLM 判分→解析反馈）

### 资源采集（Collector，见 TODO_Collector.md 逐项）
- [x] B1.1-B1.8 全部：core 骨架 / MediaWiki 适配器(维基真网联调过) / manager(断点续传+协作取消) / API / 双模式 / seed 40 词条 / CollectorView 前端 / 商用过滤

### 测试与评测
- [x] 单元 90+ + 集成 11 + collector 系列，**211 passed 零网络**（9/5 实测）
- [x] CMRC2018 离线评测集（mock：vector R@1 0.470 / bm25 0.964 / RRF 0.766 / fuse 0.818）
- [x] whoosh 批量写入 38× 加速（180s→4.7s）
- [x] B2.3 文本下限 200 字符拒收 + B2.4 doc_chunks.embedding 可空(BM25-only)

### 项目 / 部署脚本
- [x] install.ps1 / start.ps1 一键安装启动；.env.example 模板；迁移脚本体系

---

## 遗留技术债（2026-09-08 盘点，非功能项、优先低）
- [x] **`backend/test_data.py` 遗留失效脚本**（2026-09-08 已删）：`KnowledgeGraph()` 缺 user_id 无法运行且会被误收集，无任何引用 → 直接删除。
- [x] **向量检索维度校验**（2026-09-12 已修）：`VectorStore.search` / `DocVectorStore.search` 库内向量维度 ≠ 查询维度时跳过该行 + warning（原 `np.dot` 直接 ValueError→500，换嵌入模型未重建即触发）；新增 `tests/test_vector_store_dims.py`（2 例）。
- [x] **维基地区词标记 `-{…}-` 未清理**（2026-09-12 已修，详见 `TODO_Collector.md` 遗留技术债 #1）。
- [ ] **试卷拆分器 `quiz_splitter.py` 已实现但零引用（未接入上传链路）**（2026-09-12 发现，详见 `TODO_Collector.md` 遗留技术债 #2）：需先定入口形态（`/kb/upload` 自动拆 / 独立 `POST /quiz/import` / 并入 B3.1）。
- [x] **两处 `_embed()` 合并统一门面**（2026-09-12 已修）：新增 `app/core/llm/embed.py`（`embed_texts` / `EMBEDDING_MODEL` / `MAX_EMBED_CHARS`）为嵌入唯一出口；`kb_manager._embed`、`rag/manager._embed` 收敛为薄委托（保留方法名以不破坏既有"类级 patch `_embed`"的测试与脚本遮罩缝）。顺带修根因：**部分返回（条数对不上）整批作废**，杜绝 chunk 与向量错位入库（原 rag_manager 侧 `zip(chunks, embeddings)` 会静默错位）。`kind=db|query` 参数按 YAGNI 未加（换非对称嵌入模型时再加）。测试 `tests/test_embed_texts.py`（6 例）。
- [ ] **图谱 RAG 未接入 hybrid_search 双检索**（2026-09-11 调研发现）：图谱侧只有向量检索（`rag/manager.search`），未享 BM25 稀疏路。**评估结论：不是小改**——`hybrid_search/whoosh_index.py` 的 schema 把 `node_id` 定为 `NUMERIC`（KB 文件节点 int），图谱 node_id 是 TEXT，复用需改 schema + 老索引迁移/双 schema 兼容。收益（图谱写回 BM25 召回）与成本需先量化，暂缓。
- [ ] **conversations 内嵌 tools/thinking 去留 + run 与会话无关联键**（9/8 起挂着，**待决策**）：需定"是否为 agent_runs 加 conversation 外键/会话 id 字段（动 schema）"，或接受现状。
- [x] **README 门面同步 + 双语化 + 贡献指南**（2026-09-12 完成）：① 数字/路径校准 —— 测试口径改为 `533 passed, 7 deselected`（收集 540）并附复现命令；架构图与目录树的 `llm_client.py` → `core/llm/` 原语包；目录树补 `agent_run_store.py` / `agent_tools.py`；核心功能表补"运行记录与证据回放""模型回退链"。② 新增 `README.en.md` 英文镜像（文首语言切换器 + `<!-- base -->` 注释；与中文版结构同构：H2 11 / 代码块 10 / 表格行 47 两侧一致）。③ 新增 `CONTRIBUTING.md` + `CONTRIBUTING.en.md`。④ `docs/README_编写规范.md` 升 **v3**（新增第 6 章多语言维护、第 7 章贡献指南规范、第 8 章检查清单）。CI 双语文档结构校验脚本按要求**暂未实现**（见规范 6.5 ④）。

## 进度速览（2026-09-08）
- 代码层工程化 ~85%（架构✓ 测试✓ 部署✓ 文档✓ CI✓ 运维✓ 限流✓ 日志✓ Agent Loop✓）
- 测试：离线零网络集（`-m "not llm_api"`）**491 passed 全绿**；real_api 独立 7 passed（需真 key + pytest-asyncio，单独跑 `-m pytest tests/test_agent_loop_real_api.py`）
- 比赛落地待办：P0 残余（演示数据 / 阿里 embedding 向量灌库+评测 / quiz 判分联调 / OI-wiki 待议）→ 材料期 10/5 起
- W3（9/8-9/14）主线：P1 工程化补强已批量完成 → 转向 P0 演示数据 + 真实 embedding 链路
