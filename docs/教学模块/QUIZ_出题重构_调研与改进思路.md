# 出题模块（core/quiz）重构调研与改进思路

> 创建：2026-11-16
> 触发：用户判断"出题这块硬编码和不完善的地方居多，先调研给个改进思路"。
> 口径：本文只做**调研 + 方案**，不动代码。所有"实测"数字来自 2026-11-16 对
> `data/quiz/5/quiz.db`（33 题 / 11 个题集）与 `data/quiz/1/quiz.db`（6 题）的只读查询，
> 探针在 `.dsh_local/probe_quiz_stats.py` / `probe_quiz_detail.py` / `probe_quiz_dups.py` /
> `probe_quiz_shape.py` / `probe_quiz_holes.py`。

---

## 一、结论先说

**出题现在不是"有 bug"，而是缺一层「出题规格」。**

三条入口（`POST /quiz/generate`、工具 `quiz_generate`、`POST /knowledge/node/{id}/section/{sid}/quiz`）
把**题量 / 题型 / 难度 / 依据来源 / 超时 / 去重范围**全部写死在各自的模块常量里，然后共用一个
"你说出几道题、我就让模型自由发挥"的生成器。于是：

- 题目形状靠**提示词里的一句话**约束（"4 个选项""位置随机化"），没有任何代码侧核验 → 实测没生效；
- "考什么"没有规划，只有一句"请聚焦不同考点，避免与其他批次重复"，谁也没真的分派考点；
- 质量标准是**三条硬编码规则**（题长 / 外部依赖 / 文本去重），而被否掉的高价值检查（答案自校验、
  干扰项质量、依据溯源、难度一致性）一个都没落；
- 三条入口的默认值互不一致，同一个概念（题型 / 题量 / 依据）在 4 处各写一遍。

**改进主线**：引入 `QuizSpec`（出题规格，声明式、可落库、可回放）→ 规格先规划「考什么」（蓝图）
→ 按槽位生成 → 可插拔闸门核验 → 判分策略表。把"硬编码常量"变成"规格 + 配置 + 注册表"。

---

## 二、现状实测：硬编码清单

### 2.1 生成参数散落在模块常量里（都没有配置入口）

| 常量 | 位置 | 值 | 问题 |
|---|---|---|---|
| `QUIZ_BATCH_SIZE` | `core/quiz/generator.py:39` | 4 | 批量上限写死，不能按模型/题型调 |
| `QUIZ_BATCH_MAX_TOKENS` | `core/quiz/generator.py:40` | 8000 | 同上；撞顶只告警不降级 |
| `QUIZ_MAX_TOPUP_ROUNDS` | `core/quiz/generator.py:41` | 2 | 补题上限写死，与题量无关（要 20 道也只补 2 轮） |
| `QUIZ_MATERIALS_TOP_K` | `core/quiz/generator.py:46` | 8 | 独立出题页的检索条数；每批都重发同样的 8 段，token 重复付费 |
| `CHAT_QUIZ_COUNT` | `core/quiz/chat_quiz.py:43` | 1 | 对话内只出 1 道，是常量不是规格 |
| `CHAT_QUIZ_TOP_K` | `core/quiz/chat_quiz.py:48` | 0 | 有注释说明"必须 0"，但仍无规格可覆盖 |
| `CHAT_QUIZ_TYPES` | `core/quiz/chat_quiz.py:51` | 4 种客观题 | 与独立出题页（5 种）分裂，靠两处维护 |
| `CHAT_QUIZ_TIMEOUT_SECS` | `core/quiz/chat_quiz.py:59` | 120 | 墙钟与题量无关 |
| `difficulty = "medium"` | `core/quiz/chat_quiz.py:209` | 字面量 | 对话内难度永久是 medium |
| `_MIN_STEM_CHARS` 等 | `core/quiz/quality.py:38-40` | 12 / 20 / 500 | 阈值写死；注释记录**已被真机放宽两次** |
| `_JUDGE_SYNONYMS` / 否定词 / `window=6` | `core/quiz/grader.py:31-32,137-145,35` | 词表 | 判分口径是代码里的私有词表，不可配置、不可测边界 |
| `MASTERY_CORRECT_GAIN` | `core/quiz/chat_grade.py:55` | 20 | 掌握度增量写死；全节通过 = 100 亦写死 |
| `_QUIZ_CONSENT_MAX_CHARS` + 两张同意词表 | `core/quiz/consent.py:28-42` | 12 字 + 关键词 | 关键词分类器，长句一律"不确定 → 拒" |
| `points` | `schema.py:43` 默认 10 / 提示词 `generator.py:84` 写死 10 / `chat_quiz.py:182` 兜底 10 | 10 | 全库 33 题**全部 10 分**，无分值策略 |

