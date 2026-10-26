# AI Tutor 待办总表（唯一 TODO）

> 定位：本项目**唯一**的未完成清单。2026-10-11 由原五份合并 ——
> `TODO.md` / `TODO_Graph_Quality.md` / `TODO_Context.md` / `TODO_Collector.md` / `TODO_Assessment.md`（后四份已删除）。
> **✅ 已完成项一律搬 `done.md`**（只追加、不重写）；本文件只留未勾选项，状态**按代码实测校准**（不按文档印象）。
> 比赛总纲 / 时间线 / 赛道 → `docs/比赛/COMPETITION.md`；模块 SSOT 与文档路由 → `AGENTS.md §5`。
> 标记约定：`[改]` 改现有文件 ／ `[新文件]` ／ `[复用]` ／ `[需API]` 需真实外部服务 ／ `[待议]` 需用户先定。

---

## 1. 工程化收尾（P0/P1/P2）

### P0 — 收尾就绪（演示不翻车 + 材料可信）

- [ ] **quiz 简答 LLM 判分端到端联调**（走 `call_llm` 纯文本，MiniMax key 可用即可验，不依赖 embedding）
- [ ] **OI-wiki 真网联调**：`search("数据结构与算法")` 应返回 ds/算法基础板块候选、fetch 首页正文入库无 MkDocs 残留语法（普通联网即可验，无需 DASHSCOPE）——`[待议]`（2026-09-05 暂缓，待用户定时间）
- [ ] **演示数据 + 脚本**：填充 1 个完整学科图谱（15-20 节点 + prerequisite 边）；写 2-3 条预演对话路径

### P1 — 工程化补强（评委可问项）

- [ ] **Prompt 笔记优化**：教学后主动 `update_node_content` / `add_knowledge_node` 记笔记（提示词层，未动）
- [ ] **空图谱时的行动顺序 · 工具层引导**：节点不存在时把 `E-LLM-007` 变成"请先用 `add_knowledge_node` 创建它"的可自纠正提示（P1 对照实验发现模型会拿节点名当 `node_id`）
- [ ] **对话内出题（P0）遗留**（主链路已落地，见 `done.md` §6.2）：
  - [ ] 题目渲染成可点选项卡片（前端 `quiz_ready` 已带 `questions` 字段，`MessageBubble` 加 `QuizCard.vue`）
  - [ ] 已知瑕疵：KB 检索片段混入依据会导致题目轻微漂移（节点是"二叉树性质1"，却出了"每层都达最大结点数 → 满二叉树"的题）—— 可考虑降 `CHAT_QUIZ_TOP_K` 或只用节点正文
- [ ] **出题稳定性遗留**（分批 + 补题 + 批次隔离已落地，见 `done.md` §6.1）：
  - [ ] 可选优化：出题改用非思考模型（结构化任务未必需要深度推理），需 A/B 质量验证
- [ ] **建节点收口 · 收尾项**（写路径部分 2026-09-15 已闭环）：
  - [ ] **嵌入语义去重**（栈/堆栈 这类同名不同字）：Agent 热路径上要多付一次嵌入 + LLM 二次确认（嵌入已恢复，可量化收益后再定）；实现路径 = 复用 `graph_generator._find_dedup_candidates` + `_confirm_synonyms`
  - [ ] 权限守卫：**新建路径本就不触发** `_guard_human_content`，仅当将来新增"AI 覆盖已有节点"入口时才需要 —— 那时走 `update_node_content(caller="ai")` 即可

### P2 — 功能路线图（比赛可选项 / 有真实 key 后）

- [ ] **RAG 精排（Cross-Encoder）**：hybrid_search 后接 rerank，量化收益（须先有真实 embedding 灌库）
- [ ] **HyDE / Query Expansion**：召回 +15-25%，LLM 失败回退普通检索
- [ ] **出题进阶**：参数化母题模板、题目知识关联回填图谱
- [ ] **学习大纲生成 / 主动引导式提问 / 自包含 HTML 复习卡导出**
- [ ] **"AI 同学"多角色讨论 / 白板图谱联动（SVG）**
- [ ] **LLM 服务商抽象层**（deepseek/GLM 切换）；KG_TOOLS → 抽象"动作层"
- [ ] **RAG 引用卡片拖拽窗口**（知识卡片白板，借鉴 OpenMAIC）

