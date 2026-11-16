# AI Tutor 待办总表（唯一 TODO）

> 定位：本项目**唯一**的未完成清单。2026-10-11 由原五份合并 ——
> `TODO.md` / `TODO_Graph_Quality.md` / `TODO_Context.md` / `TODO_Collector.md` / `TODO_Assessment.md`（后四份已删除）。
> **✅ 已完成项一律搬 `done.md`**（只追加、不重写）；本文件只留未勾选项，状态**按代码实测校准**（不按文档印象）。
> 比赛总纲 / 时间线 / 赛道 → `docs/比赛/COMPETITION.md`；模块 SSOT 与文档路由 → `AGENTS.md §5`。
> 标记约定：`[改]` 改现有文件 ／ `[新文件]` ／ `[复用]` ／ `[需API]` 需真实外部服务 ／ `[待议]` 需用户先定。

---

## 0. 开发进度快照（2026-11-12 全量复核，2026-11-16 复核校准）

> 本节是**唯一**的"现在到哪了"入口。下列数字全部为当日实测（不是文档转述），复跑命令见每项括号。
> 复核方式：对 §1~§13 的**每一条**未勾选项做了代码级取证（grep/read），判定为
> `已完成 / 部分完成 / 仍未做 / 前提过时` 四档 —— 只记"部分完成"里的**差什么**。

### 0.1 数字基线（可复跑）

| 项 | 实测 | 复跑方式 |
|---|---|---|
| 后端测试 | **2231 passed / 1 skipped / 7 deselected**（192.55s，2026-11-16） | `backend/venv/Scripts/python.exe -m pytest backend/tests -q -m "not llm_api"`（基线口径唯一维护处 = `AGENTS.md §7`，本节不手工跟改） |
| 前端测试 | **84 passed / 5 files**（2026-11-16） | `cd frontend && npm test` |
| 运维后台测试 | **43 passed** | 单独一次 pytest 调用（两边包名都叫 `app`，合跑必 `ModuleNotFoundError`） |
| 后端代码量 | `backend/app` **31,686 行 / 174 个测试文件**（2026-11-12 快照，不随代码更新） | — |
| 前端代码量 | `frontend/src` **19,567 行**（不含 `.test.`，同日快照） | — |
| 后端分层 | `core/` 13 个模块 + `api/v1` 11 个路由文件 | — |
| 线上部署 | `47.98.160.194` 跑 `6316d36`（2026-10-22），**至少落后 66 个提交**（本地已到 `211c3e2`） | 见 §12 |

### 0.2 十条主线的一句话状态

| 主线 | 状态 | 关键证据 |
|---|---|---|
| 数据层 / 存储 | ✅ 收口 | 单一 `DATA_ROOT`；`records.py` 统一记录型库；三处 SQLite 口径统一 |
| Agent 内核 | ✅ 稳定 | loop + TurnPlan + 工具注册表 + 双记录；本轮 2231 例全绿 |
| 知识图谱 | ✅ 骨架完成，质量项开始真跑 | GQ-4/7/11/12/18/19/25 已落地（同名组 = 0、测试噪声 = 0、元概念入库拦截）；GQ-8/9（跨学科待审）/10/13/14/15/20 仍未 |
| 对话 / SSE | ✅ 事件链路已收口 | 请求内事件走私有队列；建图完成走 `graph_updated` 通知；回前台补偿已做 |
| 知识库 / RAG | ✅ 可用 | 小节化 + 索引收口 + 空范围修复 + 视频导入队列 |
| 主体性（架构债务 §3） | 🚧 **一半** | `learning_sessions` 表 + `teaching/policy.decide()` 已落地；**提示词里 9 处 ⛔ 规则未消**，验收判据 0/4 |
| 一站式学习（§10） | 🚧 缺一条线 | 部件齐、`/learn/start` 与 `learn_progress` 全空 |
| 评价体系（§7） | ⬜ 未启动 | `core/assessment/` 目录都不存在（自述"非本人职责"） |
| 教育采集 | 🚧 适配器齐、**入口缺** | `dataset_quiz`/`web_page` adapter 已实现且测试全绿，但**零生产调用方** |
| Android 客户端 | 🚧 在跑（未合入 main） | 分支 `feat/android-chat`：**72 个 `.kt`**，登录端到端 + 对话流 + Markdown/LaTeX + 会话抽屉；图谱/出题/进度仍是占位页 |

### 0.3 ⚠️ 三件必须先知道的事

1. **§11.2 的前端一档（分支 `feat/kb-task-progress`）已丢失，且不再按原方案重写。**
   2026-11-12 实测：`git stash list` 只有一条 2026-09-28 的 `wip-before-pull-...`，其
   untracked 部分与 `kbTaskProgress.js` 无关；`main` / `origin/main` / 所有 remote 分支 /
   全部 dangling commit 里都**没有** `kbTaskProgress.js` / `KbPanel.tasks.test.js`。
   2026-11-15 改走"状态查询"路线（`GET /kb/graph/status` + 轮询，见 `done.md` §14.3）后，
   那 8 个文件对**建图**这部分不再需要；§11.2 仅作历史存档。别再去 `git stash pop`。
2. **服务器停在 2026-10-22 的代码上（2026-11-12 测得落后 66 个提交，现在更多）。** 它不会自动 `git pull` ——
   reflog 显示只手工 merge 过 4 次。10-22 之后的产出一律没上线。更新步骤见 §12。
3. **Android 客户端已并入本表 §13。** `AGENTS.md §5` 里"52 个 `.kt` / 49 文件"的数字仍过时（2026-11-16 实测 **72 个 `.kt`**），以 §13 为准。

---

## 1. 工程化收尾（P0/P1/P2）

### P0 — 收尾就绪（演示不翻车 + 材料可信）

- [ ] **OI-wiki 真网联调**：`search("数据结构与算法")` 应返回 ds/算法基础板块候选、fetch 首页正文入库无 MkDocs 残留语法（普通联网即可验，无需 DASHSCOPE）——`[待议]`（2026-09-05 暂缓，待用户定时间）
  - 2026-11-12 取证：适配器已实现（`core/collector/adapters/oiwiki.py:91`：mkdocs.yml nav 发现 + raw md 抓取 + 清理），`tests/test_oiwiki_adapter.py` 17 例**全 mock 离线**（文件头自述）→ 真网仍未跑。
- [ ] **演示数据 + 脚本**：填充 1 个完整学科图谱（15-20 节点 + prerequisite 边）；写 2-3 条预演对话路径
  - 2026-11-16 取证：`backend/scripts` 仍**无一个**建演示图谱/预演对话的脚本（只有 `eval_*` / `inspect_*` / `seed_collector` 一类）；库里 user 1 现为 **17 节点**（2026-11-12 是 25 节点，期间清过垃圾节点）→ 连"15-20 节点"都不够，且**不可重现**。这是 P0 里唯一"没有代码可复用"的活。

### P1 — 工程化补强（评委可问项）

- [ ] **Prompt 笔记优化**：教学后主动 `update_node_content` / `add_knowledge_node` 记笔记（提示词层，未动）
  - 2026-11-12 取证：0 进度。`data/prompts/` 只有 `system_prompt_common.j2`（64 行），"笔记 / update_node_content / 记下" 零命中；反而 `agent_tools/tools/update_node_content.py:25-26` 的 GUIDANCE 写着"属于须先问操作，不要直接改" → **先定调性再动提示词**，否则两处互相打脸。
- [ ] **建节点收口 · 收尾项**（写路径部分 2026-09-15 已闭环）：
  - [ ] **嵌入语义去重**（栈/堆栈 这类同名不同字）：Agent 热路径上要多付一次嵌入 + LLM 二次确认（嵌入已恢复，可量化收益后再定）；实现路径 = 复用 `graph_generator._find_dedup_candidates` + `_confirm_synonyms`
    - 2026-11-12 取证：`knowledge_writer.py:59-61` 的 ponytail 注释明写"只做字符串层同名，不做嵌入语义去重"。⚠️ 该注释仍称"当前嵌入 API 欠费"，与本条"嵌入已恢复"**已经矛盾** —— 动这条时顺手校准注释。
  - [ ] 权限守卫：**新建路径本就不触发** `_guard_human_content`，仅当将来新增"AI 覆盖已有节点"入口时才需要 —— 那时走 `update_node_content(caller="ai")` 即可
- [ ] **出题非思考档的质量抽样 A/B**（真实 key）：2026-11-15 已把 `_generate_batch` 的 `thinking` 关掉（35\~39s → 6\~8s，见 `done.md` §14.1），但**题目质量是否受影响从未验证** —— 同一节点同一参数，改前/改后各抽 10\~20 题人工比对（考点评分点是否漏、干扰项是否仍"错得合理"）。这是那条唯一剩下的半句。

### P2 — 功能路线图（比赛可选项 / 有真实 key 后）