`core/config.py` 里与出题有关的只有 `quiz_storage_quota`（`config.py:119`）——
**出题行为没有任何可配置项**，改行为只能改代码。

### 2.2 提示词硬编码在 Python 字符串里

- 出题系统提示词 = `generator.py:49-89`（`QUIZ_GENERATOR_SYSTEM_PROMPT`）
- 简答判分提示词 = `grader.py:184-186`（`_SHORT_ANSWER_SYSTEM_PROMPT`）

而对话提示词的机制是 **`data/prompts/*.j2` + `core/prompt_loader.py`**（改模板热重载，
`start.ps1:461` 专门加了 `--reload-dir data/prompts`）。出题侧走反了：改一句出题要求
要改 Python、重启进程，且无法按题型/场景分模板。

### 2.3 "题型"这一个概念被写在 4 个地方

| 位置 | 表达 |
|---|---|
| 提示词 `generator.py:56-61` | "single（单选题）：只有一个正确选项，4 个选项" |
| 结构校验 `generator.py:433-457` | `single` 必须 `len(options) == 4`；`multiple` ≥3；`judge` 答案 ∈{对,错} |
| 长度闸门 `quality.py:93-95` | 有选项 → 12 字下限；无选项/简答 → 20 字 |
| 前端 `QuizView.vue:82-97`、`QuizCard.vue:26-28` | 题型中文名、图标色、控件形态各一份 |

**加一种题型要改 4~5 处**；而这 4 处已经实测漂移：`judge` 题的选项形状在库里就有两种
（id 8/34/47/86/88 带 `[{对},{错}]` 两个选项，id 79/82/83/90 是 `[]`，9 道里 5:4），两种都能
通过 `_validate_question` 的校验 —— 前端 `QuizCard.vue:113` 只好"固定给对/错两个按钮"兜底。

### 2.4 结构性缺口（比硬编码更贵）

1. **没有"批次"实体，只有 `GROUP BY`**
   `quiz_store.list_batches`（`quiz_store.py:249-283`）用 `GROUP BY COALESCE(batch_id, 'legacy-'||id)`
   现算题集。于是**出题规格、生成状态、拒绝统计、补题轮次**全都无处可存；
   出过的题无法复现，也无法回答"这套卷是按什么要求出的"。
2. **没有考点规划（blueprint）**
   `_build_user_prompt`（`generator.py:92-121`）只给主题 + 材料 + "请聚焦不同考点"。
   实测：uid5 的 33 道题里 **6 道是"下列说法正确的是"套式**，跨题集出现 0.84 相似度的近重复；
   同一个知识点可以连出 3 道"定义辨析"。没人分派"这一批覆盖哪几个考点"。
3. **难度是整批参数，不是题目属性**
   `Question`（`schema.py:26-51`）没有难度字段；`questions.difficulty` 是 `save_questions`
   整批写入的列 → 实测 **33/33 全是 `medium`**。差异只存在于提示词里的一句话，
   "难度一致性"从未校验（设计稿 §6 已自承未实现）。
4. **答案位置没随机化，只是"提示词要求过"**
   提示词 `generator.py:72` 写"正确答案位置随机化"。实测 uid5 的 21 道单选：
   `A` 6 道、`B` 13 道、`C` 2 道、**`D` 0 道**；uid1 的 4 道单选只有 B/C。这不是随机。
5. **缺陷题能过全部闸门**
   id 36（多选）四个选项**全是正确答案**（"A/B/C/D"），解析里还说"四种均正确" ——
   它等价于"以上都对"，提示词里明文禁止，但代码不查。
6. **去重只有字面归一化**
   `quality._deduplicate` 只做去标点小写比对；跨批次近重复实测存在（0.87）。
   库里已有 `kb/embedder.py::ApiEmbedder` 与 `llm/embed_texts`，语义去重是现成的。
7. **判分侧没有任何"读"**
   `attempts` 只写不读（`stats()` 只有 `COUNT(*)`）：无正确率、无错题池、无 `next_review_at`。
   实测 uid5 已作答 22/33、正确率 **0.955** —— 题太容易，且这个事实没有任何界面或调度会用到。
