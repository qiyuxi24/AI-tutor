# Git 多人协作：工作流调研与学习路径

> **用途**：TutorAgent 从「一人开发」切换到「两人共用代码库」的操作手册 + 从零到会治理的学习路线。
> **缘起**：2026-09-15 本仓库真实发生过一次双向分叉（§5 完整复盘）。本文所有建议都对着那次事故写，不是通用教程的搬运。
> **适用**：2~5 人小团队 / 竞赛小组 / 课程项目。>10 人或有正式发布流程的团队另见 §2。
> **读者**：已经会 `add/commit/push`，但要开始和别人一起写同一个库的人。

---

## 0. TL;DR（6 条结论，急着干活的看这一节）

| # | 结论 | 为什么 |
|---|---|---|
| 1 | **选定一个仓库当「唯一真相源」(upstream)**，其它仓库只能是它的派生 | 两个仓库对等写同一个 `main`，谁都不是权威 → 结构上必然分叉 |
| 2 | **用短分支 + PR，不要直推 `main`** | PR 把「合并前」变成可评论、可跑 CI、可拒绝的一步 |
| 3 | 本项目选 **GitHub Flow**，不选 Git Flow | Git Flow 的 `develop`/`release`/`hotfix` 常驻分支对两人项目是纯负担；trunk-based 要求高覆盖率测试 + 特性开关，我们还没到 |
| 4 | 开工第一件事永远是 `fetch` + `rebase`，不是写代码 | 分叉的解决成本随时间指数增长 |
| 5 | 解冲突**保语义，不保某一侧**；`--ours/--theirs` 是最后手段 | §5 实例：两边各自修了同一个 bug，选任一侧都会丢东西 |
| 6 | 学会 `git reflog` | 它几乎是唯一能救回「我以为删掉了」的东西，90% 的「完了」都能救 |

---

## 1. 概念地基（8 个词，之后全是推论）

| 词 | 一句话 | 常见误解 |
|---|---|---|
| working directory | 你正在编辑的文件 | 它和「已提交的东西」是两回事，改文件 ≠ 改历史 |
| staging area (index) | 下次提交要打包的快照 | 它**不是**「暂存草稿」，是提交的原料 |
| commit | 不可变的快照 + 父指针 + 作者信息 | 改了 commit 内容 = 换了一个新 commit（哈希会变） |
| branch | 一个**可移动的指针**（不是文件夹、不是副本） | 建分支几乎零成本，才敢「一任务一分支」 |
| HEAD | 「你现在在哪」的指针 | HEAD 指向分支名（正常）或直接指向 commit（detached） |
| remote | 远端仓库的别名（`origin`/`upstream` 只是名字） | 远程名可以随便改，`git remote rename` 不影响历史 |
| tracking branch | 本地分支 ↔ 远程分支的默认对应关系 | 推/拉省略参数时的默认目标，决定 `git status` 里那个 ahead/behind |
| merge-base | 两个分支的**最近共同祖先** | 三方合并的「第三份输入」就是它，冲突判断全靠它 |

### 1.1 fast-forward 与三方合并

```
fast-forward（无分叉，只是把指针往前挪）
  main    A---B
                 \
  feat             C---D     → git merge feat 后 main 直接指向 D，不产生新提交

三方合并（有分叉，需要造一个新提交）
  main    A---B-------E      ← E 是 merge commit，有 2 个父（B 和 D）
               \     /
  feat           C---D
```

**关键推论**：只要两边都动过同一个文件的重叠区域，就必须「三方合并」→ 必须有人做判断。所谓的「合并冲突」不是 Git 坏了，是 Git 明确告诉你「这里需要人」。

### 1.2 三种搬运提交的方式（最容易混）

| 方式 | 做什么 | 保留原提交哈希？ | 什么时候用 |
|---|---|---|---|
| `merge` | 造一个双父提交，把两条历史接起来 | 是 | 合并已完成的分支、保留分支结构 |
| `rebase` | 把你的提交**重放**到新基底上（生成新提交） | 否（哈希全变） | 同步主干到自己的特性分支、保持线性历史 |
| `cherry-pick` | 挑**单个/一串**提交复制到当前分支 | 否 | 从别的分支挑某个修复、事故回补 |

**一句话记忆**：`merge` 是「接起来」，`rebase` 是「搬过去」，`cherry-pick` 是「摘一颗」。