- [ ] **RAG 精排（Cross-Encoder）**：hybrid_search 后接 rerank，量化收益（须先有真实 embedding 灌库）
- [x] **出题进阶**：参数化母题模板、题目知识关联回填图谱
  - 2026-11-16 **已完成（未提交，工作区）**：`core/quiz/budget.py`（题量预算，`QUIZ_CHARS_PER_QUESTION=350` / [3,15]）、`blueprint.py`（考点槽位 `cover_key` + 轮转分配 + Bloom 交错 + `shortfall`）、`planning.py`（图谱 → 候选考点，节点不存在/非本用户一律按无锚点、不抛错防探测）、`schema.py`（`PlanRow`/`PlanPreviewRequest`/`missing_cover_keys`）、`generator.py`（`_build_slot_prompt` 槽位表格 + 按题型分批 + 按 `unfilled` 收敛补题，**删掉 `QUIZ_MAX_TOPUP_ROUNDS` 那个固定轮数**）、`api/v1/quiz.py`（`POST /quiz/plan/preview` + strict 闸门在落库之前 + 有 `graph_node_id` 时逐题覆盖 `knowledge_point` 修真节点 id）；`questions` 新增 `cover_key`/`bloom_level`/`option_count`/`plan_json` 四列。前端 `QuizView.vue` 三模式（自动/手动/按题型配比）+ `utils/quizPlan.js`。
  - 实测收益：机械运动 8 小节 23522 字 → 自动 15 道（旧固定 5 道）、15 道规格覆盖 8/8 考点、`shortfall=0`；「补齐缺失」只补缺口不重出整卷。
  - 验收：后端 `2336 passed + 1 skipped`、前端 `127 passed`。方案与实施状态见 `docs/教学模块/QUIZ_出题_题量与蓝图_借鉴与方案.md` §九、遗留项见 `QUIZ_出题重构_调研与改进思路.md` §七。
  - **仍未做**：真实 `/quiz/generate` 端到端（要真调 LLM 落库）、`quiz_batches` 表、P2 质量闸门（单选答案位置分布 / 多选全对 / 选项数一致）、plan 模式 preview 的 `reason` 文案仍是题量预算口径。
- [ ] **学习大纲生成 / 主动引导式提问 / 自包含 HTML 复习卡导出**
- [ ] **"AI 同学"多角色讨论 / 白板图谱联动（SVG）**
- [ ] **RAG 引用卡片拖拽窗口**（知识卡片白板，借鉴 OpenMAIC）
- [ ] **对话 Rewind（回退）与 Branch（分叉）** `[改]`（须遵守 P0-1 服务端对话权威，**严禁**前端截断后走迁移口 `POST /conversations` 全量覆盖）：
  - **后端（权威真源）**：`core/conversation_store.py` + `api/v1/conversations.py` 新增命令式端点 `POST /conversations/{id}/rewind`（按 `keep_messages` 在 `_write_txn` 内截断库里 `messages`）与 `POST /conversations/{id}/branch`（在库内从源对话复制前 `keep_messages` 条生成新对话行，不收客户端上传的消息数组）
  - **前端 Store 与视图**：`stores/chatStore.js` 新增 `rewindTo(msgIdx)` / `branchFrom(msgIdx)`（先 abort 在途流 → 调后端接口 → 同步本地离线缓存）；`ChatArea.vue` / `MessageBubble.vue` 提供操作入口
  - **验收**：`backend/tests/test_conversation_authority.py` 断言 rewind 后下一轮 `/chat*` 注入给模型的是截断后的历史，branch 后原对话不变、新对话继承前缀历史
- [ ] **AI 回复底部悬停工具栏扩展动作** `[改]`（本节随 PR 一起提供底座：`MessageBubble.vue::.msg-toolbar` + 复制按钮 + 时间戳 + `toolbarActions`/`extraActions` 扩展口）：
  - **候选扩展项（均纯前端或复用既有权威入口）**：① **TTS 语音朗读**（`window.speechSynthesis` 朗读去 Markdown 后的正文，再点停止）；② **引用追问**（优先取 `window.getSelection()` 划选文本，未划选则截取回复前 60~80 字纯文本，在 `InputArea.vue` 上方渲染可关闭引用条，发送时拼入标准 `user` 消息走 `store.send()`）；③ **头部 `用时 Xs >` 折叠**（记录流式起止时长或汇总 `tools[].result.duration_ms`，与思考过程面板合一）；④ **导出单条回复为 `.md` 笔记**；⑤ **一键针对本条知识点出题**

- [ ] **知识库长任务统一进度与状态可见**（2026-11-08 调研；建图那半 2026-11-15 已落地，剩上传与暂停/续跑）
  > 一句话：上传 / 建图这类"点完要等几十秒到几分钟"的操作，目前**看不到它在干什么**。
  > 调研结论、已落地实现、以及**为什么刷新后原先会归零**（不是显示问题，是任务真被取消了）都在 **§11**，动手前先读那一节。

---

## 2. 架构待决（2026-09-26 对话「没反应」故障复盘）

> 根因：`event_bus` 的 per-user 队列被**两个消费者共享**（对话流 `_consume_agent_events` + 常驻长连接 `/knowledge/events`），
> `text_delta` 被长连接抢走丢弃 → 对话侧拿到空流 → 前端 `onDone('')` 删掉 AI 气泡。
> 已修：对话事件改走本请求私有队列（`core/agent/events.py` + `chat_service._consume_agent_events`），
> 回归 `tests/test_event_bus.py::test_chat_stream_not_starved_by_long_lived_subscriber`。下列是**同源或同症状**的剩余项。

- [ ] **[P1] 对话列表的跨端变更完全无通知**：`api/v1/conversations.py` 全文 `notify`/`broadcast` **零命中** —— 另一台设备改名 / 删除 / 新建对话不产生任何事件。症状与建图那条同族（"后台改了、这边不刷新"），但**根因相反**：不是"推丢了"，而是**压根没推**。
  - 修法方向：会话增删改补 `notify(user_id, ...)` 并发新事件类型；⚠️ `chatStore` 的 SSE 分发是**白名单**，加新类型必须同步改前端，否则静默丢弃。
  - 2026-11-16 复核：仍 0 进度 —— `conversations.py` 6 个端点全无 `notify`/`broadcast`；前端白名单仍未含会话类事件。
- [ ] **[P2] 事件类型过窄**：前端只认 5 类（`graph_updated`/`quiz_ready`/`kb_updated`/`import_progress`/`error`），后端新增类型不改前端就静默丢弃。这是上面那条的实施约束，不是独立 bug。
  - 2026-11-16 复核：`event_bus.py:51-55` 仍是这 5 类；`chatStore.js` 的 `if/elif` 白名单**无 else**。
- [ ] **[P0] 长同步端点的结果必须走通知，不能只靠 HTTP 响应**（建图那处 2026-11-15 已闭环，本项只剩 `sections/generate`）
  - 排查口径：任何**墙钟可能超过 1 分钟**的同步 HTTP 端点都属此列。已修 = `POST /kb/graph/generate`（`runtime.spawn` + `finally` 里 `notify`，见 `done.md` §14.3）。
  - **唯一剩余物**：`POST /knowledge/node/{id}/sections/generate`（`api/v1/knowledge.py:526-553`）仍是 `await SectionGenerator.generate` 后只 `broadcast(:550)` —— 无 `notify`、无 `task_id`、**断连即取消**。
  - 更彻底的方向（未做，属结构性改造）：改成 `POST` 立即返 `task_id` + 前端轮询/订阅进度，见 `docs/上下文工程/上下文工程_调研与差距审计.md:466`。
- [ ] **[P1] 对话调用链（M3）三处收尾**（「真流式」已完成，见 `done.md` §4.5）
  - **思考吃光 `max_tokens` → 长回答可能整段空正文**（优先）：`AGENT_MAX_TOKENS=2000` + 允许思考时二者共享预算 —— 真机探针一次 52.8s 全烧在思考上、`content=0`、`finish=length`
  - `strip_think_tags` 仍是**整段正则**（未做流式状态机）：靠 `reasoning_split=True` 让 content 保持纯净；若某网关忽略该参数，流式正文可能短暂含思考标签（存量兜底仍会清库里的文本）
  - **中间轮文本会实时显示**（有意）：工具轮的过渡语在内容 delta 里，位置先于 tool_calls delta；要隐藏需前端"撤帧"机制
  - 2026-11-16 复核：三条**均未动**。`agent/loop.py:97` 仍是 `AGENT_MAX_TOKENS = 2000` 硬编码、无思考/正文预算拆分；`llm/thinking.py` `strip_think_tags` 仍是整段正则；每轮仍建 `_DeltaPump` 实时推 `TEXT_DELTA`。**建议拆成三条独立小改**（各有独立修法、互不阻塞）。
- [ ] **[P1] 观测性缺口：无法回答"回复到底送到前端没有"**
  - 后端证据齐（`agent_runs` / `debug_log`），**前端零**
  - 落点：一次 run 的**首字节延迟 / 写出事件数 / 写出字节数**（`chat_service` 消费侧计数 → `agent_runs` 或日志）
  - 附带：uvicorn access log 没进 `logs/tutor.log` → "请求到没到后端"只能翻库反推
  - 验收：给定 run_id 能直接看出"发了几条事件、前端有没有拿到"
  - 2026-11-16 复核：**三分之一已做** —— 事件数已闭环（`agent/store.py:54` `events_written` 列 + `loop.py:826` 写入 + `events.py:118` 单一计数口径）；**首字节延迟仍缺**（全库 `first_token`/`ttft`/`first_delta` 零命中）；uvicorn access log 仍只走 root `basicConfig`（未接 `logging_setup.py`）。建议与上面「对话列表跨端通知」同批做（都改事件链路）。
