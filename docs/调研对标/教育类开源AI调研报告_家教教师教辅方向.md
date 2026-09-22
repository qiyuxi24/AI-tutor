# 教育类开源 AI / Agent / Skills 调研报告（家教 · 教师 · 教辅方向）

> 创建日期：2026-09-05
> 调研人：TutorAgent 团队（AI 辅助）
> 状态：**已搁置** —— 报告结论已沉淀；未进入代码落地，待备赛后期按需取用
> 关联文档：`docs/调研对标/标杆项目对标分析.md`（功能矩阵）、`docs/调研对标/OPENMAIC_借鉴方案.md`（已落地借鉴）、`docs/教育资料采集/教育资料采集模块_设计讨论.md`（采集模块设计）
> Star 数据：GitHub API 查询，2026-09-05

---

## 一、调研目标与方法

用户诉求：找"老师 / 家教 / 教辅"一类、知名且 star 数较高的开源 AI / Agent / Skills，克隆到本地并分析能否用于 TutorAgent。

方法：
1. 多轮中英文 web 搜索收集候选池（覆盖 AI 家教、多智能体课堂、教师备课/批改、错题/题库、教育 Skills 等细分方向）
2. GitHub API 核实各仓库真实 star / 语言 / 协议 / 体量，剔除低 star 与无关项目
3. codeload zip / git shallow clone 落地仓库（github git 协议时通时断，codeload zip 最稳）
4. 派出 code-explorer 子代理通读源码，输出"模块 - 功能 - 源码路径 - 可移植性"
5. 结论映射回 TutorAgent 现状（对标 `docs/调研对标/标杆项目对标分析.md` 功能矩阵与 TODO.md）

---

## 二、候选项目总览（Star 已核实）

| 项目 | Star | 定位 | 协议 | 是否拉取 | 结论 |
|------|------|------|------|---------|------|
| **HKUDS/DeepTutor** | 38,773 | 港大"终身个性化 AI 私教"，Agent-Native 学习工作台（Chat/Quiz/Research/Visualize/Mastery Path/Reading），多智能体 + 多引擎 RAG | Apache-2.0 | ✅ 源码全量落地 | **重点分析对象**，5 个可移植点 |
| **THU-MAIC/OpenMAIC** | 31,660 | 清华多智能体互动课堂（AI 老师 + AI 同学 + 课件/测验/回放） | MIT（v0.3.0 起从 AGPL-3.0 调整） | ⚠️ 克隆中断未重拉 | 已有 `docs/调研对标/OPENMAIC_借鉴方案.md` 全覆盖，quiz 已落地 |
| **ECNU-ICALK/EduChat** | 968 | 华东师大开源中英教育对话大模型（模型 + 训练数据） | 侧重微调 | ❌ | 需 GPU 训练，与本项目"阿里云 API"架构无关 |
| **flysheep-ai/education-skills** | 92 | 中国高考 6 科导师 Skills（苏格拉底式教学） | MIT | ✅ 已落地 | 教学法 prompt 金矿 |
| **hezkvectory/hermes-edu-skills** | 87 | **170 个中文教育 Skill**（教材同步/备考/错题复盘/教师工具等 8 大类） | 见 LICENSE | ✅ 已落地 | 出题/批改/错题流程可抄 |
| Open-TutorAi/open-tutor-ai-CE | 100 | 通用教育协作平台 CE 版 | — | ❌ | star 低、JS 平台、无增量价值 |
| dfwgj/Wise-Will-agent | 0 | 开源教育 agent 产品 | — | ❌ | 未形成社区 |
| yqy624/eduagent-ai-platform | 3 | FastAPI+LangGraph+RAG 智慧教育平台 | — | ❌ | 低 star 样例级 |
| xdelin/awesome-agent4edu | 3 | 教育 Agent 资源索引列表 | — | ❌ | 仅列表，需时 web 查阅 |