> ⚠️ 铁律：**已经推给别人的分支不要 rebase**（会改写哈希，别人 pull 时炸）。只 rebase 自己的特性分支。

### 1.3 reflog：你的后悔药

`git reflog` 记录 **HEAD 每次移动**（含 reset、rebase、merge、checkout），默认保留 90 天。

```powershell
git reflog -20                      # 看清我刚才干了什么
git reset --hard HEAD@{3}           # 回到 reflog 里的第 3 个状态
git switch -c rescue <sha>          # 更稳：从丢掉的那个 commit 开一个新分支再救
```

**只有一种东西 reflog 也救不了**：没 `git add` 过、又被外部程序覆盖的工作区文件——所以**重要改动要早提交**。

---

## 2. 工作流调研：三个流派 + 两个变体

### 2.1 横向对比

| 维度 | Git Flow | GitHub Flow | Trunk-Based | GitLab Flow | OneFlow |
|---|---|---|---|---|---|
| 常驻分支 | `main` + `develop` + `release/*` + `hotfix/*` | `main` | `main`（特性分支寿命 < 1 天，甚至直推） | `main` + 环境分支（pre-prod / prod） | `main` 单一长期分支 |
| 发布方式 | release 分支冻结 + tag | 主干随时可发布（tag 可选） | 持续交付，靠灰度/开关控制可见性 | 提交按环境逐级"升舱" | tag 发布 |
| 分支寿命 | 数天~数周 | 数小时~数天 | < 1 天 | 中（跟着环境走） | 中 |
| 适合场景 | 版本化交付的产品（桌面/嵌入式/需维护多版本） | Web / SaaS / 小团队 / 竞赛项目 | 强 CI + 覆盖率高的成熟团队（Google/Meta 量级） | 需要"多环境逐步放量"的团队 | 想避开 GitFlow 分支爆炸、又需要发布分支 |
| 主要代价 | 分支爆炸、合并地狱、`develop` 长期落后 `main` | 需要 PR 纪律与 CI | 需要特性开关 + 自动化测试兜底，否则主干随时是坏的 | 环境分支同步要纪律 | 生态与工具支持少 |
| 提出者 / 代表 | Vincent Driessen（2010） | GitHub | 大厂实践（trunkbaseddevelopment.com 汇总） | GitLab | Adam Ruka |

**2026 年的实际格局**（本次检索的共识）：Git Flow 在「需要同时给多个已发布版本打补丁」的团队里仍然合理；Web/SaaS 与绝大多数中小团队事实上用 GitHub Flow；大厂与 CI 成熟的团队走 trunk-based。**注意 Git Flow 原文作者 2020 年自己加了免责说明**，说该模型不适合 Web 持续交付——引 Git Flow 时别把它当银弹。

参考资料见 §7（A 级来源）。

### 2.2 本项目该选哪个：GitHub Flow

判断依据（三条都是硬条件，缺一条就该重新评估）：

| 判断 | 本项目现状 |
|---|---|
| 需要同时维护多个已发布版本吗？ | **不需要**（单产品、无版本化交付）→ 排除 Git Flow |
| 主干随时可发布吗？ | 可以（`docker compose up -d --build` 即部署）→ 支持 GitHub Flow |
| 测试足够快到能挡在合并前吗？ | 有 `pytest -m "not llm_api"`（670+ 用例，30~45 秒）→ **够用**，这是 GitHub Flow 能成立的前提 |
| 有特性开关体系吗？ | 没有 → 排除 trunk-based（直推主干会立刻影响线上） |

结论：**`main` 永远可发布 + 每个任务开短分支 + 合并前必须过测试**。这已经是 GitHub Flow，不需要额外引入 Git Flow 的分支层级。

---

## 3. 落地架构：三种形态，选 A

### 方案 A：单仓 + Collaborator + PR（**推荐**）

```
qiyuxi24/AI-tutor            ← upstream（唯一主仓，main 只能通过 PR 改）
  ├─ feat/quiz-export        （同学开）
  └─ fix/rag-timeout         （你开）
```

- 把对方加为协作者：仓库 → Settings → Collaborators and teams → Add people（写入权限）
- 双方都：`clone` 主仓 → 开 `feat/*` 分支 → push 到**同一个**远程 → 开 PR → 对方 review → 合并
- 只有**一个** `main`，从根上消灭「谁是真相」的问题

