# README 编写规范（v3，2026-09-12）

> 本文件是本仓库 README（及子项目 README）的**唯一写作依据**。此后每次新建 / 改写 README 一律先读本规范，再动笔。
> 适用范围：`README.md`（主语言门面）与其**多语言派生镜像**（`README.en.md`）、`CONTRIBUTING.md`（贡献指南）与其派生镜像，以及由它们派生的**评审/技术证据版**（见第 5 章）；不含 `docs/` 下的设计/调研文档。
> 版本历史：v1 建立门面模板 → v2 增第 5 章「评审/技术证据场景」 → v3 增第 6 章「多语言（双语）维护」、第 7 章「贡献指南（CONTRIBUTING）」、第 8 章「检查清单」。

---

## 一、方法论来源（权威，先看再写）

| 来源 | 一句话要点 |
|------|-----------|
| [GitHub 官方文档 · About READMEs](https://docs.github.com/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-readmes) | README 回答五件事：**做什么 / 为何有用 / 怎么用 / 去哪求助 / 谁在维护**；长文档不要塞 README，放独立文档与 Wiki；LICENSE / CONTRIBUTING / CODE_OF_CONDUCT / CITATION 各自独立成文。 |
| [Make a README（Danny Guo）](https://www.makeareadme.com/) | 章节推荐顺序：名称 → 简介 → 徽章 → 视觉展示 → 安装(+依赖要求) → 使用(带示例与期望输出) → 支持 → 路线图 → 贡献 → 致谢 → 许可 → 项目状态；**太长好于太短**——若嫌长，用外链扩展而非删信息；把读者当新手。 |
| [GitHub Open Source Guide · Starting a project](https://opensource.guide/starting-a-project/#writing-a-readme) | README 是"电梯演讲"，要能在 10 秒内让人判断"这个项目对我有没有用"；必须回答 what / why / how / where。 |
| [standard-readme（RichardLitt，MIT）](https://github.com/RichardLitt/standard-readme) | 板块模板化、命名一致（Background / Install / Usage / API / FAQ / Maintainers / Contributing / License），README 是入口，深度内容一律外链。 |
| README Driven Development（Tom Preston-Werner） | 先写 README 再写代码：README 即规格。 |
| [GitHub 官方文档 · 制定仓库贡献者指南](https://docs.github.com/zh/communities/setting-up-your-project-for-healthy-contributions/setting-guidelines-for-repository-contributors) | 贡献指南文件名固定为 `CONTRIBUTING.md`（大小写不敏感），可放**根目录 / `docs/` / `.github/`** 三处；GitHub 会自动在 4 个位置露出（PR/Issue 页面链接、`/contribute` 页、仓库 Contributing 选项卡、侧栏链接）；官方建议内容 = 如何提 Issue/PR 的步骤 + 外链文档/行为准则 + 社区行为预期。 |
| [GitHub Open Source Guide · How to Contribute to Open Source](https://opensource.guide/how-to-contribute/) | 贡献前先读 README / CONTRIBUTING / 行为准则；**先搜后问**；实质性工作先开 issue 对齐；PR 早开、带测试与截图、引用 issue（`Closes #37`）；被要求修改要回应，做不下去要告知；**AI 辅助的贡献须自行核实，贡献者仍对提交内容负责**。 |
| [contribution-guide.org（Jeff Forcier）](https://www.contribution-guide.org/) | Bug 报告要做尽职调查并给足最低信息量（OS/版本/安装方式/最小复现步骤/完整输出）；**测试不可选、文档不可选**；跟随仓库既有风格压倒个人偏好；贡献默认按仓库 LICENSE 授权，**不逐文件加版权头**。 |
| [Lara Translate · How to Localize a README for GitHub](https://blog.laratranslate.com/how-to-localize-a-readme-file-github/) | 多语言文件命名 `README.<小写语言标签>.md`（区域码用连字符，如 `zh-CN`）；**语言切换器必须放在最顶部**（GitHub **不会**按浏览器语言自动切换 README）；只译散文，**不译代码 / URL / 徽章语法**；用 base commit hash 注释追踪翻译时效；最典型的失败是命令行与徽章 URL 被"顺手翻译"、翻译后 TOC 锚点失效。 |

**仓库内可对照的真实样例**（`.research/edu-repos/`，不必入库）：

| 样例 | 可借鉴点 |
|---|---|
| `DeepTutor/README.md` + `assets/README/README_CN.md` 等 11 个语言 | 顶部徽章式语言切换（shields.io `简体中文` 徽章 ↔ 语言文件）；主 README 英文 + `README_<LANG>.md` 子目录集中存放。 |
| `hermes-edu-skills-main/README.md` + `README.en.md` | 双文件平铺在仓库根目录（`README.md` = 中文，`README.en.md` = 英文）——**与本仓库选型一致**。 |
| `DeepTutor/CONTRIBUTING.md` | 贡献指南的完整章节骨架：维护者 → 分支策略 → 快速开始 → 环境搭建（`<details>` 折叠）→ 常用命令表 → 代码质量与安全 → 编码规范 → commit 格式 → 安全实践。 |

---

## 二、固定原则（不改）

1. **README 是门面，不是开发日记。** 面向"第一次打开仓库的人"（用户 / 评审 / 潜在贡献者），不面向作者本人。
2. **首屏 3 秒法则**：标题 + 一句话定位 + 解决什么问题，必须出现在第一屏，不要求滚动就能读完整句定位。
3. **问答驱动结构**：全文按 GitHub 五问组织（做什么 / 为什么 / 怎么用 / 去哪求助 / 谁维护），缺哪块补哪块。
4. **❌ 禁放清单**（凡易腐化、时效性、内部管理信息一律不进 README）：
   - TODO / 改进清单 / 竞备进度（放 TODO.md、docs/比赛/COMPETITION.md）
   - 报名截止时间、比赛日程、一次性活动信息
   - 变更日志、版本历史（放 CHANGELOG）
   - 依赖安装细节之外的长篇排障手册（外链 docs/）
5. **太长好于太短，但分层：** README 只放"快速了解 + 快速上手"；深入内容放 docs/ 并用一行链接带出，绝不在 README 内复制长文。
6. **内容先于装饰**：先有准确、诚实的文字，再有徽章 / 截图 / 表格；不得为凑装饰写空话。
7. **表驱动**：功能清单、技术栈、环境变量这类"清单型"信息一律用 Markdown 表格，压缩扫描成本。
8. **诚实**：状态不写死"✅ 完成"以外的乐观词；量化数字须可复现（写明环境与命令）。
9. **主语言唯一（SSOT，v3 新增）**：任何文档只有一份事实源（本仓库 = 中文），其他语言一律是**派生镜像**，只跟随主文件变化；派生文件禁止成为某条事实的首次落点（详见第 6 章）。

---

## 三、固定章节模板（顺序不可乱）

| # | 章节 | 必写内容 | 对应 GitHub 五问 |
|---|------|---------|-----------------|
| 0 | Badges（可选） | License / 语言等有据可查的静态徽章，宁缺毋滥 | — |
| 1 | 标题 + **语言切换器** + 一句话定位 | 项目名 + 语言切换（多语言项目必写，见 6.3；单语言项目留空）+ 电梯演讲（3 秒说清是什么） | 做什么 |
| 2 | 简介（简介/痛点/差异化） | 做什么、解决什么痛点、与同类有何不同；可放一句愿景 | 做什么·为何有用 |
| 3 | **全景（架构）** | ASCII 分层架构图 + 核心数据流 + 关键子系统；让读者一眼看到全貌 | 做什么（全景） |
| 4 | 核心功能 | 表：功能 / 一句说明 / 状态 | 做什么 |
| 5 | 技术栈 | 表：层级 / 技术 / 说明 | 做什么 |
| 6 | 快速开始 | 前置要求 → 安装 → 启动 → 验证地址；**读者默认是新手** | 怎么用 |
| 7 | 配置 | 环境变量表（名 / 是否必填 / 说明），模板文件入口 | 怎么用 |
| 8 | 项目结构 | 目录树，只到关键层级，配合一行注释 | 怎么用 |
| 9 | 质量与测试 | 测试怎么跑、数量与状态、评测方法与可复现命令 | 怎么用·谁维护 |
| 10 | 文档索引 | 精挑 docs/ 与关键文件的链接（一行一个），不堆砌 | 去哪求助 |
| 11 | 贡献 | 一句话说明是否欢迎贡献 + 外链 CONTRIBUTING / Issue 模板 | 谁维护 |
| 12 | License | 一句话 + 外链 LICENSE 全文 | 谁维护 |

> 微调规则：赛道 / 商业项目可按需合并 11-12 章；任何章节可留空跳号，但**顺序不变、不新增"日记型"章节**。
> 语言切换器位置微调（v3）：**紧跟 H1 标题的下一行**——标题必须最先（SEO / 锚点 / 目录），切换器紧随其后，仍在首屏内。

---

## 四、相关链接

**外部权威参考（点开即读）**

- GitHub 官方《About READMEs》 — https://docs.github.com/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-readmes
- GitHub 官方《制定仓库贡献者指南》 — https://docs.github.com/zh/communities/setting-up-your-project-for-healthy-contributions/setting-guidelines-for-repository-contributors
- Make a README（Danny Guo） — https://www.makeareadme.com/
- GitHub Open Source Guide · Starting a project（含 Writing a README） — https://opensource.guide/starting-a-project/#writing-a-readme
- GitHub Open Source Guide · How to Contribute to Open Source — https://opensource.guide/how-to-contribute/
- contribution-guide.org（Jeff Forcier） — https://www.contribution-guide.org/
- standard-readme（RichardLitt） — https://github.com/RichardLitt/standard-readme
- README Driven Development（Tom Preston-Werner） — https://tom.preston-werner.com/2010/08/23/readme-driven-development.html
- Lara Translate《How to Localize a README File for GitHub》 — https://blog.laratranslate.com/how-to-localize-a-readme-file-github/

**项目内部（本规范的落点与样板）**

- [根 README.md](../README.md)（中文主语言）与 [README.en.md](../README.en.md)（英文派生） — 按本规范写出的当前版本，多语言维护的活样例（第 6 章）
- [CONTRIBUTING.md](../CONTRIBUTING.md) / [CONTRIBUTING.en.md](../CONTRIBUTING.en.md) — 贡献指南，第 7 章的落地样板
- [COMPETITION.md](比赛/COMPETITION.md) — 参赛总纲（时间线/评审等，README 不再重复）
- [TODO.md](../TODO.md) — 工程改进清单（README 禁放，落这里）
- `docs/` 其余设计/调研文档 — 从根 README「文档索引」章节进入
- `.env.example`（根目录）— 配置章节的口径来源
- [国创技术报告_校评支撑版](../docs/国创技术报告_校评支撑版.md) — 第 5 章证据版 E3/E4 素材源（执行摘要 / 创新点 T1-T3 / 评测口径）

---

## 五、评审 / 技术证据场景扩展（v2）

> 背景：README 除开源门面外，常被要求"作为技术文档 / 证据随支撑材料提交"（校级评审、赛事作品包）。本章给出**判定、原则与证据版模板**，避免把门面版改造成材料堆。

### 5.1 定位判定（先答三个问题）

| 问题 | 门面版（默认） | 评审/证据版（派生） |
|------|---------------|--------------------|
| 何时出现 | 开源门面 / 文档入口 | README 需充当正式提交物或证据附件时 |
| 读者 | 开发者 / 用户 | 评委（技术评审 / 非技术评审混合） |
| 能不能单独作为支撑材料 | ❌ 不能 | **能当"技术证据/技术附件"，不能当完整申报材料**（教育价值/成效数据/团队/商业在申报书与计划书中） |

> 铁律：**证据版永远不单独代表"项目全部价值"**，它的角色是"技术真实性 + 工程完备性 + 可复核性"证据链；教育叙事归申报书，成效数据归试点报告，视觉演示归视频与 PPT。

### 5.2 证据版原则（ER1–ER6，不违反第 2 章原则）

1. **ER1 派生不双写（单一事实源）**：证据版 = 门面版"证据相关子集"复制派生 + 证据专属章节。共享事实（架构、测试数、功能状态）只允许门面版修改后**同步**到证据版，禁止两份各自维护；派生时在头部记录"基于门面版 README @commit/日期"。
2. **ER2 断言可复核**：每条技术断言旁给 1 行证据——截图文件路径、复现命令、数据来源（文档/评测脚本）。宁删不加"无法复核的亮点"。
3. **ER3 方法学自证**：凡 mock / 离线评测 / 内部数据集，必须一句话说明"为什么这么测、真实路径是什么"，否则评委误读为"数据造假"。
4. **ER4 可视化必配**：每个宣称"✅"的核心功能至少配 1 张**真实运行截图**（标注环境与日期；截图为演示数据就绪前提，见仓库 TODO P0）。
5. **ER5 诚实仍最高**：量化必须可复现，不夸大；状态列只允许 ✅（已落地）与"规划中"。
6. **ER6 比赛信息仍不外泄**：日程/截止/奖项/报名细节不进证据版正文；提交物信息只放页脚声明或申报书。

### 5.3 证据版模板（在门面版 1–5、9 章基础上增删）

| 证据章节 | 内容要求 | 覆盖评审关注 |
|---|---|---|
| E0 封面 | 项目名 / 版本 + commit / 生成日期 / 用途声明（"作为 XX 作品技术附件"） | 材料归属 |
| E1 技术真实性 | 仓库链接（可抽查）+ 架构全景（复用门面版第 3 章）+ 每子系统对应代码路径 | 技术规范性（AI+教育）、工程可信 |
| E2 工程完备 | 测试数 + 收集总数 + 复现命令 + Docker 部署文件 + 错误码/限流/事件总线说明 | 技术规范性、工程化 |
| E3 评测与质量 | 离线评测集规模与基线表 + **方法学自证（ER3）** + 复现脚本 | 技术规范性、创新优化性 |
| E4 创新点对照 | 3 个创新点 ×（一句话价值 + 与 docs/标杆项目对标分析 差异）+ 文档链接 | 创新优化性 |
| E5 功能证据 | 核心功能表（门面版第 4 章）逐行挂截图/复现步骤 | 实施显著性 |
| E6 合规与安全 | 用户隔离 / JWT / 版权双模式 / 开源许可；**删除"默认管理员 admin/admin123"类负面观感细节** | 合规、伦理（国创） |
| E7 一致性声明 | 与申报书/PPT/技术报告的口径一致声明 + 关键数字对照（端点 / 测试数等） | 材料可信 |

> 生成流程：门面版定稿 → 复制为独立文件（如 `docs/作品技术说明_评审版.md`）→ 增 E0-E7、删门面版快速开始/配置/贡献等开发者章节 → 逐个功能补真实截图 → 人工核验数字与 commit。
> 分工提示：本项目已有 `docs/国创技术报告_校评支撑版.md` 承担"技术报告"主载体，其执行摘要/创新点/评测可直接复用为 E3、E4 素材，README 证据版定位为**轻量技术附件**，不重复写长报告。

---

## 六、多语言（双语）维护规范（v3 新增）

> 目标：让「中文主文档 + 英文镜像」长期共存而不漂移，读者一次点击即可切换。
> 前提事实（决定所有设计）：**GitHub 不会按浏览器语言自动切换 README**，也不存在"仓库语言开关"；所谓"切换"只能是**文件内显式链接**。

### 6.1 三条铁律

1. **单一主语言（SSOT）**：每份文档只有一份事实源，其余语言是派生镜像。
   - 本仓库主语言 = **中文**：`README.md` / `CONTRIBUTING.md` 为事实源；英文为 `README.en.md` / `CONTRIBUTING.en.md`。
   - 选型理由：项目第一读者是中文（国内评审 + 中文社区），且现有外链、GitHub 默认渲染、历史提交都指向 `README.md`，迁移主语言代价大于收益。
   - **禁止只在镜像里新增内容**：任何新事实先在主文件落笔，再同步镜像。
2. **结构同构**：两份的**章节编号、顺序、表格行数、代码块**一一对应；镜像不得增删章节（唯二例外：顶部语言切换器、`<!-- base -->` 注释）。
3. **不译清单**：**可执行代码块**（命令、参数、配置片段、示例输出）、URL、徽章 Markdown 语法、文件名/目录/环境变量/配置键、项目名与专有名词、语言切换器中的语言名（用母语书写：`简体中文`、`English`）——**逐字符照抄**。
   例外：仓库结构树、ASCII 架构图这类**图示性**代码块**要翻译图中文字**（框线、缩进层级、节点关系必须保持不变），否则英文读者看不懂图。

### 6.2 命名与文件布局

| 项 | 规则 |
|---|---|
| 主语言文件 | `README.md` / `CONTRIBUTING.md`（无语言后缀，保留 GitHub 自动识别与既有外链） |
| 派生文件 | `README.<lang>.md`，语言标签**小写**、区域码用**连字符**（`README.en.md`、未来 `README.zh-CN.md`、`README.ja.md`） |
| 位置 | 与主文件同目录平铺（不放子目录），保证相对链接与 GitHub 渲染一致 |
| 禁止 | `README_EN.md`（大写+下划线）、`README_en.md`（下划线）、`readme-en.md`（非 `README` 前缀） |
| 数量纪律 | 语言数 ≥ 3 时才考虑用 `assets/README/` 集中收纳（见 DeepTutor）；**双语阶段平铺** |

### 6.3 语言切换器（唯一的"切换"实现方式）

写法（主文件顶部，H1 之后空一行，正文之前）：

```markdown
# 项目名 — 一句话定位

**简体中文** ｜ [English](README.en.md)
```

镜像文件镜像书写：

```markdown
# Project — one-line positioning

[简体中文](README.md) ｜ **English**
```

规则：

- **当前位置不加链接、加粗表示**；其他语言可点。
- **语言名用该语言母语书写且不翻译**（`English` 不写成"英文"）。
- 语言**不超过 3 种时用行内文本链接**；≥ 3 种可升级为徽章式（shields.io 静态徽章包一层 `<a>`，如 `<a href="README.en.md"><img alt="English" height="20" src="https://img.shields.io/badge/English-2E3440"></a>`），但**徽章不得替代文本链接**（无障碍与纯文本可读性）。
- 切换器**必须同时出现在每一份镜像顶部**（互链闭环），且链接用**仓库内相对路径**（不用 `https://github.com/...` 绝对地址，fork / 离线 / 分支预览下仍可用）。
- 不改动 TOC/锚点：切换器不占章节编号。

### 6.4 翻译边界（译什么 / 不译什么）

| ✅ 必须翻译 | ❌ 一字不改 |
|---|---|
| 章节标题、散文段落、痛点与价值描述 | 代码块及其中的命令、参数、示例输出 |
| 表格中的"说明"列、功能描述 | 表格中的字段名 / 环境变量名 / 技术名词（`DASHSCOPE_API_KEY`） |
| **ASCII 架构图 / 仓库目录树内的文字标签**（框线、缩进层级、节点关系不变） | 徽章 Markdown 与其中的 URL、`LICENSE` 等外链 |
| 图片 alt 文本、图注、切换器以外的导航性文字 | 文件路径、目录名、commit hash、端口号、版本号 |

> 硬性自检：**可执行代码块**（```bash / ```powershell / ```python …）与主文件逐字符相同（diff 为空）；命令行被翻译是最高频事故（`pip install` → 被译成"安装"）。图示性代码块（架构图 / 目录树）只需结构一致、图中文字已翻译。

### 6.5 防漂移四件套（缺一个就会烂）

| 机制 | 做法 | 成本 |
|---|---|---|
| ① 结构锚定 | 章节编号/顺序/表格行数对齐；**每次改主文件必须同 commit 改镜像**（PR 自检项） | 0（纪律） |
| ② 时效标记 | 镜像顶部 HTML 注释记录基线：`<!-- base: README.md @ c54d571 (2026-09-12) -->`；主文件结构或事实变更后更新该 hash（注释不进渲染，但可审计"翻译新鲜度"） | 0 |
| ③ PR 自检 | CONTRIBUTING 固定自检清单含"双语是否同步"一条（见第 7 章） | 0 |
| ④ CI 校验（可选加固） | 一个零依赖脚本比对两份的 `##` 标题序列 / 代码块数量 / 相对链接可达性，不一致即 fail。**当前未实现**（结构稳定后再加，勿为流程而流程） | 中 |

### 6.6 何时必须同步（触发条件）

- 章节**增删改**（含顺序调整）→ 必须同步
- 事实数字变化（测试数、模型名、端点、环境变量）→ 必须同步
- 命令/脚本路径变化 → 必须同步
- 纯排版（空格、换行）或仅中文专用说明（如 docs 中文文档链接说明）→ 可不同步，但需在下一次同步时一并处理

### 6.7 反例（别学）

| 反例 | 后果 |
|---|---|
| 只在 `README.en.md` 里加新功能 | 主语言失真，SSOT 被破坏 |
| 英文版整段删掉"质量与测试"章 | 结构不同构，读者两份信息量不等 |
| 用机器翻译生成后不校对术语 | 术语漂移（"图谱/Graph/Knowledge Map"三种叫法） |
| 切换器写成绝对 GitHub URL | fork / 私有部署下链接指向上游仓库 |
| 双语靠"记得改" | 3 个月后英文版停留在旧架构（本规范存在的理由） |

---

## 七、贡献指南（CONTRIBUTING）规范（v3 新增）

### 7.1 位置与发现入口

- 文件名：`CONTRIBUTING.md`（GitHub 大小写不敏感）；本仓库放**仓库根目录**（与 `LICENSE`、`README.md` 同级，最易被发现）。
- GitHub 自动露出 4 处：新建 Issue/PR 时的提示链接、`/<repo>/contribute` 页面、仓库顶部 "Contributing" 选项卡、仓库侧栏链接——**不需要也不要在 README 里重复贴全文**。
- README 第 11 章只写一句话 + 外链（本规范第 3 章）。

### 7.2 必备章节模板（顺序固定）

| # | 章节 | 必写内容 |
|---|---|---|
| 1 | 欢迎 + 能贡献什么 | 一句话欢迎 + 贡献类型表（Bug / 文档 / 测试 / 新功能 / 数据），并标明"哪些改动不需要提前开 issue"（错别字、失效链接、注释勘误可直提） |
| 2 | 开发环境 | 一键脚本（`install.ps1`）+ 手动步骤；**必须写明本项目的硬约束**（本仓库：`backend/venv/Scripts/python.exe`、`uvicorn --workers 1`、根 `.env` 唯一真值） |
| 3 | 跑测试（验证口径） | 可复制即跑的精确命令 + **期望输出**（如 `745 passed, 7 deselected`）+ 说明哪些用例需要真实 API key（`-m "not llm_api"`） |
| 4 | 编码规范 | 项目既有约定（分层职责、单一事实源、YAGNI 最小实现、错误码 `[E-XXX]`、用户隔离、不要绕过封装层）；**跟随既有风格优先于个人偏好**（contribution-guide.org） |
| 5 | 提交与 PR | 分支命名、commit 格式（`type: 描述`，type 表）、PR 说明模板（改了什么/为什么/怎么验证/引用 issue）、**不要混入无关改动** |
| 6 | 文档与双语义务 | 改架构/命令/数字**必须同步**改 `README.md` + `README.en.md`（第 6 章），并更新镜像顶部的 base 注释 |
| 7 | AI 辅助声明 | 用 AI 生成/改写的补丁须自行核实可跑、通过测试、符合规范；**贡献者仍对提交内容负责**（opensource.guide） |
| 8 | 沟通与响应 | 公开讨论（Issue/PR 评论）优先于私信；维护者是志愿者，响应需耐心；分歧尊重维护者最终决定，不满意可 fork |

### 7.3 写法硬要求

1. **命令可复制即跑**：给完整的 `cd` + 命令，不写"运行测试"这类空话；Windows/Linux 差异并列（本仓库以 Windows PowerShell 为主，另给 bash 等价）。
2. **写清期望输出**：让贡献者自证"我这边是通过的"（测试通过数、健康检查 URL）。
3. **对新手友好**：首屏给出"5 分钟跑起来"的最短路径；细节可折叠（`<details>`）但**不能隐藏**。
4. **不写内部管理信息**：比赛日程、内部周会、成员分工、答辩安排一律不进（第 2 章禁放清单同样适用于 CONTRIBUTING）。
5. **明确"不要做什么"**：如"不要提交 `data/` 运行时数据、`.env`、模型缓存"，减少无谓 PR。
6. **不逐文件加版权头**；贡献默认按仓库 `LICENSE` 授权（在章 1 或章 5 一句话声明）。

### 7.4 与 README / AGENTS.md 的分工

| 文档 | 只回答 |
|---|---|
| `README.md` | 这是什么 / 怎么用（门面，5 分钟读懂） |
| `CONTRIBUTING.md` | 想改它，具体怎么改、怎么验、怎么提（流程，动手时看） |
| `AGENTS.md` | AI 编码 Agent 与开发者的架构契约索引（改架构前看） |
| `docs/*.md` | 设计决策与调研细节 |

> 禁止同一段内容在四处重复；重复即漂移源。需要引用时**给链接 + 一句话摘要**。

---

## 八、检查清单（v3 新增）

### 8.1 动笔前（3 项）

- [ ] 已读本规范全文，确认目标文档类型（门面 / 镜像 / 贡献指南 / 证据版）
- [ ] 已核对**代码事实**（命令、路径、测试数、模型名、环境变量），不凭文档印象写
- [ ] 明确本次改动的**触发同步范围**（是否需要改镜像 / 证据版）

### 8.2 门面版发布前（8 项）

- [ ] 首屏 3 秒能读完"是什么"
- [ ] 13 章顺序无误（缺章可跳号，不可乱序、不可加日记型章节）
- [ ] 清单型信息全部表格化
- [ ] 所有量化数字**可复现**（同一条命令、写明期望输出）
- [ ] 禁放清单零命中（TODO、日程、变更日志、内部管理）
- [ ] 章节内的相对链接全部可达（`docs/`、`LICENSE`、脚本路径）
- [ ] 徽章语法未破坏、URL 未被改动
- [ ] 状态列无"几乎完成""基本可用"这类模糊表述

### 8.3 双语同步（5 项）

- [ ] 两份的 `##` 标题**数量与顺序完全一致**
- [ ] 两份的**可执行代码块**内容逐字符相同（diff 为空）；图示性代码块结构一致且图中文字已翻译
- [ ] 表格行数一致、清单项数一致
- [ ] 切换器互链正确（相对路径）、当前语言加粗不加链接
- [ ] 镜像顶部 `<!-- base: README.md @ <commit> (日期) -->` 已更新

### 8.4 贡献指南（4 项）

- [ ] 位于根目录且文件名为 `CONTRIBUTING.md`
- [ ] 章 1-8 齐全（模板见 7.2）
- [ ] 测试命令可复制即跑且带期望输出
- [ ] README 第 11 章已外链到它（双语 README 都要链）
