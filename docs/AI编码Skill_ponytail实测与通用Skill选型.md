# AI 编码 Skill —— ponytail 实测 与 通用 Skill 选型

> 起因：用户问"装了 ponytail 到底有没有省 token、提质量？"→ 设计 A/B 实测 → 再查外部证据 → 决定该补充哪类通用 coding skill。
> 结论摘要（**先说结论**）：**ponytail 的实测收益 = diff 收缩（真实但小），成本 = 固定 2,005 token/任务（实测）；它不省 token、也没有"更稳定"的证据。** 真正该补的是**验证类 skill（TDD / 完工前验证 / 系统化调试）**，因为实测数据表明 LLM 代码的失效模式是"**缺失检查**"而不是"过度设计"。
> 实验产物（可复现）：`reports/ponytail_ab/`（已 gitignore）+ `frontend/harness.html` + `frontend/harness/main.js`。
> 日期：2026-09-15。

---

## 1. 实验设计（A/B 同任务）

| 项 | 内容 |
|---|---|
| 任务规格（两次完全一致） | 只改 `frontend/src/components/MessageBubble.vue`：① AI 回复去掉气泡（背景/圆角/内边距）直接平铺；② 工具调用按调用顺序纵向展示；③ 不改数据层/不加依赖；④ 验收 = `vite build` + 无头浏览器截图核对 |
| 变量 | 是否在动手前 `use_skill ponytail` |
| 验收手段 | `vite build` + `read_lints` + 无头 Edge `--headless=new --screenshot` |
| 夹具 | `frontend/harness.html` + `frontend/harness/main.js`：真实 `MessageBubble` 组件 + 一条含 3 个工具调用（done/done/error）、2 段思考、正文的 assistant 消息；dev server 起在 5199 |

**夹具/截图方案本身值得复用**：`msedge --headless=new --screenshot=out.png --window-size=900,560 <url>` 直接截图，
**不需要装 playwright / 不需要下载浏览器**（系统 Edge 即可），前端组件级改动因此有了零成本的"看得见"验收。

---

## 2. A/B 记录

### 记录 A —— 不加载 skill（基线）

```diff
模板：tool-chip → tool-line，加序号 <span class="tool-index">{{ idx + 1 }}</span>
CSS ：.ai-bubble 去底色/内边距/圆角；.tool-area 改 flex-direction: column
      chip 的描边+底色 3 条规则删掉改纯文字着色；动画 chipFadeIn → toolFadeIn
文案：✗ → ✗ 失败
```

| 指标 | 值 |
|---|---|
| 工具调用 | 3 次编辑 + 1 次截图 + build/lint/统计 |
| diff | **+27 / −22 行**（3,242 字符 / **1,062 token**） |
| 验证 | `vite build` ✓ 6.53s、lint 0、截图 ✓ |

### 记录 B —— 加载 ponytail 后（先把文件 `git checkout` 回滚再改）

阶梯第 4 阶「原生特性优先」直接命中：两条需求都是纯 CSS。

```diff
.ai-bubble { background: none; padding: 0; border-radius: 0; }
.tool-area { flex-direction: column; align-items: flex-start; }   /* 并删掉死属性 flex-wrap */
```

| 指标 | 值 |
|---|---|
| 加载成本 | `SKILL.md` = 6,904 字符 / **2,005 token**（tiktoken cl100k 实测） |
| diff | **+7 / −4 行**（737 字符 / **257 token**） |
| 模板改动 | **零**（不改类名、不加序号、不动文案） |
| 被"阶"掉的项 | ① 序号（规格只说"按顺序"，column 流已满足）② chip 描边风格（不属"对话框"，未越权改设计）③ `✗ 失败` 文案 |
| 验证 | `vite build` ✓ 5.09s、lint 0、截图 ✓ |

### 对比

| 指标 | A 无 skill | B 有 skill |
|---|---|---|
| diff | +27/−22 行，1,062 token | **+7/−4 行，257 token** |
| 技能加载 | 0 | **2,005 token** |
| **单任务 token 总账** | **≈1,062** | **≈2,262（反而贵 ~1.1K）** |
| 未要求改动 | 2 处 | 0 |
| 视觉完成度 | 纯文字行 + 序号，无盒子 | 保留 3 个 chip 盒子 |
| 验证 | build✓ lint✓ 截图✓ | build✓ lint✓ 截图✓ |

