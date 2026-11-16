# AI Tutor 已完成模块归档（DONE）

> 用途：`TODO.md` 的**完成侧归档**。待办留在 `TODO.md`，做完的**搬**到这里（不是复制）。
>
> **维护约定**
> 1. **只追加，不重写**：新完成项追加到对应分节末尾；无对应分节就在文件末尾新建 `## <模块名>`。整文件重写既白烧 token，也有覆盖他人改动的风险。
> 2. 本文件是**历史台账**，不是契约；实现细节以代码 docstring 与 `docs/*.md` 为准。
> 3. 迁出时保留日期与关键证据（文件 / 测试名），便于回溯"当时验证到哪一步"。
>
> 来源：`TODO.md`（2026-09-13 迁出 P0/P1 已完成项 + 原「✅ 已完成（归档）」节）、`TODO_Collector.md`（B1/B2 已完成项；该文件已于 2026-10-11 并入 `TODO.md`）。
> 上次整理：2026-09-13（最近追加：2026-10-10 §4.5 真流式）

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

### 1.1 文档同步 + 改名（2026-09-05）
- README.md 全面重写（去"无 Docker"过时说法；补知识库/出题/采集/混合检索）
- `frontend/index.html` `<title>` + `lang="zh-CN"`
- LoginView / ActivityBar / OnboardingGuide / style.css 等品牌文案
- `backend/app/main.py` `FastAPI(title=...)`；start.ps1 / install.ps1 横幅；deploy/README、error_codes.md、`docs/比赛/AI-Tutor_项目概述.md` 标题与文案
- **名字沿革**：2026-09-05 由 "AI Tutor" 改为 "TutorAgent"；**2026-10-10 全库改回 "AI Tutor"**（界面标题 / 活动栏 logo / 引导弹窗 / 运维后台 / 两个 FastAPI title / README·CONTRIBUTING·TODO·docs 正文）
- **刻意保留（功能性标识，改名会破坏运行态或与线上不一致）**：logger 名 `ai-tutor`、localStorage key `ai_tutor_*`、采集 UA `TutorAgent-Collector/0.1` 与 `TutorAgent/1.0`、npm 包名 `tutoragent-admin`、部署路径 `/opt/tutoragent` / systemd 单元 `tutoragent.service` / 防火墙规则名 `TutorAgent LAN`

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
- **节点掌握度手动调整**（2026-09-28 已移除）：曾给 NodeDetail 加 0-100 range 滑块 → `PUT /knowledge/node/{id}/mastery`；现掌握度只由出题判分写入
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

### 4.3 图谱生成质量 P0：同名并轨 + 去重不静默 + 可复现体检（2026-09-20，GQ-1~3）
- 由来：对 user 5 的书籍建图结果全量审计（311 节点 / 659 边 / 23 组同名重复 / 42.5% 空壳），
  结论与量化目标见 `TODO.md` §4。
- **GQ-1 写入层精确同名合并**：`knowledge_graph.normalize_node_name()`（NFKC + 去空白 + 剥
  「（复习）/_extended/小结…」碎片尾缀）与 `KnowledgeGraph.find_node_by_name(name, subject)`
  是判重**唯一实现**，`create_node_with_content` 命中已有节点则并轨（**返回实际落点 ID**，
  调用方用它建边/回执），`knowledge_writer._find_same_name` 退化为薄壳。
  原先并轨只存在于 Agent 写路径 → 书籍建图走 `create_node_with_content` 绕过（原漏洞 #4）。
  `graph_generator._write_to_graph` 增加 `node_id_alias`：边引用被合并掉的 id 时改指落点
  （原先只按 name 记映射，边按 id 引用 → 漏建边）；并轨幂等（同段正文不叠两遍）。
- **GQ-2 嵌入失败不得静默**：`_find_dedup_candidates` 拿不到向量时 `logger.warning` +
  `dedup_status = unavailable`；hash 兜底记 `degraded`；状态经 `_write_to_graph` → `_generate`
  aggregate 带出（原状：欠费 → 返回 `{}` → 零合并且零告警）。
- **GQ-3 质检脚本**：`backend/scripts/inspect_graph_quality.py`（默认只读）：规模/深度、
  L1 同名组 + L2 字符串相似对（不依赖嵌入）、空壳率、结构健康（悬空边/自环/重复边/前置环/
  孤立/连通分量）、tags 分布异常、字段填充、拓扑。**体检基线口径由脚本固化**（与手工审计
  的差异原因见 `backend/scripts/inspect_graph_quality.py` 注释）。
- 证据：`tests/test_node_write_paths.py`（14 例，含建图路径并轨/幂等/学科隔离/去重状态）、
  `tests/test_graph_quality_script.py`（4 例，判重边界 + 合并动作）；全量 `770 passed, 7 deselected`。

### 4.4 小节级学习状态 + 「去学习」教学闭环（2026-09-29 ~ 09-30）
- 由来：用户要"细到每个 markdown 小节"的进度 —— 自己标「已懂 / 不懂 / 已读完」，每个知识点加
  「去学习」进对话，AI **按小节**教：已懂的先出题验证，不懂/没看过的按顺序讲，全部小节通过
  → 节点掌握度置 100。
- 存储：`manifest.sections[].learn = {mark, read, passed, attempts, mark_by, updated_at}`，与生成状态
  `status`（pending/filled/failed）**分开放** —— 重跑生成不碰学习痕迹；老 manifest 无该键 →
  读默认值、不写盘（零迁移）。API `PUT /knowledge/node/{id}/section/{sid}/learn`。
- 判分闭环：`questions.section_id`（就地补列）→ 题带小节就走**小节口径**（答对 = 该节 passed；
  全通过 → 节点 100；答错第一次重出同节题、第二次降级为「不懂」），不带则维持旧的节点 +20。
- 教学上下文：`chat_service._build_learn_block` 把**小节清单 + 状态 + 本轮起点 + 该节正文 +
  教学规则**注入 system prompt（无小节的老节点 → 空串，提示词零影响）。