### 方案 B：Fork 模型（对方不能加协作者时）

```
qiyuxi24/AI-tutor            ← upstream（主仓）
zhuzixuan2007/AI-tutor       ← fork（同学的 origin，他的 main 只用来同步）
```

- 命名约定：`origin` = 自己的 fork，`upstream` = 主仓（**别反过来**，否则 `pull` 会拉错）
- 提「从 fork 到主仓」的 PR；主仓 `main` 永不被 fork 直推

### 方案 C：双远程对等（**本项目现状，应淘汰**）

现在的情况：`origin` = 你的仓库，`github-desktop-zhuzixuan2007` = 同学仓库，**两边都能写 `main`**。

为什么结构上必然分叉：
1. 每个 `main` 都是「我的 `main` + 我合并进来的对方的某个旧状态」→ 谁也不是全集
2. 双方各自 merge 对方 → 每次操作都产生新的 merge 提交（我们的历史里就有 4 个 `Merge branch 'qiyuxi24:main' into main`），历史图变成麻花
3. 没有任何一方能说「以谁为准」，冲突解决策略无从收敛

**如果暂时不改造成 A/B**，最低限度约定：
- 只有一个仓库（主仓）允许直推 `main`；另一个仓库的 `main` 只允许被 `pull --ff-only` 更新
- 任何功能改动都必须走分支 + PR

### 3.1 把现状改成方案 A/B 的命令

```powershell
# 1) 先看清现状
git remote -v
git branch -vv

# 2) 统一命名（方案 A：主仓就是 origin）
git remote rename origin upstream
git remote rename github-desktop-zhuzixuan2007 origin
git fetch --all --prune
git switch main
git branch --set-upstream-to=upstream/main main   # 方案 B 时指向 upstream；方案 A 指向 origin

# 3) 顺手清理：陈旧分支、陈旧的 cherry-pick/rebase 中间态
git branch -vv                    # 看哪些分支已合并
git branch --merged main          # 已合并的可删
git cherry-pick --quit            # 只在被 sequencer 卡住时用（见 §5.2）
```

---

## 4. 日常 SOP（照抄即可）

### 4.1 开工（每次动手前，30 秒）

```powershell
git status                       # 有脏改动先 stash / 提交，别带着脏状态切分支
git switch main
git fetch --all --prune          # 先拉远程引用，不合并工作区
git pull --ff-only               # 只允许快进；拒了 → 说明有人直推了 main，先问清楚再动
git switch -c feat/quiz-export   # 一任务一分支，名字带类型前缀
```

> `--ff-only` 是关键：它保证「我的本地 main 只是远程 main 的镜像」。一旦它失败，就是警报，不是麻烦。

### 4.2 写代码期间

```powershell
git add -p                                    # 分块暂存，别把调试残留一起提交
git commit -m "feat(quiz): 试卷导出支持 Word/PDF"

git fetch origin && git rebase origin/main    # 每天至少一次，代价最小
git push -u origin feat/quiz-export           # 首次推分支
git push --force-with-lease                   # rebase 之后重推
```

> **`--force-with-lease` 而不是 `--force`**：前者会在「远程被别人动过」时拒绝，后者会把别人的提交直接覆盖掉。

### 4.3 提交信息规范（Conventional Commits）

```
<type>(<scope>): <subject>
```

| type | 含义 | | type | 含义 |
|---|---|---|---|---|
| `feat` | 新功能 | | `refactor` | 重构（不改行为） |
| `fix` | 修 bug | | `test` | 测试 |
| `docs` | 文档 | | `chore` | 杂务（依赖、配置、脚本） |
| `perf` | 性能 | | `ci` / `build` | CI / 构建 |

为什么值得（不是洁癖）：机器可读 → 自动生成 changelog、语义化版本号可从提交推导、PR 标题直接当 release note。
本项目已有实践：`feat(quiz): …`、`fix(ops): …`、`fix(docker): …`。

正文里写清 **为什么**（不是「改了什么」，diff 已经说了改了什么）：

```
fix(kg): 修正 get_prerequisites 的前置边方向

边方向语义是 A → B 表示 A 是 B 的前置；原实现按 B → A 查，
导致沿先修边扩跳时取到了后继节点。
```

### 4.4 收口（合并回 main）

**团队走 PR**：开 PR → 至少 1 人 review → 合并。
- 小团队建议 **Squash merge**：把分支压成一个提交，历史干净、回滚容易
- 需要保留「这个功能分了 5 步做」的信息时用 **Merge commit**

