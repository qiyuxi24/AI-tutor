# TutorAgent 备赛与工程化清单

> 定位：知识图谱驱动的自适应导学 Agent（知识图谱 + 上传知识库 RAG + AI 出题 + 资源采集）
> 赛道：**三赛同投**（国创 / AI+教育 OPC / AIC），总纲·时间线·注意事项见 `docs/比赛/COMPETITION.md`；本文件只列**未完成**工程化待办
> 今日：2026-09-13（距 10-15 双截止约 4.5 周）
>
> - **✅ 已完成项一律搬去 `done.md`（那里只追加、不重写）；本文件只保留未勾选项，保持精简省 token。**
> - **⚠️ 清单状态按代码实测校准（非按文档印象勾选）。**
> - 阶段就绪度评估结论（2026-09-08 复核）也在 `done.md`。

---

## P0 — 收尾就绪（演示不翻车 + 材料可信）

- [ ]  **quiz 简答 LLM 判分端到端联调**（走 call_llm 纯文本，MiniMax key 可用即可验，不依赖 embedding）
- [ ]  **OI-wiki 真网联调**（原 B2.1 收尾）：`search("数据结构与算法")` 应返回 ds/算法基础板块候选、fetch 首页正文入库无 MkDocs 残留语法 —— 普通联网即可验（无需 DASHSCOPE），**待议**（2026-09-05 暂缓，待用户定时间）
- [ ]  **演示数据 + 脚本**：填充 1 个完整学科图谱（15-20 节点 + prerequisite 边）；写 2-3 条预演对话路径

## P1 — 工程化补强（评委可问项）

- [ ]  **Prompt 笔记优化**：教学后主动 `update_node_content` / `add_knowledge_node` 记笔记（提示词层，未动）
- [ ]  **空图谱时的行动顺序（后续项，待议）** —— 提示词层 MPV 已完成（见 `done.md` §2.2）

  - [ ]  **工具层引导** —— 节点不存在时把 `E-LLM-007` 变成"请先用 `add_knowledge_node` 创建它"的可自纠正提示（P1 对照实验发现模型会拿节点名当 `node_id`）
- [ ]  **对话内出题（P0）遗留** —— 主链路已落地（见 `done.md` §6.2）

  - [ ]  P1：题目渲染成可点选项卡片（前端 `quiz_ready` 已带 `questions` 字段，`MessageBubble` 加 `QuizCard.vue`）
  - [ ]  已知瑕疵：KB 检索片段混入依据会导致题目轻微漂移（节点是"二叉树性质1"，却出了"每层都达最大结点数 → 满二叉树"的题）—— 可考虑降 `CHAT_QUIZ_TOP_K` 或只用节点正文
- [ ]  **出题稳定性遗留** —— 分批 + 补题 + 批次隔离已落地（见 `done.md` §6.1）

  - [ ]  可选优化：出题改用非思考模型（结构化任务未必需要深度推理），需 A/B 质量验证
- [ ]  **建节点收口 · 收尾项**（写路径部分 2026-09-15 已闭环）

  - [ ]  **嵌入语义去重**（栈/堆栈 这类同名不同字）：Agent 热路径上要多付一次嵌入 + LLM 二次确认，（嵌入已恢复，可量化收益后再定）；实现路径 = 复用 `graph_generator._find_dedup_candidates` + `_confirm_synonyms`
  - [ ]  权限守卫：**新建路径本就不触发** `_guard_human_content`（调研 §8 已核实），仅当将来新增"AI 覆盖已有节点"入口时才需要 —— 那时走 `update_node_content(caller="ai")` 即可

## P2 — 功能路线图（比赛可选项 / 有真实 key 后）

- [ ]  **RAG 精排（Cross-Encoder）**：hybrid_search 后接 rerank，量化收益（W4 可选，须先有真实 embedding 灌库）
- [ ]  **HyDE / Query Expansion**：召回 +15-25%，LLM 失败回退普通检索
- [ ]  **出题进阶**：参数化母题模板、题目知识关联回填图谱
- [ ]  **学习大纲生成 / 主动引导式提问 / 自包含 HTML 复习卡导出**
- [ ]  **"AI 同学"多角色讨论 / 白板图谱联动（SVG）**
- [ ]  **LLM 服务商抽象层**（deepseek/GLM 切换）；KG_TOOLS → 抽象"动作层"
- [ ]  **RAG 引用卡片拖拽窗口**（知识卡片白板，借鉴 OpenMAIC）