- **2026-09-30 增补（本次）**：
  1. **开场轮与后续轮是两套提示词**（`_build_learn_block(..., opener=)`，由"会话里还没有 assistant
     消息"判定）：开场轮**只报菜单**（分类点名 + 走法），不注入起点正文、不给教学/出题规则，
     报完停下等学生回应；后续轮才给 6 条规则（规则 0 改为"别再重复报清单"）。
     —— 真机日志证明"把报菜单叠在规则 1 之上"**两次都压不住**（模型第一轮直接调 quiz_generate，
     最终回复只有 28 字符），必须把竞争的那条规则从开场轮拿走。
  2. **开场轮"禁工具"是协议层硬保证**：`run_agent_loop(no_tools=True)` → `tool_choice="none"`
     （保留 tools 块以维持缓存前缀；网关 400 时降级为不带 tools）+ **丢弃**网关不守规仍吐出的
     `tool_calls`。判定收口在 `chat_service.learn_mode_kind()`，提示词侧与禁工具侧共用。
     —— **提示词里写"禁止调用任何工具"实测无效**（模型开场轮照样 `quiz_generate`，回复 16 字符）。
  3. **题卡归属校验**：学习对话打 `nodeId` 标；`quiz_ready`（后台出题 ~40s）按 `node_id`
     投回**属于它的那段对话**，找不到就不塞 —— 修掉"点最小代价生成树却收到图的存储结构的题卡"。
  4. **讲完一节自动记「已懂」**：新增工具 `mark_section_understood`（tier=free）——模型讲完一节
     调它把该节记为 `mark_by="ai"`；**硬触发兜底**：按小节出题时（`quiz_generate._mark_section_taught`）
     也顺带记「已懂」（真机实测 `mark_section_understood` 4 次采样一次没被调）。
     「不懂」仍只能由学生自己标、或连续两次答错时系统降级。
     `mark_by` 同时落地在用户端点（`"user"`）与降级路径（`"ai"`），前端据此区分提示语
     （AI 记的加虚线描边），**已懂 ≠ 通过**的口径不变。
  5. 可观测性：`_build_system_prompt` 打一行 INFO（`学习模式/开场轮…知识点=xxx`），
     分支走对没走对看一眼日志即可。
- 证据：`tests/test_section_learn.py`（存储层 + API）、`tests/test_section_grade_flow.py`（判分口径）、
  `tests/test_learn_mode.py`（学习块：开场轮/后续轮两套）、`tests/test_mark_section_tool.py`（新工具）。
  真机冒烟脚本 `backend/scripts/smoke_learn_mode.py`（提示词类改动只能真跑验证；
  **注意它会污染所跑账号的题库与事件流**，见开发日志 2026-09-30 补的"踩坑"）。

### 4.5 真流式：正文逐 token 下发（2026-10-10）
- 原状：`loop.py` 两处 `emitter.emit(TEXT_DELTA, text=整段)`、`_chat_once` 未开 `stream=True`
  → 流式只到**事件粒度**；2000 token 级回答要等整段生成（真机同问题 **52.8s**）才一次性蹦出。
- 做法：新增 `core/llm/stream.py::chat_create_stream`（`chat_create` 的流式变体：正文逐 chunk
  回调 `on_delta`，把 chunk 累积成**与非流式同形**的响应对象；**不建 client、不自己回退** ——
  候选链/重试仍唯一在 `chat_create`）→ `loop._chat_once(on_delta=...)` → `_DeltaPump` 逐片推
  `TEXT_DELTA`，并按片数门控收尾整段帧（**推过就不补** = 不重复；**一片没推才补** = 前端不丢内容）。
  同时 `tool_calls` delta 按 `index` 聚合回 `message.tool_calls`，本模块之后的逻辑完全不分叉。
- 真机依据（2026-10-10，MiniMax-M3 + NWPU 备用网关）：`stream=True` 逐片下发（首字节 425ms /
  首正文 2022ms / 整段 14.4s）；**`tool_calls` 确以 delta 下发**（设计文档 §11 的阻塞问题）；
  `stream_options.include_usage` 可用（末 chunk 带 usage，含 `cached_tokens`）；
  `reasoning_details` 流式元素形状与非流式一致。
- 端到端：`run_agent_loop` 真机 **131 片**、首帧 6.0s / 整段 21.4s，且拼接正文 == `result.text`
  （**无重复、无丢失**，这条是"门控收尾帧"的核心验收）。
- 回归：`tests/test_llm_stream.py`（7 例）+ `tests/test_agent_loop.py` 流式 4 例；离线全量 **1621 passed**。
- 有意行为变化：工具轮的过渡语（"我先查一下…"）现在也会实时显示 —— 内容 delta 出现在 tool_calls
  delta **之前**，不先发就做不到"最终轮边生成边显示"；契约见
  `test_tool_round_narration_streams_before_final_answer`。
- 未闭环（留在 `TODO.md`）：`strip_think_tags` 仍是整段正则（未做流式状态机）；隐藏中间轮文本需前端"撤帧"。

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
- 方案 / 禁区 / 未做项（robots、留痕、搜索侧过滤）：`docs/教育资料采集/采集_网页正文与开放来源_实施方案.md` §三。