---

## 2. 架构待决（2026-09-26 对话「没反应」故障复盘）

> 根因：`event_bus` 的 per-user 队列被**两个消费者共享**（对话流 `_consume_agent_events` + 常驻长连接 `/knowledge/events`），
> `text_delta` 被长连接抢走丢弃 → 对话侧拿到空流 → 前端 `onDone('')` 删掉 AI 气泡。
> 已修：对话事件改走本请求私有队列（`core/agent/events.py` + `chat_service._consume_agent_events`），
> 回归 `tests/test_event_bus.py::test_chat_stream_not_starved_by_long_lived_subscriber`。下列是**同源或同症状**的剩余项。

- [ ] **[P0] SSE 通道分层**（本次只修掉"对话流"这一处冲突）
  - 不变量已钉进 `event_bus.publish` docstring：通知类（`graph_updated`/`quiz_ready`/`error`）走 per-user 队列；请求内 6 事件必须走 `AgentEventEmitter(queue=...)` 私有队列
  - 剩余缺口：同一 user 开两个标签页 = 两条 `subscribe(user_id)`，`quiz_ready` 仍随机进其一（队列层面无解，要 per-connection 投递才能解）
  - **待决策**：a) 按通道分队列（改动集中在 `event_bus.py`，**推荐**）／b) per-connection 队列 + 订阅路由表（能解多标签页，改动最大）／c) 维持现状
  - 验收：加"同一 user 多连接订阅"测试，断言通知类事件按预期投递
- [ ] **[P1] 对话调用链（M3）三处收尾**（「真流式」已完成，见 `done.md` §4.5）
  - **思考吃光 `max_tokens` → 长回答可能整段空正文**（优先）：`AGENT_MAX_TOKENS=2000` + 允许思考时二者共享预算 —— 真机探针一次 52.8s 全烧在思考上、`content=0`、`finish=length`
  - `strip_think_tags` 仍是**整段正则**（未做流式状态机）：靠 `reasoning_split=True` 让 content 保持纯净；若某网关忽略该参数，流式正文可能短暂含思考标签（存量兜底仍会清库里的文本）
  - **中间轮文本会实时显示**（有意）：工具轮的过渡语在内容 delta 里，位置先于 tool_calls delta；要隐藏需前端"撤帧"机制
- [ ] **[P1] 观测性缺口：无法回答"回复到底送到前端没有"**
  - 后端证据齐（`agent_runs` / `debug_log`），**前端零**
  - 落点：一次 run 的**首字节延迟 / 写出事件数 / 写出字节数**（`chat_service` 消费侧计数 → `agent_runs` 或日志）
  - 附带：uvicorn access log 没进 `logs/tutor.log` → "请求到没到后端"只能翻库反推
  - 验收：给定 run_id 能直接看出"发了几条事件、前端有没有拿到"
- [ ] **[P2] 双写持久化：两条真相来源没有裁决规则**
  - `syncToBackend` 每次 persist **全量重传所有对话**（O(全部历史) payload）；删除不传播；缺版本向量 → 多设备仍可能漂移（当前是"更新的赢"降级处理，不是真合并）
  - 已修①（2026-09-26）`updated_at` 语义；已修②（2026-10-07）**防覆盖**（`BEGIN IMMEDIATE` 内比较 + 旧快照拒绝写入），回归 `tests/test_conversation_store.py`
  - **仍未做**：删除的跨端传播；真合并（两端同时各加一条消息 → 仍是"新的赢"，另一条丢）
- [ ] **[P2] 前端 store 耦合：图谱长连接决定对话可用性**
  - `stores/chatStore.js` 同管对话 + 图谱 + SSE + 同步 + 出题回流；长连接生命周期由 `HomeView.onMounted → store.init()` 决定
  - 本次故障能潜伏很久正因为这个耦合（图谱通道能吞掉对话正文）
  - **待决策**：是否按关注点拆 store；不拆则至少把「**通道只管送到、渲染只有一份**」写成约定

---