8. **题量/补题两轮不一致**
   首轮按 `_types_for_batch` 把题型切给各批（`generator.py:205-216`），补题却把**全部题型**
   再传一遍（`generator.py:397`）——补出来的题与首批题型分布不可比。
9. **作答链路会丢 `questionId`**
   `ChatArea.vue:393` 是 `@submit-quiz="(p) => store.send(p.answer)"`，把 `p.questionId` 扔了，
   后端只能 `get_pending_question()` 猜"最近一道未作答的题"（`quiz_store.py:191-206`）。
   两个对话各有一道待答题时必错判（`core/quiz/TODO.md` P1 已记）。
10. **`knowledge_point` 同时是"节点 id"和"LLM 编的中文名"**
    对话内出题写的是节点 id（`chat_quiz.py:242-243`），独立出题页从活动栏进入时没有锚点，
    写的是模型自创的名字 → 判分回写要靠 `resolve_node_id` 三档猜（`chat_grade.py:61-93`）。
    这是**生成时没锚定**的后果，在判分侧打补丁（实测能救 89%，剩 5 道如实放弃）。

---

## 三、改进思路：五层，从"规格"开始

### 第 0 层：先量化（不改行为）

新增只读脚本 `backend/scripts/inspect_quiz_quality.py`（对齐 `inspect_graph_quality.py` 的形态），
固化本文二节的指标作为基线，改完能对比：

| 指标 | 口径 | 当前基线（uid5 / uid1） |
|---|---|---|
| 答案位置分布 | 单选题 `answer[0]` 计数 + 卡方 | A6/B13/C2/D0 ／ B1/C3 |
| 退化项 | 多选全选 / 无干扰项 / 解析为空 | 1 ／ 0 |
| 题型形状一致性 | judge 带 options 的比例 | 5/9 带、4/9 不带（同一题型两种形状） |
| 题干套式占比 | 含"下列说法/下列哪项" | 6/33 |
| 跨题集近重复 | 归一化 + `SequenceMatcher ≥ 0.8` | 1 ／ 1（0.87） |
| 覆盖度 | 每题 `cover_key` 的分布（新字段落地后） | 无此字段 |
| 作答正确率 | `attempts.correct` 均值（题数 ≥ 3 才统计） | 0.955（22 次） |

### 第 1 层：`QuizSpec` —— 出题规格（核心）

新增 `core/quiz/spec.py`，Pydantic 模型（**只描述"要什么"，不含任何 prompt 文本**）：

```python
class QuizSpec(BaseModel):
    # 锚点：考哪块内容（三者互斥，优先 section > node > kb）
    anchor: AnchorSpec            # kind: node|section|kb|none, id, subject
    # 依据来源策略：node_only（对话内）/ node_then_kb / kb_only
    grounding: GroundingPolicy
    # 题量与题型配比（不是"题型列表"，是"每类各几道"）
    blueprint: list[SlotSpec]     # {type, count, points, difficulty, bloom?}
    # 难度：整卷目标 + 允许分布
    difficulty: DifficultySpec    # target, spread, adaptive: bool
    # 质量闸门与阈值（哪些检查跑、阈值多少）
    gates: GateConfig
    # 去重范围与预算
    dedup: DedupSpec              # scope: node|section|user_subject, limit, semantic: bool
    # 生成预算
    budget: BudgetSpec            # batch_size, max_tokens, max_topup_rounds, wall_clock
```

配套 `core/quiz/presets.py`（纯数据，一行一个场景）：

| 预设 | 用途 | 关键差异 |
|---|---|---|
| `chat_quick` | 对话内即学即测 | 1 道、只客观题、`grounding=node_only`、无检索、墙钟 120s（= 现有行为） |
| `section_check` | 小节验证 | 1~2 道、按小节正文、挂 manifest 路由（= 现有行为） |
| `chapter_test` | 章节自测（独立出题页） | 5~10 道、5 种题型、`node_then_kb` |
| `mock_exam` | 整卷模考 | 蓝图配比 + 分值 + 时间；Phase 4 |
| `review_wrong` | 错题重练 | 从 `attempts` 取错题母版变式；Phase 4 |