### 7.4 正文预览 + 维基正文修复（2026-09-23）
- **知识库正文预览**（用户："那些 md 看不到内容"）：`GET /kb/node/{node_id}/text` + `kb_manager.get_document_text`（读正文唯一出口）+ `KbTextPreviewDialog.vue`；`KbPanel` 文件节点「查看正文」按钮与**双击文件**两个入口。落点较原方案 §4 改为知识库侧直读（同样覆盖手动上传文件），详见 `docs/教育资料采集/采集_网页正文与开放来源_实施方案.md` §2.6。
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
- **「同一 user 两标签页，`quiz_ready` 随机进其一」**（2026-11-10 核实已不存在）：`event_bus.subscribe(user_id=…)` 早已是 **per-connection fan-out**（每次调用新建队列并登记进 `_user_queues[user_id]` 这个 set，`broadcast`/`notify` 给每条队列各一份）。TODO §2 那条的"剩余缺口 + 三选一待决策"前提已过时，搬来此处。回归 `tests/test_event_bus.py:214`、`:228`。
- **对话内出题的两条遗留**（2026-11-10 核实已完成）：① 题目卡片 —— `frontend/src/components/QuizCard.vue` 存在并挂于 `MessageBubble.vue:401-404`；② 题目漂移 —— `core/quiz/chat_quiz.py:48` 定 `CHAT_QUIZ_TOP_K = 0`（"出题依据只允许用当前节点的正文"，注释记录的正是这次实测结论）。
- **HyDE / Query Expansion**（2026-11-10 核实已实现）：`core/rag_pipeline/query_expansion.py::hyde_query` 存在，开关 `core/config.py:116` 的 `rag_hyde_enabled`（env `RAG_HYDE_ENABLED`，**默认 `False`**）。从 P2「待实现」降级为「待评估收益」—— 配置注释已写明"收益主要体现在口语化提问，离线集上很可能看不到提升"。
- **`<400 字拒收`是否改成"标记待补"**（2026-09-26 定案）：已落地**两档守门** —— `core/kb/graph_generator.py:119` 的 `GRAPH_SHALLOW_REJECT_CHARS = 150`（低于此值才算截断产物、拒收且留 skeleton）+ `GRAPH_MIN_CONTENT_CHARS = 400`（完整性目标线，介于两者之间**保留但标记待补**，名字进 `rejected_shallow`）。TODO §9 那条待拍板项已无内容。

## 11. 硬伤修复（2026-10-11，审计驱动）

一次针对「会导致故障 / 数据损坏 / 静默错误」的全库审计后修复。回归：后端全量 **1654 passed**（`-m "not llm_api"`）、`backend-admin` **43 passed**。

- **删除即出 RAG 索引收口**：`KnowledgeGraph.remove_node` / `remove_subject` 内调 `_purge_rag_index_for_nodes`。原先只有 API 层两处显式清，Agent 的 `delete_node` 工具 / AI 建议采纳（`merge_ai_suggestion`）走 kg 直删会留索引 → 已删知识点仍被 S6 按需检索注入。回归 `tests/test_kg_rag_purge.py`（`tests/test_api_delete_subject.py` 相应收窄为只锁 HTTP 契约）。
- **同步→异步桥统一复用主 loop**（新模块 `core/runtime.py`）：`rag_search` / `download_resource` / `kg_taxonomy` 三处同步桥原先各自 `asyncio.run` 新建临时 loop → 在临时 loop 里复用 `llm/clients.py` 的 AsyncOpenAI 单例会**静默降级**（嵌入返回 `[]` → RAG 退化 BM25-only；归属判定返回 `{}` → 节点落「未分类」）。现 `run_sync` 优先 `run_coroutine_threadsafe` 回主 loop（`main.py` lifespan 注册 / 关闭清除），无主 loop 才退回临时 loop。同模块 `spawn` 统一保后台任务强引用（裸 `asyncio.create_task` 返回值无人持有，任务可能被 GC → `chat_quiz._INFLIGHT` 永不释放 → 该用户永久「已有一道题在生成中」）；接线 `chat_service` ×2 / `chat_quiz` / `section_generator` / `rag_pipeline.sources`。回归 `tests/test_runtime_bridge.py`。
- **SSRF**：`/llm/models` 的 `base_url` 一律过 `net_guard.is_blocked_url`（新建 / 修改 / 探测三处），拒绝内网 / 回环 / 保留地址。回归 `tests/test_llm_models_api.py::test_create_rejects_loopback_base_url`。
- **运维后台默认密钥拒绝启动**：`Settings.require_secret_key()` 在 lifespan 校验，未配 `ADMIN_SECRET_KEY` 直接拒启（原默认密钥公开在源码里，任何人可伪造后台 JWT）。回归 `backend-admin/tests/test_admin_api.py`。
- **断连取消 agent 任务**：`process_message_stream` 的 `finally` 取消仍在跑的 `agent_task` —— 客户端关页后它会继续用已关闭的 kg（sqlite "Cannot operate on a closed database"）并白烧额度。回归 `tests/test_learn_stream_path.py`。
- **上传体积上限**：`/kb/upload` 加 50MB 上限（原 `await file.read()` 整文件入内存且无限制）。

**未做（明确记录）**：
- `get_current_user` 不校验 `users.status`（禁用账号旧 token 仍可用至过期）—— 每请求加一次 DB 读、且会波及大量用 `get_current_user_from_token` 的测试；更优解是 token 版本号（新列 + 迁移），另立需求。
- `index_user_graph` 全量重建「先清空后重建、中途失败无回滚」（可重跑修复，非静默损坏）。
- 删 KB 文件不回收图谱侧 `doc_node_marks` / `nodes.sources` 引用（ghost 引用，影响较轻）。

## 12. 2026-11-12 全量复核后确认已完成（从 TODO 搬入）

本节由「对 TODO §1~§10 每一条未勾选项做代码级取证」得出，**只搬经实测确认已完成的**，
不是文档转述。当日基线：后端 **1968 passed / 1 skipped / 7 deselected**、前端 **42 passed (3 files)**、
`backend-admin` **43 passed**。

- **GQ-11 aggregate 暴露合并数 / 跳过数 / 失败块数**：`aggregate` 含 `merged_nodes` / `skipped_docs` / `failed_docs` / `failed_chunks`（`core/kb/graph_generator.py:1553-1564`），并进 `graph_updated` 通知体（`api/v1/kb.py:482-484`），前端 `chatStore.js:940-943` 消费。
- **GQ-18 溯源贯通（L0）**：体检口径第三处同步已落实 —— `scripts/inspect_graph_quality.py:113` `support_counts`、`:450-454` 支撑度、`:151` `_align_metrics` 对齐率、`:535-550` 打印（未就绪显式跳过）。`edges.sources` 仍按计划暂缓（无消费者）。
- **GQ-19 新资料演进流程（L3，按资料增量）**：五步全落地 —— `_process_book`（`graph_generator.py:1608-1675`）→ 幂等检查 `:1619`、续填 `:1635-1640`、标记落表 `:1668-1672`（`evidence.kind=new/hit`）、只填被标记 `:1674`。回归 `test_graph_topdown.py:415`（只动 hit 节点）/`:448`（同资料二次调用跳过）/`:471`（`skipped_docs`）。
- **「没有学习会话这一等对象」（架构债务 §3 的第二条根因）**：`core/learning/session.py:53-90` `LearnTurn` 一等对象 + `kg/schema.py:133` `learning_sessions` 表 + `upsert/get`（`sections.py:230-261`）。旧临时属性（`kg.student_input` / `kg.quiz_consent` / `kg.learn_target`）已删，并有静态守卫 `tests/test_learning_session.py:185 test_retired_temp_attrs_are_gone_from_app`。
  - ⚠️ 残留转 TODO §10 P1：表里缺「待办队列 / 最近一题状态」两列，无 API 暴露，前端零消费，`reconcile()` 零调用方。