**两人项目可以本地合并，但必须守规矩**：

```powershell
git switch main && git pull --ff-only
git merge --no-ff feat/quiz-export -m "Merge feat/quiz-export"
backend\venv\Scripts\python.exe -m pytest backend/tests -q -m "not llm_api"   # 合并后跑全量
git push origin main
git branch -d feat/quiz-export
git push origin --delete feat/quiz-export
```

### 4.5 冲突处理 SOP（5 步）

1. **看清单**：`git status --short` → `UU` 是未解决冲突，`AU/UA/DU` 同理
2. **先读意图，别急着选边**：`git log --oneline -5 HEAD -- <file>` 与对方分支的同一命令，看清两边**各自想干什么**
3. **分类**（决定怎么解）：
   - 「互相覆盖」→ 保谁的业务语义正确就留谁
   - 「各自修了同一个问题」→ **融合**，删掉重复的那部分（§5 实例）
   - 「一边重命名/搬家，一边改内容」→ 通常是保留搬家的位置 + 搬入内容改动
4. **删干净标记 + 编译 + 跑全量测试**：
   ```powershell
   git grep -nE "^(<<<<<<<|=======$|>>>>>>>)"       # 无输出 = 标记清完了
   backend\venv\Scripts\python.exe -m py_compile <改动文件>
   backend\venv\Scripts\python.exe -m pytest backend/tests -q -m "not llm_api"
   ```
5. **提交**：`git add <具体文件>` → `git commit`（merge 用默认模板，正文写清「怎么解的、为什么」）

**禁忌清单**（每条都对应一类真实事故）：

| 别做 | 后果 |
|---|---|
| `git add -A` / `git commit -a` | 把并发会话的 WIP、调试残留一起提交 |
| 解冲突只改主文件不跑测试 | 语法通过 ≠ 语义正确（§5 的 `break` bug 就是这样潜伏的） |
| `git push --force` | 覆盖别人已推的提交 |
| `git checkout --ours/--theirs` 直接收工 | 丢另一侧的全部改动，含你不知道的修复 |
| 在共享分支上 `rebase` | 改写别人的历史 |
| 放弃 `cherry-pick`/`rebase` 后不清 `--quit/--abort` | 仓库长期卡在中间态（§5.2） |

### 4.6 救火速查表

| 症状 | 命令 |
|---|---|
| 提交信息写错了（未推） | `git commit --amend` |
| 提交信息写错了（已推、只有你） | `git commit --amend` + `git push --force-with-lease` |
| 提交错了东西（已推、别人可能已拉） | `git revert <sha>`（造一个反向提交，安全） |
| 想撤销最近一次提交但保留改动 | `git reset --soft HEAD~1` |
| 想彻底丢弃本地改动 | `git reset --hard HEAD` / 单个文件 `git restore <file>` |
| 切分支前的临时保存 | `git stash push -m "wip"` / `git stash pop` |
| 不知道哪个提交引入的 bug | `git bisect start` → `bad` / `good` |
| 忘了自己在哪、干了什么 | `git reflog -20`、`git status`、`git log --oneline --graph --all -20` |
| 想临时看某个旧版本 | `git switch --detach <sha>`（看完 `git switch -` 回来） |
| 想看某行是谁写的/为什么 | `git blame <file>`、`git log -L 10,20:<file>` |

---

## 5. 真实复盘：2026-09-15 的双向分叉

### 5.1 分叉全貌

```
origin/main（qiyuxi24）                github-desktop-zhuzixuan2007/main（同学，= 本地分支 pr/1）
2e19ef7 context engineering      ←×→   98928e1 Merge …
       ↑                                ├─ 15c1210 Merge branch 'qiyuxi24:main' into main
       │                                ├─ 8d2230e 出题模块大改（35 文件 +3428 行）
   独有 1 个提交                         ├─ 396818b / 43798b4 Merge …
                                        └─ 376287b quiz_store 修复
                                        （独有 6 个提交）
```

双方**都以为同步过了**——但每边都只同步到对方的**上一个**状态：
- `origin/main` 有 1 个提交同学没有（`context engineering`）
- 同学有 6 个提交 `origin/main` 没有（含整个出题模块 + 试题下载）

### 5.2 三个根源（同时发生才这么乱）