**收益**：三条入口不再各写一份默认值，而是 `preset + 调用方覆盖`；规格随题目一起落库，
出过的卷可回放、可 A/B、可回答"这套按什么要求出的"。
**约束**：`chat_quick` 的规格必须**逐字段等价于今天的行为**，否则会动到 40s/120s 的既有承诺。

### 第 2 层：蓝图（先定"考什么"，再出题）

新增 `core/quiz/blueprint.py`，纯函数：

```
anchor 正文 / 小节清单 / 已考题干
   → 候选考点（小节标题、正文小标题、节点 summary 的关键名词）
   → 按 spec.blueprint 分配槽位（每槽 = 一个考点 × 一个题型 × 一个难度）
   → 生成时每个槽位单独一次调用（可并发、可重试、可单独补）
```

- 考点 `cover_key` 落到题行（新列），去重从"题干文本像不像"升级为"**这个考点考过没有**"。
- 一举解决三件事：题目漂到节点之外、同一考点反复出、某个考点一次没考到。
- 实现不需要新表：小节标题来自 `kg.list_sections`，正文来自 `node_content_text`。

### 第 3 层：生成器按槽位、提示词模板化

- 提示词迁到 `data/prompts/quiz/{generate,solve_check,grade}.j2`，由 `prompt_loader` 扩展
  支持子目录。**规格参数注入到 user 消息、不进 system**（system 保持稳定以命中前缀缓存，
  见 `docs/上下文工程/上下文工程_Prompt缓存命中率_调研与优化方案.md`）。
- 按槽位生成替代"整批一句话"：单次输出短（不再依赖拆批躲 `max_tokens`），补题变成
  "哪个槽位空着补哪个"，`_types_for_batch` / `_split_counts` 这两个补丁式函数可以直接删掉。

### 第 4 层：闸门（可插拔 + 可配置 + 可计数）

把 `quality.py` 从"函数里硬编码两条规则"改成注册表 `core/quiz/gates/`：

| 闸门 | 现状 | 目标 |
|---|---|---|
| `structure` | 有（但形状口径写死） | 从题型目录读 `options.min/max` |
| `self_contained` | 有（正则词表） | 保留，词表移配置 |
| `dedup_exact` | 有 | 保留 |
| `dedup_semantic` | **无** | 复用 `ApiEmbedder`，阈值可配 |
| `answer_position` | **无**（只写在提示词） | 代码侧配额：同一批内 A/B/C/D 各 ≤ ceil(n/4)+1，超了交换选项位置 |
| `distractor` | **无** | 干扰项两两不等价、不得全对/全错、禁"以上都对"；有解析时校验"每个错误选项解析里被点名" |
| `grounding` | **无** | 要求 `analysis` 附一句**原文引用**，用字符串包含核验（不做语义判断，只做"说过没说过"） |
| `answer_self_check` | **无** | 高风险题型（single/multiple/fill）二次独立求解，与 `answer` 不符 → 丢弃或降级复核；`thinking=False`、单独 `kind="quiz_verify"` 计费 |
| `difficulty_consistency` | **无** | 落地后再做（需要 `difficulty_est`） |

每个闸门返回 `(ok, reason)`，拒绝计数进批次统计 —— **调阈值看数据，不靠肉眼改常量**
（`quality.py` 头注释里那两次"被真机逼着放宽"就是这个缺口的证据）。

### 第 5 层：判分策略表 + 回读闭环

- `grader.py` 的分支改成 `STRATEGY = {"single": ..., "multiple": ..., ...}`，
  每道题可带 `grading_json`（fill 等价答案集、multiple 部分分策略、short_answer rubric）。
  否定词窗口、字母集合、部分分公式都变成策略的默认参数，而不是模块私有常量。
- `attempts` 加 `next_review_at`，补最低限度的"读"：`GET /quiz/stats` 出正确率/薄弱点，
  错题池查询（`wrong_attempts(limit)`）；调度与 SM-2 属 `TODO.md §10` P2，不在本次。
- `questionId` 显式带回：`ChatArea.vue:393` 透传 `p.questionId`，新增
  `POST /quiz/{id}/answer` 走独立通道，`get_pending_question` 降级为兼容路径。

---

## 四、数据模型改动（向后兼容，走既有补列范式）

`questions` 补列（`quiz_store._ensure_columns`，AGENTS.md §1 的既定范式）：

