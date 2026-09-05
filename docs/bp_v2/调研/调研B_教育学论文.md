# 调研 B：AI-tutor 教育学论文调研报告

> **调研者**：general-purpose-2 子 agent
> **项目**：AI-tutor · 大模型驱动的主动交互导学系统
> **用途**：为商业计划书第 2 章"根本痛点"和第 4 章"技术壁垒-教学方法论创新"提供学术理论支撑
> **调研日期**：2026-09-05
> **总字数**：约 3,600 字
> **配套任务分解**：docs/商业计划书_v2_任务分解.md

---

## 一、检索式练习 / Testing Effect（最重要）

### 核心论文

- **Roediger & Karpicke (2006)** "Test-Enhanced Learning: Taking Memory Tests Improves Long-Term Retention" *Psychological Science* 17:249-255
  - 经典实验：本科生读短文后，SSSS（重复读 4 次） vs STTT（读 1 次 + 测试 3 次）
  - **关键数据**：即时测试中 SSSS 略优（6% 差距）；**1 周后 STTT 长期回忆率 61% vs SSSS 仅 40%，反转 14 个百分点**；延迟越长，主动检索优势越大
  - 一句话结论：**"测试不仅是评估手段，更是强大的学习事件"**——延迟条件下的测试比重复学习保留率高约 50%
  - DOI: 10.1111/j.1467-9280.2006.01693.x

- **Karpicke & Roediger (2008)** "The Critical Importance of Retrieval for Learning" *Science* 319:966-968
  - 实验：大学生学 40 个斯瓦希里语-英语词对，4 种条件（ST / S_N T / S_T N / S_N T_N）
  - **关键数据**：1 周后保留率——持续测试组（ST / S_N T）**0.81**，持续重读但停止测试组（S_T N / S_N T_N）**0.36**，差距 **0.45**；且学生预测完全不准（均预测 ~50%）
  - 一句话结论：**"过度学习后继续重读几乎无益，但继续测试能极大提升长期保留"**
  - DOI: 10.1126/science.1152408

### 后续工作

- **Karpicke & Grimaldi (2012)** "Retrieval-Based Learning: A Perspective for Enhancing Meaningful Learning" *Educational Psychology Review* 24(3):401-418
  - 提出"基于检索的学习"框架，强调应纳入课堂测验与计算机辅助训练；指出**学生元认知盲区**——多数人不了解检索练习的价值
  - DOI: 10.1007/s10648-012-9201-2

- **Rowland (2014)** "The Effect of Testing on Student Learning" *Educational Psychology Review* — meta-analysis 整合 61 项研究、159 个对比，确认检索练习稳健产生 d=0.50-0.88 中-大效应量

- **Adesope, Trevisan, McCarrey (2017)** "Examining the Effects of Retrieval Practice on Conceptual Learning" — d≈0.50，含迁移任务

- **Karpicke (2017)** "Retrieval-Based Learning: Progress in Associative and Semantic Memory" — 综述强调"测试效应在实验室与课堂同样稳健"

---

## 二、主动学习

### Freeman et al. (2014) PNAS — 关键 meta-analysis

- **Freeman, Eddy, McDonough, Smith, Okoroafor, Jordt, Wenderoth (2014)** "Active learning increases student performance in science, engineering, and mathematics" *PNAS* 111(23):8410-8415
  - **关键数据**：
    - 整合 **225 项研究**、158 个效应量（考试分数） + 67 个效应量（不及格率）
    - **考试分数主动学习组比传统讲授组高 0.47 SD**（约 6 个百分位提升）
    - **传统讲授组不及格率 33.8%，主动学习组 21.8%，OR=1.95**（不及格风险高出 1.5 倍）
    - 在所有 STEM 学科、不同班级规模（最大效应在 n≤50 小班）下稳健
  - 一句话结论：**"主动学习使平均考试分数提升约半个标准差，是 STEM 高等教育最有力的实证教学实践之一"**
  - DOI: 10.1073/pnas.1319030111

### 经典基础文献

- **Bonwell & Eison (1991)** *Active Learning: Creating Excitement in the Classroom* — 首次系统界定"主动学习"

- **Chi & Wylie (2014)** "Active-Constructive-Interactive: A Framework for Distinguishing Learning Activities" *Educational Psychologist*
  - 提出 **ICAP 框架**（Interactive > Constructive > Active > Passive），区分学习投入层级——**直接给答案属于"被动"层级**，需重构交互设计