**关键观察**：star 头部（万级）基本被 DeepTutor / OpenMAIC 垄断；"教师 / 教辅"细分方向的强能力反而沉淀在低 star 的 **Skills 仓库**中（prompt 型资产，体积 KB 级，可直接抄）。真正值得看的就两类：① 全栈头部平台（看架构思想）② 中文教育 Skills（看教学流程 / prompt）。

---

## 三、仓库落地位置

- 根目录：`.research/edu-repos/`（已在 `.gitignore` 中，不污染 git）
- 已拉取：`DeepTutor/`（完整，~250MB，含 `deeptutor/` Python 后端 + `web/` Next.js 前端）、`hermes-edu-skills-main/`、`education-skills-main/`
- OpenMAIC：克隆中断仅剩空 `.git`（已删残留）。如需可后续用 codeload zip 慢速补拉，或直接 web 查阅

---

## 四、DeepTutor 源码级分析（重点）

### 4.1 定位
港大 HKUDS，38.8k★，周更活跃（调研时最新 v1.6.4，2026-09-03）。Apache-2.0 商用安全。核心理念：把 Chat/Quiz/Research/Visualize/Solve/Mastery Path/Reading 等十余种学习模式统一进一个 Agent Loop（`ChatOrchestrator`），多智能体 + LlamaIndex/GraphRAG/LightRAG 多引擎 RAG。

### 4.2 后端模块地图（deeptutor/，源码实测）
| 顶层包 | 一句话职责 |
|--------|-----------|
| `api/` | FastAPI 入口与 REST/WS routers（按 knowledge/book/reading/mastery_path/question 分文件） |
| `runtime/` | 运行编排：`ChatOrchestrator` + capability/tool 注册表 + agentic 循环（`runtime/orchestrator.py`） |
| `capabilities/` | "学习模式"插件集（mastery/solve/reading/course_study…），各子包自带 tools+prompts |
| `learning/` | Mastery Path 纯引擎（掌握度/间隔重复/判分/仓储），pydantic、无 LLM 依赖 |
| `services/memory/` | L1/L2/L3 三层记忆子系统 |
| `agents/` | 统一 BaseAgent + pipeline agent（question/research/math_animator…） |
| `book/` | "活书"编译引擎（输入 → 交互式 Book） |
| `knowledge/` | 知识库/KB 管理与 RAG 索引 |
| `tools/` | 内置工具集，`builtin_specs.py` 字符串路径懒加载声明 |
| `multi_user/` | 多用户/家长-学习者账户（guardian/learner） |

### 4.3 最值得 TutorAgent 借鉴的 5 点（按价值/移植成本排序）

1. **掌握度引擎整体移植** — `deeptutor/learning/mastery.py` + `policy.py` + `models.py`（纯函数零 LLM）：
   recency 加权 0..1 分 + confidence cap；`is_mastered/next_objective` 用 gate 判定"通关"，MEMORY/PROCEDURE ≥0.9，CONCEPT/DESIGN 走 Feynman 定性评估（`mastery_assess`）；`next_objective` 直接返回"下一步动作"枚举。
   → **直击本项目短板**：现有图谱节点 mastery 是简单启发式（对标文档已识别），可把该纯函数引擎映射到图谱节点上重写。
2. **"引擎持答案、模型现场出题"判分模式** — `learning/grading.py` + `PendingQuestion`：
   题目+标准答案经 `mastery_quiz` 工具注册进引擎持久化，引擎持答案跨轮次确定性判分，模型自己看不到答案（防泄题）。
   → 增强现有 `quiz/` 模块的判分与防作弊。
3. **Mastery 是"chat loop 之上挂一层 capability"而非独立状态机** — `capabilities/mastery/`：
   复用整张 chat 工具面 + 自有工具挂载，与 function calling 架构天然契合；而非为每个模式写独立状态机。
4. **中文 tutor 教学 prompt 现成** — `capabilities/mastery/prompts/zh/system.md`（另有 en 版）：
   可对照本项目"自适应引导/递归式教学"系统提示词逐段取长。
