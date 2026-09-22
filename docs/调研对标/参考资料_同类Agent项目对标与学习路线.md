# 参考资料：同类 AI Tutor / 学习 Agent 项目对标与学习路线

> 创建：2026-08-31
> 定位：调研得到的**链接 + 内容整理**，供后续实现参考。`docs/调研对标/标杆项目对标分析.md` 给出差距结论与优先级，本文件给出**一手资料清单**（仓库 / 论文 / 文章），学习时对照代码。
> 用法：按项目分类，每条标注类型（开源仓库/论文/文章/官网）；实现时按"五、实现路线"逐项对照链接回看。

---

## 〇、本次调研背景

为回答"作为一个工具适配度高的 tutor 工具，我们还差什么"，拉取了 4 个同类开源项目做功能矩阵对比：

| 项目 | 仓库 | 关注点 |
|------|------|--------|
| DeepTutor | https://github.com/HKUDS/DeepTutor | 终身个性化辅导私教，Agent-Native 多智能体，37k+ star |
| adaptive-edu | https://github.com/Qintsg/adaptive-edu | 知识图谱自适应学习，三端，获"超星杯 AI+教育"二等奖 |
| Autonomous Learning System (ALS) | https://github.com/ydsgangge-ux/autonomous_learning_system | 个人"认知工厂"，SM-2 间隔复习 + 元认知审计 |
| OpenMAIC | https://github.com/THU-MAIC/OpenMAIC | 智能学习助手，出题模块（已借鉴落地） |

---

## 一、DeepTutor（HKUDS，对标首选）

### 链接
- 仓库：https://github.com/HKUDS/DeepTutor （Apache-2.0，v1.6.2，37k+ star）
- 官网：https://deeptutor.info
- 论文：DeepTutor: Agent-Native Personalized Learning with Long-term Deep Memory（arXiv:2604.26962）
- 中文解读：https://zhuanlan.zhihu.com/p/2068009707298632414 （Agent-Native 多智能体架构 + 学习闭环拆解）
- 技术栈：Python 3.11-3.13 + FastAPI + Next.js/React + WebSocket；LlamaIndex / GraphRAG / LightRAG 多引擎 RAG；MinerU / Docling 文档解析

### 内容整理
DeepTutor 定位是"终身个性化辅导私教"，核心是 **Agent-Native 统一运行时**：所有功能模式（导学、阅读、出题、对话……）都挂在同一个 ChatOrchestrator Agent Loop 上，共享会话上下文，而不是每种模式一个独立流程。四个能力块：

1. **知识获取**
   - Mastery Path：把知识组织成"关卡式"路径（课程→概念→测验→关卡→掌握度），分级通关，进度可视化
   - Book Engine（活书编译器）：教材 → 文本/测验/闪卡/时间线块四种"学习块"，每页悬浮"AI 助教"
   - Immersive Reading：文档与对话并排，按页引用（学术论文场景很实用）
   - 多引擎 RAG 抽象：LlamaIndex/GraphRAG/LightRAG 可切换，适配不同数据结构
2. **知识强化**
   - Interactive Quiz：自适应难度测验 + 答案分析
   - 三大可审计记忆（L1/L2/L3）：L1 事件轨迹 / L2 结构化摘要 / L3 综合反思，全部可溯源审计
   - Memory Graph：实体+关系图谱承载长期记忆（≈我们的知识图谱）
3. **研究产出**
   - Deep Research：生成带引用来源的研究报告
   - 多智能体：Subagents（专家角色）+ Partners（多渠道部署，15+ IM 平台接入）
   - Tools：支持计算器、画布、office 技能（子进程沙箱）等