## 3. 架构债务：主体性错层 —— "提示词当程序"（2026-10-07 只读审计）

> 由来：一次以「**谁在决定下一步做什么**」为主线的只读审计。结论：**架构里没有主体** ——
> 唯一的发起者是学生的一条消息，唯一的决策者是服务端硬编码状态机，模型只负责把已经定好的
> 下一步**说出口**；"主动 / 自适应"在实现中没有落点。
> 本节是**根因级**债务：上面「架构待决」那几条大多是它的**症状**，先看这里再看那里。
> 全项目问题清单 + 分级重构路线 → `docs/架构/整体架构_问题清单与重构方案.md`；施工计划 → `docs/架构/重构实施计划_可执行清单.md`。

- [ ] **[根因·P0] 主动性放在了错误的层：教学策略 = 提示词散文 + 硬拦 + 兜底**
  - 证据：`_build_learn_block` 实测 **176 行**中文规则（`chat_service.py:278-454`）；项目自述"**提示词里写'禁止'实测无效**"（`done.md`）、"`mark_section_understood` 4 次采样一次没被调"
  - 症状（每加一个"AI 主动"能力，必配一个"AI 不做系统替它做"的兜底）：开场轮协议层硬禁工具（`agent/loop.py:431/468`）、越级记账硬拒（`tools/mark_section_understood.py:95`）、模型没出题后端补一道（`chat_service.py:539-579`）
  - 测试口径失真：`test_learn_mode.py` 47 例里 **10 条**是 `test_block_*`（锁的是"文档里有没有这句话"，不是行为对不对）
  - **待决策**：把教学策略提升为一等对象（会话状态 + 动作集合），还是继续堆规则文案？
- [ ] **[根因·P0] 没有「学习会话」这一等对象 → 状态碎片化 + 每轮重算**
  - 状态散在 4 处：`nodes.mastery`（SQLite）/ `manifest.sections[].learn`（文件）/ `questions.section_id`（题库）/ **每请求新建 kg 实例上的临时属性** `kg.student_input | quiz_consent | learn_target`（`chat_service.py:494-536`）
  - 后果：模型没有跨轮记忆主体，只能靠一条提示词去对冲它自己上一轮的承诺
  - 关联：「遗留技术债」里 `conversations` 与 `agent_runs` 无关联键；一次 run 不是一等对象
- [ ] **[P1] 掌握度没有"力"：闭环缺最后一环**
  - 排序只看 prerequisite，**不读 mastery**（`knowledge_graph.py:780`、`:799-813`）；mastery 只在 `:888-894` 决定"推荐指针"
  - 出题 handler 不校验 mastery（`tools/quiz_generate.py:180-287`），只有提示词层一句否决
  - 无复习调度 / 错题队列：全后端唯一常驻循环是每日 GC（`main.py:108-114`）
  - 结论：**"自适应"目前是单轮启发式，不是跨会话调度**
- [ ] **[P1] 学生的主体性被压缩成"≤12 字同意词"**
  - `core/quiz/consent.py:28` `_QUIZ_CONSENT_MAX_CHARS = 12`，长句一律判"不确定 → 不放行"
  - 顺序被**显式**排除重排（`chat_service.py:270-274`）；画像 `goals` 只当语气参数（`chat_service.py:606-607`），不参与路径 / 选题
  - **待决策**：是否开放「顺序 / 范围 / 目标」三个真正的决策通道？
- [ ] **[P1] 系统没有"发起"通道 → 主动必须伪装成学生发言**
  - 前端为开一场学习**伪造一条用户消息**：`send('开始学习「X」')`（`frontend/src/stores/chatStore.js:801`）
  - 通道其实已存在却没被用来推进学习：`quiz_ready` 只往对话塞一张题卡，**不发请求、不推进小节**
  - **待决策**：复用通知通道做"薄弱点题自动送达"，还是引入调度器（间隔复习）？
- [ ] **[P2] 叙述与实现缺 SSOT 守卫**
  - "学习模式"这个行为最密集的层**没有设计文档**（`docs/` 全库无"学习模式"，只有 `done.md` §4.4 + `chat_service` 注释）
  - 参照系契约自认"不宜直接宣称图谱是每次调用的参照系"
