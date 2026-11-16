# 出题题量与蓝图：业界借鉴与改造方案（只动独立出题页）

> 创建：2026-11-16
> 触发：用户指出「出题太硬编码，出几道题？」并要求先借鉴网上成熟方案。
> 范围：**只改独立出题页**（`QuizView.vue` → `POST /quiz/generate` → `generate_quiz`）。
> 对话内出题（`chat_quiz`）不动 —— 它的 1 道 / node_only / 120s 是学习模式的承诺，另案。
> 前置调研（硬编码清单 + 实测漂移）：`docs/教学模块/QUIZ_出题重构_调研与改进思路.md`

---

## 一、先回答「出几道题」

你问的这个数字，现在是**四处各自写死的值碰巧对上了**，没有一处由内容决定：

| 位置 | 值 | 性质 |
|---|---|---|
| `frontend/src/views/QuizView.vue:51` | `questionCount = ref(5)` | 前端默认 |
| `QuizView.vue:464` | `[3, 5, 8, 10]` | 下拉四个选项，无依据 |
| `backend/app/core/quiz/schema.py:60` | `Field(5, ge=1, le=20)` | 后端默认 + 上限 |
| `generator.py:39` | `QUIZ_BATCH_SIZE = 4` | 每批几道 |
| `generator.py:41` | `QUIZ_MAX_TOPUP_ROUNDS = 2` | 补几轮（**与题量无关**） |

`QUIZ_MAX_TOPUP_ROUNDS = 2` 这条最露骨：首轮最多拿 `4 × ceil(n/4)` 道，补题最多再补 `2 × 4 = 8` 道。**要 20 道时补题额度只有 8 道**，被过滤掉 9 道就凑不满 —— 而这个"凑不满"是**常量决定的，不是内容决定的**。

### 实测：题量与内容量完全脱钩

查 `data/quiz/5/quiz.db` + `data/knowledge/knowledge.db`（探针 `.dsh_local/probe_content_size.py` / `probe_coverage.py`）：

| 节点 | 正文 | 已出题数 | 按内容可出（1 题/300 字） | 覆盖率 |
|---|---|---|---|---|
| 机械运动 | 17377 字 | 4 | ≈57 | **7%** |
| 速度与平均速度、瞬时速度 | 13198 字 | 2 | ≈43 | **5%** |
| 参考系与坐标系 | 11040 字 | 4 | ≈36 | **11%** |
| 匀变速直线运动 | 10442 字 | 1 | ≈34 | **3%** |
| 子空间的定义与判定 | 1186 字 | 1 | ≈3 | 33% |

- uid5 有 **123 个知识点**，只有 **20 个**出过题（16%）
- 12 个题集的题量分布：`1 题×3、2 题×3、3 题×1、4 题×3、5 题×2` —— 下拉里的 **8 和 10 一次没用过**
- 平均正文 4059 字/节点，按 Questgen 的 1 题/60 词口径能出 ≈45 题，实际出 1~5 题

**结论**：题量的真问题是**没有"内容预算"这个概念**，下拉框的四个数字和正文规模毫无关系。用户选 5 只是因为它是默认值。

---

## 二、业界怎么处理（4 个可直接借鉴的实现）

### 2.1 tldw_server：`question_plan` —— 题量按题型显式分派，凑不齐就整体失败

