# TutorAgent 评价体系（Assessment）— 调研 + 实施 TODO

> 创建：2026-09-19
> 定位：**调研交付物**，建设期属于其他同学（**非本人职责**）；本文件把"为什么做 / 怎么做 / 验收什么"打包交付，建设者据此动手
> 状态：调研已完成、**建设未启动**

**设计依据**：
- `docs/教学模块/QUIZ_出题逻辑调研.md`（2026-08-29）—— 难度评估方案（IRT 2PL / LLM 模拟自评）
- `docs/教学模块/学习进度仪表盘设计.md`（2026-08-31）—— 展示层（图谱科技树化 + 仪表盘 v1，与评价耦合但未与分析打通）
- `docs/比赛/COMPETITION.md` §5.2 —— 痛点 2 评价反馈（学完没有反馈，看不到进度）
- `docs/比赛/国创/商业计划书_v2_任务分解.md` —— "多维度评价反馈"作为差异化

**标记约定**：
- `[改]` 改现有文件 / `[新文件]` / `[复用]` / `[需API]` 需真实外部服务 / `[待议]` 需建设者先定
- 每项含「落点 + 做法 + 验收 + 依赖」；验收一律可离线跑
- **[交接]** 标记：留给建设者的关键决策点 / 接口约定 / 测试清单

---

## 0. 30 秒交接（建设者从这里开始）

**为什么做**：
- 痛点 2（学完没反馈）已在仪表盘层解决"展示"，但**评价体系本身**（难度评估 / 学习分析 / 学习报告 / 评价指标）尚未建
- 比赛叙事需要"多维度评价反馈"作为差异化（BP/PPT 已立项）
- 当前"评价"逻辑散落在 4 处（`quiz/chat_quiz` + `agent_tools/grade_answer` + `agent_tools/update_mastery` + 仪表盘 + `graph_analyzer`），**无统一抽象层**

**做什么**：
- 建 `backend/app/core/assessment/` 独立模块
- 三阶段：P0 骨架 + 难度评估 → P1 学习分析 + 学习报告 + 评价指标 → P2 把 `quiz/` 中的判分 + 掌握度联动迁过来

**不做什么**（避免越界）：
- 不动 `core/quiz/` 的出题 + schema 部分（保持稳定）
- 不动 `agent_tools/tools/grade_answer.py` 的对外接口（向后兼容）
- **不建独立进度表**（沿用 `nodes.mastery` 单一数据源原则，参 `学习进度仪表盘设计.md` §三）
- **不主动 commit**（项目硬约束，AGENTS.md §1）

---

## 一、现状盘点（建设前必读）

### 已有资产（散落在 4 处）

| 位置 | 内容 | 备注 |
|---|---|---|
| `backend/app/core/quiz/`（8 文件） | `grader.py`(判分)、`chat_quiz.py`(出题判分主入口)、`quality.py`(质量过滤管道)、`schema.py`/`generator.py`/`quiz_store.py`/`exporter.py` | 模块名"quiz"但**已超出出题语义** |
| `agent_tools/tools/grade_answer.py` | 答对自动 +20（**掌握度唯一主信号**） | 入口给 Agent 工具 |
| `agent_tools/tools/update_mastery.py` | 手动调，**仅 3 种硬证据**（早就会 / 没学过 / 自己纠正） | 触发面已收口 |
| `docs/教学模块/学习进度仪表盘设计.md` | 前端展示层（图谱科技树化 v1 已落地） | **只展示不分析** |

### 缺的（评价体系真正的核心）

1. **难度评估**（IRT 2PL / LLM 模拟自评）— `QUIZ_出题逻辑调研.md` §四 提到但**未实现**
2. **学习分析**（薄弱点诊断 / 进步曲线 / 学习模式）— 仪表盘只展示不分析
3. **学习报告**（阶段性总结 / 学习轨迹复盘）— 没有
4. **评价指标体系**（形成性 vs 总结性 / 多维度评分 / 布鲁姆分类法）— 没有
5. **学习画像评价维度**（`core/profile/` 只存不分析）— 没有
6. **学习事件流**（学时 / 活跃度 / 路径回放）— 没有

### 架构问题（为什么必须独立）