- **教学策略「抽出判定出口」（架构债务 §3 第一条的前半）**：`core/teaching/policy.py:123 decide()` 是唯一策略出口，`chat_service.py:471/590` 调用；`tests/test_learn_block_golden.py` 用 golden 逐字符比对锁等价重构。
  - ⚠️ 后半（消除 `_build_learn_block` 的 9 处 ⛔ 规则）未做，仍在 TODO。
- **掌握度「进入路径排序」（架构债务 §3 第三条的前半）**：`core/kg/paths.py:97` 改走 `core/graph_order.order_key`，键 = `(前置未达标, mastery 升序, id)`（`core/graph_order.py:48-57`）。原记录"排序只看 prerequisite、不读 mastery"已过时。
- **事件数观测（观测性缺口的三分之一）**：`core/agent/store.py:54` `events_written` 列 + `loop.py:826` 写入 + `core/agent/events.py:118` 单一计数口径。首字节延迟仍缺。
- **对话删除的跨端传播（双写持久化的前半）**：`core/conversation_store.py:419` `soft_delete` / `:429` tombstones / `:437-465` `sync` 返回 tombstones；前端 `chatStore.js:223-237` `adoptRemote` 按墓碑删本地。原记录"删除不传播 + 每次 persist 全量重传"已过时（`persist` 现只写 localStorage `:1084-1097`，`sync` 只在 `init` 调一次 `:696`）。仅剩"真合并"未做。
- **SSE 通道分层（原 [P0]）**：`event_bus.subscribe(user_id=…)` 是 per-connection fan-out（`event_bus.py:156-163`），`broadcast(:175)` / `notify(:196)` 各投每队列一份 → "多标签页互相抢事件"这个前提彻底作废。剩余物已收窄为"对话列表跨端无通知"与"事件类型过窄"两条子项。
- **建图完成走通知（长同步端点收口的第一处）**：`api/v1/kb.py:549-567` `finally` 内 `notify(GRAPH_UPDATED, ...)`，成功/失败/取消都发。回归 `tests/test_kb_graph_generate_notify.py`（7 例）。`sections/generate` 仍是同步等待，未做。

## 13. 2026-11-16 LLM 服务商抽象层（方言表）+ 对话框内切换模型

**根因**：`agent/loop.py` 曾**无条件**发 `extra_body={"reasoning_split": True}` —— 那是 MiniMax-M3 专有字段。换 deepseek / GLM 后严格网关 400、宽容网关静默忽略（后者最贵：不报错，但思考永远拿不到）。

**收口到一张表** = `backend/app/core/llm/provider.py`（`Provider` 冻结 dataclass + `PROVIDERS`：minimax / deepseek / glm / qwen / openai / 兜底 `openai-compat`）。解析顺序 = 显式 `provider` 字段 → 模型名前缀 → **兜底零字段**（关键：未知 provider 一个参数都不发，不是"发个默认值试试"）。

**六处爆炸点**：
1. 请求侧 `extra_body` → `fallback.chat_create(thinking=)` **按每一档**注入（候选链主备可跨服务商，一个进程级 extra_body 必然对某一档发错字段）；
2. 响应侧思考字段 → `provider.reasoning_texts`（`reasoning_details` 与 `reasoning_content` 都收，`stream._accumulate` 同步供 `on_reasoning` 回调）；
3. 多轮回填 → `agent/context.assistant_snapshot` **按网关实际下发的字段**回填（DeepSeek reasoner 不回填 `reasoning_content` 会 400 `"must be passed back to the API"`）；**不查表**——它给过的它必然认得；
4. token 校准 → `token_counter._get_calibration_factor` 查方言表（GLM 原先压根没有表项）；
5. `ModelStore` 加 `provider` 字段（`models.json` + `_ACCESS_FIELDS` + 对外快照）；
6. 连通性探测 → `api/v1/llm_models._probe_model` 按方言取 `probe_tokens` + `extra_body`（原先硬编码 `max_tokens=1` → reasoner 类被误判 `unavailable`）。

**前端（对话框内直接切换）**：
- 新增 `frontend/src/components/ModelPicker.vue`，挂在 `InputArea.vue` 工具栏右侧、发送键左边；标签只显示当前档名，点开才是列表（状态点：可用绿 / 未验证灰 / 不可用红）。
- 设置页 `ModelsPane.vue` 加「服务商」预设下拉（选预设自动填 base_url、给模型名提示），`presets` 由后端 `/llm/models` 带出（前端不维护第二份）。
- 新增 `POST /api/v1/llm/models/deactivate` + `frontend/src/api/index.js::deactivateLlmModel` —— 对话框里选「系统默认」= 取消激活回落 .env 档，不是选了个叫"默认"的模型。
- 两处读同一份后端状态（`/llm/models`），`InputArea` 在 `onMounted` 与每次打开面板时对齐一次标签。

**动作层（`KG_TOOLS` → `Action`）判定：现在不做**。`core/agent_tools/registry.py` 已是「一 spec 三投影」（`KG_TOOLS` / `_TOOL_BY_NAME` / `build_tools_prompt`），`plan.py::LEARN_TOOLSETS` 已是白名单雏形；唯一缝隙是 `agent/gateway.py:129-131` 手造 `SimpleNamespace` 假 `tool_call` 的 6 行 shim。**触发条件**：非 OpenAI 形状 provider / 不支持 function calling 的模型 / 出现第二个写路径宿主。