- [ ] **本节验收（"改完了"的判据）**
  - 一次学习推进不再依赖提示词里的 ⛔ 条目：`_build_learn_block` 规则数大幅下降，且 `test_block_forbids_*` 系列是**被删除**而不是被改写
  - "下一步做什么"有单一归属对象，且从它能直接读出：当前节 / 待办动作 / 是否已获学生同意
  - 至少一条"非学生触发"的学习推进在真实链路上可用
  - mastery 进入路径或选题，且有一处调度（错题优先或间隔复习）

---

## 4. 知识图谱生成质量（GQ / KG-D）

> 生成质量专项（粒度 / 去重 / 质控 / 多源增量）。相关 SSOT：`docs/知识图谱/知识图谱_参照系契约.md`（粒度口径）、
> `docs/知识图谱/知识图谱_数据结构与检索通路_评审与改良方案.md`（KG-D 逐项验收）、`docs/知识图谱/知识图谱_外部调研_论文开源与多源融合.md`（L0~L4 完整方案）、
> `backend/app/core/kb/graph_generator.py`（生成实现）。体检脚本 = `backend/scripts/inspect_graph_quality.py`。

### 定树 / 粒度 / 多源增量（GQ）

- [ ] **GQ-4 一次性合并已存在的重复节点**（工具已就绪，**待用户确认执行**）
  - 落点：`backend/scripts/inspect_graph_quality.py --fix-dupes`（默认 dry-run）→ 加 `--apply` 执行
  - 保留者规则：先排除**碎片名**节点（`_review`/「（复习）」不当正主），再取正文更长者；被删节点正文若不在保留者里**先并入再删**
  - 边处理：重定向到保留者；重定向后重复/自环/成前置环的边丢弃
  - dry-run 实测（2026-09-20）：全库 25 组 / 55 个待删节点
  - ⚠ 会**删节点与 MD 文件**（不可回滚）→ 执行前先备份 `data/knowledge/{knowledge.db,nodes/}`；也可与"全量重建 vs 增量修复"一并决策
  - 验收：重跑体检脚本后同名组 = 0，uid5 节点数 311 → **283**
- [ ] **GQ-7 禁止碎片类节点**：prompt 明确不输出"章节摘要/回顾/复习/小结"；入库侧对这类 name 做拦截或降级为 relation
- [ ] **GQ-8 建图写 `confidence`**：给每个节点打 0–1 置信度，低分节点不参与注入/出题（当前 1.6% 有值）
- [ ] **GQ-9 主题隔离**：启用 `board`（当前几乎为空）区分书籍来源；跨学科节点标记待审，杜绝 KMP/最小生成树混入强化学习图
- [ ] **GQ-10 建图 token 消耗落盘**（复用 `token_counter.extract_usage`，写 aggregate + 报告文件）
- [ ] **GQ-11 aggregate 暴露合并数 / 跳过数 / 失败块数**给前端
- [ ] **GQ-12 清理历史噪声**：`123`（29 字手工占位）、`未识别内容片段`、u1 的 `test_*` 测试节点（7 个）
- [ ] **GQ-13 深度 A/B 对照**：同一本书跑"浅（现状）"vs"深"，用 `docs/知识图谱/知识图谱_检索与注入_方法与现状.md` §3 的口径比较出题质量与检索命中率
- [ ] **GQ-14 图谱开/关 A/B 重复运行取均值**：2026-09-12 首跑（5 例）与 2026-09-23 重跑（14 例）**结论相反**（+37.5pp vs -2.4pp）→ 该指标不稳定。需锁定模型版本、每组重复 ≥3 次，给出均值与区间才能对外引用
- [ ] **GQ-15 增强项（幂等键）**：`(user_id, subject, 文件指纹 hash)` → 命中则提示"这批资料已建过，是否只补增量"（per-user 建图互斥已落地 2026-09-26）
- [ ] **GQ-18 溯源贯通（L0，多源维护前置）** 🔴 P0
  - ✅ 存储层（`nodes.sources`）、写入路径（`_record_sources`）、反查消费者（`GET /knowledge/source/{doc_id}/nodes`）均已落地
  - ⏳ 第三处同步：`scripts/inspect_graph_quality.py` 的体检口径待落实
  - ⏳ 刻意暂缓：`edges.sources`（边级溯源，现无消费者，待 L2 冲突层启动时再补）