**① 结构性：双远程对等写 `main`**
上面的 `Merge branch 'qiyuxi24:main' into main` 出现 4 次，每次都是「把对方合进来」。两边都在合对方，历史互相缠绕，谁也不是全集。

**② 重复劳动：两边各自修了同一个 bug**
`call_llm` 的 `max_tokens` 参数化 + 空回复重试：你修了一份（`finish_reason == "length"` 告警 + `thinking` 开关），同学也修了一份（`_EMPTY_RETRY_TOKENS = 8000` 预算抬升）→ 合并时**必然冲突**，而且两边方案互补，任何一侧单独收下都是退步。

**③ 中间态没清理：一次被放弃的 `cherry-pick`**
`.git/sequencer/` 里留着 6 个 `pick` 的 todo（2026-09-13 创建），但序列早已被放弃。后果：之后每次 `git status` 都显示 `Cherry-pick currently in progress`，而工作区其实是干净的 → **干扰每一次决策**，差点让人以为在解一个不存在的冲突。

正确的清场命令（工作区干净、无 `CHERRY_PICK_HEAD` 时安全）：

```powershell
git cherry-pick --quit      # 保留工作区，只清中间状态
# 想连改动一起回滚：git cherry-pick --abort
```

### 5.3 处置过程与结论

**冲突 2 个文件，都按「保语义」融合**：

| 文件 | 两边各有什么 | 解法 |
|---|---|---|
| `core/llm/call.py` | main：`thinking` 开关（`extra_body(thinking)`）+ 截断告警；pr/1：空回复重试时把预算抬到 8000 + `finish_reason` 日志 | 保留 `thinking` 开关，采纳预算抬升重试，日志合并成一处 |
| `core/kb/graph_generator.py` | main：`thinking=False` + JSON 失败重试；pr/1：异常时 `sleep(2)` 重试 | 把异常重试并入同一个 `GRAPH_JSON_RETRIES` 循环；提示词规则两边都保留（篇幅上限 + 引号禁忌） |

**顺带发现同学那侧一个真 bug**：

```python
# core/kb/graph_generator.py :: GraphGenerator._call_generator_llm
for attempt in range(2):
    try:
        raw = await call_llm(...)
        break            # ← 这行会跳过下面的 JSON 解析
    except Exception as e:
        ...
data = extract_json(raw)      # 循环外，成功路径永远走不到 → data 未绑定
...
nodes = data.get("nodes", []) # UnboundLocalError
```

**这就是「盲目 `--theirs` 会带进 bug」的实证**：对方那侧看起来「更完整」（它有重试），但里面藏着会让整条链路崩的缺陷。解冲突时**必须回读控制流**，不能只对齐文本。

### 5.4 教训清单（本文最值钱的部分）

1. **修 bug 前先看对方分支**：`git log --oneline -20 -- <file>`（含所有远程分支）——对方可能已经修了，重复修 = 制造冲突
2. **`cherry-pick` / `rebase` 被放弃后立刻清中间态**（`--quit` / `--abort`），不要让 `.git/sequencer` 残留
3. **同一时刻只允许一个人写 `main`**（这是 #1 号结论的日常落地形式）
4. **合并后必须跑全量测试，而且要在「只有一个写者」的时间窗里跑**：我们那次测试跑到一半撞上并发写文件，出现了 2 个**假失败**（同一批用例重跑就全绿）→ 排查成本极高
5. **工作区有别人的未提交改动时，用路径限定的提交**：`git commit -F msg` 只提交已暂存内容，**永远不要 `-a`**；否则会把并发会话的 WIP 一起打包
6. **解决冲突的产物要能说清「为什么这么解」**：写进 merge commit 正文（本例已写），否则三个月后没人敢动这段代码

### 5.5 本次的最终状态

| 项 | 结果 |
|---|---|
| 合并提交 | `6fe427c Merge branch 'pr/1'（同学出题模块改造）into main` |
| 推送 `origin`（qiyuxi24） | ✅ `2e19ef7..6fe427c  main -> main` |
| 推送同学仓库 | ❌ `! [remote rejected] (permission denied)` —— 你对该仓库只有读权限，需要他拉取或把你加为 collaborator |
| 验证 | `pytest -m "not llm_api"` → **670 passed, 7 deselected** |