最贴近我们场景的一个。[设计文档](https://raw.githubusercontent.com/rmusser01/tldw_server/25dbf5e7b26b973ae78ac302350589e720eb7980/Docs/superpowers/specs/2026-07-07-advanced-quiz-generation-controls-design.md)：

```json
{
  "num_questions": 12,
  "question_plan": [
    { "question_type": "multiple_choice", "count": 5, "option_count": 5 },
    { "question_type": "multi_select",   "count": 2, "option_count": 5 },
    { "question_type": "matching",       "count": 2, "pair_count": 4 },
    { "question_type": "true_false",     "count": 2 },
    { "question_type": "fill_blank",     "count": 1 }
  ]
}
```

三条我们可以直接抄：

1. **`question_types`（集合）换成 `question_plan`（带数量的清单）** —— 我们现在的 `question_types: ["single","judge"]` 是"允许哪些类型"，模型爱出几种出几种；改成"每种各几道"才有可验收的契约。
2. **`sum(count) 必须 == num_questions`，重复类型拒绝** —— 用 Pydantic `extra="forbid"` + 校验函数钉死。
3. **凑不齐不是"少给几道"，而是整体失败** —— 原文：*"If the model returns too few valid questions, fail the entire generation before any quiz or question is saved"*，错误信息可测：`Generated quiz did not satisfy requested question plan: multiple_choice expected 5, got 4`。

第 3 条正是我们现在的病灶：`generator.py:416` 数量不足只 `logger.warning`，前端弹一句"实际生成 M 道（差 N 道）"就结束了 —— 用户拿到的是半套卷。

另外它的 `option_count`（2~6，默认 4）也值得抄：我们的"单选题必须 4 个选项"是 `_validate_question` 硬编码。

### 2.2 Questgen：题量 `Auto` = 按内容长度算，不是让用户猜

[API 文档](https://www.questgen.ai/questgen-api-documentation)：

> `count` (int or string, optional): Number of questions to generate. **The default is "Auto" which generates approximately 1 question for every 60 words with a maximum of 20 questions.**

这是"出几道题"最朴素的正确答案：**由输入决定，用户可覆盖**。同页还有：

- `mcqOptionsCount`：默认 4，可选 **3/4/5/6**
- `difficultylevel`：Easy/Medium/Hard
- 文本上限 6000 words

⚠️ 注意它的 1 题/60 词是**英文词**口径。中文一个"词"约 1.5 字，所以折算约 **1 题/90 汉字**。这个密度偏高（每道选择题还要 3~4 个选项 + 解析），我们取保守值。

### 2.3 教育测量学：blueprint（双向细目表）—— 题量由「覆盖 × 层次 × 分值」推出

这是考试设计的标准做法，AI 出题的质量文献几乎都指向它。[Pathfinder SAGE 的教师指南](https://sage.ai-pathfinder.eu/teaching/exam_questions)：

> Build an assessment blueprint (coverage, weighting, constraints). **Before generating questions**, decide what proportion of points/time goes to each topic and skill. This reduces "AI drift".
>
> Ask AI to create a blueprint table (**outcome × item type × cognitive level × number of items × points**).

同一份材料给了两份可直接用的提示词模板（"Blueprint first" / "Generate MCQs with rationales and misconception-based distractors"），其中三条规则我们一条都没做：

- **"For each distractor, name the likely misconception it represents"** —— 干扰项必须对应一个真实误解
- **"Avoid 'all of the above' and 'none of the above'"** —— 我们提示词写了，代码不查（实测 id 36 四个选项全对）
- **"Solve each retained question independently before accepting the generated key"** —— 出完自己再解一遍

Bloom 层次配比有现成的行政口径，例如卢旺达 NESA 的试卷规范：[30% / 20% / 20% / 10% / 10% / 10%](https://www.nesa.gov.rw/index.php?eID=dumpFile&f=119495&t=f&token=3d79cf804dca683669a263bbab671f27915b2103)（记忆/理解/应用/分析/评价/创造）。这份比例可以直接做默认值。

还有一个我们该抄的细节，来自 [Western University 的 Test Blueprint 样例](https://teaching.uwo.ca/elearning/imgs/Test%20Blue%20Print%20Example.pdf)：细目表是 **学习成果 × 认知层次** 的矩阵，每格填题数，行尾给百分比 —— 这正好是"考点 × 层次"的二维分配，和我们要的 blueprint 同构。

### 2.4 Ragas：`testset_size` + 权重表 —— 总量与构成分开表达

[Ragas 的 `default_query_distribution`](https://docs.ragas.io/en/stable/references/synthesizers/index.md) 把"生成多少"和"各类占多少"拆成两个独立参数：

```python
# 总量
generate_with_langchain_docs(documents, testset_size=50, query_distribution=[...])
# 构成（合成器 × 权重，权重和必须 = 1）
default_queries = [
    SingleHopSpecificQuerySynthesizer(llm=llm),   # 单跳具体
    MultiHopAbstractQuerySynthesizer(llm=llm),    # 多跳抽象
    MultiHopSpecificQuerySynthesizer(llm=llm),    # 多跳具体
]
return [(query, 1 / len(available_queries)) for query in available_queries]
```

值得抄的两点：

- **权重表是数据，不是分支代码** —— 换构成 = 换一张表
- **按可用性过滤合成器**：`if query.get_node_clusters(kg)` 为空就跳过该合成器，一个都不剩才报错 —— 对应我们的"这个节点没有例题类内容，就不要硬造例题题"

### 2.5 顺带一条：选择题应该是 3 个选项还是 4 个？

[Rodriguez (2005) 的 80 年元分析](https://onlinelibrary.wiley.com/doi/abs/10.1111/j.1745-3992.2005.00006.x)结论是 **3 个选项最优**：从 4 降到 3 只让分数上升 **1.0~1.2%**（猜测效应被高估了），但省下 25% 的阅读量。Pathfinder 的材料也照此写了规则：*"Prefer 3 options per item unless there is a strong reason for 4"*。

我们 `_validate_question` 硬卡 4 个 —— 这个数字建议跟着 `option_count` 一起可配，默认仍 4（中文题干扰项往往需要 4 个才够区分），但不写死在代码里。

---

## 三、方案：三层改造，只动独立出题页

### 第 1 层：题量从「下拉框」变成「内容预算」

新增 `backend/app/core/quiz/budget.py`（纯函数，零 IO、零 LLM，可单测）：

```python
QUIZ_CHARS_PER_QUESTION = 350   # 中文保守值（Questgen 英文 60 词 ≈ 90 汉字，我们取 4 倍富余）
QUIZ_MIN_QUESTIONS = 3
QUIZ_MAX_QUESTIONS = 15         # 与并发批次、300s 前端超时挂钩
QUIZ_SECTIONS_PER_QUESTION = 1  # 每节至少一道

def auto_question_count(*, section_count: int, content_chars: int) -> int:
    """按内容预算算题量：每节至少一道，但不超出正文能撑的量。"""
    content_cap = max(QUIZ_MIN_QUESTIONS, content_chars // QUIZ_CHARS_PER_QUESTION)
    desired = max(QUIZ_MIN_QUESTIONS, section_count * QUIZ_SECTIONS_PER_QUESTION)
    return min(max(desired, content_cap), QUIZ_MAX_QUESTIONS)
```

用实测数据代入：

| 节点 | 小节数 | 正文 | 内容上限 | **算出题量** |
|---|---|---|---|---|
| 机械运动 | 8 | 17377 字 | 49 | **15**（撞上限） |
| 参考系与坐标系 | 5 | 11040 字 | 31 | **15** |
| 位置、位移与路程 | 5 | 9023 字 | 25 | **15** |
| 子空间的定义与判定 | 1（未小节化） | 1186 字 | 3 | **3** |
| 某个 300 字的薄节点 | 1（未小节化） | 300 字 | 3 | **3** |

（小节数实测：uid5 有 24 个节点已小节化，平均 **5.5 节**、最多 8 节 —— 探针 `.dsh_local/probe_sections.py`）

前端把下拉换成**三种模式**：

```
[ 自动 (推荐) ] [ 手动 ] [ 按题型配比 ]

自动 →  显示只读结果 + 依据：
        「15 道 · 依据：8 个小节、正文 17377 字 · 预计覆盖 8 个考点」
手动 →  el-input-number 3~15，旁边小字给自动值作参考
配比 →  每种题型一个 count 输入框，底部实时合计（见第 2 层）
```

关键点：**"自动"要能说出依据**。用户不信任一个不解释的数字，就像现在不信任那个 `[3,5,8,10]`。

> 有 `graph_node_id` 时能精确算（小节数来自 `kg.list_sections`，正文来自 `node_content_text`）；
> 只有 `subject` 字符串时拿不到小节，退化为 `section_count=1` + 检索命中片段数估算。
> 这个分支要显式写在 docstring 里，别让调用方猜。

### 第 2 层：`question_plan` 取代 `question_types`（抄 tldw 的契约）

`schema.py` 加模型（`extra="forbid"`）：

```python
class PlanRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["single", "multiple", "judge", "fill", "short_answer"]
    count: int = Field(..., ge=1, le=20)
    option_count: int | None = Field(None, ge=2, le=6)   # 仅 single / multiple
    points: int | None = Field(None, ge=0, le=100)       # 分值，默认按题型
```

`QuizGenerateRequest` 加 `question_plan: list[PlanRow] | None`，校验：

- `question_plan` 与 `question_types` **互斥**（同 tldw）
- `sum(count) == question_count`
- 同 `type` 不重复
- `option_count` 只允许出现在 `single`/`multiple` 行上
- **旧的 `question_types` 路径保留**（外部客户端兼容），映射成等分的 plan

于是"题型勾选框"变成"每种几道"的固定五行表单（tldw 的 WebUI 也这么做，理由是**不用做动态增删行的复杂 UI**），底部一个只读合计。

### 第 3 层：blueprint —— 出题前先定「考什么」

新增 `backend/app/core/quiz/blueprint.py`（纯函数）：

```
输入：node 小节清单 + 各自 brief + 已考题干（去重用）+ plan + bloom 配比
输出：list[Slot]，每个 slot = {cover_key, cover_label, type, points, bloom, option_count}
```

分配算法（不用 LLM，纯规则）：

1. 每个小节 = 一个候选考点（`cover_key = f"{node_id}/{section_id}"`）
2. 按 `plan` 里各题型的 count 排序，最多的题型先分
3. **轮转分配**：`考点[i % len(候选考点)]` —— 保证每个考点被覆盖一次后才轮到第二次（这是治"同一考点连出 3 道"的最简办法）
4. Bloom 层次按配比表轮转填充（默认 30/20/20/10/10/10，按 slot 总数取整；四舍五入后余数补给"理解"层）
5. 小节数 < 题数时，多出来的 slot 用"同小节的第二个侧面"（正文小标题 `##`）扩容；实在没有 → 缩减题量并如实告知

每个 slot 一条独立生成请求（可以并发、可以单独重试、可以单独补）。这直接删掉两个补丁式函数：

- `_split_counts`（`generator.py:199`）—— 改由 slots 分组
- `_types_for_batch`（`generator.py:205`）—— 每批题型由 slots 决定，不再需要"切分算法"
- 补题循环改成"**哪个 slot 空着补哪个**"，`QUIZ_MAX_TOPUP_ROUNDS = 2` 这个常量就自然消失了

### 第 4 层：凑不齐就整体失败（抄 tldw）

```python
missing = [s for s in slots if s.cover_key not in produced]
if missing:
    raise QuizPlanUnsatisfied(
        f"出题未满足规格：{fmt(missing)}"   # 可子串断言的稳定文案
    )
```

配套：`POST /quiz/generate` 在这条路径上 **不落库任何题**（tldw 明确写了 "fail the entire generation before any quiz or question is saved"）—— 现在是先拿到多少存多少，用户拿到半套卷还不知道差在哪。

⚠️ **这条要打折实施**：严格失败会让"偶发一条 slot 被闸门拒"直接变成整次失败，体验更差。建议：
- **默认**：允许 `SHORTFALL_TOLERANCE = 0.2`（15 道里缺 ≤3 道仍交付，但响应里 `satisfied=false` + 明确列出缺哪些考点）
- **严格模式**：请求里 `strict: true` 时按 tldw 全失败

这是我和 tldw 有意不同的地方，理由写在上面 —— 我们的调用是交互式的（用户等 60s），它的场景更接近批量生成。

---

## 四、前端改动（`QuizView.vue`）

只动配置区（`:449-482`）和 `handleGenerate`（`:108-167`）：

| 现状 | 改成 |
|---|---|
| `[3,5,8,10]` 下拉 | 三模式切换 + 自动模式的依据说明 |
| 5 个题型 checkbox | 固定五行「题型 × 数量 × 选项数」表单（合计只读） |
| 提交 `{question_types: [...]}` | 提交 `{question_count, question_plan, count_mode}` |
| 提示"实际生成 M 道（差 N 道）" | 提示"缺 X 个考点：A、B"，并给出「重试缺失部分」按钮 |

新增一个**零成本预览端点**（纯计算，不调 LLM）：

```
POST /quiz/plan/preview   { subject?, graph_node_id?, count_mode, question_count? }
→ { question_count, reason, sections: [{title, planned}], slots_preview: [...] }
```

前端在"自动"模式下打开页面就打一次，把"15 道 · 8 个小节 · 17377 字"显示出来。**这是让题量可信的关键** —— 现在用户不知道 5 是怎么来的。

⚠️ 该端点要做**输入校验**：`graph_node_id` 必须属当前用户（`kg.get_node` 已带 user_id 护栏），否则等于让人探测别人的图谱规模。

---

## 五、数据模型

`questions` 补列（沿用 `quiz_store._ensure_columns` 范式，AGENTS.md §1）：

| 列 | 用途 |
|---|---|
| `cover_key` | 这个 slot 考的考点（`{node_id}/{section_id}` 或 `{node_id}/#h2-3`） |
| `bloom_level` | 认知层次（软标签，不校验） |
| `option_count` | 实际选项数（判分与前端渲染都该读它，不再靠"是不是 4"） |
| `plan_json` | 该题的 slot 原文（规格随题走，出过的卷可复现） |

`quiz_batches` 表（新增，一行一批）：`spec_json` / `count_mode` / `planned` / `produced` / `satisfied` / `shortfall_json`。

> 上一份调研里我把 `quiz_batches` 列为"可选、待拍板"。这次建议**就加**：
> 因为 `count_mode` 与 `satisfied` 没有别的地方能存，而"这次为什么少给了 3 道"正是要回答的问题。
> 退化方案（spec 冗余进每题）在 15 题 × 每行几百字节的量级下能跑，但查不了"整卷是否满足规格"。

---

## 六、分期

| 阶段 | 内容 | 验收 |
|---|---|---|
| **A**（0.5 天） | `budget.py` + `POST /quiz/plan/preview` + 前端自动模式；后端仍走旧生成路径 | `test_quiz_budget` 覆盖上表 4 个算例；前端显示"15 道 · 8 小节 · 17377 字"；**不改任何生成行为** |
| **B**（1 天） | `PlanRow` + `question_plan` + 互斥/合计校验；旧 `question_types` 映射成 plan | `test_quiz_plan_schema` 8 例（含 tldw 那 7 条 + 我们自己的 option_count 兼容）；旧请求逐字节等价 |
| **C**（1.5 天） | `blueprint.py` 槽位分配 + 按槽生成（删 `_split_counts`/`_types_for_batch`/补题常量） | `test_quiz_blueprint`：同一批题不撞考点、小节全覆盖、slots 数 = plan 合计 |
| **D**（0.5 天） | 缺口容忍 + `satisfied` 字段 + 前端缺口提示 | 缺 3 道以内仍交付且 UI 列明缺哪些考点；`strict:true` 全失败且库里 0 行 |
| **E**（可选） | `option_count` 生效（含 3 选项）、`bloom` 配比、干扰项检查 | 各自单独立项 |

回归口径：后端 `2214 passed + 1 skipped / 7 deselected`、前端 vitest `83 passed`。

**要同期重写的测试**：`test_quiz_generator.py` 里 `test_split_counts`（:457）、`test_types_for_batch_covers_all_types_without_overlap`（:467）、`test_types_for_batch_single_type_repeats`（:476）、`test_generate_quiz_splits_into_batches`（:482）—— 它们断言的正是 C 阶段要删掉的两个函数。**不是删掉了事**，是换成"槽位分配"的等价断言。

---

## 七、不做什么

- **不改对话内出题**（`chat_quiz`）：1 道 / `node_only` / 120s 是学习模式三条硬约束，题量模型对它不适用（它的题量永远是 1）。
- **不做自适应选题（IRT）**：需要每题 ≥30 次作答，库里最多 1 次。
- **不做 AI 生成 blueprint**：Pathfinder 建议让 AI 提议细目表，但我们的考点来自小节标题 —— 规则分配比 LLM 更稳、零成本、可单测。LLM 只在"小节正文里有哪些可考侧面"这一层介入（且已有 `SECTION_PLAN_SYSTEM_PROMPT` 在做同类事）。
- **不引 `response_format`**：`llm/fallback.py::chat_create` 不传该参数，各 provider 支持度不一，加了会在降级档静默 400。继续走"解析 + 闸门"。
- **不把题量上限抬到 20 以上**：`QUIZ_MAX_QUESTIONS=15` 是并发批次（15/4≈4 批）与 300s 前端超时的交集。要更大得先改成后台任务 + 通知（对标 `POST /kb/graph/generate` 的改造，AGENTS.md 有专节），那是另一件事。

---

## 八、一句话总结

现在「出几道题」是**四个数字碰巧一致**；业界（Questgen / tldw / Ragas / 考试学 blueprint）的一致做法是：
**总量由内容预算算出，构成由带数量的规范表声明，凑不齐要显式失败**。

我们的改造就照这三句走，且只动独立出题页。

---

## 九、实施状态（2026-11-16 落地）

**A~D 四个阶段已全部实现并通过回归**，E 阶段的 `option_count` 与 `bloom` 也随蓝图一起生效了。

### 9.1 新增模块（都是纯函数，零 IO，可单测）

| 文件 | 职责 | 关键常量 / 契约 |
|---|---|---|
| `backend/app/core/quiz/budget.py` | 题量预算 | `QUIZ_CHARS_PER_QUESTION=350`（Questgen 60 词≈90 汉字，取 4 倍富余）、`QUIZ_MIN_QUESTIONS=3`、`QUIZ_MAX_QUESTIONS=15` |
| `backend/app/core/quiz/blueprint.py` | 考点候选 + 槽位分配 | `build_candidates` / `allocate` / `_bloom_sequence`；`DEFAULT_OPTION_COUNT=4` |
| `backend/app/core/quiz/planning.py` | 读图谱 → 三者串联 | `load_candidates` / `plan_for_node` / `blueprint_to_dict` / `budget_to_dict` |
| `backend/app/core/quiz/schema.py` | 请求模型（改造） | `PlanRow` / `PlanPreviewRequest` / `QuizGenerateRequest` 新增 `question_plan`、`count_mode`、`strict`、`graph_node_id`、`missing_cover_keys` |

**题量算法（这是"出几道题"的答案）**：

```
content_cap = max(3, 正文总字数 // 350)     # 内容能承载多少
section_floor = 小节数                       # 每节至少一道
count = clamp(max(content_cap, section_floor), 3, 15)
```

取 `max` 而不是 `min` —— **覆盖率优先**。实测 8 个小节 / 400 字的小节点 → 8 道（而不是 `400//350=1` 道夹到 3 道）。

### 9.2 端点

- **`POST /quiz/plan/preview`**（新）：纯计算，**不调 LLM、不落库、不发通知**。返回 `{count, reason, count_mode, capped, section_count, content_chars, sections[], slot_preview}`。
  无锚点（没传 / 不存在 / 非本用户）三种情况**响应逐字段相同** —— 否则这个端点就成了"别人的图谱里有没有这个节点"的探测器。
- **`POST /quiz/generate`**（扩展）：`question_plan` 非空走蓝图路径，否则走老路径。**老路径的请求参数与响应字段集合逐字节等价**（`test_old_path_response_shape_unchanged` 盯着，多一个键就红）。

### 9.3 关键设计决定

1. **互斥判定用 `"question_types" in self.model_fields_set`，不能用 `is not None`** —— `default_factory` 让该字段永远非 None，用后者会导致每次请求都报"互斥"。
2. **`effective_plan` 在无 plan 时返回 `[]`，不在老路径上等分** —— 老路径的生成逻辑一个字节都不动。
3. **strict 闸门在 `save_questions` 之前**：先落库再删会留下"秒出秒删"的空题集，而且删不干净（attempts 可能已挂上）。
4. **有 `graph_node_id` 时逐题覆盖 `knowledge_point` 为真节点 id** —— 这修的是调研里最严重的一条：LLM 现编知识点名 → `chat_grade.resolve_node_id` 命中不了 → 答对也不涨掌握度，且静默无日志。
5. **补题轮数不再有常量上限**：按 `unfilled` 收敛，`progressed` 守卫防死循环。老路径的 `QUIZ_MAX_TOPUP_ROUNDS=2` 保留未动（`test_generator_blueprint_no_topup_round_cap` 明确断言它 == 2）。
6. **`missing_cover_keys` 在老路径上显式报错**而不是静默忽略 —— 静默忽略正是这个字段最初踩的坑（前端传了、Pydantic 吞了、用户点「补齐缺失」却得到一整批新题）。

### 9.4 验收数字（实测）

| 项 | 结果 |
|---|---|
| 后端全量 `pytest backend/tests -q -m "not llm_api"` | **2336 passed, 1 skipped, 7 deselected**（118.01s） |
| 前端 `npm test` | **8 files / 127 passed**（9.62s） |
| 真机 preview（uid=5, `mechanical_motion`） | count=15 / 8 节 / 23522 字 / `capped=true` |
| 15 道规格摊 8 个考点 | shortfall=0、covered 8/8、`{single:7, judge:4, multiple:2, fill:2}` |
| 防探测 | 不传节点与传不存在的节点，响应 `identical: True` |

### 9.5 分期表的实际结果

| 阶段 | 状态 |
|---|---|
| A（budget + preview + 前端自动模式） | ✅ 完成 |
| B（PlanRow + question_plan + 互斥校验） | ✅ 完成 |
| C（blueprint 槽位分配 + 按槽生成） | ✅ 完成 |
| D（缺口容忍 + satisfied + 前端缺口提示） | ✅ 完成，另加 `missing_cover_keys` 的「补齐缺失」闭环 |
| E（option_count / bloom 配比） | ✅ 随 C 一起做了（槽位里逐题写死选项数与认知层次） |

**没做的**：真实 `/quiz/generate` 的端到端（要真调 LLM 落库，成本与数据污染考虑，留给人工验证）；`quiz_batches` 表（`count_mode` 与 `satisfied` 目前不落库，前端只在当次会话里用）。

**已知的小瑕疵**：`count_mode='plan'` 时 preview 的 `reason` 仍是题量预算文案（"15 道（已到上限）· 依据：8 个小节…"），读起来与"按你的配比出 5 道"略有落差。纯文案问题，未改。