### 可借鉴点（对应本项目）
| DeepTutor 特性 | 本项目现状 | 差距 |
|---------------|-----------|------|
| 统一 Agent 运行时（ChatOrchestrator） | 已有两阶段流式 + function calling（Agent Loop 雏形） | △ 多模式共享上下文需强化 |
| Mastery Path 分级通关 | 学习路径（Kahn 拓扑）+ 掌握度字段 | △ 缺"关卡化"和进度可视化 |
| 三层记忆 L1/L2/L3 | 用户画像 v2（≈L2 结构化） | ✗ L1 事件轨迹、L3 综合反思未做 |
| Immersive Reading | 知识库目录树阅读 | ✗ 无文档-对话并排 |
| Book Engine 学习块 | 知识库文件/节点 | ✗ 无测验/闪卡块 |
| Deep Research | 递归式教学 | ✗ 无带引用的研究报告 |
| 多引擎 RAG 抽象 | 混合检索（text-embedding-v4 + BM25） | △ 单引擎，接口可抽象 |

---

## 二、adaptive-edu（Qintsg，知识追踪参考）

### 链接
- 仓库：https://github.com/Qintsg/adaptive-edu （AFL-3.0，上海第二工业大学，获"超星杯 AI+教育"应用创新赛二等奖）
- 技术栈：Vue3 + Django + PostgreSQL + Neo4j + Qdrant + GraphRAG + LangChain + DeepSeek

### 内容整理
知识图谱驱动的个性化自适应学习系统，三端（学生/教师/管理员）：

1. **知识追踪（Knowledge Tracing）**：自研 **MEFKT**（Memory-Enhanced Factorized Knowledge Tracing）模型，multi-head attention + 记忆增强，预测学生对知识点的掌握概率；并**与规则算法并行兜底**，防止模型空窗期失效
2. **个性化路径规划**：图谱 + 掌握度 → 规划学习路径 → 任务学习 → 阶段测试 → 反馈报告，形成闭环
3. **A3 多智能体资源生成**：Agent 自动生成教学资源
4. **教学管理**：课程资产导入 / 验收工具链

### 可借鉴点（对应本项目）
| adaptive-edu 特性 | 本项目现状 | 差距 |
|------------------|-----------|------|
| MEFKT 知识追踪 | 掌握度是简单启发式（读/学/掌握标记） | △ 无模型化预测；可先做轻量交互事件模型 |
| 初测→阶段测→报告闭环 | quiz 出题/判分独立存在 | ✗ 未与图谱掌握度、复习计划联动 |
| 三端（学生/教师/管理员） | 仅学生端 + admin | ✗ 教师端未做（参赛加分项） |
| 规则+模型并行兜底 | — | 思路可借鉴：模型路径失败自动降级规则 |

---

## 三、Autonomous Learning System（ALS，SM-2 间隔复习参考）

### 链接
- 仓库：https://github.com/ydsgangge-ux/autonomous_learning_system （MIT，v3.2）
- 技术栈：FastAPI + SQLite + ChromaDB + NetworkX + APScheduler

### 内容整理
定位个人 AGI 学习的"认知工厂"，流水线：

1. **元认知审计（Meta-Cognition Audit）**：LLM 对学习状态/知识结构进行自我审计
2. **因果推理**：概念间因果链提取（进 NetworkX 图谱）
3. **沙盒验证**：代码级验证知识（sandbox）
4. **跨域融合**：跨学科类比推理
5. **间隔复习调度**：**SM-2 算法** + APScheduler 定时调度，到期推送复习任务
6. **知识缺口主动探索**：检测薄弱 → LLM 生成针对性学习任务

### 可借鉴点（对应本项目）
| ALS 特性 | 本项目现状 | 差距 |
|---------|-----------|------|
| **SM-2 间隔复习** | ✗ 完全没有 | 最关键缺口，学习工具"抗遗忘"标配，纯 Python 可落地 |
| 知识缺口检测+任务生成 | 图谱可看薄弱节点 | △ 无 LLM 驱动"缺口报告 + 推荐任务" |
| 元认知审计 | 画像 + 对话 | △ 可做成定期"学习状态报告" |

---

## 四、OpenMAIC（THU-MAIC，已借鉴）