**验收**：`backend/tests/test_llm_provider_dialect.py`（17 例，覆盖解析顺序 / 逐档注入 / 两方言思考字段 / 流式累积）+ `test_llm_models_api.py` 新增 5 例（presets / deactivate / provider 往返 / 未知 provider 400 / 探测按方言取 tokens）+ `test_minimax_thinking.py` 与 `test_llm_call.py` 的断言从「透传 extra_body」改写为「只传 thinking」。后端全量 **2220 passed / 1 skipped / 7 deselected**（234.85s），前端 **83 passed (5 files)**，`vite build` 通过，图标审计 MISSING: none。


## 14. 2026-11-16 TODO 清理（从 `TODO.md` 迁入的已完成项）

> 口径：对 `TODO.md` 里**勾选过的**、以及备注已写"已完成但未勾选"的条目做代码/数据级复核，落点仍在 → 从 TODO 删除、证据搬到这里。
> **只搬经实测确认的**；部分完成的只搬已完成那半，剩余半句留在 TODO。
> 复核基线（2026-11-16，口径唯一维护处 = `AGENTS.md §7`）：后端 **2231 passed / 1 skipped / 7 deselected**、前端 **84 passed (5 files)**、`backend-admin` **43 passed**。
> 顺手删掉一处重复：LLM 服务商抽象层原先在 TODO §1 P2 又抄了一遍，其权威记录是本文 §13。

### 14.1 出题 / 判分

- **简答 LLM 判分端到端联调**（2026-11-15 真 key 跑通）：`POST /quiz/generate` 带 `question_types=["short_answer"]` → `POST /quiz/{id}/grade?user_answer=…`；出题 **5.56s / 3 道**，同一道题两例作答 **1.1s / 4 分**（答得较全）与 **1.01s / 0 分**（空泛），评语各自具体点名缺了什么 —— **不是降级固定文案**，LLM 判分真的按内容给分。脚本 `.dsh_local/aic_defense/probe_short_answer_e2e.py`。
  - 口径坑：`questions[].id` 是生成器本地标签 `q1/q2/q3`（`core/quiz/generator.py:411-413`），库内整数 id 在 `question_ids[]`；判分端点 **POST 但参数在 query**，裸 `urllib` 不显式 `method="POST"` 会发成 GET → 405。
- **独立出题页判分回写掌握度**（2026-11-15）：`QuizGenerateRequest.graph_node_id` → `api/v1/quiz.py::_touch_graph()`（带 `section_id` 走 `apply_section_after_answer`，否则 `apply_mastery_after_answer`；沿用 `chat_grade.resolve_node_id()` 三档兜底）。图谱写/读失败只 `warning`，**绝不影响判分结果**。前端 `HomeView` 补解构 `nodeId`（原本在 `emit` 里有、这里丢了）→ `QuizView` 出题带 `graph_node_id`、判分后展示掌握度变化、`emit('mastery-changed')` → `refreshGraph()`。回归 `tests/test_quiz_grade_mastery.py`（7 例）。
- **空图谱行动顺序 · 工具层引导**（2026-11-10）：`core/kg/edges.py::add_edge` 的两条 `ValueError` 改成可自纠正文案（点名 `add_knowledge_node` + "要传**节点 ID** 不是知识点名字"）。原先只说"节点不存在"→ 模型解读成"参数没传对"→ 同参数重试 → 撞 `loop._LoopGuard` 熔断 → 用户看到"AI 说在整理图谱然后什么都没发生"。回归 `tests/test_kg_edge_error_hint.py`（3 例，断言"文案里有可执行动作"而非具体措辞）。
- **出题改非思考档**（2026-11-15）：`_generate_batch` 的 `call_llm(...)` 加 `thinking=False`（思考与正文共享 `max_tokens`，思考吃满 → 空正文 → `E-LLM-006` → 整批作废）。回归 `tests/test_quiz_generator.py::test_generate_batch_turns_thinking_off`。**实测提速**：同一请求（`subject=二叉树遍历`、5 题）**35.13s / 38.97s → 7.91s / 8.08s / 6.69s**。取证 `.dsh_local/aic_defense/probe_thinking_ab.py`（单次 A/B：`thinking=True` 7.49s/completion=437 vs `False` 2.66s/completion=203，网关确实透传 `thinking.type=disabled`）与 `probe_quiz_profile.py`（批内 6~9s、两批真并发）。
  - ⚠️ 踩坑：首轮量到"比改前更慢"是**测错对象** —— 8000 端口进程从未重载。改完后端代码必须重启，`GET /api/health` 的 `boot_id` 变了才算。
  - ⚠️ **质量 A/B 仍未做**（只关了思考、没验题目质量）→ 已作为独立待办留在 `TODO.md §1 P1`。

### 14.2 图谱存量清理（GQ）

- **GQ-4 存量重名已清零**：2026-11-16 实测 `SELECT user_id, lower(trim(name)) … HAVING count(*)>1` → **同名组 = 0**（2026-11-12 还有 2 组：u1「测试节点」`new_003/new_004`、「鸿蒙开发入门」`harmony_dev_intro/harmonyos_intro`，现只剩 `harmonyos_intro`）。达成验收口径"同名组 = 0"。
  - 归因诚实说明：**不是** `inspect_graph_quality.py --fix-dupes --apply` 的产物，而是 2026-11-15 的 `backend/scripts/refile_nodes.py`（`_refile_backup/junk_20261115_1954*`）与期间重建达成。工具保留（默认 dry-run、`--apply` 才动），以后再出现重名照旧用它。
- **GQ-7 禁止碎片类节点**（2026-11-15 两侧都落地）：提示词侧 `kb/graph_generator.py:167`「禁止把本章小结/复习回顾/章节导读当概念」；入库侧 `kg/_common.py::is_meta_concept_name` 判**结尾**元概念词（概览/综述/概述/总览/导论/导读/分类/分支/总结/小结/回顾/摘要/历史），`_write_concept_tree` 落库前拦截 → `aggregate["dropped_meta"]` 点名（依据 W3C SKOS：Collection 与 Concept 互斥）。命中面实测 151 节点命中 7 个，反例（「加权最近邻分类器」「算法的分类方法与设计范式」）正确放过。
  - ⚠️ **存量 7 个元概念节点仍未处理**（2026-11-15 跑过 `meta_demote`，但库里仍有多条以它们为源的 `prerequisite` 边）→ 留在 `TODO.md §4`。