- [ ] **[P2] 双写持久化：两条真相来源没有裁决规则**
  - `syncToBackend` 每次 persist **全量重传所有对话**（O(全部历史) payload）；删除不传播；缺版本向量 → 多设备仍可能漂移（当前是"更新的赢"降级处理，不是真合并）
  - 已修①（2026-09-26）`updated_at` 语义；已修②（2026-10-07）**防覆盖**（`BEGIN IMMEDIATE` 内比较 + 旧快照拒绝写入），回归 `tests/test_conversation_store.py`
  - **仍未做**：删除的跨端传播；真合并（两端同时各加一条消息 → 仍是"新的赢"，另一条丢）
  - 2026-11-12 复核：**前半句前提已过时、后半句仍成立** ——
    删除传播**已做**（`conversation_store.py:419` `soft_delete` / `:429` tombstones / `:437-465` sync 返回 tombstones；前端 `chatStore.js:223-237` `adoptRemote` 按墓碑删本地）；
    "每次 persist 全量重传"也已过时（`persist` 只写 localStorage `chatStore.js:1084-1097`，`sync` 仅 `init` 调一次 `:696`）。
    **真正剩下的只有"真合并"**（两端并发各加一条 → 新的赢，另一条丢）。
- [ ] **[P2] 前端 store 耦合：图谱长连接决定对话可用性**
  - `stores/chatStore.js` 同管对话 + 图谱 + SSE + 同步 + 出题回流；长连接生命周期由 `HomeView.onMounted → store.init()` 决定
  - 本次故障能潜伏很久正因为这个耦合（图谱通道能吞掉对话正文）
  - **待决策**：是否按关注点拆 store；不拆则至少把「**通道只管送到、渲染只有一份**」写成约定
  - 2026-11-16 复核：仍 0 进度，且**文件已长到 1540 行**（`stores/` 下只有 `authStore.js` / `chatStore.js` 两个业务 store）。这条会随每条新增能力继续恶化，建议在 §10 一站式学习开工**之前**定调。

---

## 3. 架构债务：主体性错层 —— "提示词当程序"（2026-10-07 只读审计）

> 由来：一次以「**谁在决定下一步做什么**」为主线的只读审计。结论：**架构里没有主体** ——
> 唯一的发起者是学生的一条消息，唯一的决策者是服务端硬编码状态机，模型只负责把已经定好的
> 下一步**说出口**；"主动 / 自适应"在实现中没有落点。
> 本节是**根因级**债务：上面「架构待决」那几条大多是它的**症状**，先看这里再看那里。
> 全项目问题清单 + 分级重构路线 → `docs/架构/整体架构_问题清单与重构方案.md`；施工计划 → `docs/架构/重构实施计划_可执行清单.md`。

- [ ] **[根因·P0] 主动性放在了错误的层：教学策略 = 提示词散文 + 硬拦 + 兜底**
  - 证据：`_build_learn_block` 实测 **171 行**中文规则（`chat_service.py:551-721`）；项目自述"**提示词里写'禁止'实测无效**"（`done.md`）、"`mark_section_understood` 4 次采样一次没被调"
  - 症状（每加一个"AI 主动"能力，必配一个"AI 不做系统替它做"的兜底）：开场轮协议层硬禁工具（`agent/loop.py:431/468`）、越级记账硬拒（`tools/mark_section_understood.py:95`）、模型没出题后端补一道（`chat_service.py:539-579`）
  - 测试口径失真：`test_learn_mode.py` 47 例里 **10 条**是 `test_block_*`（锁的是"文档里有没有这句话"，不是行为对不对）
  - **待决策**：把教学策略提升为一等对象（会话状态 + 动作集合），还是继续堆规则文案？
  - 2026-11-16 复核：**"抽出判定出口"这一半已做**（`core/teaching/policy.py:123` `decide()` 是唯一策略出口，B2 落地）；**"消除提示词散文"那一半没做** —— `_build_learn_block`（`chat_service.py:551-721`）仍 **171 行 / 9 处 ⛔**（集中在 `:649-686`）。所以本条应改写为"消除 `_build_learn_block` 的规则堆叠"，而不是"要不要抽出策略"。

- [ ] **[P1] 掌握度没有"力"：闭环缺最后一环**
  - 排序只看 prerequisite，**不读 mastery**（`knowledge_graph.py:780`、`:799-813`）；mastery 只在 `:888-894` 决定"推荐指针"
  - 出题 handler 不校验 mastery（`tools/quiz_generate.py:180-287`），只有提示词层一句否决
  - 无复习调度 / 错题队列：全后端唯一常驻循环是每日 GC（`main.py:108-114`）
  - 2026-11-12 复核：**第一条已不成立** —— `kg/paths.py:97` 已改走 `graph_order.order_key`，键 = `(前置未达标, mastery 升序, id)`（`core/graph_order.py:48-57`）→ 排序**已经读 mastery**。
    后两条仍成立：`tools/quiz_generate.py` 全文件 `mastery` **零命中**；`main.py:103` `_JOBS` 仍只有 5 个 GC 任务，`learning/session.py:43` 自述 `review` 未实现。**本条应缩为「出题不校验掌握度 + 无复习调度」。**
  - 结论：**"自适应"目前是单轮启发式，不是跨会话调度**
- [ ] **[P1] 学生的主体性被压缩成"≤12 字同意词"**
  - `core/quiz/consent.py:28` `_QUIZ_CONSENT_MAX_CHARS = 12`，长句一律判"不确定 → 不放行"
  - 顺序被**显式**排除重排（`chat_service.py:270-274`）；画像 `goals` 只当语气参数（`chat_service.py:606-607`），不参与路径 / 选题
  - **待决策**：是否开放「顺序 / 范围 / 目标」三个真正的决策通道？
  - 2026-11-12 复核：仍 0 进度。`consent.py:28` 仍是 `12`；`teaching/policy.py:115` `pick_start` 仍"刻意不按不懂优先重排"；画像 `goals` 仅经 `chat_service.py:792` 注入提示词。
- [ ] **[P1] 系统没有"发起"通道 → 主动必须伪装成学生发言**
  - 前端为开一场学习**伪造一条用户消息**：`send('开始学习「X」')`（`frontend/src/stores/chatStore.js:801`）
  - 通道其实已存在却没被用来推进学习：`quiz_ready` 只往对话塞一张题卡，**不发请求、不推进小节**
  - **待决策**：复用通知通道做"薄弱点题自动送达"，还是引入调度器（间隔复习）？
  - 2026-11-12 复核：仍 0 进度。伪造消息在 `chatStore.js:1040`（行号已漂移）；`quiz_ready` 处理仍只渲题卡（`chatStore.js:849-871`）。
- [ ] **[P2] 叙述与实现缺 SSOT 守卫**
  - "学习模式"这个行为最密集的层**没有设计文档**（`docs/` 全库无"学习模式"，只有 `done.md` §4.4 + `chat_service` 注释）
  - 参照系契约自认"不宜直接宣称图谱是每次调用的参照系"
- [ ] **本节验收（"改完了"的判据）** —— **2026-11-16 复核：0/4 达成**
  - 一次学习推进不再依赖提示词里的 ⛔ 条目：`_build_learn_block` 规则数大幅下降，且 `test_block_forbids_*` 系列是**被删除**而不是被改写
  - "下一步做什么"有单一归属对象，且从它能直接读出：当前节 / 待办动作 / 是否已获学生同意
  - 至少一条"非学生触发"的学习推进在真实链路上可用
  - mastery 进入路径或选题，且有一处调度（错题优先或间隔复习）
  - 实测（2026-11-16）：规则数未降（`chat_service.py:551-721` 仍 171 行 / 9 处 ⛔）；`test_learn_mode.py` 仍有 **9 条** `test_block_*`（从未删除）；无"非学生触发"推进；mastery **已进排序**但**未进选题**、无任何调度。第 2 条半达成（`LearnTurn` 可读出"当前节 + 待办动作"，但"是否已获同意"仍靠 `consent.py` 的 12 字判定）。

---

## 4. 知识图谱生成质量（GQ / KG-D）

> 生成质量专项（粒度 / 去重 / 质控 / 多源增量）。相关 SSOT：`docs/知识图谱/知识图谱_参照系契约.md`（粒度口径）、
> `docs/知识图谱/知识图谱_数据结构与检索通路_评审与改良方案.md`（KG-D 逐项验收）、`docs/知识图谱/知识图谱_外部调研_论文开源与多源融合.md`（L0~L4 完整方案）、
> `backend/app/core/kb/graph_generator.py`（生成实现）。体检脚本 = `backend/scripts/inspect_graph_quality.py`。

### 定树 / 粒度 / 多源增量（GQ）

- [ ] **GQ-8 建图写 `confidence`**：给每个节点打 0–1 置信度，低分节点不参与注入/出题（当前 1.6% 有值）
  - 2026-11-12 复核：列存在（`kg/schema.py:43`），手写/建议入口会写（`knowledge.py:695`、`knowledge_writer.py:137-184`）；**建图路径完全不写** —— `_write_concept_tree` 的 `node_data`（`graph_generator.py:1066-1077`）无 `confidence` 键，全文 0 处 `confidence`。当前库 11/162 ≈ 6.8%。注入/出题也无按置信度过滤。
- [ ] **GQ-9 主题隔离**：启用 `board`（u5 已补 47/123、全库 56，仍非全覆盖）区分书籍来源；跨学科节点标记待审，杜绝 KMP/最小生成树混入强化学习图
  - 2026-11-16 复核：**board 那半已补** —— `backend/scripts/backfill_board.py` 于 2026-11-15 跑过（备份在 `data/knowledge/_refile_backup/board_*.json`），库里 u5 **47/123** 有 board（全库 56 个）。**仍缺**跨学科节点"待审"标记机制（冲突节点现在只是混在同一图里）。
- [ ] **GQ-10 建图 token 消耗落盘**（复用 `token_counter.extract_usage`，写 aggregate + 报告文件）
  - 2026-11-12 复核：仍未做。只有间接归因（`llm/call.py:110` `call_llm(kind=...)` 记账，库里 kind=`kb_graph_extract/kb_graph_fill/kb_section_write/kb_graph_dedupe`）；`aggregate`（`graph_generator.py:1547-1565`）无 token 字段，`_graph_generated_payload` 无 token，`reports/` 无建图成本报告。