### 链接
- 仓库：https://github.com/THU-MAIC/OpenMAIC （AGPL-3.0，清华大学）
- 技术栈：Next.js + React + TypeScript

### 内容整理
基于大模型的智能学习助手，核心两块：**智能出题 + 知识图谱**。我们的 `backend/app/core/quiz/` 出题模块（schema/generator/grader/quality/quiz_store）即由此借鉴落地，API：`POST /quiz/generate`、`GET /quiz/questions`、`POST /quiz/{id}/grade`、`GET /quiz/stats`，错误码 E-QUIZ-001~005。

### 已落地成果
- 依据 = 上传教材 KB 混合检索
- 生成 → 质量过滤（quality）→ 判分（grader）→ 统计（quiz_store）

---

## 五、实现路线（后续按此推进）

> 依据 `docs/调研对标/标杆项目对标分析.md` 的三维排序（演示冲击力 + 实现成本 + Agent 赛道契合度），6 周窗口内建议：

### 🥇 第一梯队（2-4 周）
1. **学习进度仪表盘 + 掌握度可视化**
   - 后端 `GET /knowledge/stats`：总节点/掌握/学习中/未学、平均掌握度、按学科/板块分布
   - 前端 HomeView 仪表盘面板（进度条 + 图表）
   - 参考：DeepTutor Mastery Path 的进度展示
2. **间隔复习（SM-2）**
   - 卡片 = 图谱节点 + 掌握度；SM-2 调度（ease_factor / interval / due_date / lapses）
   - API：`GET /review/due`、`POST /review/{node_id}/grade`（Again/Hard/Good/Easy）
   - 参考：ALS `planning/` 模块、SM-2 算法（super-memo 经典实现）
3. **测评闭环（quiz ↔ 图谱联动）**
   - 出题按薄弱节点优先选题；判分结果回写节点掌握度
   - 打通"诊断→练习→反馈"完整链路

### 🥈 第二梯队
4. **主动知识缺口检测报告**：LLM 基于图谱掌握度 + 会话历史生成"薄弱清单 + 推荐任务"（参考 ALS exploration）
5. **复习卡导出（自包含 HTML）**：已有 TODO 计划，演示加分
6. **轻量多角色讨论（"AI 同学"）**：prompt 级改造，展示 Agent 多角色能力

### 🥉 第三梯队（赛后 / 论文方向）
7. **MEFKT 知识追踪**（参考 adaptive-edu，arXiv 论文 + 官方仓库）
8. **三层记忆架构 L1/L2/L3**（参考 DeepTutor：事件轨迹 JSONL → 画像 → 周期综合）
9. **沉浸式阅读 / Deep Research / 多智能体 subagent**（大工程，赛后再议）

---

## 六、延伸论文 / 资料（按需深入）

| 主题 | 链接 | 说明 |
|------|------|------|
| DeepTutor 论文 | arXiv:2604.26962（见仓库 README） | Agent-Native 学习闭环 |
| MEFKT 知识追踪 | adaptive-edu 仓库文档 / README | Memory-Enhanced Factorized KT |
| SM-2 算法 | https://super-memo.com/en/archives1990/english-html （经典算法原文） | 间隔复习调度 |
| RAG-Fusion | arXiv:2402.03367 | 多查询生成 + RRF 融合（我们已落地 RRF 部分） |
| HyDE | arXiv:2312.04467 | 零样本假设文档增强检索（待做，需真实 LLM key） |
| 多智能体学习系统综述 | 500-AI-Agents-Projects 等聚合列表（GitHub 搜索 "AI tutor agents"） | 横向参考 |

---

## 七、结论一句话

TutorAgent 底座（图谱 + 混合 RAG + Agent 工具链）已强于多数同类，**缺的是"学习工具化"表层模块：间隔复习、仪表盘、测评闭环、缺口报告**。这些模块评审一眼可见"学习产品感"，且纯 Python + 复用现有图谱/quiz 即可落地，是未来 6 周最优投入方向。