- **GQ-12 历史噪声清零**：2026-11-16 实测 `123` / `未识别内容片段` / `%_review` / `%（复习）` / u1 的 `test_*` / 「测试节点」**全部查无**（u1 节点数 25 → 17）。由 `refile_nodes.py --delete-junk`（2026-11-15，两次，备份 `_refile_backup/junk_*`，`--restore-junk` 可装回）完成。
- **GQ-25 建图失败/跳过对用户可见**（2026-11-10）：`kb.py::_graph_generated_payload` 把 `failed_docs`/`skipped_docs`/`merged_nodes` 放进 `graph_updated` 通知，`chatStore.handleGraphUpdated` 收到后弹 `ElMessage.warning("N 份资料未入图：…")`（>5 个截断）。顺带把建图结果从"依赖 HTTP 响应"改为"完成即通知"。回归 `tests/test_kb_graph_generate_notify.py`（7 例）+ `chatStore.lifecycle.test.js`「建图结果由 graph_updated 通知收口」6 例。

### 14.3 消息链路 / 知识库任务

- **前端回前台 / 多标签补偿**（2026-11-10）：`chatStore` 挂 `visibilitychange` + `focus` → `fetchPendingNotices()` + `refreshGraph()`；2s 去重窗口、页面 `hidden` 时不拉；**全局只挂一份**监听器并转发给"当前活跃 store 实例"（每实例各挂会让 N 个实例同时拉，去重窗口挡不住），`dispose()` 只摘自己那份。**不重连**（重连归 boot_id 那套）。回归 `chatStore.lifecycle.test.js`「回前台补偿」6 例（含"重建实例不叠加监听器"）。
- **开发态"后端已重载"误报**（2026-11-09）：`GET /api/health` 增进程级 `boot_id`（`main.py::BOOT_ID`，模块导入一次）；前端 `_checkBackendBoot()` 每次 SSE 连上（首连 + 重连）比一次，**只有 id 变了**才发 `BACKEND_RELOADED_EVENT`；`onerror` 不再判定任何事；拿不到 `boot_id` 什么都不做（宁可漏一次刷新，不可误报 —— 误报会冲掉用户正在填的作答）。回归 `chatStore.lifecycle.test.js`（3 例）+ `backend/tests/test_health_boot_id.py`（3 例）。
- **SSE 通道分层（原 [P0]）**：`event_bus.subscribe(user_id=…)` 是 **per-connection fan-out**（每次调用新建队列进 `_user_queues[user_id]` 这个 set，`event_bus.py:156-163` 注释写明旧语义已废弃），`broadcast` / `notify` 各投每队列一份 → "多标签页抢事件"这个前提作废。回归 `tests/test_event_bus.py:214`、`:228`。剩余物已收窄为"对话列表跨端无通知"与"事件类型过窄"两条（仍留在 `TODO.md §2`）。
- **§11.4-B 建图断连保活 + 状态可查**（2026-11-15）：`generate_graph` 改 `runtime.spawn` 后台化（入队即返回 `{"status":"accepted"}`，客户端断连不再取消 handler；互斥表 `_GRAPH_INFLIGHT: dict[int, str]` 同时记学科名供状态查询）；新增 `GET /kb/graph/status`（`building` / `subject` / `progress{filled,total,percent}`）；前端 `chatStore.init()` 末尾 `restoreGraphBuildStatus()` 接回"生成中"，并开 `startGraphStatusPoll()`（`GRAPH_STATUS_POLL_MS = 3000`，常量在 store 不在 KbPanel），完成判定仍由 `graph_updated` 通知收口，`dispose()` 里停表。UI = `KbPanel.vue` 的 `.kb-gen-progress`（`el-progress` + "已补全 N / M 个知识点"）—— **必须给绝对数**，因为进度是增量补全口径、百分比不从 0 开始。回归 `test_kb_graph_guard.py`(9) + `test_kb_graph_status.py`(6) + `test_kb_graph_generate_notify.py`(7) + 前端「刷新后恢复生成中」4 例 + 「进度轮询」3 例。
- **§11.4-A「只修显示」作废**（2026-11-15）：原方案是 `kbTasks` 写 localStorage 重放，但任务其实已经被取消 → 只会重放一条**过时**进度。改选状态查询路线（更准），localStorage 重放不再做。
- **建图过程可见（2026-11-16，用户提"生成节点要有个进度条 / 视觉反馈，不至于很无聊"）**：进度条本已有（`KbPanel.vue` 的 `.kb-gen-progress` + `GET /kb/graph/status` 3 秒轮询），缺的是**画布本身在建图中完全静止** —— 节点要等收尾的 `graph_updated` 才一次性铺上去。三处补丁：
  ① `chatStore.startGraphStatusPoll()` 在 `total`/`filled` **变化**时调 `refreshGraph()`（不是 `fetchGraph` —— 首次建图时 `currentSubject` 还是 null，要靠 refreshGraph → fetchSubjects → ensureSubjectSelected 把新学科自动选中），新节点由内核既有的 320ms "花开"入场过渡长出来；数字没变不拉（3 秒一拍，多数拍子没变化）；用户正在看**别的**学科时不动作（`watchingOther`），不掀掉眼前的图。
  ② 骨架态可视化：`content_status === 'skeleton'` 画**空心**、翻 `filled` 才填色。三种画法各自落地 —— 力导向 `forceGraphEngine.js`（`.node-body` fill → `none`，骨架描边加粗到 1.6 / 不透明度 0.9 否则空心圈看不见）、思维导图 `GraphMindmap.vue`（`g.is-skeleton` + `.mm-dot` fill none）、大括号 `GraphBraceNode.vue`（`.bn-row.is-skeleton` 空心点）；`graphTree.js::leaf()` 增 `contentStatus` 字段供两个树视图读。
  ③ ⚠️ **`content_status` 必须进 `graphFingerprint`**：建图中节点 id 与 mastery 都不变、只有它翻档，漏了它重拉画布会被指纹判"没变"直接 return，表现为"节点永远是空心的"。
  另有画布内常驻进度条 `.build-progress-chip`（`HomeView.vue`，底部居中、让开右栏宽度、呼吸点动画）—— 右栏那条收起/切页就看不见，而建图是分钟级后台任务。
  回归：`chatStore.lifecycle.test.js` +4 例（推进才重拉 / 数字没变不拉 / 看别的学科不动作）、`graphTree.test.js` +1 例（保留 contentStatus）。前端 122 passed（7 files）、`vite build` 通过。