- [ ] **GQ-13 深度 A/B 对照**：同一本书跑"浅（现状）"vs"深"，用 `docs/知识图谱/知识图谱_检索与注入_方法与现状.md` §3 的口径比较出题质量与检索命中率
  - 2026-11-12 复核：仍未做。`backend/scripts/smoke_graph_depth.py` 只是**单节点深度小样预览**（`:16` 自述"不代表落库形态"），不做 A/B。
- [ ] **GQ-14 图谱开/关 A/B 重复运行取均值**：2026-09-12 首跑（5 例）与 2026-09-23 重跑（14 例）**结论相反**（+37.5pp vs -2.4pp）→ 该指标不稳定。需锁定模型版本、每组重复 ≥3 次，给出均值与区间才能对外引用
  - 2026-11-12 复核：**脚本已就绪、缺"重复取均值"** —— `backend/scripts/eval_graph_injection.py` 有 off/on/slice 三组 + 规则指标（`:493-505` 参数表），报告在 `backend/eval_data/graph_injection_report_20260912.json` / `graph_injection_report.json`；但参数表**无 `--repeat`**、无均值与区间字段。→ 只剩"加重复次数 + 出均值/区间"这一小改。
- [ ] **GQ-15 增强项（幂等键）**：`(user_id, subject, 文件指纹 hash)` → 命中则提示"这批资料已建过，是否只补增量"（per-user 建图互斥已落地 2026-09-26）
  - 2026-11-12 复核：**换了一种实现、效果等价** —— 无 `file_hash` 登记表，但建图 409 互斥（`kb.py:530`）+ 资料级 `doc_node_marks` 幂等（`graph_generator.py:1619-1625`，已建完即跳过）。缺的只是"提示用户"那一步（现在静默跳过）。
- [ ] **GQ-20 合并决策落表 `merge_decisions`** 🟠 P1：`(kept_id, merged_id, score, evidence, decided_by, ts)` → 支持**回滚**与人工复核
  - 2026-11-12 复核：仍未做 —— `sqlite_master` 无此表，全库仅设计文档（`docs/知识图谱/知识图谱_外部调研_论文开源与多源融合.md:151/161/163`）提到。
- [ ] **GQ-26 溯源粒度：从「章节标题」升到「切片/段落」** 🟠 P2（暂缓）：现状 `chunk_id` 恒为 `None`（索引侧 `doc_chunks` 与建图侧 `collect_units` 是两套独立切分，边界不共享）。三条升级路按用途选一条：① 对齐切分体系（细粒度覆盖统计/冲突裁决）② 加引文（`part_index` + 片段前 ~80 字，人工核对）③ 检索命中记账进当轮 `agent_runs` 证据（回溯 AI 回答依据）

### 数据结构与检索通路（KG-D）

- [ ] **KG-D7~D11（P1 剩余）**：~~`parent_id/path` 层级~~（主题层 2026-09-27 整体下线，**不做层级**）；`sources` 溯源 → **GQ-18 ✅**；按 relation 分策略扩跳；跨源 RRF 融合。**KG-D11 结构化选片 ✅ 2026-10-11 落地**（改用「全量骨架常驻 + 焦点 ±2 跳邻域带摘要」，见 `done.md` / 参照系契约 §4.2）
  - 2026-11-12 复核：剩两项 —— 按 relation 分策略扩跳（`graph_middleware.py:116` `_prereq_pairs` 只认 `prerequisite`）、跨源 RRF 融合（`rrf_fuse` 只用于 `rag_pipeline` 的多 query 融合，非跨源/跨图谱）。
- [ ] **KG-D12~D15（P2）**：FTS5、`confidence` 赋能或删列、向量改 BLOB、`quiz.questions.node_id` 改名
  - 2026-11-12 复核：**四项全部未动** —— D12 无 FTS5（`core/kg` 无 `MATCH`/virtual table，只有 LIKE + whoosh 覆盖 kb/rag）；D13 `confidence` 既未赋能也未删列；D14 向量仍 JSON TEXT（`rag/vector_store.py:50`、`kb/doc_vector_store.py:48`），无嵌入模型/维度记录；D15 `questions.node_id` 仍叫 `node_id` 且是 `INTEGER`（`quiz/quiz_store.py:47`）。

---

## 5. 上下文工程（预算 / 裁剪 / 预估）

> 配额 SSOT = `docs/上下文工程/上下文工程_预算框架.md`（**B=48K**，S1–S9 段位，让位顺序 历史→检索→图谱→画像）。
> 现状审计 + 论文依据 → `docs/上下文工程/上下文工程_调研与差距审计.md`；代码结构 → `backend/app/core/agent/README.md`。
> 探针（**改前先跑、改后对比**）：`backend/venv/Scripts/python.exe reports/probe_context_budget.py`（`reports/` 在 `.gitignore` 里，新克隆无此脚本）。
> 铁律：**不要动其他模块**；复用已有组件；改动必须可机器复核；验收一律可离线跑。

### 未完成项

- [ ] **P2-① 运行时分段记账** `[改]`
  - 现状只有静态探针；框架不变量 B-1~B-7 需要运行时可见
  - 落点：`agent/store.py` 的 `agent_runs` 表（**扩展字段，不新建表；加列必须写进 `store._COLUMN_MIGRATIONS`**）+ `agent/loop.py` 落库处；`chat_service` 把分段体量传进去
  - 验收：`GET /agent/runs/{run_id}` 能看到该次 run 的各段 token；跑 20 轮对话可取 P95
  - 2026-11-12 复核：仍未做。`agent_runs` 只有总量（`agent/store.py:68` `_SCHEMA`、`:51-58` `_COLUMN_MIGRATIONS` 仅 `token_estimate`/`events_written`/`conv_id`）；`loop._persist_run:698-720` 不传任何分段；`core/agent/*` 全文无 `segment`/`seg_tokens`/`breakdown`。
- [ ] **P2-② 用实测拐点替换文献值** `[需API]`
  - 框架 §1.2 的 S6/S7 = 2K、S8 = 21K 目前是"文献曲线 + 体量估算"，**不是本项目实测**
  - 做法：固定的 20 轮教学对话，扫 S6/S7 的 top_k 与 S8 的轮数，按正确性/成本画曲线取拐点
  - 2026-11-12 复核：仍未做。`docs/上下文工程/上下文工程_预算框架.md` §1.2 仍全部标【论文】/【判断】，无实测回填。
  - 验收：出"段配额 vs 指标"对照表，回填框架 §1.2 并升 v3

### 待决策（需用户先定）

| # | 事项 | 影响 |
|---|---|---|
| D-1 | **S6/S7 的 top_k 与去重规则**（本轮只做了**体量截断**，策略未动）：S6 是否跳过已出现在 S4 的节点？跨源融合配额怎么分？是否需要"检索段总开关"？ | `_build_retrieval_context` 的上层策略 |
| D-3 | S1 预留定 3K 后，`agent/loop.py` 的 `_chat_once` **max_tokens 要不要从 2000 提到 3000**？（M3 思考与正文共享预算，偏低会导致空回复走重试） | 回复质量 vs 成本 |
| D-4 | 是否给"固定段超线"配**硬失败**（拒绝发请求）而非"先降级、再 error 照发"？ | 严格度 |
| D-5 | **S4 图谱注入已常年顶到上限**（`GRAPH_INJECT_MAX_CHARS=12000` 下反复走降级阶梯）：调 S4 目标（6K）/上限，还是维持"降级保底"？ | 决定**还能不能再加新能力** —— 任何新固定段都会先挤掉图谱 |

### 待核验（外部信息，做之前先确认）

- **Prompt 缓存**：缓存作用域（全局/账号级/节点级）、真实 TTL、`tool_choice="none"` 支持度（MiniMax 有自动前缀缓存，实测命中率仅 ≈15%，见 `docs/上下文工程/上下文工程_Prompt缓存命中率_调研与优化方案.md`）
- **M3 在 48K 处的真实有效长度**：§2.1 的"多数模型有效 < 50%"是**别的模型**的结论；48K 这个数需要本项目自己的数据背书（P2-② 可顺带做）
- **Anthropic Agent Skills 官方页正文**（当前只取到二手整理页）→ 影响 S3 的渐进披露细节
- **P1-④ query 双端放置的 A/B**：本轮按论文（Lost in the Middle）落地，**尚未在本项目数据集上验证**（框架 §4.4 要求"唯一允许的注入顺序改动，必须有 A/B"）

---

## 6. 教育资源采集（Collector）

> 设计依据：`docs/教育资料采集/教育资料采集模块_设计讨论.md`（决策 #1-25 + §11 里程碑）。
> **Batch 1（B1.1-B1.8）与 Batch 2（B2.1-B2.5）已完成，见 `done.md` §7；本文件只留未完成项。**
> 铁律：**去耦合、独立测试（尽量）、复用已有组件**。`[需API]` 必须真实网络/外部服务才能验收，其余一律 mock 可测。

### 待 API / 真网验证（B1/B2 收尾）

- [ ] **B1.6 真实嵌入灌库**：`venv/Scripts/python.exe scripts/seed_collector.py --user <演示账号> --embed api`，40 词条入库后 `stats` 正常，替换 mock 哈希向量
  - 2026-11-12 复核：**脚本就绪、从未在演示账号上跑过** —— `scripts/seed_collector.py:120-124` 有 `--user/--subject/--embed mock|api`，40 篇种子齐备（`data/collector/seed/dsa/*.md`）；但 `data/kb/5` 的 33 个文件与种子 **0 命中**（唯一命中是 `data/kb/1` 的「二分查找.md」）。`data/kb/5/rag.db` 2890 chunks 已是 1024 维真嵌入 → **"替换 mock 向量"的验收口径从未正式执行过**。