- [ ] **GQ-19 新资料演进流程（L3，"按资料增量"）** 🔴 P0 —— 5 步（首次建图 = 概念树为空时的特例，走同一路径）：
  1. **出目录**：新资料先抽自己的目录/概念清单（与阶段① 同一动作、同一粒度）
  2. **对账（三分类）**：`新增`（建节点）/ `命中`（覆盖了图谱已有节点 → 打标记，= 增补工作集）/ `无关`（不动）；实现上是**双向匹配**
  3. **标记落表**：`(user_id, doc_id, node_id, status: pending|filled, evidence, created_at)` —— 与 `sources`（GQ-18）分工：`sources` = 「节点 ← 多份资料」长期事实，本表 = 「资料 → 待补节点」过程状态
  4. **切片检索定位原文**：对新增/命中概念，用概念名+摘要检索该资料切片（复用 `kb_manager` 向量 + BM25，不重喂全文）
  5. **增补（只补被标记的）**：新增概念写深节点（GQ-23 单概念独立生成）；被标记节点把该资料新内容**并入**（复用 `_merge_content_into`）；补完转 `filled` + 把 `doc_id` 写入 `sources`
  - 幂等键 `(user_id, subject, doc_id)`；与 GQ-15 **合并**（同一把锁 + 同一张"资料已建"登记表），不要做成两套
  - 验收：加第二本教材后 —— ① 能列出「本次被标记的节点清单」② 新概念挂进正确板块、被标记节点正文被补充、`sources` 正确累积 ③ **未被标记的节点零改动**（`updated_at` 对比验证）
- [ ] **GQ-20 合并决策落表 `merge_decisions`** 🟠 P1：`(kept_id, merged_id, score, evidence, decided_by, ts)` → 支持**回滚**与人工复核
- [ ] **GQ-25 建图失败/跳过对用户可见** 🟠 P1：后端已带 `aggregate.failed_docs`/`skipped_docs`；**前端未做** —— `KnowledgeView.vue` 的 `genResult` 仍只显示 created/skipped nodes。验收：部分资料失败时结果区明确列出"未处理的资料"
- [ ] **GQ-26 溯源粒度：从「章节标题」升到「切片/段落」** 🟠 P2（暂缓）：现状 `chunk_id` 恒为 `None`（索引侧 `doc_chunks` 与建图侧 `collect_units` 是两套独立切分，边界不共享）。三条升级路按用途选一条：① 对齐切分体系（细粒度覆盖统计/冲突裁决）② 加引文（`part_index` + 片段前 ~80 字，人工核对）③ 检索命中记账进当轮 `agent_runs` 证据（回溯 AI 回答依据）

### 数据结构与检索通路（KG-D）

- [ ] **KG-D7~D11（P1 剩余）**：~~`parent_id/path` 层级~~（主题层 2026-09-27 整体下线，**不做层级**）；`sources` 溯源 → **GQ-18**；按 relation 分策略扩跳；跨源 RRF 融合。**KG-D11 结构化选片 ✅ 2026-10-11 落地**（改用「全量骨架常驻 + 焦点 ±2 跳邻域带摘要」，见 `done.md` / 参照系契约 §4.2）
- [ ] **KG-D12~D15（P2）**：FTS5、`confidence` 赋能或删列、向量改 BLOB、`quiz.questions.node_id` 改名

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
- [ ] **P2-② 用实测拐点替换文献值** `[需API]`
  - 框架 §1.2 的 S6/S7 = 2K、S8 = 21K 目前是"文献曲线 + 体量估算"，**不是本项目实测**
  - 做法：固定的 20 轮教学对话，扫 S6/S7 的 top_k 与 S8 的轮数，按正确性/成本画曲线取拐点
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
- [ ] **Batch 1 端到端对话引用**：灌库后对话中挂载「自动采集」目录提问 DSA 概念 → 回答带 KB 引用（先完成上一条）
- [ ] **B2.1 OI-wiki 真网联调**（同 §1 P0 待议项）