### 三条结论

1. **ponytail 的机制是压缩 diff，不是压缩 token。** 它自己有**固定 2,005 token/任务**的加载成本 →
   **回本阈值 ≈「不省则要多写的代码 > 2K token（约 200+ 行）」**。小改动**净亏**。
2. **它的真实收益在维护面与边界**：不动模板 → 不引入命名/文案漂移；不做审美加料；把"要不要序号"这类决策**退回用户**（其原文：ship the lazy version and question it）。
3. **代价是多一轮往返**。想要 A 的观感得再补一句 → 小任务里这一轮的成本 > 它省下的 805 token。

### 最终处置（用户拍板）

用 **A 版观感**：`+26 / −21 行，2,969 字符 / 964 token`（`reports/ponytail_ab/final_A.patch`），`vite build` ✓ 5.98s、lint 0、截图 ✓。

> ⚠️ 实验局限：B 在同一会话内进行，复用了 A 的探查结果（无需重读文件），故 B 的耗时是**下界**；
> 严格可比的只有「diff 体量」与「技能加载成本」两项。

---

## 3. 外部证据（按证据强度排序）

### 3.1 ⭐ 最强证据：上下文文件类（含 skill/规则文件）**不提升成功率，但抬高成本**

**arXiv 2602.11988（ETH Zurich，v2 2026-06-23）《Evaluating AGENTS.md: Are Repository-Level Context Files Helpful for Coding Agents?》**

- **总体不提升任务成功率**，但**推理成本平均增加 >20%**；在多个 LLM、多个 coding agent、LLM 生成 / 开发者提交两类来源上均成立。
- 机制：**指令型内容（instructions）能被较好地遵循**；而**仓库概览型内容（repository overviews）——尽管流行且被模型厂商推荐——并无帮助**。
- 结论：上下文文件**只对"说明非标准编码实践"有用**；想靠它提性能必须先严格评估。

> 对我们的直接含义：ponytail 属"指令型"，我们实测它**确实被遵循**（diff 缩到 1/4），但**没有成功率收益**；
> 而 `AGENTS.md` 里的**架构总览 / 目录速览属"仓库概览型" → 按此文应瘦身**，只保留本项目特有的硬约束（非标准实践）。

### 3.2 ⭐ 最强证据：LLM 代码的失效模式是"缺失检查"，不是"过度设计"

**arXiv 2503.20197（中山大学 SYSUSELab）《Enhancing the Robustness of LLM-Generated Code》**

- LLM 代码健壮性缺陷 **>90% 来源于"缺失检查"**（null / range / type / boolean / assertion / error handling 共 7 类）；
- **RRI < 0（弱于人类代码）占 43.1%**；**9 类问题模式中没有"过度设计"这一类**；
- 附加数据：缺陷出现在**第一行**的占 70%；RobGen 框架可减少 20.0% 弱健壮性代码。

> 直接含义：**"代码更少 ⇒ 更稳定"没有证据支持**；真正短板是**检查/验证的缺失** → 该补的是**测试与验证类 skill**。
> 注：ponytail 在 `When NOT to be lazy` 里明确豁免校验/错误处理/安全/可访问性，所以它**不会主动把人推进这个坑**，但也不能拿"更稳定"给它记账。

### 3.3 官方自报（**不可核，仅当方向性暗示**）：`ponytail.dev`

代码量 **−54%**、token **−22%**、成本 **−20%**、速度 **+27%**、"safety kept 100%"，脚注称"FastAPI+React 仓库，12 个功能任务的中位数"。

举证缺口：无对照组说明、无模型版本、无原始数据、无评测脚本、**无通过率指标**、页面无 star/安装量/社区评价。
其 "token −22%" 是**任务侧**数字（少写代码省的），**不含技能自身加载成本**（我们实测 2,005 token/任务）。

### 3.4 候选仓库实时数据（GitHub API，2026-09-15 抓取）