- [ ] **Batch 1 端到端对话引用**：灌库后对话中挂载「自动采集」目录提问 DSA 概念 → 回答带 KB 引用（先完成上一条）
  - 2026-11-12 复核：未做（依赖上条）。
- [ ] **B2.1 OI-wiki 真网联调**（同 §1 P0 待议项）—— 同 §1 取证，mock 全绿、真网未跑。

### Batch 3 — 题库导入 + web_page 漏斗 + 来源统计

- [ ] **B3.1 结构化题库导入** `[新文件]` `adapters/dataset_quiz.py`
  - 读取开放数据集 JSON（如 CMMLU）→ 映射 `Question` schema → `quiz_store.save_questions`
  - subject/knowledge_point 来自数据集字段；测试 `test_dataset_quiz_adapter.py`
  - **预研项：存储配额/TTL**（入库批量导入**之前**必须完成，防百万题撑爆磁盘）：`storage_quota` 配置 + 超限告警
  - 2026-11-12 复核：**代码已完成、缺入口** —— `adapters/dataset_quiz.py:96` `import_from_json` + 配额（`quiz/quota.py`，`settings.quiz_storage_quota=50_000`），`test_dataset_quiz_adapter.py` 7 例。但**零生产调用方**（只有测试调用），无 API/脚本入口 → 用户点不到。
- [ ] **B3.2 通用网页正文抓取** `[新文件]` `adapters/web_page.py`
  - 站点白名单（个人模式）+ `trafilatura` 主用 / `readability-lxml` 后备；`httpx` 下载 → 正文抽取 → 转 MD 入库（license 由站点映射表给定，默认 L2）；测试 `test_web_page_adapter.py`
  - 2026-11-12 复核：**代码已完成、缺入口** —— `adapters/web_page.py` 已实现+注册，`requirements.txt:37-38` 已有 `trafilatura`/`readability-lxml`，`test_web_page_adapter.py` **21 例**。但 `candidate_from_url:167` **无任何调用方**。
- [ ] **B3.4 未做部分**：采集页资源列表条目入口 + 采集前 URL 预览（P0）—— 方案见 `docs/教育资料采集/采集_网页正文与开放来源_实施方案.md` §2.5
  - 2026-11-12 复核：仍未做。`CollectorView.vue` 只有「搜索→勾选→任务」三步（模板 300-390 行），无 URL 输入、无 preview dialog（全文无 `preview`）；`api/v1/collector.py` 仅 6 个端点，无 preview/inspect。

### Batch 4 — 登录站 + CollectorRagSource + 本地模型（v1.5+）

- [ ] **B4.1 Cookie 会话复用** `[新文件]` `collector/session.py`：浏览器辅助登录（复用 agent-browser/playwright）→ Cookie 存用户级目录（隔离/不进日志）；httpx 携带 Cookie 抓取；合法需登录站标注 L2
  - 2026-11-12 复核：仍未做 —— 文件不存在（`core/collector/` 下只有 manager/http/store/subjects/types/adapters + chapterizer/pipeline_ingest/quiz_splitter）。⚠️ 别和 `core/learning/session.py`（学习会话）搞混。
- [ ] **B4.2 CollectorRagSource 评估** `[改]` `rag_pipeline/sources.py` 新增 `CollectorRagSource`（name="collector"）+ `pipeline.register()` 一行；评估是否入对话引用（默认仅在采集页使用）
  - 2026-11-12 复核：仍未做 —— `rag_pipeline/sources.py:174-176` 只注册 Graph / Kb / GraphStructure 三个源。
- [ ] **B4.3 本地 embedding 正式启用** `[改]`：装 sentence-transformers（可选依赖）；`app/core/llm/embed.py` 加 `EMBED_MODE=local|api|auto`，auto 降级链 API→本地→hash；补 `test_embed_mode_config.py`
  - 2026-11-12 复核：仍未做 —— `llm/embed.py` 无任何 `EMBED_MODE`（只有 `EMBEDDING_MODEL`/`MAX_EMBED_CHARS`/`EMBED_BATCH_SIZE`），`requirements.txt:31` 的 `sentence-transformers` 仍是注释。

### v2 — 高级（暂不排期）

- [ ] 权威目录按需查询：K12 教材章节/考核类大纲实时查官方源
- [ ] 整书 LLM 智能切章（`[需API]`）
- [ ] 全学科目录数据批量内置（14 门类 + 117 学科种子词自动化生成）
- [ ] 采集覆盖率前端雷达图细化（学段→学科→板块 drill-down）

---

## 7. 评价体系（Assessment）

> 定位：**调研交付物**，建设期属于其他同学（**非本人职责**）；调研已完成、**建设未启动**。
> 方案已拍板 **A. 独立 `core/assessment/`**（见下）；设计依据 `docs/教学模块/QUIZ_出题逻辑调研.md`、`docs/教学模块/学习进度仪表盘设计.md`。
> **不做什么**：不动 `core/quiz/` 的出题 + schema；不动 `agent_tools/tools/grade_answer.py` 对外接口；**不建独立进度表**（沿用 `nodes.mastery` 单一数据源）。
> **2026-11-12 复核：整节 0 进度** —— `backend/app/core/assessment/` 目录**不存在**，`api/v1/` 无 `assessment.py`（`main.py:168-178` 的 12 个 router 里没有），无任何 `test_assessment_*`。本节 9 条建设项 + 3 条迁移项全部从零。

### P0 — 骨架 + 难度评估（建议先做）

- [ ] **P0-① 建空模块骨架** `[新文件]`：`backend/app/core/assessment/__init__.py` + 目录占位（纯新增，不动任何现有文件）
- [ ] **P0-② 难度评估** `[新文件]` `core/assessment/difficulty.py`：IRT 2PL `P(θ)=1/(1+exp(-a(θ-b)))` + LLM 模拟自评（`difficulty = 1-(0.5×低能力正确率+0.3×中+0.2×高)`）+ 区分度；**MVP 建议先做 LLM 模拟自评**；依赖 `core/llm/call_llm`；验收 `tests/test_assessment_difficulty.py` ≥5 例
- [ ] **P0-③ 评价数据模型** `[新文件]` `core/assessment/schema.py`：`Attempt` / `Progress` / `Report`；存储决策见 D-A1；验收 `tests/test_assessment_schema.py` ≥3 例
- [ ] **P0-④ README** `[新文件]` `core/assessment/README.md`（模块职责 + 数据流 + 改码坑，沿用 `core/agent/README.md` 模板）

### P1 — 学习分析 + 学习报告 + 评价指标

- [ ] **P1-① 学习分析** `[新文件]` `core/assessment/analytics.py`：薄弱点诊断 / 进步曲线（7/30/90 天）/ 学习模式；复用 `core/knowledge_graph.py::get_node_mastery`；验收 ≥5 例
- [ ] **P1-② 学习报告** `[新文件]` `core/assessment/report.py`：周报/月报（阶段总结 + 薄弱点 + 进步 + 推荐）；方案见 D-A3；验收 ≥3 例
- [ ] **P1-③ 评价指标体系** `[新文件]` `core/assessment/metrics.py`：形成性 + 总结性 + 多维度评分 + 布鲁姆 6 档；验收 ≥3 例
- [ ] **P1-④ API 端点** `[新文件]` `app/api/v1/assessment.py`：`GET /assessment/report?period=week|month`、`GET /assessment/difficulty/{question_id}`、`GET /assessment/analytics`；验收 ≥5 例

### P2 — 拆分迁移（可选）

- [ ] **P2-① `quiz/grader.py` → `assessment/grader.py`** `[改]`：保持 `grade_question` 接口不变，`quiz/grader.py` 改薄委托；验收 `tests/test_quiz_grader.py` 全绿
- [ ] **P2-② 拆出 mastery 更新策略** `[改]`：把"答对 +20 触发 mastery 更新"迁到 `assessment/mastery.py`；验收 `test_chat_quiz.py` + `test_tools_registry.py` 全绿
  - ⚠️ 2026-11-16 口径变更：答错不再「不变」→ 节点级 −20 / 小节级 20/N / 下限 10（见 §10 P2 一期，计划 `docs/教学模块/错题本与答错降级_实施计划_2026-11-16.md`）。迁移时两条规则一起搬，别只搬答对。
- [ ] **P2-③ `grade_answer` 工具调用链重接** `[改]`：`handler` 改调 `assessment.grader`；DESCRIPTION / GUIDANCE 不变；验收 `test_agent_loop.py` + `test_tools_registry.py` 全绿

### 决策点（建设者开工前先拍板）

| # | 决策点 | 默认建议 |
|---|---|---|
| **D-A1** | 数据存储：复用 `quiz_store.attempts` vs 新建 `assessment_*.json` vs 扩 `nodes`？ | **复用 attempts 表** |
| **D-A2** | 难度评估自评时机：每次出题 / 抽样 / 异步后台批跑？ | **异步后台批跑**（每周 N 题抽样） |
| **D-A3** | 学习报告：LLM 总结 vs 模板填充？ | **MVP 模板填充**，LLM 作为可选增强 |
| **D-A4** | 建独立 `study_events` 表（学时/路径回放）？ | **暂不建**（沿用对话 `updated_at` 估算） |
| **D-A5** | P0/P1/P2 顺序：是否一定先 P0 完整再做 P1？ | **P0 完整 → P1 → P2**，每阶段独立验收 |