| 列 | 用途 |
|---|---|
| `anchor_kind` / `anchor_id` | 这题锚在哪个节点/小节/文件（**生成时写死，不再在判分侧猜**） |
| `cover_key` | 蓝图槽位（考的是哪个考点） |
| `difficulty_target` / `difficulty_est` | 目标难度 / 实测难度（后者可空） |
| `bloom_level` | 认知层次（模型给，先用软标签，不做校验） |
| `grading_json` | 题型专属判分配置 |
| `gate_flags` | 闸门结论 `{gate: ok/reason}`，便于回看"这题为什么能入库" |
| `spec_id` | 指回批次规格 |

新增 `quiz_batches` 表（**推荐**，一行一个出题批次：`spec_json` / `status` / `stats_json` /
`created_at`）。理由：`GROUP BY` 现算题集拿不到规格与生成状态，"补题/失败/部分成功"也无处记。
代价是一张新表 + `main.py::_prune_once` 的 `_JOBS` 加一行清理 —— 若评审倾向不加表，
退化方案是 `spec_json` 冗余写进每题（体积 ×N，不推荐）。

⚠️ 不要复用 `questions.node_id`（`INTEGER`，是知识库文件 id）：图谱节点 id 是 TEXT，
`docs/教学模块/试卷归档_调研与实施方案.md` §2.2 已警告过一次。

---

## 五、分期与验收

| 阶段 | 内容 | 工作量 | 验收 |
|---|---|---|---|
| **P0** 度量与收口 | `inspect_quiz_quality.py`；把 2.1 表的常量搬进 `settings`/`spec` 默认值（**行为不变**） | 0.5~1 天 | 脚本输出与本文基线一致；既有 `test_quiz_*` 全绿 |
| **P1** 规格 + 蓝图 | `spec.py` / `presets.py` / `blueprint.py`；三入口归一为 `Spec → 生成`；`quiz_batches` + `spec_json`；提示词迁 `data/prompts/quiz/` | 2~3 天 | 新增 `test_quiz_spec` / `test_quiz_blueprint`；`chat_quick` 与今日行为逐字段等价（golden 比对 prompt）；三入口产出差异只剩 spec |
| **P2** 闸门 | `gates/` 六个检查器 + 配置阈值 + 拒绝计数；`answer_position` 配额；`semantic dedup` | 2 天 | 每闸门一例（含"id 36 这种全对多选必须被拒"）；库内重跑旧 spec，退化项归零 |
| **P3** 判分与回读 | 策略表 + `grading_json`；`next_review_at`；正确率统计；`questionId` 显式带回 | 1.5 天 | `test_quiz_grader` 全绿 + 新策略用例；断线/多对话错判场景回归 |
| **P4**（可选） | 整卷模考蓝图、母题参数化、试卷/数据集导入接线、难度校准回路 | 视需要 | 各自单独立项 |

基线与回归口径：后端 `2187 passed + 1 skipped / 7 deselected`、前端 vitest `80 passed`。
新增测试一律与实现同提交（`AGENTS.md §7`）。

---

## 六、权衡与"不做什么"

**做**
- 规格声明化 + 落库（这是"硬编码"的正解，不是把常量换个地方藏起来）
- 蓝图（考点分派）—— 投入产出最高：直接治漂移与重复
- 代码侧核验替代提示词祈使句（答案位置、全对多选、来源溯源）

**不做（有意）**
- **不引结构化输出强约束**（`response_format`）：`llm/fallback.py` 目前不传该参数，
  各 provider 支持度不一，加了会在降级档静默 400。走"解析 + 闸门"，与现状一致。
- **不建第二套进度表**：难度估计/学情报告归 `TODO.md §7` 的 `core/assessment/`（他人建设），
  本次只**留字段与读口**（`difficulty_est`、`attempts` 查询），不越界。
- **不做 IRT / 自适应选题**：需要稳定题库 + 每題 ≥30 次作答，当前库里最多 1 次。
- **不重写判分算法**：`grader.py` 的分支逻辑已被 6 例真机 bug 打磨过，本次只做"策略化"搬运，
  不改判分结果（golden 用例守护）。
- **不动 40s/120s 的对话内承诺**：`chat_quick` 预设必须保持 1 道、node_only、无检索。

**风险**
1. **前缀缓存**：提示词迁文件后，若把 spec 注入 system，每套题的前缀都不同 → 缓存全失效。
   → 规格只进 user 消息，system 按模板版本稳定。