---

## 三、苏格拉底式方法 / Socratic Method

### 经典文献

- **Paul & Elder (2007)** *The Thinker's Guide to the Art of Socratic Questioning*
  - 提出苏格拉底式提问 6 大类型：澄清假设、追问证据、探究视角/后果、质疑问题本身、区分问题各部分、元认知追问
  - 核心理念："**教学不是传递答案，而是通过提问帮助学习者自己建构理解**"

### LLM 实现 Socratic Tutoring 的最新论文（2024-2026）

- **Favero, Pérez-Ortiz, Käser, Oliver (2024)** "Enhancing Critical Thinking in Education by means of a Socratic Chatbot" arXiv:2409.05511（ECAI'24 AIEER Workshop）
  - 基于 Llama2-7B/13B 本地部署的苏格拉底式聊天机器人；**显著优于标准聊天机器人在反思与批判性思维上**
  - 关键差异：**苏格拉底式提问 vs 直接给答案**
  - arXiv:2409.05511

- **Zhang, Lin, Kuang, Xu, Hu (2024)** "SPL: A Socratic Playground for Learning Powered by Large Language Model"（EDM 2024 Workshop）
  - 基于 GPT-4 的对话式 ITS，模拟苏格拉底教学法，essay writing 实验有效改善批判性思维
  - arXiv:2406.13919

- **Bhatt Ambati et al. (2025)** "Socratic Students: Teaching Language Models to Learn by Asking Questions"（ODQS 框架）
  - **数学推理 Pass@5 提升 54.7%（绝对值），编码任务提升 22.9%**；以更少轮次达到相同准确率
  - lacuna.tiptreesystems.com/paper/socratic-students

- **Bonino et al.** SocraticLLM（CIKM 2024）— 数学领域苏格拉底式微调数据集 SocraticMATH

- **Xu, Qiao, Cheng, Liu, Zhao (2025)** "Enhancing Self-Regulated Learning and Learning Experience in Generative AI Environments: The Critical Role of Metacognitive Support" *BJET* 56(5):1842-1863
  - 4 周准实验，n=68：**GenAI 环境下缺乏元认知支撑时学生自我调节能力会下降**

---

## 四、认知负荷理论 / Cognitive Load Theory

### 核心论文

- **Sweller, van Merrienboer, Paas (1998)** "Cognitive Architecture and Instructional Design" *Educational Psychology Review* 10(3):251-296
  - 提出三类认知负荷：
    - **内在负荷（Intrinsic）**：由材料本身的元素交互度决定
    - **外在负荷（Extraneous）**：由教学呈现方式引入，与学习目标无关
    - **相关负荷（Germane）**：投入于图式建构与自动化的有益负荷
  - 教学设计原则：**降低外在负荷、优化内在负荷、提升相关负荷**
  - DOI: 10.1023/A:1022193728208

- **Sweller, van Merrienboer, Paas (2019)** "Cognitive Architecture and Instructional Design: 20 Years Later" *Educational Psychology Review* 31:261-292
  - **关键修订**：在新 CLT 模型中，**"germane load" 不再视为总负荷的独立贡献者**，而是工作记忆资源的"再分配"
  - 实践意义：**直接给答案可能占用工作记忆却无助于图式建构，反而浪费相关负荷**

- **Kirschner, Sweller, Clark (2006)** "Why Minimal Guidance During Instruction Does Not Work" *Educational Psychologist* 41(2):87-98
  - 论证对初学者直接"发现式学习"（minimal guidance）常因增加无关认知负荷而失败；与"AI 直接给答案"虽形式相反，但都绕过学习者主动建构——都背离有效学习原理

---

## 五、AI 过度依赖 / Cognitive Offloading（2024-2026 最新）

### 关键证据 1：直接给答案 → 长期保留下降

- **Bastani et al. (2025)** "Generative AI Can Harm Learning"（Wharton/UPenn, PNAS 公开工作论文）
  - **关键数据**：约 1,000 名高中数学学生随机分组——
    - "GPT Base"（直接给答案）组：**练习时成绩提升 48%**，但**撤掉 AI 后测试比对照组低 17%**
    - "GPT Tutor"（引导式提问、不直接给答案）组：**练习时成绩提升 127%**，撤掉 AI 后无负效应
  - 一句话结论：**直接给答案的 AI 会产生"依赖陷阱"；引导式 AI 才是真正的"学习加速器"**
  - 这是 BP **"为什么 AI-tutor 必须不直接给答案"** 的核心引文