---

## 8. 遗留技术债（跨模块，非功能项、优先低）

- [ ] **试卷拆分器 `quiz_splitter.py` 已实现但零引用**（未接入上传链路）：`collector/quiz_splitter.py` 已有 `tests/test_quiz_splitter.py` 9 例覆盖但全库零调用。**待决策（入口三选一）**：① `/kb/upload` 命中试卷形态后自动拆分；② 独立 `POST /quiz/import`；③ 与 B3.1 `dataset_quiz` 合并做。**接入注意**：`quiz_store.save_questions` 收 `Question` 对象（须 `Question(**q.to_question_dict())`）。
  - 2026-11-12 复核：仍零引用（只有 `quiz_splitter.py:398` 自身 `__main__` 自检 + 测试 9 例）；`api/v1/kb.py` 无 `/quiz/import` 类端点。
- [ ] **`collector/pipeline_ingest.py` + `chapterizer.py` 零引用**：整书切章入库链路（`ingest_book_chapters`）已实现且 6 例测试通过，但采集链路 `manager.run_task` 直接调 `kb_manager.upload_and_index`，**未接此管线**。需定入口：采集任务整书入库 / 手动导入 / 判定不用后删除。
  - 2026-11-12 复核：仍零引用（`pipeline_ingest` 只被自己 docstring 举例、`chapterizer` 只被 `pipeline_ingest:27` 引用）；`collector/manager.py:161-197` `run_task` 直调 `_ingest`。
- [ ] **采集库引用计数 UPDATE 跑在事件循环线程**（**口径已统一，根因未解**，2026-11-04 复核）：原记录说"`collector/store.py` 用默认 5s，调小会把锁等待变成 `database is locked`，正确修法是给引用计数另开只写连接"——**前半句已过时**（2026-11-04 已把 `busy_timeout` 从默认 5s 提到 10000ms，与其它长驻库一致，见 `done.md` §14.4），**后半句仍成立**：`manager.start_task` / `run_task` 都是 `async def`，里面的 `store.create_task` / `add_resource` / `update_task` / `update_resource_status` 是**同步 sqlite 写**，直接跑在事件循环线程上 → 写锁等待期间整个 loop 停摆（现在最长 10s，比原来的 5s 更久）。单用户单任务下几乎不会撞，**多端同时采集时才是真问题**。正确修法仍是「引用计数/状态回写另开只写连接 + `run_in_executor` 或独立队列」，属结构性改动，不在"顺手修债"范围。
  - 2026-11-12 复核：仍未做。`manager.py` 的 `start_task`/`run_task`/`_ingest` 全是 `async def`，内部直接同步 sqlite 写（`:116/122/132/176/189/270/286`），全文**无** `run_in_executor` / `asyncio.to_thread`。

---

## 9. 待用户拍板

- **深度 vs 成本**：GQ-5/6/16/23 会改变单次建图耗时与 token 量，是否接受"一次建图更慢但质量更高"？（GQ-23 的"单概念独立生成"预计总 token 上升，用成本换深度）
- **是否对已有图谱全量重建**（用新 prompt 重跑）还是只做增量修复（补写空壳）？—— 建议先修代码，再挑一本书重建做 A/B（GQ-13），别直接全量重跑。
- **多源融合的 4 个开放问题**（`docs/知识图谱/知识图谱_外部调研_论文开源与多源融合.md` §八）：融合单位 / 跨教材推断先修是否算"创造知识" / 冲突是否对学生可见 / 资料删除策略。

---

## 10. 一站式学习（One-stop Learning，2026-10-31）

> 目标：**从一个入口进来，只回答一次「你想学什么」，其余全自动** —— 采资料 → 建图 → 排路径 → 讲一节 → 出题判分 → 复习。用户不再需要在「知识库 / 图谱 / 对话 / 出题」四个页面之间手动搬上下文。
>
> 现状卡点：导入（队列）、建图、出题各自都能用，但**彼此不串联** —— 导入完要等通知、建图要另点、学会自己回图谱看进度。缺的不是某个功能，是"把已有环节接成一条线"。

### P0 — 一条串联起来的任务链

- [ ] **统一任务模型**：把「导入 → 建图 → 首轮路径规划」做成一条可暂停/续跑的任务链，复用 `core/kb/import_queue.py` 的任务表 + 每用户单 worker 模式（加 step 字段或 step 表，**不另造第二套调度**）。
  - 2026-11-12 复核：仍未做。`import_tasks` 列（`kb/kb_store.py:81-97`）只有 status/total/done/failed/error，**无 step**；`import_queue.py` 全文无 graph/learn/chain 关联，仍只做视频导入。
- [ ] **一个入口**：`POST /learn/start` 接收 主题名 / 教材文件 / 视频链接 三类输入，自动判定后续动作（无资料 → 先采集；有资料 → 建图；有图 → 生成路径），返回 `learn_session_id` + 首条进度事件。
  - 2026-11-12 复核：仍未做 —— `api/v1/` 无 learn 模块，`main.py:168-178` 全部 router 里没有 learn。
- [ ] **进度归一**：任务链每一步投递到同一个 `learn_progress` 事件（复用现有 SSE 通道），前端只画一条进度，不再分别监听 `import_progress` / `graph_updated` / `quiz_ready`。
  - 2026-11-12 复核：仍未做 —— `event_bus.py:6-17` 事件注册表只有 text_delta/…/import_progress/error，**无 `learn_progress`**。

### P1 — 学习会话成为一等对象

- [ ] **`learn_sessions` 表**（用户 / 主题 / 当前节点 / 待办队列 / 最近一题状态），**取代**现在散在前端 store 与提示词里的"当前学到哪"（与 §3「提示词当程序」同根因：状态写在提示词里，程序看不见）。
  - 2026-11-12 复核：**表已存在、语义更窄**（比本行描述领先半步）—— `kg/schema.py:133-144` `learning_sessions(user_id, node_id, section_id, intended_action, consent, reason, updated_at)` + `upsert/get`（`sections.py:230-261`）。**缺**：「待办队列 / 最近一题状态」两列、无 API 暴露、前端零消费（`learning_session` 只在后端出现）、库里仅 1 行。→ 本条应缩为"补两列 + 开 API + 前端消费"。
- [ ] **自动推进**：答完一题 → 判分写掌握度 → 按薄弱点选下一节 → 直接进下一轮讲解，**不需要用户再打"继续"**（`quiz_ready` 通道已具备触发能力，缺的是"推进策略"的收口处）。
  - 2026-11-12 复核：仍未做。策略出口已有（`teaching/policy.py:123 decide()`，`chat_service.py:471/590` 调用），但判分后**无任何自动触发链路**（无调度、无 `quiz_ready` → 讲解的接线）。
- [ ] **中断可续**：关掉应用再回来落在同一节、同一题；`learn_sessions` 是唯一真源，前端不再拼状态。
  - 2026-11-12 复核：**后端能读回、前端不消费** —— `learning/session.py:199 load` / `:204 reconcile` 存在，但 `reconcile()` 全库**零调用方**、无端点、前端零消费。

### P2 — 复习与长期闭环

- [ ] **一期 · 错题挂知识点 + 答错降级（2026-11-16 立项，实施计划见 `docs/教学模块/错题本与答错降级_实施计划_2026-11-16.md`）** `[改]`
  - 用户口径：「错题先挂在相应知识点/小节上，**不新增页面**；题目做错了就改掌握度，最低到『了解』那一档」。
  - 决策：节点级答错 **−20 / 下限 10**；小节级按节数比例 **penalty = max(1, round(20/N))**、同下限；「了解」在四档里无对应档 → 定为 10（weak 1–29 桶、>0，永不退回 unstarted），只改常量即可调整。
  - 落地：掌握度唯一增量入口 `kg.add_mastery` 加 `floor` 钳位（留写事务内）；`chat_grade` 两个判分出口补降级；`quiz_store.wrong_question_ids()` 派生「最近一次作答为错」（订正自动移出，**不建表不加列**）；`GET /knowledge/node/{id}/quizzes` 回填 `wrong`；`NodeDetail.vue` 加一个纯文本角标。**不新增前端页面/路由/图标、不新增表/列、不碰 `_JOBS`**。
  - 验收：答错 → 掌握度下降且不低于 10、mastery=0 的节点不被抬高；错题在节点详情可见、订正后消失；`mastery_events` 有 `quiz_wrong` 负 delta；前后端三套测试零失败。
- [ ] **二期 · 到期队列 / 间隔重复 / 日历视图**（待一期验收后开工，`[待议]`）
  - 从 `attempts.correct + created_at` 派生**固定间隔阶梯 1/3/7/15/30 天**（答错回第 1 档），**不写 SM-2**（旧口径，措辞已在 `docs/比赛/四人分工与TODO认领表.md` D3 更正）；在 `main.py::_prune_once` 的 `_JOBS` 旁加**按日**常驻调度，每日一条 `notify` 摘要；日历 = 该到期队列的按天视图，**不新增功能实体**。
  - ⚠️ 三套时区口径必须先统一：`attempts.created_at` 是 SQLite `datetime('now')`=**UTC**、`mastery_events.created_at` 是 Python `datetime.now()`=**本地**、`conversations.created_at` 是 `time.time()`=**epoch 秒**；前端 `frontend/src/utils/time.js` 不管时区 → 二期**全程只传 `YYYY-MM-DD` 日期粒度**，不传时间戳。
  - ⚠️ 生产单进程（`uvicorn --workers 1`）→ 只能承诺「按日」，不得承诺精确到分钟。
  - 真实库回放（`data/quiz/5/quiz.db`，22 条 attempts / 22 道不同题 / 每题仅答 1 次）：按 1/3/7/15/30 阶梯 → 到期分布 2026-10-11 共 1 题（逾期）、11-16 共 1 题、11-17 共 20 题。