### 14.4 技术债（2026-11-04 一批 + 一条跨端决策）

- **节流器内存只增不减 + `get_retry_after` 凭空建条目**：`get_retry_after` 改只读 `.get()`；新增 `sweep(now=None) -> int` 全量回收，由 `is_allowed` 按 `sweep_interval`（默认 2×窗口）机会性触发。⚠️ `_last_sweep` 初值必须是 0 而非 `time.time()`（否则打桩到更早时刻的调用方永不触发清理）。回归 `tests/test_rate_limiter_sweep.py`（6 例）+ `test_rate_limiter.py`（12 例）。
- **常驻 SSE 无心跳 → nginx 300s 必掐断**：`event_bus.subscribe()` 在 `q.get()` 外包 `asyncio.wait_for(timeout=SSE_HEARTBEAT_SECONDS)`，超时 yield 注释帧 `: keepalive`（不以 `data:` 开头 → EventSource 忽略）。常量 **15s**，必须小于反代读超时。回归 `tests/test_event_bus_heartbeat.py`（6 例，含"取消时仍摘队列减计数"），两个取帧 helper 加 `_parse_frame()` 跳过注释帧。
- **`/docs`、`/redoc`、`/openapi.json` 生产默认公开**：新增 `Settings.api_docs_enabled`（**默认 False**），`main.py` 按开关传三个 URL；`/api/health` 不受影响。回归 `tests/test_api_docs_exposure.py`（6 例，其中一条**读 `main.py` 源码**断言开关真接上了，防"测试自己建了个正确的 app"）。
- **`conversations` 零索引 + 墓碑永不回收**：两个 partial index（`idx_conversations_user_updated`、`idx_conversations_tombstones`）由 `_ensure_indexes()` 给老库就地补；新增模块级 `purge_tombstones()`（默认保留 90 天）挂进 `main.py::_JOBS`。⚠️ 墓碑不能删太早（离线设备下次 `sync` 会把对话复活）；库不存在时返 0、**不得建目录**。回归 `tests/test_conversation_indexes.py`（13 例，含 `EXPLAIN QUERY PLAN` 断言真走索引）。
- **SQLite 连接参数三套标准**：`collector.db` / `quiz.db` / rag 向量库统一 `timeout=10.0` + `PRAGMA busy_timeout=10000`。回归 `tests/test_sqlite_connection_contract.py`（7 例）。⚠️ 口径统一 ≠ loop 阻塞问题解决 —— 采集侧那半仍留在 `TODO.md §8`。
- **弃用 API**：`datetime.utcnow()` → `datetime.now(timezone.utc)`（编码结果不变）；pydantic `.dict()` → `.model_dump()`；`HTTP_422_UNPROCESSABLE_ENTITY` → `HTTP_422_UNPROCESSABLE_CONTENT`。回归 `tests/test_no_deprecated_api.py`（5 例，用 **tokenize** 扫源码而非正则 —— docstring 里的 `Question.dict()` 是文档不是代码）。
- **`scripts/backup.sh` 补建**：`docs/运维部署/运维_生产上线与日常运营指南.md` 三处 + cron 示例都引用它，仓库原先没有。要点：停容器 → `tar czf` → 启动，`trap … EXIT` + `STOPPED` 幂等 flag 保证**打包失败也一定拉起服务**；保留日备 7 天 / 周备 4 周。⚠️ `[ x ] && continue` 在判 false 时返回 1 会触发 `set -e`；`set -o pipefail` 下 `grep` 无匹配也要 `|| true`；不能排除 whoosh 段 / `*.db` / `-wal`。回归 `tests/test_backup_script.py`（10 例 + `bash -n`）。
- **CI 只跑后端套件**：新增 `backend-admin-tests.yml`（**独立 job、独立一次 pytest** —— 两边包名都叫 `app`，合跑必 `ModuleNotFoundError`）与 `frontend-tests.yml`（`npm test` + `npm run build`，另加 `frontend-admin` 构建）。
- **`conversations` 内嵌 tools/thinking 去留 + run 与会话无关联键**（2026-10-31 定）：`agent_runs.conv_id` 已加（可空，老库自动补列）；assistant 的 `tools` 摘要由服务端从 run 的 `rounds` 派生后落库（与 `tool_start`/`tool_result` 同形，前端零改动），`thinking` **不落**（体积大、刷新后本就不该重放）。权威口径 = `AGENTS.md §1`「对话历史的真相在服务端」。墓碑 GC 那半见上。

### 14.5 出题模块重构（题量由内容决定 + 考点蓝图）

> 起因：用户「重新设计出题的部分，目前感觉硬编码和不完善的部分居多」+「出几道题？」。范围限定**独立出题页**，对话内 `chat_quiz` 不动。