### Batch 3 — 题库导入 + web_page 漏斗 + 来源统计

- [ ] **B3.1 结构化题库导入** `[新文件]` `adapters/dataset_quiz.py`
  - 读取开放数据集 JSON（如 CMMLU）→ 映射 `Question` schema → `quiz_store.save_questions`
  - subject/knowledge_point 来自数据集字段；测试 `test_dataset_quiz_adapter.py`
  - **预研项：存储配额/TTL**（入库批量导入**之前**必须完成，防百万题撑爆磁盘）：`storage_quota` 配置 + 超限告警
- [ ] **B3.2 通用网页正文抓取** `[新文件]` `adapters/web_page.py`
  - 站点白名单（个人模式）+ `trafilatura` 主用 / `readability-lxml` 后备；`httpx` 下载 → 正文抽取 → 转 MD 入库（license 由站点映射表给定，默认 L2）；测试 `test_web_page_adapter.py`
- [ ] **B3.4 未做部分**：采集页资源列表条目入口 + 采集前 URL 预览（P0）—— 方案见 `docs/教育资料采集/采集_网页正文与开放来源_实施方案.md` §2.5

### Batch 4 — 登录站 + CollectorRagSource + 本地模型（v1.5+）

- [ ] **B4.1 Cookie 会话复用** `[新文件]` `collector/session.py`：浏览器辅助登录（复用 agent-browser/playwright）→ Cookie 存用户级目录（隔离/不进日志）；httpx 携带 Cookie 抓取；合法需登录站标注 L2
- [ ] **B4.2 CollectorRagSource 评估** `[改]` `rag_pipeline/sources.py` 新增 `CollectorRagSource`（name="collector"）+ `pipeline.register()` 一行；评估是否入对话引用（默认仅在采集页使用）
- [ ] **B4.3 本地 embedding 正式启用** `[改]`：装 sentence-transformers（可选依赖）；`app/core/llm/embed.py` 加 `EMBED_MODE=local|api|auto`，auto 降级链 API→本地→hash；补 `test_embed_mode_config.py`

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
- [ ] **`collector/pipeline_ingest.py` + `chapterizer.py` 零引用**：整书切章入库链路（`ingest_book_chapters`）已实现且 6 例测试通过，但采集链路 `manager.run_task` 直接调 `kb_manager.upload_and_index`，**未接此管线**。需定入口：采集任务整书入库 / 手动导入 / 判定不用后删除。
- [ ] **`conversations` 内嵌 tools/thinking 去留 + run 与会话无关联键**（**待决策**）：需定"是否为 `agent_runs` 加 conversation 外键/会话 id（动 schema）"，或接受现状。
- [ ] **采集库 `busy_timeout` 未收紧**：`collector/store.py` 用默认 5s，而引用计数 UPDATE 跑在事件循环线程 → 极端情况下可能把 loop 卡住最长 5s。**未当场收紧的理由**：`PRAGMA busy_timeout` 是连接级，调小会把锁等待变成 `database is locked`（比卡 5s 更糟）。**正确修法**：给引用计数另开只写连接（或独立线程/队列），而非全局调 busy_timeout。

---

## 9. 待用户拍板

- **深度 vs 成本**：GQ-5/6/16/23 会改变单次建图耗时与 token 量，是否接受"一次建图更慢但质量更高"？（GQ-23 的"单概念独立生成"预计总 token 上升，用成本换深度）
- **是否对已有图谱全量重建**（用新 prompt 重跑）还是只做增量修复（GQ-4 合并 + 补写空壳）？—— 建议先修代码，再挑一本书重建做 A/B（GQ-13），别直接全量重跑。
- **现有 `<400 字拒收`（`GRAPH_MIN_CONTENT_CHARS`）是否改为"标记待补、不拒收"**（保下限但不误杀"短而精"的概念）？
- **多源融合的 4 个开放问题**（`docs/知识图谱/知识图谱_外部调研_论文开源与多源融合.md` §八）：融合单位 / 跨教材推断先修是否算"创造知识" / 冲突是否对学生可见 / 资料删除策略。