| 项目 | ⭐ Stars | Forks | 创建 | 最近推送 | 许可 | 定位 |
|---|---|---|---|---|---|---|
| `obra/superpowers` | **286,952** | 25,659 | 2025-10-09 | 2026-09-14 | MIT | 14 个 skill 的**方法论 + 技能体系**（TDD 警长） |
| `mattpocock/skills` | **262,519** | 22,148 | 2026-02-03 | 2026-09-15 | MIT | 工程师技能集（`/tdd` `/code-review` `/to-spec` `/grill-with-docs`） |
| `github/spec-kit` | **136,946** | 12,272 | 2025-08-21 | 2026-09-14 | MIT | **Spec-Driven Development**（spec→plan→tasks→implement），GitHub 官方 |
| `bmad-code-org/BMAD-METHOD` | 53,044 | 5,988 | 2025-04-13 | 2026-09-15 | **NOASSERTION** | 角色化敏捷团队流程（PM/Architect/SM/Dev/QA persona） |
| `DietrichGebert/ponytail`（在用） | —— | —— | —— | —— | MIT | 防过度设计（七阶阶梯，压缩 diff） |

> ⚠️ star 数**不等于效果证据**。上表仅证明"活跃 + 被广泛安装"；三个仓库都**没有公开可复现的评测**，
> 与 §3.1 的 ETH 结论叠加后，正确姿态是：**当成流程纪律引入，装完必须自测收益**。

---

## 4. 选型建议（按"是否命中实测短板"分级）

### Tier 1 —— 建议补：验证环（直接命中 §3.2 的实证短板）

| 候选 | 内容 | 为什么 |
|---|---|---|
| `obra/superpowers` 子集 | `test-driven-development`（先写失败测试）、`verification-before-completion`（完工前验证）、`systematic-debugging`（系统化调试） | >90% 缺陷来自"缺失检查"；本项目**已有 671 个离线测试**，测试基建成熟 → 引入成本低；TDD 是唯一被反复验证能减少缺失检查的工程手段 |
| `obra/superpowers` 主干 | `writing-plans` / `executing-plans` / `finishing-a-development-branch` | 3h+ / 多文件任务减少返工（社区口径，**无量化证据**） |

### Tier 2 —— 按需补：规格环（大改动防返工）

`github/spec-kit`（官方 + MIT + 137K）：先出正式 spec → plan → tasks → implement。
适合"多资料图谱综合维护""采集器接入"这类跨文件长任务；**小改动上是纯开销**。

### Tier 3 —— 可选：轻量工程技能

`mattpocock/skills` 的 `/code-review` `/tdd` `/to-spec`（体积小、npm 化打包、更新最勤）。

### 明确不推荐

- **BMAD-METHOD**：角色化多人流程，个人单线教学项目**过重**；许可为 NOASSERTION（非标准 SPDX），对参赛"原创无纠纷"不友好。
- **继续堆"仓库概览型"文档**：§3.1 实证无用且 +20% 推理成本 → 反过来应给 `AGENTS.md` **瘦身**。
- **给 ponytail 追加 `-gain`**（已有结论：纯记分板，单 agent 无价值）。

### ⚠️ 两套纪律的张力（必须先说清）

| | ponytail | superpowers（TDD） |
|---|---|---|
| 主张 | 少写代码，测试也 YAGNI | 所有产品代码**必须先有失败测试** |
| 冲突点 | "能不加就不加" | "必须补上验证" |

**裁决规则（建议写进 AGENTS.md）**：
- **小改动 / 纯样式 / 文案** → ponytail 纪律（不写测试，别为指标造测试）；
- **中大型 / 数据层 / 安全或金额路径 / 并发** → TDD 纪律（先失败测试，再实现）；
- 两者共同的不可让项：**输入校验、错误处理、安全、可访问性永不简化**（ponytail 原文如此，与 §3.2 结论一致）。

---

## 5. 落地清单（可执行）

1. **保留 ponytail**（纪律有效），但**对外口径改掉**：不宣称"省 token / 更稳定"；正确表述 = "压缩 diff、守住边界"。
2. **补验证环**：装 `obra/superpowers` 的 `test-driven-development` + `verification-before-completion` + `systematic-debugging` 三个 skill 到 `.codebuddy/skills/`（该目录已 gitignore，沿用"成员自行安装"约定）。
   ```powershell
   $env:HTTP_PROXY="http://127.0.0.1:7897"; $env:HTTPS_PROXY="http://127.0.0.1:7897"
   git clone --depth 1 https://github.com/obra/superpowers.git $env:TEMP\superpowers
   Copy-Item "$env:TEMP\superpowers\skills\test-driven-development"  .codebuddy\skills\ -Recurse
   Copy-Item "$env:TEMP\superpowers\skills\verification-before-completion" .codebuddy\skills\ -Recurse
   Copy-Item "$env:TEMP\superpowers\skills\systematic-debugging" .codebuddy\skills\ -Recurse
   ```