### 关键证据 2：长期知识保留受损

- **Barcaui (2025)** "ChatGPT as a cognitive crutch: Evidence from a randomized controlled trial on knowledge retention" *Social Sciences and Humanities Open* 12:102287
  - **关键数据**：n=120 大学本科生，随机分 ChatGPT 组 vs 传统学习组
  - **45 天 surprise 测试**：ChatGPT 组 57.5% 正确 vs 对照组 68.5% 正确，**t(83)=-3.19, p=0.002, Cohen's d=0.68**
  - 结论：**"无限制 ChatGPT 使用损害长期知识保留，可能通过减少认知努力损害持久记忆"**——直接支持认知卸载理论与合意困难原则
  - DOI: 10.1016/j.ssaho.2025.102287

### 关键证据 3：元认知懒惰

- **Fan, Tang, Le, Shen, Tan, Zhao, Shen, Li, Gaševic (2025)** "Beware of Metacognitive Laziness: Effects of Generative Artificial Intelligence on Learning Motivation, Processes, and Performance" *BJET* 56(2):489-530
  - n=117 中国大学生，写作任务随机分 4 组
  - **关键发现**：**ChatGPT 组作文分数最高，但知识获得与迁移无显著差异**；更倾向"复制-粘贴"被动参与
  - 提出"**元认知懒惰（metacognitive laziness）**"概念——AI 依赖可能削弱自我调节学习能力
  - DOI: 10.1111/bjet.13544

### 关键证据 4：神经层面证据

- **Kosmyna et al. (2025)** "Your Brain on ChatGPT" MIT Media Lab
  - **关键数据**：n=54 参与者，4 个月写作实验 + EEG 监测
  - **ChatGPT 组在所有脑电频段（与创造力、记忆形成、语义处理相关）连接性最弱**；产出"几乎无原创性"的同质化作文；事后无法准确引用自己写过的话
  - 首次用神经证据证明**"直接给答案 → 写作过程的认知参与显著降低"**

### 关键证据 5：批判性思维相关性

- **Gerlich (2025)** "The hidden costs of AI: A systematic review" *MDPI Societies*
  - **关键数据**：n=666 跨年龄参与者——**AI 工具使用频率与批判性思维能力显著负相关**；高信任 AI → 减少验证 → 弱化批判性思维（形成危险反馈回路）

---

## 六、期望困难 / Desirable Difficulties

### 经典文献

- **Bjork & Bjork (2012)** "Making Things Hard on Yourself, But in a Good Way: Creating Desirable Difficulties to Enhance Learning"
  - 核心理论：每个记忆有"提取强度"（retrieval strength，当前可及性）和"存储强度"（storage strength，固化程度）；**高提取强度时反而存储强度增长缓慢**
  - 4 大合意困难：**间隔练习（spacing）、交错练习（interleaving）、检索练习（retrieval）、变化条件（variation）**
  - 与直接给答案的关系：直接给答案 = "提取强度极高 + 存储强度极低"——正是合意困难的反面

- **Bjork (1994)** "Memory and Metamemory Considerations in the Training of Human Beings" in *Metacognition*

---

## 七、LLM 作为自适应辅导系统的有效性（meta-analysis）

### Meta-analysis 实证

- **Liu, Zuo, Lu (2025)** "The Impact of ChatGPT on Students' Academic Achievement: A Meta-Analysis" *JCAL* 41(4):e70096
  - **关键数据**：整合 37 项研究（2022-2025），**整体 Hedges' g = 0.577（95% CI [0.395, 0.759]）**——中等正向效应
  - DOI: 10.1111/jcal.70096

- **Kulik & Fletcher (2016)** "Effectiveness of Intelligent Tutoring Systems: A Meta-Analytic Review" *Review of Educational Research* 86(1):42-78
  - 50 项评估：**ITS 比传统教学提升 g=0.66**

- **Deng et al. (2024)** Review of 69 studies on ChatGPT in education — 取决于使用方式

- **Wang & Fan (2025)** Meta-analysis of 51 ChatGPT 研究：客观学习 g=0.867（大学习效应），感知学习 g=0.456，高阶思维 g=0.457

### Bloom's 2-Sigma 问题与最新修正

- **Bloom (1984)** "The 2 Sigma Problem" *Educational Researcher* — 一对一辅导比传统教学提升 2 SD（AI 教育产品"圣杯"基准）
- **Nickow, Oreopoulos, Quan (2024)** meta-analysis 96 项 PreK-12 实验：**真实一对一辅导效果仅 g=0.29-0.37**，远小于 Bloom 原始声明