- [ ] **学习报告**：按会话出「学了哪些节点 / 掌握度变化 / 薄弱点 / 建议下一步」，可导出 Markdown。
  - 2026-11-12 复核：仍未做（无 report 模块/端点）。

### 验收（先写测试再实现）

- [ ] 新用户 3 步内进入可交互课堂：选主题/给资料 → 等任务链跑完 → 直接开始第一节。
- [ ] 中途关闭页面 5 分钟回来，能回到原节点继续，且不重复出同一题（`avoid_questions` 已具备）。
- [ ] 一条 20 集的 B 站合集从导入到第一节开讲全自动，中间 0 次手动点击。
  - 2026-11-12 复核：三条前提均未达成（§10 P0 三条全空）。

---

## 11. 知识库任务进度：调研结论与施工清单（2026-11-08）

> 触发问题：知识库里"上传 / 生成图谱"这类操作，**看不到它在干什么**，也没有任何量化进度。
> 本节记录**实测结论**（不是猜测）、**已落地的实现**（原 §11.4-B，2026-11-15，已迁 `done.md` §14.3）、以及**还缺什么**。
> 结论先行：**四类任务里有三类后端数据已经齐了**，只有上传缺段内信号；但更严重的问题在别处 ——
> **建图任务在前端刷新/关页时会被真的取消**，不只是"显示丢了"。

### 11.1 四类任务的现状（逐条实测）

| 任务 | 状态可见 | 量化进度 | 数据从哪来 | 结论 |
|---|---|---|---|---|
| 视频课程导入 | ✅ 已完善 | ✅ **真实百分比 + ETA** | `import_items.stage` 落库 + `GET /kb/import/tasks` 返回 `elapsed_secs`/`eta_secs`/`avg_item_secs` | **不用动**（只是局限在导入页） |
| 建图 | ✅ **可查**（`GET /kb/graph/status`） | ✅ **可派生**（`content_status`） | 状态端点给 `building`/`subject`，进度由节点 `content_status` 派生 | 2026-11-15 已闭环（见 `done.md` §14.3），前端 3s 轮询 |
| 出题 | ⚠️ 仅 409 | ❌ 只有时间型 | `quiz_ready` 是终态事件（约 40s 一次到达） | 只能做时间型进度，性价比低 |
| 上传文件 | ❌ 只有"上传中"文字 | ❌ 无段内信号 | 解析完才知道分块数 | **唯一需要改后端的**（且改动很小） |

**建图进度的口径**（实测确认，非推断）：阶段①把概念树落成 `content_status='skeleton'` 的节点，
阶段②逐概念写正文并翻成 `filled`。所以

```
进度 = filled / total     ← 「已写出正文的概念数 / 总概念数」
```

比时间估算准，且刷新/换设备后照样算得出。实测真实数据：

```
GET /api/v1/knowledge/graph?subject=线性代数  →  total=17 filled=17 → 100%
样例节点字段含 content_status = True
```

⚠️ 建图是**增量补全**（已有学科会并轨），所以比例**不是从 0 开始**。UI 必须同时给绝对数
（`已写 12/30`），否则用户会以为卡住了 —— 不要只画一条从半空开始的进度条。

**上传为什么没有真实百分比**（诚实边界，别硬编）：
- 分块数要**解析完**才知道（`kb_manager._index_document` 里 `chunks = chunk_text(text)`）；
- 嵌入是批量内部循环（`llm/embed.py` 的 `EMBED_BATCH_SIZE=10`），批次之间没有回调出口。
- 所以可量化的只有**文件级 `i/N`**；单文件内部只报阶段文字（解析中 / 向量化中 / 入库）。

### 11.2 原前端设计草稿（分支 `feat/kb-task-progress`，已丢失，仅存档）

> 🔴 **2026-11-12 核实：这批代码已经丢失，不要再去 `git stash pop`。**
> 实测：`git stash list` 只有一条 `stash@{0}: On main: wip-before-pull-20260928-215150`（2026-09-28，
> untracked 部分不含它）；`main` / `origin/main` / 所有 remote 分支 / 全部 dangling commit
> 里都**没有** `kbTaskProgress.js` 或 `KbPanel.tasks.test.js`。→ 建图那半已被"状态查询"路线（`done.md` §14.3）取代、**不再重写**；下表只作"上传任务面板"若要补时的参考。
>
> 原记录（保留作施工规格）：代码在 `git stash`（**8 个文件**：5 个改动 + 3 个新增，后者在 stash 的
> untracked 部分）。要捡起来：`git stash pop`。

改动面（**全部前端，后端零改动**）：

| 文件 | 内容 |
|---|---|
| `frontend/src/utils/kbTaskProgress.js` | 新增：`isIndeterminate` / `taskPercent` / `taskCounter` / `taskSubtitle` / `taskTone` 纯函数 |
| `frontend/src/utils/kbTaskProgress.test.js` | 新增：12 例（NaN 防护、边界钳位、不可量化判定） |
| `frontend/src/components/KbPanel.tasks.test.js` | 新增：11 例，jsdom 真挂载（显示开关 / 进度条宽度 / 不画 0% 空条） |
| `frontend/src/components/KbPanel.vue` | +276：任务面板 UI、上传接入、建图轮询、样式 |
| `frontend/src/stores/chatStore.js` | +56：`kbTasks` 状态 + `beginKbTask`/`updateKbTask`/`endKbTask`/`dismissKbTask`/`clearFinishedKbTasks` |
| `frontend/src/api/kb.js` | +13：`getGraphProgress(subject)` |
| `frontend/src/views/KbView.vue` | +3：传 `show-tasks`（**只在独立页显示**，侧栏窄放不下） |
| `frontend/vitest.config.js` | +14：启用 `vue()` 插件（挂 SFC 必需；插件本就是 devDependency） |

**已验证**（2026-11-08）：前端 `49 passed (5 files)`（原 27 + 新增 22）、`npm run build` 通过、
后端 `1953 passed / 7 deselected`（唯一 FAILED 是既有的 `test_backup_script` WSL 路径问题）。
真实 Chrome 测过像素宽度：容器 560px 下三条进度条实测 `364 / 218 / 544`，与 67% / 40% / 100% 对应。

**关键设计取舍**（改的时候别推翻）：
- **任务状态放 store，不放组件**：KbPanel 在**两处**挂载（图谱页侧栏 + 知识库独立页），且
  `HomeView` 用 `:key="viewEpoch"` 让它重挂。状态留在组件里 → 切个视图回来任务就"消失"。
- **不可量化时绝不画 0% 空条**：视觉上等于"卡死"。用 `···` 不确定态代替。
- **任务面板只在知识库独立页渲染**（`showTasks` prop）：侧栏最宽 560 还要塞目录树。
  副作用：从侧栏发起建图，得切到独立页才看得到进度。

### 11.3 真正的坑：刷新/关页 → **任务被取消**，不是"显示丢了"

这是本次调研最重要的发现，比进度条重要得多。**2026-11-15 已修**（`runtime.spawn` 后台化 + 状态端点），下面是当时的诊断与实测，保留作"为什么这么改"的证据。

**现象分层**（两层都要修，别只修第一层）：

1. **显示层**：`kbTasks` 是纯内存 `ref`，没写 localStorage → 刷新后清空。
   （这一层好补：按 `user_id` 隔离写 localStorage，与对话缓存的 key 风格一致。）
2. **任务层**（**既有缺陷，非本次引入**）：建图请求 `POST /kb/graph/generate` **没有**
   `asyncio.shield`、也没有 `runtime.spawn` 后台化 —— 它靠前端请求保活。客户端一断连
   （刷新 / 关页 / 切网络），FastAPI 取消 handler 协程 → 建图真的停在半路。

实测（用一个永不返回的假生成器模拟）：

```
占位后 is_building = True
进入生成 = True
被取消 = True | 跑完 = False     ← 任务确实没跑完
释放后 is_building = False       ← finally 里的 end_graph_build 正常释放，不会永久锁死
```

**降低伤害的既有机制**：建图是分阶段落库的 —— 阶段①的概念树已经写成 skeleton 节点，
所以刷新后图谱里**看得到骨架**，只是阶段②的正文没写完。这正是 11.1 的 `filled/total`
能把"还剩多少"说清楚的原因。

**还缺的**（2026-11-15 已修前两条，见本表 ✅ 行与 `done.md` §14.3）：

| 缺口 | 位置 | 说明 |
|---|---|---|
| ~~没有 GET 暴露"正在建图"~~ | ~~`core/kb/graph_generator.py::is_graph_building`~~ | ✅ 已加 `GET /kb/graph/status`（`api/v1/kb.py`）：`building` / `subject` / `progress{filled,total,percent}` |
| ~~断连即取消~~ | ~~`api/v1/kb.py::generate_graph`~~ | ✅ 已改 `runtime.spawn` 后台化：入队即返回 `{"status":"accepted"}`，断连不再取消；互斥表同时记学科名（`_GRAPH_INFLIGHT: dict[int, str]`） |
| 无暂停/续跑/重试 | —— | 对比视频导入有完整队列（`import_queue`），建图没有 |

### 11.4 施工清单（A/B 已于 2026-11-15 落地或作废，只剩 C）