---

## 架构待决（2026-09-26 对话「没反应」故障复盘）

> 根因：`event_bus` 的 per-user 队列被**两个消费者共享**（对话流 `_consume_agent_events` + 常驻长连接 `/knowledge/events`），
> `text_delta`（正文）被长连接抢走丢弃 → 对话侧拿到空流 → 前端 `onDone('')` 删掉 AI 气泡。
> 已修：对话事件改走本请求私有队列（`core/agent/events.py` + `chat_service._consume_agent_events`），
> 回归测试 `tests/test_event_bus.py::test_chat_stream_not_starved_by_long_lived_subscriber`。下列是**同源或同症状**的剩余项。

- [ ]  **[P0] SSE 通道分层**（本次只修掉"对话流"这一处冲突，同模型下还有别的）
  - 不变量已钉进 `event_bus.publish` docstring：通知类（`graph_updated`/`quiz_ready`/`error`）走 per-user 队列；请求内 6 事件（`agent_start`/`thinking`/`tool_*`/`text_delta`/`agent_done`）必须走 `AgentEventEmitter(queue=...)` 私有队列
  - 剩余缺口：同一 user 开两个标签页 = 两条 `subscribe(user_id)`，`quiz_ready` 仍随机进其一（队列层面无解，要 per-connection 投递才能解）
  - **待决策**：a) 按通道分队列（改动集中在 `event_bus.py`，**推荐**）／b) per-connection 队列 + 订阅路由表（能解多标签页，改动最大）／c) 维持现状
  - 验收：加"同一 user 多连接订阅"测试，断言通知类事件按预期投递
- [ ]  **[P1] 对话调用链（M3）三处收尾**（「真流式」已完成，完成侧见 `done.md` §4.5）
  - **思考吃光 `max_tokens` → 长回答可能整段空正文**（优先）：`AGENT_MAX_TOKENS=2000` + 允许思考时二者共享预算 —— 真机探针一次 52.8s 全烧在思考上、`content=0`、`finish=length`。流式让"思考中"不再卡死，但**不保证有正文**
  - `strip_think_tags` 仍是**整段正则**（未做流式状态机）：靠 `reasoning_split=True` 让 content 保持纯净；若某网关忽略该参数，流式正文可能短暂含思考标签（存量兜底仍会清库里的文本）
  - **中间轮文本会实时显示**（有意）：工具轮的过渡语（"我先查一下…"）在内容 delta 里，位置先于 tool_calls delta，不先发就做不到"最终轮边生成边显示"；要隐藏需前端"撤帧"机制
- [ ]  **[P1] 观测性缺口：无法回答"回复到底送到前端没有"**
  - 后端证据齐（`agent_runs` / `debug_log`），**前端零**：一个 token 没收到时只在控制台静默（本次已改成留可见错误，但那是补丁不是观测）
  - 落点：一次 run 的**首字节延迟 / 写出事件数 / 写出字节数**（`chat_service` 消费侧计数 → `agent_runs` 或日志）
  - 附带：uvicorn access log 没进 `logs/tutor.log` → "请求到没到后端"只能翻库反推（本次排查就是这么查的）
  - 验收：给定 run_id 能直接看出"发了几条事件、前端有没有拿到"
- [ ]  **[P2] 双写持久化：两条真相来源没有裁决规则**
  - `syncToBackend` 每次 persist **全量重传所有对话**（O(全部历史) payload）；删除不传播（本地删了另一设备还在）；缺版本向量 → 多设备仍可能漂移（当前是"更新的赢"降级处理，不是真合并）
  - 已修①（2026-09-26）：`updated_at` 语义（缺省=服务端当前时间、内容未变不推进、`sync_from_client` 回填真实值）→ `tests/test_conversation_store.py`
  - 已修②（2026-10-07 多端登录）：**防覆盖** —— 前端每条对话带本地 `updatedAt`；后端在 `BEGIN IMMEDIATE` 内比较，**旧快照拒绝写入**并返回库里版本；前端 `adoptRemote` 用权威版本刷回本地。回归 `tests/test_conversation_store.py::test_stale_client_updated_at_does_not_overwrite` / `test_sync_rejects_stale_snapshot`
  - **仍未做**：删除的跨端传播；真合并（两端同时各加一条消息 → 仍是"新的赢"，另一条丢）