3. **AGENTS.md 瘦身**（依 §3.1）：保留硬约束 / 命令 / 非标准约定；把大段"目录速览 + 架构总览"移到 `docs/` 或压成指针。
4. **自测收益**（照 §1 的方法）：装完挑一个多文件任务，按"有无验证环"各做一次，比 **测试通过率 / 返工轮数 / diff 行数**，别只看 token。
5. 不装：BMAD、`-gain`、任何"仓库概览生成器"。

---

## 6. 复现方式

```powershell
# 1) 夹具（保留在工作区，实验用；不需要可删）
frontend/harness.html + frontend/harness/main.js

# 2) 起 dev server（避开 5173 上与后端的代理）
cd frontend; node node_modules\vite\bin\vite.js --port 5199 --strictPort

# 3) 截图（系统 Edge，无需装浏览器）
& "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" --headless=new --disable-gpu `
  --hide-scrollbars --no-first-run --user-data-dir="$env:TEMP\edge-shot" `
  --window-size=900,560 --screenshot="reports\ponytail_ab\shot.png" "http://localhost:5199/harness.html"

# 4) 量 diff 体量（token 口径与实验一致）
backend\venv\Scripts\python.exe -c "import tiktoken,pathlib;e=tiktoken.get_encoding('cl100k_base');print(len(e.encode(pathlib.Path(r'reports\ponytail_ab\final_A.patch').read_text(encoding='utf-8'))))"
```

产物清单：`baseline.png`（改前）、`runA_no_skill.png`、`runB_ponytail.png`、`final_A_version.png`、`runA.patch`、`runB.patch`、`final_A.patch`、`buildA/B/Final.log`。

---

## 7. 局限与待办

- **单任务实验**（n=1 任务、2 次运行）→ 只能证明"小改动净亏"这个量级关系，不能外推成功率。
- **成功率未测**：本次验收是"能编译 + 视觉正确"，**没测教学对话下的真实成功率**（那需要后端 + 真实 LLM 的端到端用例，成本高）。
- **外部证据半依赖**：ETH 论文只给出"成本 +>20%、成功率无提升"的总体结论；具体 agent 数/任务数需读全文表格。
- 待办：装完 Tier 1 后按 §5.4 做一次"有无验证环"的自测，把结果回填本文档。

---

## 8. 附录：AGENTS.md 该怎么写（规范 + 实证 + 本次瘦身）

### 8.1 标准（[agents.md](https://agents.md/)：Linux Foundation / Agentic AI Foundation 托管，自称 60,000+ 项目采用）

- **定位** = "给 agent 看的 README"：专用、位置固定、纯 Markdown、**零 schema（无必填字段）**。
- **与 README 分工**：README 给人（快速上手）；AGENTS.md 给 agent（构建/测试/风格/约定，更细碎更精确）——**互补，不替代**。
- **官方推荐章节**：Setup commands / Build & test commands / Testing instructions / Code style / Security considerations / PR & commit 规范 / 部署步骤 / 大型数据集说明。
- **官方判据（原话）**："任何你会告诉一位新同事的事情都适合放进来。"
- **大仓玩法**：子目录再放一份 AGENTS.md，agent 读**最近的一份**；冲突时**就近优先，用户对话中的显式指令覆盖一切**。
- **写作风格**：写**可直接执行的命令**、工具特定技巧、明确格式、提交前必跑流程、主动性期望（如"没人要求也要补测试"）。

### 8.2 实证：两条互补的量化结论

| 论文 | 结论 | 对"写什么"的含义 |
|---|---|---|
| **arXiv 2601.20404**（10 仓库 / 124 PR，有 vs 无 AGENTS.md） | 运行时间中位 **−28.64%**、输出 token **−16.58%**，任务完成度无显著差异 | **命令/约定类"能省摸索"的内容值得写**——它买的是效率 |
| **ETH arXiv 2602.11988** | **成功率无提升**、成本 **+>20%**；**指令型内容被良好遵循，仓库概览型内容无用** | **架构/概览叙述不值得写**——它只买成本 |