5. **L1/L2/L3 分层记忆** — `services/memory/`（注意：用户记忆不是 graph）：
   L1=每 surface 每日 append-only JSONL 事件轨迹；L2=每 surface 一份 Markdown 总结；L3=四个槽位（recent/profile/scope/preferences）；LLM consolidator 做 L1→L2→L3 增量 merge。
   → 比现在单层 JSON 画像成熟，对标文档已列为第三梯队架构亮点。

### 4.4 不建议整库集成的理由
- 体量极大（前后端 + 250MB 仓库），远超本项目轻量架构（FastAPI + SQLite + Vue3）
- 移动/桌面/IM 渠道（Partners、CLI）等大量能力与参赛展示无关
- 正确姿势 = **抽取高价值低耦合模块自研**（与 OpenMAIC 借鉴同策略），Apache-2.0 下无合规风险

---

## 五、OpenMAIC（复用既有方案，不重复调研）

- 31.7k★，多智能体课堂。**AI 出题模块（quiz/）已由本项目借鉴落地**（`docs/调研对标/OPENMAIC_借鉴方案.md`）
- 该文档已给出完整模块清单与优先级（两阶段流水线、动作引擎、服务商抽象、AI 同学多角色等），本次不重复
- 若后续需要看源码，用 codeload zip 补拉即可

---

## 六、Skills 类仓库分析

### 6.1 Hermes Edu Skills（hezkvectory/hermes-edu-skills，87★，170 个中文 skill）
> 原仓库 `zhongweiv/hermes-edu-skills`（CSDN/知乎引流文指向）已无法通过 API 查询，当前以 fork/续作 `hezkvectory` 为准。

**组织规范**（全库统一）：
- SKILL.md frontmatter：`name/description/version/author/license/platforms/metadata`；`metadata.hermes` 是核心索引（tags/source/workflow/category/stages/subjects/abilities/scenarios/quality_tier/requires_tools…）
- 每个 skill **仅一个 SKILL.md**（无 scripts 子文件），正文固定节：Problem / Best For / Not For / Inputs / Recommended Workflow / Output Format / **Quality Checks** / **Standalone Fallback** / Example Prompts / When To Use / Invocation Signals
- `catalog.json`：全库 14k 行元数据索引，供外部按元数据检索/路由

**8 大类**（skills/ 下，共 170）：teacher-tools(31)、exam-prep(27)、daily-practice(28)、textbook-sync(41)、learning-core(15)、reading-writing(10)、language-learning、career-learning、family-education

**与本项目相关的 skill**：
- 教师出题 = `teacher-{数/物/化/生/语/英/史/地/政/通用}-homework-generation`；备课教案 = `*-lesson-planning`；单元复习 = `*-unit-review`；学情分析 = `teacher-class-analysis-lite`；家长报告 = `teacher-parent-report-lite`
- 备考冲刺 = exam-prep 下 `senior-gaokao-sprint`/`junior-zhongkao-sprint`/`primary-final-review`/`college-cet4/6`/`postgraduate-english`/`teacher-certification` 等
- 错题复盘 = learning-core 下 `agent-mistake-review`；答疑 = `agent-question-explanation`；苏格拉底导师 = `agent-socratic-tutor`

**可直接抄的流程资产**：
- 分层出题：**基础/提高/挑战 = 60/30/10** + 质量门（题量匹配时长、难度层次清晰、答案可核验、不得含混/超纲）→ 约束本项目 `quiz/` 生成（与 OpenMAIC 出题互补，禁复刻真题）
- 错题四步闭环（`agent-mistake-review`）：判错因 → 订正说明 → 抽象错误模式 → 生成重做+同类题+间隔复习动作；质量检查强调"错因必须基于题目与原答案、避免一切归为粗心、复习动作可执行"→ 与 TODO 的 SM-2 间隔复习设计直接衔接
- 教案/学情报告框架（`lesson-planning`、`class-analysis-lite`）
- Standalone Fallback 设计思想（无平台工具时也给出降级方案）

