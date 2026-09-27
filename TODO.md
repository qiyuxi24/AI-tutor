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
- [ ]  **[P1] 「流式」名不副实：回复只在最后一次性到达**
  - `core/agent/loop.py` 两处 `emitter.emit(TEXT_DELTA, text=整段)`（自然收尾 + `_force_finish`）：`_chat_once` 未开 `stream=True`，流式只到**事件粒度**、不到 token 粒度
  - 后果：长回答首屏等待 = **整段生成时间**（实测 205 字 3.9s；2000 token 级回答 = 40s+ 的「AI 思考中…」然后整段蹦出）
  - 代价：`strip_think_tags` 要从"整段正则"改成**流式状态机**（`<think>` 会跨 chunk 边界）+ `agent_runs` 证据收集 / token 计量 / fallback 重试链都要跟着改
  - **待决策**：先只做下面那条观测性（首字延迟可观测），再定要不要全链路改造
- [ ]  **[P1] 观测性缺口：无法回答"回复到底送到前端没有"**
  - 后端证据齐（`agent_runs` / `debug_log`），**前端零**：一个 token 没收到时只在控制台静默（本次已改成留可见错误，但那是补丁不是观测）
  - 落点：一次 run 的**首字节延迟 / 写出事件数 / 写出字节数**（`chat_service` 消费侧计数 → `agent_runs` 或日志）
  - 附带：uvicorn access log 没进 `logs/tutor.log` → "请求到没到后端"只能翻库反推（本次排查就是这么查的）
  - 验收：给定 run_id 能直接看出"发了几条事件、前端有没有拿到"
- [ ]  **[P2] 双写持久化：两条真相来源没有裁决规则**
  - `syncToBackend` 每次 persist **全量重传所有对话**（O(全部历史) payload）；删除不传播（本地删了另一设备还在）；缺版本向量 / "最后写入者"仲裁 → 多设备必然漂移
  - 已修部分：`updated_at` 语义（缺省=服务端当前时间、内容未变不推进、`sync_from_client` 回填真实值）→ `tests/test_conversation_store.py`
- [ ]  **[P2] 前端 store 耦合：图谱长连接决定对话可用性**
  - `stores/chatStore.js` 同管对话 + 图谱 + SSE + 同步 + 出题回流；长连接生命周期由 `HomeView.onMounted → store.init()` 决定
  - 本次故障能潜伏很久正因为这个耦合（图谱通道能吞掉对话正文）
  - **待决策**：是否按关注点拆 store；不拆则至少把「**通道只管送到、渲染只有一份**」写成约定

---

## 遗留技术债（2026-09-08 盘点，非功能项、优先低）

- [ ]  **试卷拆分器 `quiz_splitter.py` 已实现但零引用（未接入上传链路）**（2026-09-12 发现，详见 `TODO_Collector.md` 遗留技术债 #2）：需先定入口形态（`/kb/upload` 自动拆 / 独立 `POST /quiz/import` / 并入 B3.1）。
- [ ]  **`collector/pipeline_ingest.py` + `chapterizer.py` 零引用（2026-09-15 core 审计）**：整书切章入库链路（`ingest_book_chapters`）已实现且 6 例测试通过，但采集链路 `manager.run_task` 直接调 `kb_manager.upload_and_index`，**未接此管线**（`chapterizer` 只被它引用，同属链内）。需定入口：采集任务整书入库 / 手动导入 / 判定不用后删除。
- [ ]  **conversations 内嵌 tools/thinking 去留 + run 与会话无关联键**（9/8 起挂着，**待决策**）：需定"是否为 agent_runs 加 conversation 外键/会话 id 字段（动 schema）"，或接受现状。
- [ ]  **守卫测试有一条断言不可靠**（2026-09-26）：`tests/test_event_bus.py::test_request_scoped_event_bypasses_user_broadcast_queue` 里「per-user 队列为空」那条断言，在破坏态下会因事件被长连接消费掉而**误绿**；改成 monkeypatch `publish` 断言其**未被调用**才精确（约 5 行）。

## 进度速览（2026-09-13）

- 代码层工程化 ~85%（架构✓ 测试✓ 部署✓ 文档✓ CI✓ 运维✓ 限流✓ 日志✓ Agent Loop✓）
- 测试：离线零网络集（`-m "not llm_api"`）**580 passed, 7 deselected**；real_api 独立 7 passed（需真 key + pytest-asyncio，单独跑 `-m pytest tests/test_agent_loop_real_api.py`）
- 比赛落地待办：P0（演示数据 / 阿里 embedding 向量灌库+评测 / quiz 判分联调 / OI-wiki 待议）→ 材料期 10/5 起
- 已完成项归档：`done.md`