### Khanmigo（Khan Academy 大规模实证）

- **Oreopoulos & Low (2026)** NBER Working Paper 35620 — 美国田纳西州 18 所中学 2 年 RCT（n=6,902 学生-学期）
  - **关键数据**：每学期数学成就提升 **1.26 百分位（≈0.04 SD）**；完整学年 0.142 SD
  - **关键洞察**：效果"**与 Khan Academy 无 AI 练习效果相近**"；**学生参与度是真正瓶颈**——仅 33% 的练习日与 AI 对话，14% 的练习环节触发对话
  - 中位学生与 Khanmigo 对话比例极低，验证了"**必须主动设计引导式交互而非被动等待学生提问**"

### Harvard 物理 AI 辅导 RCT

- **Kestin, Miller, Klales, Milbourne, Ponti (2024-2025)** Harvard 大学物理课 RCT
  - **GPT-4 苏格拉底式提示的 AI 导师 vs 高质量主动学习课堂**：**AI 导师组在相同时间内学习量是主动学习组的 2 倍以上**；学习动机与参与度也更高

---

## 八、关键论文清单（13 篇）

| # | 论文 | 作者 | 年份 | 期刊/会议 | 核心贡献 | 链接 |
|---|---|---|---|---|---|---|
| 1 | Test-Enhanced Learning | Roediger & Karpicke | 2006 | Psych Sci 17:249-255 | 检索练习长期保留优势量化 | DOI 10.1111/j.1467-9280.2006.01693.x |
| 2 | The Critical Importance of Retrieval for Learning | Karpicke & Roediger | 2008 | Science 319:966-968 | 1 周后 ST 组 0.81 vs S_T N 组 0.36 | DOI 10.1126/science.1152408 |
| 3 | Retrieval-Based Learning | Karpicke & Grimaldi | 2012 | EPR 24(3):401-418 | 元认知盲区揭示 | DOI 10.1007/s10648-012-9201-2 |
| 4 | Active learning increases student performance | Freeman et al. | 2014 | PNAS 111(23):8410-8415 | 225 研究 meta, 0.47 SD | DOI 10.1073/pnas.1319030111 |
| 5 | Cognitive Architecture and Instructional Design | Sweller, van Merrienboer, Paas | 1998 | EPR 10(3):251-296 | 三类认知负荷奠基 | DOI 10.1023/A:1022193728205 |
| 6 | Cognitive Architecture 20 Years Later | Sweller et al. | 2019 | EPR 31:261-292 | 新 CLT 修订 | DOI 10.1007/s10648-019-09465-5 |
| 7 | Desirable Difficulties | Bjork & Bjork | 2012 | Psychology and the Real World | 提取 vs 存储强度 | bjorklab.psych.ucla.edu |
| 8 | Generative AI Can Harm Learning | Bastani et al. | 2025 | Wharton PNAS | -17% backfire 关键证据 | papers.ssrn.com sol3 papers.cfm |
| 9 | ChatGPT as a cognitive crutch (RCT) | Barcaui | 2025 | SSHO 12:102287 | 45 天保留 d=0.68 | DOI 10.1016/j.ssaho.2025.102287 |
| 10 | Beware of Metacognitive Laziness | Fan et al. | 2025 | BJET 56(2):489-530 | "元认知懒惰"概念 | DOI 10.1111/bjet.13544 |
| 11 | Effectiveness of ITS Meta-Analysis | Kulik & Fletcher | 2016 | RER 86(1):42-78 | ITS g=0.66 | DOI 10.3102/0034654315581425 |
| 12 | ChatGPT Academic Achievement Meta | Liu, Zuo, Lu | 2025 | JCAL 41(4):e70096 | 37 研究 g=0.577 | DOI 10.1111/jcal.70096 |
| 13 | Khanmigo Two-Year Trial | Oreopoulos & Low | 2026 | NBER WP 35620 | 大规模 RCT 0.04 SD/term | nber.org/papers/w35620 |

---

## 九、给 BP 写作的可直接引用素材

### 9.1 用于第 2.3 节"根本痛点"

> **引用句 1**："现有 AI 工具本质上'你问它答'，跳过大脑的主动加工环节——直接给答案会产生'流畅错觉'。**Karpicke & Roediger (2008, *Science* 319:966-968) 实验显示 1 周后重复学习组保留率仅 36%，而主动检索组高达 81%**。"（出处：Science 2008, DOI: 10.1126/science.1152408）