- [ ] **C · 完全对齐视频导入**（持久化队列，1–2 天）
  建图也做成 `import_queue` 式任务（暂停/续跑/重试/重排），需要新增表。
  与 §10「统一任务模型」是同一件事的两个入口，**别另造第二套调度**（§10 P0 原文即
  「复用 `core/kb/import_queue.py` 的任务表 + 每用户单 worker 模式，不另造第二套调度」）。
  做完这条，§10 的「统一任务模型」就只剩"把导入和建图接成一条链"。

### 11.5 不能改坏的不变量

- **SSE 请求内事件必须走请求私有队列**：若将来给建图加实时进度，**不要**用 `event_bus`
  的 per-user 队列发 `import_progress` 那类高频事件（会与常驻长连接抢事件，见 `AGENTS.md §2`）。
  要么走 `notify(..., persist=False)` 的既有通道，要么让前端轮询（当前实现用的是轮询）。
- **`ctx.kb` / `node_ids` 的语义**：`None` = 不限范围，`[]` = 空范围（检索不到）。详见 §1 那条
  「知识库检索范围」；回归用例 `backend/tests/test_kb_empty_scope.py`（随 **PR #8** 提交，
  合并前在 `fix/kb-empty-scope` 分支上）。
- **建图互斥 GQ-15**：任何后台化改造都必须保住 `try_begin_graph_build` / `end_graph_build` 的
  成对释放（放 `finally`），否则该用户会被永久锁死，此后每次建图都 409。
- **`content_status` 是进度真源**：别为进度另建一张表或新字段。它**同时是渲染信号**
  （2026-11-16）：前端拿它画"骨架空心 / 写完实心"，且必须进 `forceGraphEngine.js::graphFingerprint`
  —— 建图中节点 id 与 mastery 都不变、只有它翻档，漏了它重拉画布会被判"没变"直接返回。
  进度轮询（`chatStore.startGraphStatusPoll`）在 `total`/`filled` 变化时调 `refreshGraph()`
  让节点一个个长出来，用户正在看别的学科时不动作。

---

## 12. 线上实例运维（2026-11-12 全量体检 + 清理）

> 实例手册（拓扑 / 凭据位置 / 日常命令）= `docs/运维部署/运维_线上实例手册.md`。
> 本节只记**这次实测的结论**与**必须跟进的两件事**。

### 12.1 体检结论（全绿，服务器没坏）

| 项 | 实测 |
|---|---|
| 机器 | `47.98.160.194`，`up 37 days`，load `0.13/0.11/0.09`，无 failed systemd 单元 |
| 内存 | 3623 MiB（用 1105），`pressure/memory avg10=0.00` |
| 磁盘 | 清理后 `/dev/vda3 40G 用 8.4G（23%）`（清理前 11G / 30%） |
| 容器 | `ai-tutor` / `edututor-api` / `edututor-web` 全 `Up 3 weeks (healthy)`，`restarts=0` |
| 应用 | uvicorn 层 **5xx = 0**；容器 access log `5843×200 / 83×405 / 74×404 / 3×401`，无 5xx |
| 站点 | `/` 200、`/api/health` 200、`/gate` 401（正常）、`/admin/`（带 Cookie）200 |
| 出网 | MiniMax 200、DashScope 200、npmmirror 200、PyPI 清华源 200 |

**本次清理动作**（全部为"只删可再生缓存"，未动业务数据）：
`docker builder prune -af`（回收 **5.13 GB** build cache）→ `docker image prune -f`（0B）→
`apt-get clean`（137 MB）→ `journalctl --vacuum-size=32M`（回收 48 MB）→
**新增 2 GB swapfile**（`/swapfile`，已写 `/etc/fstab`，`vm.swappiness=10`）——
这台机器原本**无 swap**，2 核 3.5G 跑三个容器，一次 LLM 批量任务就可能触发 OOM killer。

### 12.2 必须跟进：部署落后（2026-11-12 测为 66 个提交，现在更多）

线上跑的是 `6316d36`（2026-10-22 20:26，**且这个提交从未 push**；内容与当时 origin 的
`df647bb` 逐行等价，`git diff --ignore-cr-at-eol` 为空）。2026-11-12 时 `origin/main` 是 `046f655`，
本地 HEAD 现已是 `211c3e2`（2026-11-16）—— **具体落后多少要上服务器 `git log` 才算得准**，别照抄 66。

**服务器不会自动更新** —— reflog 显示只在 10-06 / 10-11 / 10-18 / 10-22 被手工 merge 过 4 次。
所以 10-22 之后的**所有**本地产出（建图通知收口、`boot_id`、视频导入队列、前端聊天交互重构…）
**全部没上线**。前端静态产物更老，还停在 2026-10-11 的构建。

更新步骤（已验证网络可达，回滚点 `ai-tutor:rollback-20261018` 在）：

```bash
ssh tutor-server
cd /opt/ai-tutor
git fetch origin && git reset --hard origin/main      # 该目录无未提交改动，已确认 clean
docker compose build && docker compose up -d          # 重建后再验 /api/health
```

⚠️ 顺序：先确认 `data/` 挂载没丢（compose 里 9 个子目录逐个挂，`f2397ed` 又补了
`llm/keys/bili` 三个卷）→ 再 `up -d`。构建前建议先按上一节确认磁盘（现已 29G 可用，够）。

### 12.3 顺手记下的三个风险（未处理，等拍板）

1. **18080 对公网开放**：`47.98.160.194:18080` 外网可达（302 → `/admin/` → 401 Basic），
   那是同机的 **edututor** 项目后台，不属本项目。建议收进安全组白名单或只留 22/80。
2. **无 TLS**：443 关闭、`/etc/letsencrypt` 不存在，网关口令与 JWT 全走明文 HTTP。
3. **SSH 持续被爆破**：fail2ban `Currently failed: 85 / Total failed: 283 / 当前封禁 0`；
   策略已是密钥-only（`PasswordAuthentication no`），暂无实际风险，但建议查 fail2ban 为何不封。

另外：**演示模式仍是开启状态**（`/etc/nginx/tutor-demo.d/demo.conf`，从 2026-10-19 21:24 起）。
即公网免口令直达 + 关闭注册 + 在线/并发各限 3。非演示期执行 `/root/tutor-demo.sh off` 恢复口令门。

---

## 13. Android 客户端（2026-11-12 新增，2026-11-16 复核校准）

> 分支 `origin/feat/android-chat`（HEAD `b10ca1b`），**72 个 `.kt`**，尚未合入 `main`。
> ⚠️ `AGENTS.md §5` 的"52 个 `.kt` / 49 文件"仍是 2026-11-12 的数字，**已过时**，以本节为准。

已完成（按分支提交）：

| 提交 | 内容 |
|---|---|
| `ac0f3de` | 对话数据层 —— SSE 流解析与事件契约（`text_delta.token` 字段名、跳过 `:` 心跳帧） |
| `0143fdf` | 引入 LaTeX 公式渲染（MIT 许可） |
| `898c464` | Markdown 渲染 —— 表格、公式、HTML 标签与转义 |
| `5a17167` | 对话页 —— 流式渲染、引用与消息工具栏 |
| `88ca068` | 登录链路端到端（RSA-OAEP **MGF1-SHA256** 显式指定 + 加密会话存储） |
| `3f9281d` | 五 tab 壳（加 tab 只改一处枚举） |
| `a8a7d0c` / `cb7e568` / `b10ca1b` | 数据与网络契约补齐 / Markdown 跨公式粗体修复 / 会话管理抽屉 + 知识库作用域 + 登出 |

测试：`MarkdownParserTest`（403 行）、`MarkdownRealContentTest`（240 行）、`QuotedMessageBuilderTest`（117 行）。

未做（对照 `Android/README.md` 的里程碑）：
- **图谱 / 出题 / 进度三个 tab 仍是占位页**：`GraphScreen.kt` 30 行、`QuizScreen.kt` 29 行、`ProgressScreen.kt` 28 行，都走 `PlaceholderScreen`。
- 离线会话缓存（切网/断网后能否续读）。
- 真机回归（登录 → 断网 → 切前台 → 补拉），以及 Android 与 Web 的 SSE 事件白名单同步。

---

## 14. 建议的下一步（按投入产出比排序，待拍板）

| # | 事项 | 成本 | 为什么值得先做 |
|---|---|---|---|
| 1 | **服务器追平 origin/main**（§12.2） | ~20 分钟 | 本地累积的产出一次上线，比分头做新功能收益大得多 |
| 2 | **B3.1/B3.2 接入口**（§6） | 半天 | adapter + 21 例测试都已就绪、**零调用方**；补一个 `POST /collector/preview` + 前端 URL 输入即可兑现两个 TODO |
| 3 | **§2 长同步端点收口**（`sections/generate`） | 半天 | 建图那条已闭环，这条同样"断连即取消"；照它的写法（`runtime.spawn` + `finally` 里 `notify`）改 |
| 4 | **§10 P1 补 `learning_sessions` 两列 + API** | 1 天 | 表已存在，补 API + 前端消费即可兑现"中断可续"，是 §10 里最便宜的一段 |
| 5 | **§3 消除 `_build_learn_block` 的 9 处 ⛔** | 1~2 天 | 唯一的架构根因级债务；但风险最高（改的是每轮注入），建议单独开窗做 |
| 6 | **§7 评价体系** | 数天 | 整块空白，但自述"非本人职责" → **先确认还要不要做** |

⚠️ 做 #4 之前先定 §2 那条「前端 store 耦合」的调性（`chatStore.js` 已 1540 行），
否则新能力会继续堆进同一个文件。

---