- [ ]  **[P2] 前端 store 耦合：图谱长连接决定对话可用性**
  - `stores/chatStore.js` 同管对话 + 图谱 + SSE + 同步 + 出题回流；长连接生命周期由 `HomeView.onMounted → store.init()` 决定
  - 本次故障能潜伏很久正因为这个耦合（图谱通道能吞掉对话正文）
  - **待决策**：是否按关注点拆 store；不拆则至少把「**通道只管送到、渲染只有一份**」写成约定

---

## 架构债务：主体性错层 —— "提示词当程序"（2026-10-07 只读审计）

> 由来：一次以「**谁在决定下一步做什么**」为主线的只读审计。结论：**架构里没有主体** ——
> 唯一的发起者是学生的一条消息，唯一的决策者是服务端硬编码状态机，模型只负责把已经定好的
> 下一步**说出口**；叙述里的"主动 / 自适应"在实现中没有落点（主动权在文案里给了 AI，
> 在实现里给了服务器，唯独没给学生）。
> 本节是**根因级**债务：上面「架构待决」那 4 条（SSE 分层 / 流式 / 可观测 / 双写 / store 耦合）
> 大多是它的**症状**，先看这里再看那里。
>
> **全项目问题清单 + 分级重构路线（P0~P2 / Phase A-B-C）** 见
> `docs/架构/整体架构_问题清单与重构方案.md`（2026-10-07 六方向只读审计）。

- [ ] **[根因·P0] 主动性放在了错误的层：教学策略 = 提示词散文 + 硬拦 + 兜底**
  - 证据：`_build_learn_block` 实测 **176 行**中文规则（`chat_service.py:278-454`；曾记为"400 行"，
    与其自报的行号范围自相矛盾 —— 2026-10-10 复核修正）；项目自述
    "**提示词里写'禁止'实测无效**"（`done.md:148-151`）、"`mark_section_understood`
    4 次采样一次没被调"（`done.md:154-156`）
  - 症状（每加一个"AI 主动"能力，必配一个"AI 不做系统替它做"的兜底）：
    开场轮协议层硬禁工具（`agent/loop.py:431/468`）、越级记账硬拒
    （`tools/mark_section_understood.py:95`）、模型没出题后端补一道（`chat_service.py:539-579`）
  - 测试口径失真：`test_learn_mode.py` 47 例里 **10 条**是 `test_block_*`
    （`forbids_*` / `requires_*` / `declares_*`）—— 锁的是"文档里有没有这句话"，不是行为对不对
    （2026-10-10 复核修正：原文"47 例里一半"高估了）
  - **待决策**：把教学策略提升为一等对象（会话状态 + 动作集合），还是继续堆规则文案？
- [ ] **[根因·P0] 没有「学习会话」这一等对象 → 状态碎片化 + 每轮重算**
  - 状态散在 4 处：`nodes.mastery`（SQLite）/ `manifest.sections[].learn`（文件）/
    `questions.section_id`（题库）/ **每请求新建 kg 实例上的临时属性**
    `kg.student_input | quiz_consent | learn_target`（`chat_service.py:494-536`）
  - 后果：模型没有跨轮记忆主体，只能靠一条提示词去对冲它自己上一轮的承诺 ——
    "**状态权威（优先于你自己说过的话）**"（`chat_service.py:395-398`）
  - 关联：本文件「遗留技术债」里 `conversations` 与 `agent_runs` 无关联键；一次 run 不是一等对象
- [ ] **[P1] 掌握度没有"力"：闭环缺最后一环**
  - 排序只看 prerequisite，**不读 mastery**（`knowledge_graph.py:780`、`:799-813`，已核实）；
    mastery 只在 `:888-894` 决定"推荐指针"
  - 出题 handler 不校验 mastery（`tools/quiz_generate.py:180-287`），只有提示词层一句否决（`:82-83`）
  - 无复习调度 / 错题队列：全后端唯一常驻循环是每日 GC（`main.py:108-114`）
  - 结论：**"自适应"目前是单轮启发式，不是跨会话调度**；"千人一面"的风险来自
    "同教材 → 同图 → 同顺序"