同学的仓库这次推不进去，正好说明 §3 的必要性：**只要 A、B 两个仓库都能写同一个 `main`，就永远需要「谁来推」这种人工协调**；改成方案 A（单仓 + collaborator）后，一次 `git pull` 就够了。

---

## 6. 学习路径（4 阶段，每阶段都有可验证的验收标准）

> 学法建议：**别通读文档**。拿本仓库当靶子，按下面每阶段的「练习」真做一遍——这四条练习全部做完，你就有维护这个仓库协作的能力了。

### L0 · 会用（约半天）

**目标**：能安全地"改文件 → 提交 → 看历史 → 反悔"。

必会：`status` / `add -p` / `commit` / `log --oneline --graph --all` / `diff`（`--staged`）/ `restore` / `switch -c` / `merge` / `stash`

练习（拿本仓库做）：
```powershell
git log --oneline --graph --all -30        # 看懂 §5.1 那张图的 ASCII 版本
git switch -c sandbox/l0                  # 随便改一个 .md，提交 2 次
git restore <file>                        # 撤销未暂存改动
git reset --hard HEAD~1                   # 丢掉第 2 次提交
git reflog -5                             # 再把它救回来
```

✅ 验收：能解释 `git status` 输出里 `M`、`MM`、`A`、`??`、`UU` 各是什么意思。

### L1 · 会协作（1~2 天）

**目标**：能和别人在同一个库上并行工作，不制造分叉。

必会：`remote -v` / `remote add|rename` / `fetch --all --prune` / `pull --ff-only` / `push -u` / tracking branch / `clone` / PR 流程 / fork 与 collaborator 的区别

练习：
```powershell
git remote -v                              # 认出 §3 里三个远程的现状
git fetch --all --prune
git branch -vv                             # 判断每个本地分支领先/落后多少
# 开一个真 PR 走完：分支 → push → 开 PR → 自审 diff → squash merge → 删分支
```

✅ 验收：能说清「`fetch` 和 `pull` 的区别」「`--ff-only` 为什么是好东西」「fork 与 collaborator 两种协作方式的取舍」。

### L2 · 会救火（2~3 天）

**目标**：出错时能在 5 分钟内定位并恢复。

必会：`reflog` / `reset --soft|--mixed|--hard` / `revert` / `amend` / `force-with-lease` / `rebase` / `rebase -i` / `cherry-pick`（含 `--continue/--skip/--abort`）/ 冲突三件套 / `bisect`

练习（**在一个临时 clone 里做**，别拿生产库试）：
```powershell
git clone . ../git-drill && cd ../git-drill
git switch -c drill/a && <改文件> && git commit -am "a"
git switch main && <改同一行> && git commit -am "b"
git merge drill/a                          # 故意制造冲突，按 §4.5 的 5 步解
git rebase -i HEAD~3                       # 试 squash / reword / drop
git bisect start                           # 定位一个人为埋进去的坏提交
```

✅ 验收：能独立完成「解一个含 3 处冲突的合并」「把 5 个乱提交整理成 1 个」「找回一个被 `reset --hard` 丢掉的提交」。

### L3 · 会治理（持续）

**目标**：让上面的流程**由系统保证**，而不是靠自觉。

要做的事（按性价比排序）：
1. **保护 `main`**：GitHub → Settings → Branches → 禁止直推、要求 PR、要求 CI 通过
2. **CI**：GitHub Actions 跑 `pytest -m "not llm_api"`（我们已有全套离线用例，约 40 秒，最适合当门禁）
   ```yaml
   # .github/workflows/test.yml（示意）
   on: [pull_request]
   jobs:
     test:
       runs-on: ubuntu-latest
       steps:
         - uses: actions/checkout@v4
         - uses: actions/setup-python@v5
           with: { python-version: "3.11" }
         - run: pip install -r backend/requirements.txt
         - run: python -m pytest backend/tests -q -m "not llm_api"
   ```
3. **提交规范机器化**：`commitlint` / pre-commit hook（也可先用 `.gitmessage` 模板）
4. **`.gitattributes`**：统一 LF/CRLF（Windows + Linux 混编时能省掉大量「整个文件都变了」的假 diff）
5. **发布**：tag + 语义化版本（`git tag -a v1.0.0 -m "…"` + `git push --tags`）
6. **定期盘点**：删已合并分支、看 `git log --merges` 复盘分叉

✅ 验收：有人直推 `main` 时 CI/保护规则会**自动拦住**；任何一次合并都有 PR 记录可回溯。