> **引用句 2**："**Bastani et al. (2025) 对约 1,000 名高中生的 RCT 表明**：直接给答案的'GPT Base'组在撤掉 AI 后测试比对照组**低 17%**——印证了直接给答案的 AI 反而损害学习。"（出处：Wharton PNAS 公开工作论文）

> **引用句 3**："**Barcaui (2025, *Social Sciences and Humanities Open*) 的 n=120 RCT 表明**：45 天后惊喜测试，ChatGPT 组 57.5% vs 传统学习组 68.5%（**Cohen's d=0.68**），直接给答案的 AI 损害长期知识保留。"（出处：DOI 10.1016/j.ssaho.2025.102287）

> **引用句 4**："**Fan et al. (2025, *BJET*) 对 117 名中国大学生的随机实验**：ChatGPT 组写作分数最高，但**知识获得与迁移无显著差异**——'元认知懒惰'（metacognitive laziness）现象。"（出处：DOI 10.1111/bjet.13544）

### 9.2 用于第 4 章"技术壁垒-教学方法论创新"

> **引用句 1**："AI-tutor 的'不直接给答案'方法论得到经典学习科学的有力支撑——**Freeman et al. (2014, *PNAS*) 对 225 项研究的 meta-analysis 显示主动学习使平均考试分数提升 0.47 SD，不及格风险降至 1/1.95**。"（出处：DOI 10.1073/pnas.1319030111）

> **引用句 2**："检索式练习效应（testing effect）已重复验证 50+ 年——**Karpicke & Roediger (2006, *Psychological Science*)：1 周后重复测试组保留率 61% vs 重复阅读组 40%**；延迟越长，主动检索优势越大。"（出处：Roediger & Karpicke, Psych Sci 2006）

> **引用句 3**："苏格拉底式教学法的 AI 实现已得到 2024-2026 多项实证支持——**Favero et al. (2024, arXiv:2409.05511)** 验证 Llama2 微调的苏格拉底式机器人比标准 chatbot 显著提升批判性思维；**Kestin et al. (2024-2025, Harvard) 的 GPT-4 苏格拉底式 AI 导师** 在物理课上学习量是高质量主动学习课堂的 2 倍以上。"（出处：arXiv 2409.05511；Kestin et al. 2024-25）

> **引用句 4**："从认知负荷理论视角看，直接给答案挤占工作记忆却无助于图式建构——**Sweller, van Merrienboer, Paas (1998, *EPR*) 提出'内在 / 外在 / 相关'三类认知负荷**，2019 修订版进一步指出应降低外在负荷、优化内在负荷；AI-tutor 的'引导式对话'正符合该原则。"（出处：DOI 10.1023/A:1022193728205；10.1007/s10648-019-09465-5）

> **引用句 5**："Bjork & Bjork (2012) 的合意困难理论为'为什么引导式比直接给答案更有效'提供统一解释——**高提取强度下存储强度增长趋近于零**；AI-tutor 通过'先问后答 + 间隔回访'主动设计合意困难。"（出处：bjorklab.psych.ucla.edu）

> **引用句 6**："**Liu, Zuo, Lu (2025, *JCAL*) meta-analysis 整合 37 项研究**：ChatGPT 类 AI 助教整体提升学生学业成就 **g=0.577**（中等效应）；同时 **Bastani et al. (2025)** 显示'GPT Tutor'（引导式）相对'GPT Base'（直接给答案）效果提升 **127% vs 48%**，差距 2.6 倍——为 AI-tutor 的差异化路线提供数据支撑。"（出处：DOI 10.1111/jcal.70096）

### 9.3 用于第 4 章"为什么是知识图谱驱动"

> **引用句 1**："知识图谱作为'认知地图'符合 ICAP 框架（Chi & Wylie 2014）中的**Interactive / Constructive 高阶学习投入**——可视化结构 + 引导式对话把学习从'被动接受答案'提升到'主动建构知识网络'。"（出处：Chi & Wylie 2014, *Educational Psychologist*）

> **引用句 2**："**Karpicke & Grimaldi (2012, *EPR*) 强调'元认知支持'是关键缺失**：学生需知道'自己已知/未知什么'才能优化学习；AI-tutor 的学情画像（mastery 0-1）+ 路径推荐正是元认知支持的具体工程化实现。"（出处：DOI 10.1007/s10648-012-9201-2）