→ 合成判据：**只写"不写它就会做错、或要摸索半天"的内容；凡是"读一大段才有点用"的，删掉或改成一行指针。**

### 8.3 本次瘦身（2026-09-15 执行）

| 指标 | 前 | 后 |
|---|---|---|
| 行数 / 字节 | 334 行 / 29,299 B | **97 行 / 11,460 B** |
| token（tiktoken cl100k；对 HEAD 版口径） | 8,840 | **4,258（−52%）** |

**保留**：命令与环境、硬约束（`--workers 1` / venv Python / **Jinja2 双花括号** / 存量库补列 / 用户隔离 / 并发提交纪律）、项目特有约定（单一事实源、加工具 1 处定义 3 份产物、SSE 三处同步、掌握度主信号 = 判分）、改架构前的坑清单（**每条一句 + 指针**）、**写代码纪律（skill 分工 + 永不让步项）**、文档路由、测试与提交纪律。

**删除**：架构 ASCII 图、目录速览表（20+ 行）、REST 端点总表（40+ 行）、函数签名与返回值字段表、SSE payload 表、坑的详细叙述（改为"结论 + 指针"）。

**内容去向（防止"信息消失"）**：模块职责 → 各包 `README.md`（`core/agent/`、`core/agent_tools/` 已有）；端点 schema → 运行后端后的 `/docs`；设计缘由与踩坑史 → `docs/项目历程_决策与效果记录.md` + `.codebuddy/memory/MEMORY.md`；跨会话硬约束 → `MEMORY.md`。

**新增**：§3「写代码的纪律」把 6 个已装 skill 与场景绑定，并写明**外部 skill 与本文件冲突时以本文件为准**（例：`brainstorming`/`writing-plans` 的 "frequent commits" ✗ 本项目"不主动 commit"）。

**风险核对**：删除的长叙述若只存在于 AGENTS.md 就会丢 → 逐条核对了替代落点，未发现"仅此一处"的内容；日后若发现，补进对应 `docs/` 而**不要**加回 AGENTS.md（否则又会长回来）。

### 8.4 维护约定（建议）

- 加内容前先自问：这条是"命令/坑/路由"吗？不是 → 放 `docs/`，这里只留一行指针。
- 改动命令、端口、测试口径后**必须同步本文件**（它现在只承诺三件事，漂移会很显眼）。
- 评审标准（对齐 8.2）：**删掉它会让 agent 做错或变慢 = 该留；删掉它 agent 照样做对 = 该删。**

---

## 9. 实际补装清单（2026-09-15 执行）

| 来源 | 已装（`.codebuddy/skills/`，该目录 gitignore，成员自行安装） |
|---|---|
| `DietrichGebert/ponytail`（原有） | `ponytail`、`ponytail-review`、`ponytail-audit`、`ponytail-debt`、`ponytail-help`（`-gain` 不装） |
| `obra/superpowers`（本次，MIT，28.7万⭐） | `test-driven-development`、`verification-before-completion`、`systematic-debugging`、**`brainstorming`、`writing-plans`、`executing-plans`** |

**为什么补的是 superpowers 主干而不是 Tier 2 的 `spec-kit`**：`spec-kit` 需要 `uv`/`pipx` + Python 3.11 工具链，安装会往仓库写入 `.specify/`（四层模板栈）与 `specs/` 新目录并需要 `specify init --here`，与现有 `docs/` + `TODO*.md` 体系重叠；而 superpowers 是**纯 `SKILL.md`、零新依赖、已验证与本平台 frontmatter 兼容**，`brainstorming → writing-plans → executing-plans` 正好覆盖"大改动先出计划"这一目的。
若仍要试 spec-kit：

```powershell
uv tool install specify-cli --from git+https://github.com/github/spec-kit.git@vX.Y.Z
cd <repo>; specify init --here --force --non-interactive --integration claude   # 或 --integration-options="--skills"
```

**未装**：`using-git-worktrees`、`dispatching-parallel-agents`、`subagent-driven-development`（本项目单线性会话，用不上）、code-review 对（缺 reviewer 角色）、BMAD（过重 + NOASSERTION 许可）。