- `core/quiz/` 名字叫"quiz"，但实际承载了**判分 + 质量过滤 + 掌握度联动**——语义边界已溢出
- `chat_quiz.py` 既是出题上下文管理，又是判分（`grade_pending_answer`）——单一职责不清
- 判分 + 掌握度 + 评价之间缺少统一抽象层
- 未来加 IRT / 分析 / 报告 / 画像评价维度会继续乱，必须有独立出口

---

## 二、业界参考（直接可落地的方案）

| 来源 | 关键点 | 落地难度 |
|---|---|---|
| CSDN《AI 智能题库系统实战》（2026） | **IRT 2PL 模型** + **LLM 模拟自评** + 难度一致性过滤 | 低（已有调研） |
| JAIT 2025（DOI:10.12720/jait.16.10.1430-1441） | RAG 锚定 + 出题可溯源 | 中（kb + hybrid_search 已具备） |
| EMNLP 2024 Distractor Survey | 干扰项应反映**学生常见认知误区** | 中（quality.py 可加） |
| 布鲁姆分类法（教育学经典） | 记忆/理解/应用/分析/评价/创造 6 档 | 低（schema 已有 `cognitive_level`） |
| OpenMAIC 借鉴方案 | 学习进度可视化 + 阶段总结 | 中（需建报告模块） |

---

## 三、方案对比 + 拍板

| 方案 | 改动面 | 收益 | 风险 | 拍板 |
|---|---|---|---|---|
| **A. 独立 `core/assessment/`** | 大（~15 文件迁移） | 边界清晰、扩展性好、未来加 IRT/分析/报告都有归属 | 回归测试多 | ✅ **2026-09-19** |
| B. 原地扩展 `core/quiz/` | 中（~5 文件） | 改动小、无新模块 | `quiz/` 继续越界 | ❌ |
| C. 最小改动（仅加 difficulty.py 等到 quiz/） | 小（~3 文件） | 零迁移 | 语义最乱、BP 包装难 | ❌ |

**选定 A**，具体落地按 P0/P1/P2 分阶段，每阶段独立验收。

---

## 四、实施路线（建设者按此推进）

### P0 — 骨架 + 难度评估（建议先做，建立存在感）

#### P0-① 建空模块骨架 `[新文件]`
- 落点：`backend/app/core/assessment/__init__.py` + 目录占位
- 不动任何现有文件，纯新增
- 验收：`from app.core.assessment import ...` 可导入（即使内容为空模块）

#### P0-② 难度评估 `[新文件]` `core/assessment/difficulty.py`
- IRT 2PL 模型：`P(θ) = 1/(1+exp(-a(θ-b)))`，a=区分度 b=难度
- LLM 模拟自评：`difficulty = 1 - (0.5×低能力正确率 + 0.3×中能力正确率 + 0.2×高能力正确率)`
- 区分度：`高能力正确率 - 低能力正确率`
- **MVP 建议先做 LLM 模拟自评**（无统计依赖），IRT 作为后续增强
- 依赖：`core/llm/call_llm`（已有，复用）
- 验收：`tests/test_assessment_difficulty.py` ≥5 例（mock LLM 答对率 → 难度计算正确；IRT 参数估计；边界值）

#### P0-③ 评价数据模型 `[新文件]` `core/assessment/schema.py`
- `Attempt`：一次作答记录（user_id, node_id, question_id, score, max_score, correct, timestamp, difficulty_at_attempt）
- `Progress`：掌握度快照（user_id, node_id, mastery, last_updated, attempt_count）
- `Report`：学习报告（user_id, period, summary, weak_points, progress, recommendations）
- 存储决策 → 见 §五 D-A1
- 验收：`tests/test_assessment_schema.py` ≥3 例（构造 / 序列化 / 校验）

#### P0-④ README 与架构文档 `[新文件]` `core/assessment/README.md`
- 模块职责 + 数据流 + 改码坑（沿用 `core/agent/README.md` 模板）
- 验收：文档完整可读

---

### P1 — 学习分析 + 学习报告 + 评价指标

#### P1-① 学习分析 `[新文件]` `core/assessment/analytics.py`
- 薄弱点诊断：哪些节点 mastery 长期 < 30 且被推荐多次？
- 进步曲线：近 7/30/90 天 mastery 平均值趋势
- 学习模式：何时学 / 学多久 / 卡在哪个难度
- 复用：`core/knowledge_graph.py::get_node_mastery`（已有）
- 验收：`tests/test_assessment_analytics.py` ≥5 例（mock Progress 列表 → 薄弱点/进步曲线计算正确）