---

## 7. 参考资料（按权威度分级，A 级可作依据）

**A 级（官方 / 一手）**
- Pro Git（中文全本）：https://git-scm.com/book/zh/v2 —— 概念地基与 §1 的唯一权威来源
- git-scm 命令参考：https://git-scm.com/docs
- GitHub Flow 官方：https://docs.github.com/zh/get-started/using-github/github-flow
- GitHub 协作 / PR 文档：https://docs.github.com/zh/pull-requests
- Trunk-Based Development：https://trunkbaseddevelopment.com
- Conventional Commits 1.0.0（含中文）：https://www.conventionalcommits.org/zh-hans/v1.0.0/
- 语义化版本 2.0.0：https://semver.org/lang/zh-CN/
- Git Flow 原文（含作者 2020 的更新与免责）：https://nvie.com/posts/a-successful-git-branching-model/

**B 级（高质量二手 / 交互式）**
- Learn Git Branching（交互式，强烈建议当游戏玩一遍）：https://learngitbranching.js.org/?locale=zh_CN
- Oh Shit, Git!?!（常见事故自救）：https://ohshitgit.com/zh
- Atlassian Git 教程（工作流对比写得清楚）：https://www.atlassian.com/git/tutorials/comparing-workflows

**C 级（本次检索到的中文概览，作起点不作依据）**
- Git 工作流 2026 实战：trunk-based vs GitFlow vs GitHub Flow — https://xtechtools.com/learn/git-workflow-2026/
- Git 分支工作流：从 Git Flow 到 Trunk-Based — https://zzqdeco.github.io/blog/git-branching-workflows/
- GitHub 协作指南：从 Fork 到 Pull Request — https://divineengine.net/article/how-to-collaborate-on-github/
- GitHub 协作实战：从 Fork 到 Pull Request 完整流程 — https://blog.csdn.net/gitblog_00099/article/details/150742266

**工具**
- `gh` CLI（命令行开 PR / 看 CI）：https://cli.github.com
- VS Code「GitLens」（看每行历史）、SourceTree / GitKraken / `lazygit`（图形化）
- `git delta`（更可读的 diff）

---

## 8. 一页速查表

| 场景 | 命令 |
|---|---|
| 开工 | `git switch main && git fetch --all --prune && git pull --ff-only` |
| 开任务分支 | `git switch -c feat/<名>` |
| 分块提交 | `git add -p && git commit -m "feat(x): …"` |
| 同步主干 | `git fetch && git rebase origin/main` |
| 重推（rebase 后） | `git push --force-with-lease` |
| 看全局 | `git log --oneline --graph --all -30` |
| 看某个文件的历史 | `git log --oneline -- <file>` / `git blame <file>` |
| 找我领先/落后多少 | `git branch -vv` / `git rev-list --left-right --count A...B` |
| 查分叉点 | `git merge-base A B` |
| 看谁改过这块 | `git log -L 10,20:<file>` |
| 出事了 | `git status` → `git reflog -20` → `git reset --hard <sha>` 或 `git revert <sha>` |
| 冲突标记检查 | `git grep -nE "^(<<<<<<<\|=======$\|>>>>>>>)"` |
| 清中间态 | `git cherry-pick --quit` / `git rebase --abort` / `git merge --abort` |
| 合并前保险 | `git merge-base --is-ancestor <sha> main; echo $?`（0=已包含） |

---

## 9. 本项目待办（把方案落地）

- [ ] **决定协作架构**：`qiyuxi24/AI-tutor` 作为主仓 + 把同学加为 collaborator（方案 A），或维持 fork 模型（方案 B）
- [ ] 按 §3.1 统一远程命名（`origin` = 主仓 / 自己的 fork，`upstream` = 主仓），避免 `pull` 拉错源
- [ ] `main` 开保护分支：禁止直推、要求 PR、要求 CI
- [ ] 加 GitHub Actions 跑 `pytest -m "not llm_api"`（离线 670 用例，40 秒，最适合当门禁）
- [ ] 提交规范写入 `CONTRIBUTING.md`（Conventional Commits + 一个正例/反例）
- [ ] 加 `.gitattributes` 统一换行符（本仓库已有多次 `LF will be replaced by CRLF` 警告）
- [ ] 把同学仓库那次 `permission denied` 的结论同步给他：他需要 `git pull` 主仓，或把你加为 collaborator