2. **成本**：按槽位生成 + 答案自校验 = 调用数上升。→ 自校验只对客观题、`thinking=False`、
   可配置开关；先量收益（退化项减少）再决定默认开。
3. **测试面**：`test_quiz_generator.py` 的 24 例大量断言当前分批行为（`_split_counts` /
   `_types_for_batch`）。P1 会用蓝图槽位替换它们 —— 这批用例需**同期重写**，不是删掉。
4. **前端**：题型目录改为后端下发（`GET /quiz/types`）会让 `QuizView`/`QuizCard` 依赖一个
   新请求；若暂不做，至少让前端从一处常量表读，别继续在 3 个文件里各写一遍。

---

## 七、落地结果（2026-11-16）

本文的 P0~P2 里**与"硬编码 + 覆盖率"直接相关的那部分已经实现**；P3（判分策略化）与 P4 未动。
落地的是**并行开发**的一条更窄的路线：只改独立出题页，用"题量预算 + 考点蓝图"两层替代原来的
`question_count` 常量与 `question_types` 自由出题。

### 7.1 第二章的硬编码清单，逐条现状

| 原硬编码 | 现状 |
|---|---|
| `QuizView.vue:51` `ref(5)` 默认题量 | 改为 `preview` 算出的 `count`（`autoCount` computed），失败才回落 5 |
| `QuizView.vue:464` 下拉 `[3,5,8,10]` | 三模式：自动（内容决定）/ 手动（1~15）/ 按题型配比 |
| `schema.py:60` `Field(5, ge=1, le=20)` | 保留兼容，但新路径由 `question_plan` 合计决定；上限口径统一到 `QUIZ_MAX_QUESTIONS=15` |
| `generator.py:41` `QUIZ_MAX_TOPUP_ROUNDS=2` | 蓝图路径**不再有轮数上限**（按 `unfilled` 收敛 + `progressed` 守卫）；老路径常量保留未动 |
| 难度全 `medium` | 仍按请求传入（未做自适应），但蓝图槽位里带 `bloom` 认知层次 |
| 分值全 10 分 | `DEFAULT_POINTS`：简答 20、其余 10；槽位逐题写死，模型忽略也会被 `q.points = slot.points` 纠正 |
| 判断题选项两种形状并存 | 蓝图槽位对非选项题型写 `option_count=0`，落库统一 |
| 单选答案位置 A6/B14/C2/D0 | **未修**（属 P2 闸门，本轮没做） |
| 多选全对（"以上都对"） | **未修**（同上） |
| `knowledge_point` 一半是 LLM 现编的名字 | **已修**：有 `graph_node_id` 时逐题覆盖为真节点 id；无锚点时保留但打 WARNING |
| 覆盖率 7%（机械运动 17377 字只出 4 题） | **已修**：题量由内容算（23522 字 / 8 节 → 15 道，covered 8/8） |

### 7.2 与本文章节对应关系

| 本文建议 | 落地情况 |
|---|---|
| P0 度量脚本 | ✅ `backend/scripts/inspect_quiz_quality.py`（只读，28 例测试） |
| P1 规格 + 蓝图 | ✅ 但**没有** `spec.py`/`presets.py`/`quiz_batches` 表 —— 用 `PlanRow` + `Blueprint` 两个 dataclass 就够了，落库仍是每题一行 |
| P1 提示词迁 `data/prompts/quiz/` | ❌ 未做（提示词仍内联在 `generator.py`，按槽位动态拼表，迁文件的收益不大） |
| P2 六个闸门 | ❌ 未做（答案位置配额、全对多选、语义去重都还欠着） |
| P3 判分策略化 | ❌ 未做 |
| 三入口归一 | ❌ 未做（按用户要求只改独立出题页，`chat_quiz` 一个字节没动） |

### 7.3 仍然欠着的（下一轮候选）

1. **P2 的闸门**：单选答案位置分布（卡方检验）、多选全对、题干套式检测、跨题集近重复 —— `inspect_quiz_quality.py` 已经把度量做出来了，缺的是"不达标就拒绝"的闸门。
2. **`quiz_batches` 表**：`count_mode` 与 `satisfied` 目前只在当次响应里，事后查不了"这次为什么少给 3 道"。
3. **真实端到端**：未跑过真调 LLM 的 `/quiz/generate`，蓝图路径的提示词在真实模型上的表现未验证。
4. **preview 在 plan 模式下的 reason 文案**（小瑕疵，见另一份文档 §9.5）。