#### P1-② 学习报告 `[新文件]` `core/assessment/report.py`
- 周报 / 月报：阶段总结 + 薄弱点 + 进步 + 推荐
- **方案二选一**（见 D-A3）：LLM 总结（成本高但质量好）vs 模板填充（成本低但单调）
- 验收：`tests/test_assessment_report.py` ≥3 例（模板填充报告生成正确）

#### P1-③ 评价指标体系 `[新文件]` `core/assessment/metrics.py`
- 形成性评价（过程）+ 总结性评价（结果）+ 多维度评分（认知层级 + 掌握度 + 进步 + 应用能力）
- 布鲁姆分类：6 档（记忆/理解/应用/分析/评价/创造）
- 验收：`tests/test_assessment_metrics.py` ≥3 例

#### P1-④ API 端点 `[新文件]` `app/api/v1/assessment.py`
- `GET /assessment/report?period=week|month`：学习报告
- `GET /assessment/difficulty/{question_id}`：单题难度
- `GET /assessment/analytics`：薄弱点 + 进步
- 验收：`tests/test_assessment_api.py` ≥5 例

---

### P2 — 拆分迁移（可选，建设期充裕再做）

#### P2-① `quiz/grader.py` → `assessment/grader.py` 迁移 `[改]`
- 保持 `grade_question` 接口不变（向后兼容）
- `quiz/grader.py` 改为 `assessment.grader` 的薄委托（保持调用链不破）
- 验收：现有 `tests/test_quiz_grader.py` 全绿 + 新模块等价测试

#### P2-② `chat_quiz.grade_pending_answer` 拆出 mastery 更新策略 `[改]`
- 把"答对 +20 触发 mastery 更新"逻辑迁到 `assessment/mastery.py`
- `chat_quiz.grade_pending_answer` 改为薄委托
- 验收：`tests/test_chat_quiz.py` + `tests/test_tools_registry.py` 全绿

#### P2-③ `grade_answer` 工具调用链重接 `[改]`
- `agent_tools/tools/grade_answer.py::handler` 改为调 `assessment.grader` 而非 `chat_quiz.grade_pending_answer`
- DESCRIPTION / GUIDANCE 不变
- 验收：`tests/test_agent_loop.py` + `tests/test_tools_registry.py` 全绿

---

## 五、决策点（建设者开工前先拍板，别自己定）

| # | 决策点 | 影响 | 默认建议 |
|---|---|---|---|
| **D-A1** | 数据存储：复用 `quiz_store.attempts` 表 vs 新建 `assessment_*.json` vs 扩展 `nodes` 表？ | P0-③ 验收 | **复用 attempts 表**（最小改动，DB 不动 schema） |
| **D-A2** | 难度评估自评时机：每次出题都跑（成本高）vs 抽样（每周 N 题）vs 异步后台批跑？ | P0-② 性能 + 成本 | **异步后台批跑**（每周 N 题抽样，不阻塞出题） |
| **D-A3** | 学习报告生成：LLM 总结 vs 模板填充？ | P1-② 成本 / 质量 | **MVP 模板填充**，LLM 作为可选增强 |
| **D-A4** | 是否建独立 `study_events` 表（学时统计 / 路径回放）？ | 数据模型 | **暂不建**（沿用对话 `updated_at` 估算，参 `学习进度仪表盘设计.md` §P3） |
| **D-A5** | P0/P1/P2 顺序：是否一定先 P0 完整再做 P1？ | 整体节奏 | **P0 完整 → P1 → P2**，每阶段独立验收 |

---

## 六、测试与验收

每阶段独立验收，**离线可跑**（除特别标注 `[需API]`）：

| 阶段 | 验收项 | 命令 |
|---|---|---|
| **P0** | 难度评估 + schema + README | `pytest backend/tests -q -m "not llm_api" -k assessment` |
| **P1** | 学习分析 + 报告 + 指标 + API | 同上 |
| **P2** | 全量回归 = **711 passed, 7 deselected**（2026-09-15 实测基线） | `pytest backend/tests -q -m "not llm_api"` |