- [ ] **[P1] 学生的主体性被压缩成"≤12 字同意词"**
  - `core/quiz/consent.py:28` `_QUIZ_CONSENT_MAX_CHARS = 12`，长句一律判"不确定 → 不放行"（`:55-61`）
  - 顺序被**显式**排除重排（`chat_service.py:270-274` 注释"刻意不按'不懂优先'"）；
    画像 `goals` 只当语气参数（`chat_service.py:606-607`），不参与路径 / 选题
  - 域外问题要"礼貌地把话题拉回清单内的节点"（`data/prompts/system_prompt_common.j2:19-20`
    + 参照系契约 L1）
  - **待决策**：是否开放「顺序 / 范围 / 目标」三个真正的决策通道？
- [ ] **[P1] 系统没有"发起"通道 → 主动必须伪装成学生发言**
  - 前端为开一场学习**伪造一条用户消息**：`send('开始学习「X」')`
    （`frontend/src/stores/chatStore.js:801`，注释见 `:788`）
  - 通道其实已存在却没被用来推进学习：`quiz_ready` 只往对话塞一张题卡，
    **不发请求、不推进小节**（`chatStore.js:692-694` → `:725-747`）
  - **待决策**：复用通知通道做"薄弱点题自动送达"，还是引入调度器（间隔复习）？
- [ ] **[P2] 叙述与实现缺 SSOT 守卫**
  - "学习模式"这个行为最密集的层**没有设计文档**（`docs/` 全库无"学习模式"，
    只有 `done.md:131-165` 一节 + `chat_service` 注释）
  - 参照系契约自认"不宜直接宣称图谱是每次调用的参照系"（`:106-108`）
  - （`README.md:95`「SSE 逐 token 推文」原先与本文件「流式名不副实」冲突，2026-10-10 真流式落地后**两者一致**，该冲突消解 → `done.md` §4.5）

- [ ] **本节验收（"改完了"的判据，不是"写完文档了"）**
  - 一次学习推进不再依赖提示词里的 ⛔ 条目：`_build_learn_block` 规则数大幅下降，
    且 `test_block_forbids_*` 系列是**被删除**而不是被改写
  - "下一步做什么"有单一归属对象，且从它能直接读出：当前节 / 待办动作 / 是否已获学生同意
  - 至少一条"非学生触发"的学习推进在真实链路上可用（不是只在脚本里）
  - mastery 进入路径或选题，且有一处调度（错题优先或间隔复习）

## 遗留技术债（2026-09-08 盘点，非功能项、优先低）

- [ ]  **试卷拆分器 `quiz_splitter.py` 已实现但零引用（未接入上传链路）**（2026-09-12 发现，详见 `TODO_Collector.md` 遗留技术债 #2）：需先定入口形态（`/kb/upload` 自动拆 / 独立 `POST /quiz/import` / 并入 B3.1）。
- [ ]  **`collector/pipeline_ingest.py` + `chapterizer.py` 零引用（2026-09-15 core 审计）**：整书切章入库链路（`ingest_book_chapters`）已实现且 6 例测试通过，但采集链路 `manager.run_task` 直接调 `kb_manager.upload_and_index`，**未接此管线**（`chapterizer` 只被它引用，同属链内）。需定入口：采集任务整书入库 / 手动导入 / 判定不用后删除。
- [ ]  **conversations 内嵌 tools/thinking 去留 + run 与会话无关联键**（9/8 起挂着，**待决策**）：需定"是否为 agent_runs 加 conversation 外键/会话 id 字段（动 schema）"，或接受现状。

## 进度速览（2026-09-13）

- 代码层工程化 ~85%（架构✓ 测试✓ 部署✓ 文档✓ CI✓ 运维✓ 限流✓ 日志✓ Agent Loop✓）
- 测试：离线零网络集（`-m "not llm_api"`）**580 passed, 7 deselected**；real_api 独立 7 passed（需真 key + pytest-asyncio，单独跑 `-m pytest tests/test_agent_loop_real_api.py`）
- 比赛落地待办：P0（演示数据 / 阿里 embedding 向量灌库+评测 / quiz 判分联调 / OI-wiki 待议）→ 材料期 10/5 起
- 已完成项归档：`done.md`