**根因（实测 `data/quiz/5/quiz.db`，34 题 / 12 题集）**：题量是四个数字碰巧一致 —— `QuizView.vue:51` `ref(5)` / 下拉 `[3,5,8,10]` / `schema.py` `Field(5, ge=1, le=20)` / `generator.py:41` `QUIZ_MAX_TOPUP_ROUNDS = 2`。最后一条最露骨：要 20 道时补题额度也只有 8 道，凑不满由**常量**决定而非内容。连带：机械运动 17377 字只出过 4 题（1 题/300 字可出 57 道 = 覆盖率 7%）、123 个知识点仅 20 个出过题、难度 34/34 全 medium、分值 34/34 全 10 分、单选答案位置 A6/B14/C2/**D0**（卡方 20.91 > 7.815）。最严重一条：`knowledge_point` 20 个唯一值里 **10 个是 LLM 现编的自由文本名**，`chat_grade.resolve_node_id` 两条路（`get_node` / `find_node_by_name`）都不命中 → 答对也**不涨掌握度且无任何日志**。

**新增四个模块**（`backend/app/core/quiz/`）：
- `budget.py`：`QUIZ_CHARS_PER_QUESTION = 350`（Questgen 60 词≈90 汉字取 4 倍富余）、`QUIZ_MIN_QUESTIONS = 3`、`QUIZ_MAX_QUESTIONS = 15`；`auto_question_count` 取 `max(content_cap, section_floor)` 再夹到 [3,15] —— **max 不是 min，覆盖率优先**（8 节/400 字 → 8 道）。`CountBudget.reason` 三分支（撞上限 / 每节至少一道 / 按正文长度）。
- `blueprint.py`：`CandidatePoint` / `Slot` / `Blueprint`。`build_candidates` 有小节按小节（`cover_key = "{node_id}/{section_id}"`）、否则正文 `## ` 小标题、否则 `#body`；**去重口径是 cover_key 不是 title**。`_bloom_sequence` 按 30/20/20/10/10/10 配比**交错排列**（不是先 5 个识记再 3 个理解）。`allocate` 按 count 降序 + 全局 cursor 轮转做覆盖优先；`shortfall = sum(max(0, n-1) for n in used.values())`（键是 `(cover_key, type)`）。
- `planning.py`：`load_candidates(user_id, graph_node_id)` 经 `KnowledgeGraph.get_node`（带 user_id 护栏）→ `list_sections` + `node_content_text` + 逐节 `read_section`。节点不存在 / 非本用户 → 返 None 且只 `logger.info`，**不抛异常也不回显别人规模**（防图谱规模探测）。
- `schema.py`：`QUIZ_TYPES` / `OPTION_TYPES` / `BLOOM_LEVELS` / `DEFAULT_BLOOM_MIX` / `DEFAULT_POINTS`（简答 20 其余 10）、`PlanRow`（`extra="forbid"`）、`PlanPreviewRequest`、`QuizGenerateRequest` 加 `question_plan` / `count_mode` / `strict` / `missing_cover_keys`。

**端点**：`POST /quiz/plan/preview`（**纯计算**，不落库不调 LLM）；`POST /quiz/generate` 有 plan 时走蓝图路径。

**六条关键决定**（都是踩过的坑）：
1. **互斥判定用 `"question_types" in self.model_fields_set`，不能用 `is not None`** —— `default_factory` 永远非 None，写成后者会让新路径必然报"互斥"。
2. `effective_plan` 在无 plan 时返回 `[]`，**不在老路径上等分** —— 保老路径逐字节等价（`test_old_path_response_shape_unchanged` 断言响应键集合，多一个就红）。
3. **strict 闸门必须在 `save_questions` 之前** —— 先落库再删会留下"秒出秒删"的空题集，而且删不干净（attempts 可能已挂上）。
4. 有 `graph_node_id` 时**逐题把 `knowledge_point` 覆盖成真节点 id**（这是修"答对不涨掌握度"的那一刀）；无锚点则保留模型名并 `logger.warning`。
5. 补题轮数按 `unfilled` 收敛（`progressed` 守卫防死循环），**老常量 `QUIZ_MAX_TOPUP_ROUNDS` 保留不动**，测试专门断言它仍是 2。
6. `missing_cover_keys` 只在 `question_plan` 路径下有效，老路径传了**显式报错**而不是静默忽略 —— 静默忽略正是这个字段原本踩的坑。

**收尾时我修掉的真缺陷**：
- `_to_slot_questions` 原对"带 cover_key 但本批没这个键"的题按题型兜底塞槽 → 把讲 A 考点的题贴上 B 考点落库，**覆盖率报表从此不可信**（而覆盖率正是这次要修的东西）。改为只有空串才兜底，带错键直接丢弃留给补题。
- `_HEADING_NOISE_RE` 的分隔符写成可选 → `"3D 打印"` 被剥成 `"D 打印"`、`"2048 游戏"` 剥成 `"游戏"`。改为要求"序号 + 分隔符"同时出现。
- `shortfall` 原按 `len(cands) × 题型数` 算容量，`[single×4, judge×1]` 配 3 候选算出 0。改按 `(cover_key, type)` 计数。
- 删掉两个"声明未用"的常量（`QUIZ_SECTION_MIN_CHARS`、`MIN_CANDIDATES_PER_SLOT`）。

**实测收益（uid=5，`mechanical_motion`）**：preview 返回 `count=15 / capped=True / 8 小节 / 23522 字`，`reason="15 道（已到上限）· 依据：8 个小节、正文 23522 字"`（旧版固定 5 道）；15 道规格（单7/多2/判4/填2）→ `shortfall=0`、**覆盖 8/8 考点**；无锚点与"节点不存在"两种请求的响应**逐字段相同**（防探测已验证 `identical: True`）。

**验收**：后端 `2336 passed + 1 skipped / 7 deselected`（118.01s，本轮 +6 例；新增 `test_quiz_budget.py` 18 + `test_quiz_blueprint.py` 24 + `test_quiz_plan_preview.py` 21 + `test_quiz_store_plan_columns.py` + `test_inspect_quiz_quality.py` 28）、前端 `127 passed（8 files）`（9.62s，含 `quizPlan.test.js` 15 + `QuizView.plan.test.js` 10）。只读体检脚本 `backend/scripts/inspect_quiz_quality.py`（752 行，默认 `--user N`，`-wal`/`-journal` 为空时用 `immutable=1` 打开，有 `test_run_does_not_write_anything` 锁住）。

**仍未做**：真实 `/quiz/generate` 端到端（要真调 LLM 落库）、`quiz_batches` 表、P2 质量闸门（单选答案位置分布 / 多选全对 / 选项数一致）、plan 模式 preview 的 `reason` 文案仍是题量预算口径。

**订正一处过时记录**：本文 §11「未做（明确记录）」曾写"`get_current_user` 不校验 `users.status`（禁用账号旧 token 仍可用至过期）"—— **2026-11-16 已修**（提交 `4f5c56c`，`core/auth.py::_ensure_user_active`：token 的 uid 必须真实存在于 `users` 且未 `disabled`，否则 401 `[E-AUTH-003]`；`status` 为 NULL/空串的老库行按正常处理）。该条已不再是欠账。