**新增测试覆盖**（每阶段最少）：
- P0：≥11 例（难度 5 + schema 3 + README 引用 3）
- P1：≥16 例（分析 5 + 报告 3 + 指标 3 + API 5）
- P2：保持现有回归（不增不减）

---

## 七、风险与依赖

| 风险 | 影响 | 缓解 |
|---|---|---|
| **真实 embedding 欠费** | 难度自评 / 学习分析的语义去重需 embedding → 欠费时降级 | 复用 `hybrid_search` 降级链：API → 本地 → hash（参 `core/kb/embedder.py::get_embedder`） |
| **评测数据集缺失** | 评估难度评估质量需历史作答数据 → 暂无 | 先用 mock 数据验证管线；真实评估 = 比赛后用 1 个月用户数据 |
| **比赛时间线（10-15 双截止）** | 评价体系属 P2 加分项，若时间紧 | **只做 P0 难度评估**作为亮点，P1/P2 推到赛后 |
| **并发写入** | 仓库常有多个 AI 会话并发 | 提交按文件族拆、`git add` 路径限定、不主动 commit |
| **`nodes.mastery` 单一数据源** | 任何对 mastery 的改动要保证一致性 | 复用现有 `update_mastery` 调用链，不另开入口 |

---

## 八、相关文档与跳转（建设者按需查阅）

| 要查什么 | 去哪 |
|---|---|
| 端点 / 请求响应 schema | 起后端后 `http://localhost:8000/docs`（**唯一端点级权威**） |
| 整体架构与快速上手 | 根 `README.md` |
| **Agent 内核逐模块 / 改码坑** | `backend/app/core/agent/README.md` |
| **工具系统（一工具一文件、三条硬约定）** | `backend/app/core/agent_tools/tools/README.md` |
| **LLM 原语包** | `backend/app/core/llm/README.md` |
| 上下文工程（预算 / 裁剪 / 预估） | `docs/上下文工程/上下文工程_预算框架.md` + `TODO_Context.md` |
| 知识图谱（**唯一参照 = 参照系契约**） | `docs/知识图谱/知识图谱_参照系契约.md` |
| RAG / 文档解析 / 检索 / 出题 | `docs/RAG/RAG_*.md`、`docs/教学模块/QUIZ_出题逻辑调研.md` |
| 实施模式参照 | `TODO_Context.md`（P0/P1/P2 + 30 秒交接 + 测试验收） |
| 顶层总览 | `TODO.md` |
| 采集模块参照 | `TODO_Collector.md`（Batch 划分 + 里程碑表） |
| 部署运维 | `deploy/README.md` → `docs/运维部署/Docker_*.md` → `docs/运维部署/运维_*.md` |
| 历程 / 决策 / 踩坑 | `docs/项目历程_决策与效果记录.md` |
| 跨会话决策与硬约束 | `.codebuddy/memory/MEMORY.md` |

---

## 九、留给建设者的备忘（开工必读）

1. **开工前必读** §0「30 秒交接」+ §三「方案对比」+ §五「决策点」+ §九本节
2. **任何对 `core/quiz/` 的改动必须先看** `core/quiz/` 下 README（如缺先建）+ `quiz_store.py` 模型
3. **任何对 mastery 字段的改动必须先看** `AGENTS.md` §1 硬约束中"掌握度更新的唯一主信号 = 出题判分"
4. **任何对 `agent_tools/tools/` 的改动必须先看** `core/agent_tools/tools/README.md`「一工具一文件 + 三条硬约定 + 检查清单」
5. **不主动 commit**（项目硬约束）：按文件族拆、测试与修复同提交、提交前跑离线全量；并发时 `git status` 先看清别人 WIP
6. **测试基准**：`pytest backend/tests -q -m "not llm_api"` → 期望 **745 passed, 7 deselected**（2026-09-20 实测）
7. **Python 必须用** `backend/venv/Scripts/python.exe`（系统 Python fastapi 过旧会导入失败）
8. **uvicorn 必须 `--workers 1`**（EventBus 用户队列与进程内定时 GC 依赖单进程）
9. **`.env` 只有根目录一份**，不要在 `backend/` 下另建
10. **遇到坑**先查 `docs/项目历程_决策与效果记录.md` + `.codebuddy/memory/MEMORY.md` + `.codebuddy/memory/2026-09-19.md`