**绑定 Hermes 运行时、无法直接用的部分**：`requires_tools`（context.load / entitlement.check / workflow.create / plan.generate / memory.write / mistake.query_recent / practice.grade_answers）→ 需替换为 TutorAgent 后端已有的 function-calling 工具；`catalog.json` 的元数据契约、`export_mode/release_channel` 依赖平台，Web 对话 agent 不适用。

### 6.2 flysheep-ai/education-skills（92★，MIT，6 个高考科目 tutor）
- 结构：`gaokao-science-tutor` / `gaokao-liberal-arts-tutor` / `gaokao-chinese-tutor` / `gaokao-english-tutor` / `gaokao-general-tech-tutor`；每个含 SKILL.md + README + 可选 examples/reference
- **教学法注入方式**（最值得抄的部分）：纯叙述体人格 prompt
  - 四大原则：不直接给答案、渐进式引导每次只推一小步、苏格拉底式提问、高三老师语气
  - 五步流程：理解题目 → 激活已有知识 → 引导建立思路 → 检查理解 → 总结提升
  - 分科引导语 + 情景脚本（"没思路 / 思路错了 / 只要答案 / 焦虑"各给接法）

---

## 七、与 TutorAgent 现状映射与落地建议

> 参照 `docs/调研对标/标杆项目对标分析.md` 功能差距表。以下为调研新增/强化的建议，按性价比排序。

| # | 建议 | 来源 | 落点（TutorAgent） | 成本 |
|---|------|------|-------------------|------|
| 1 | 苏格拉底 + 渐进式教学策略注入自适应引导模式 | flysheep 四原则/五步/情景脚本 + Hermes socratic-tutor | `chat_service._build_system_prompt()` | 1~2 天，纯 prompt |
| 2 | 分层出题约束（60/30/10 + 质量门） | Hermes homework-generation | `quiz/` 生成 prompt 与质量过滤 | 0.5~1 天 |
| 3 | 错题复盘闭环（错因分类→同类重做→间隔复习动作） | Hermes mistake-review | 与 SM-2 间隔复习 TODO 一并设计 | 中（并入复习模块） |
| 4 | 掌握度引擎重写（recency + gate + next_objective） | DeepTutor `learning/` 纯函数 | 图谱节点 mastery 计算 | 2~3 天 |
| 5 | 判分防泄题（引擎持答案跨轮次） | DeepTutor PendingQuestion | `quiz/` 判分 | 中 |
| 6 | 教案 / 学情报告框架 | Hermes lesson-planning / class-analysis | 演示"教师视角"卖点 | 低-medium |
| 7 | L1/L2/L3 分层记忆（思想级） | DeepTutor `services/memory/` | 用户画像演进 | 架构级，赛后可做 |

**不适合投入的方向**：整库集成 DeepTutor/OpenMAIC；EduChat 类模型项目；Open-TutorAI 等低 star 平台。

---

## 八、搁置记录

- 2026-09-05：用户确认此话题**先搁置**，不进入代码落地。
- 已保留资产（随时可取用）：
  - `.research/edu-repos/` 下 3 个仓库源码（DeepTutor 250MB / Hermes 170 skills / flysheep 6 skills）
  - 本报告 + `docs/调研对标/标杆项目对标分析.md` + `docs/调研对标/OPENMAIC_借鉴方案.md`
- 后续若重启：建议从上表 #1/#2/#4 任一项切入，均不与在研的"教育资料采集模块"冲突。

---

## 参考链接

- DeepTutor：https://github.com/HKUDS/DeepTutor （docs: deeptutor.info；arXiv 2604.26962）
- OpenMAIC：https://github.com/THU-MAIC/OpenMAIC （在线 demo: open.maic.chat / openmaic.io）
- Hermes Edu Skills：https://github.com/hezkvectory/hermes-edu-skills
- flysheep education-skills：https://github.com/flysheep-ai/education-skills
- EduChat：https://github.com/ECNU-ICALK/EduChat