---

## 十、参考资料

- [Roediger & Karpicke 2006, Psych Sci 17:249-255](https://doi.org/10.1111/j.1467-9280.2006.01693.x)
- [Karpicke & Roediger 2008, Science 319:966-968](https://doi.org/10.1126/science.1152408)
- [Karpicke & Grimaldi 2012, EPR 24(3):401-418](https://eric.ed.gov/?id=EJ977133)
- [Freeman et al. 2014, PNAS 111(23):8410-8415](https://doi.org/10.1073/pnas.1319030111)
- [Chi & Wylie 2014, Educational Psychologist](https://www.taylorandfrancis.com/journals/0267-1522)
- [Sweller, van Merrienboer, Paas 1998, EPR 10(3):251-296](https://link.springer.com/article/10.1007/BF01418832)
- [Sweller et al. 2019, EPR 31:261-292](https://link.springer.com/article/10.1007/s10648-019-09465-5)
- [Bjork & Bjork 2012](https://bjorklab.psych.ucla.edu/)
- [Favero et al. 2024, arXiv:2409.05511](https://arxiv.org/abs/2409.05511)
- [Zhang et al. 2024, arXiv:2406.13919](https://arxiv.org/abs/2406.13919)
- [Bhatt Ambati et al. 2025, Socratic Students (ODQS)](https://lacuna.tiptreesystems.com/paper/socratic-students-teaching-language-models-to-learn-by-asking-questions/)
- [Bastani et al. 2025, Generative AI Can Harm Learning](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5165471)
- [Barcaui 2025, SSHO 12:102287](https://doi.org/10.1016/j.ssaho.2025.102287)
- [Fan et al. 2025, BJET 56(2):489-530](https://doi.org/10.1111/bjet.13544)
- [Kosmyna et al. 2025, MIT Media Lab "Your Brain on ChatGPT"](https://www.media.mit.edu/publications/your-brain-on-chatgpt/)
- [Gerlich 2025, MDPI Societies](https://www.mdpi.com/journal/societies)
- [Kulik & Fletcher 2016, RER 86(1):42-78](https://doi.org/10.3102/0034654315581425)
- [Liu, Zuo, Lu 2025, JCAL 41(4):e70096](https://doi.org/10.1111/jcal.70096)
- [Wang & Fan 2025, ChatGPT learning meta-analysis](https://eric.ed.gov/)
- [Deng et al. 2024, ChatGPT in education review](https://onlinelibrary.wiley.com/journal/14678535)
- [Bloom 1984, Educational Researcher 13(6):4-16](https://doi.org/10.3102/0013189X013006004)
- [Nickow, Oreopoulos, Quan 2024, tutoring meta-analysis](https://www.nber.org/papers/w27476)
- [Oreopoulos & Low 2026, NBER WP 35620 (Khanmigo)](https://www.nber.org/papers/w35620)
- [Kestin et al. 2024-2025, Harvard AI tutor vs active learning](https://www.harvard.edu/)
- [Xu et al. 2025, BJET 56(5):1842-1863 (metacognitive support)](https://onlinelibrary.wiley.com/journal/14678535)
- [Rowland 2014, EPR, retrieval practice meta](https://link.springer.com/article/10.1007/s10648-013-9240-8)
- [Adesope, Trevisan, McCarrey 2017, ER](https://psycnet.apa.org/)

---

## 备注（诚实标注）

1. **关于 BP 文档第 2.3 节已引用的"24h 保留率 < 10%"**：严格学术表述中，**Roediger & Karpicke (2006)** 给出的是 **1 周延迟**（1-week delayed recall）数据——SSSS 40% / STTT 61%。如 BP 需要精确写"24h"，建议改为"1 周延迟"或注明数据来源于相关衍生研究。我未找到严格意义的"24h 后 < 10%"数据出处。
2. **关于 Bjork & Bjork (2012)**：经典出处是 *Psychology and the Real World* 编著章节；如需正式期刊版本可参考 Bjork (1994) in *Metacognition*。
3. **关于 Bastani et al. (2025) Wharton PNAS**：原始论文在 Wharton 工作论文系列 + PNAS 公开评议流程中，DOI 待最终确认。
4. **关于 Khanmigo NBER WP 35620**：发布于 2026 年 8 月，尚未经过同行评审；引用时建议标注"工作论文"。

—— 调研完毕 ——