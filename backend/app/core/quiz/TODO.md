# `core/quiz` —— 出题模块 TODO

> 建立：2026-11-15。本文件只写**这个模块自己的**待办与取舍；全项目待办总表仍是根 `TODO.md`。
> 数据口径来自 2026-11-15 对 `data/quiz/5/quiz.db`（47 题）与 `data/knowledge/knowledge.db`（122 节点 / user 5）的实测，不是推测。

---

## 1. 现状（实测，别凭感觉改）

| 指标 | 实测值 | 含义 |
|---|---|---|
| 题库总题数 | 47（12 个题集） | 只有 5 号用户有量 |
| 被作答过的题 | 5 / 47 | **89% 的题从生下来没被碰过** |
| `source_docs` 非空 | 0 / 47 | 独立出题路径完全没写血统 |
| `difficulty` 取值 | 全部 `medium` | 难度是整批参数，不是行字段 |
| `knowledge_point` 唯一值 | 35 个 | 只有 2 个能 `get_node()` 命中 |
| 掌握度回写成功 | 3 / 47 → **42 / 47（89%）** | 修 `resolve_node_id` 后的验收值 |
| `mastery_events` | 6 条（4 manual / 1 quiz_correct / 1 restore） | 掌握度几乎没动过 |

**核心事实**：从活动栏直接进「出题」页时，`knowledge_point` 是 **LLM 自己编的中文知识点名**（`generator.py` 的输出 schema 里就写着"关联知识点"），
而判分回写只认**图谱节点 id** → `kg.get_node("二叉树前序遍历规则")` 必空 → `apply_mastery_after_answer` 静默 return `""`。
同一概念被 LLM 写成 20 多种样子（`'二叉树前序遍历规则'` / `'二叉树前序、中序和后序规则'` / …），不是模型不稳定，是**这个字段没有锚点**。

---

## 2. 待修（按优先级，改完一条划掉一条）

### P0 —— 已完成（2026-11-15）

- [x] **`knowledge_point` 解析不到时判分静默丢分**
  三档兜底：本身就是节点 id → `find_node_by_name(kp)` → `find_node_by_name(subject)`。
  实测 `subject` 当锚点能救回 **42/47 = 89%**（`'二叉树遍历'` → `binary_tree_traversal`）。
  都解析不到（如 `subject='线性代数'`，对应 56 个节点、无法归到某一个）→ **如实不写**，不假装成功。
  落点：`chat_grade.resolve_node_id()`，被 `apply_mastery_after_answer` / `apply_section_after_answer` 共用。
  **实测验收**：真实库 47 题，可回写率 **3/47 (6%) → 42/47 (89%)**（`.dsh_local/probe_run/probe_fix_gain.py`）。
- [x] **`POST /quiz/{id}/grade` 无幂等 → 重复判分重复加分**
  实测 q2 同一题被判对两次 = 白送 40 分。幂等键 = `(question_id, user_answer)`：
  同题同答案再来一次 → 返回缓存结果 + `idempotent=true`、**不再 record_attempt、不再加掌握度**；
  换答案（订正）是**新**作答，照常判分留痕。落点：`QuizStore.find_attempt` + `quiz.py` 端点。
- [x] **`_grade_multiple` 把所有字母当选项**：`'选A因为它是LIFO'` → 抽出 `A,l,i,f,o` → 判"误选 4 个"→ 0 分。
  改为：整段只由选项字母组成才按字符拆；夹带文字时只认**独立出现**的大写选项字母。
- [x] **`_grade_judge` 只认字面「对/错」**：`'正确'` / `'T'` / `'√'` 全判错（学生按常识作答反而扣分）。
  加 `_JUDGE_SYNONYMS` 同义词表（对/正确/是/T/true/√/Y ↔ 错/错误/否/F/false/×/N）。
- [x] **`_fuzzy_match` 子串当语义等价**：`'不是栈'` 判对（否定句）、`'队列不是栈'` 判对。
  加否定词守卫：答案出现在作答中时，跳过前 6 字内同分句有否定词的匹配点；
  只要有一处未被否定就算对（`'栈是一种LIFO结构'` / `'栈溢出'` 仍判对）。
- [x] **独立出题路径不写 `source_docs`**：题无血统。改为复用 `chat_quiz._source_docs_for()` 取节点 `sources`。

### P1 —— 下一步（未动）

- [ ] **`get_pending_question` 是跨节点/跨对话的全局"最近一道"**
  现在靠 `LEFT JOIN attempts WHERE a.id IS NULL ORDER BY q.id DESC` 取**全库**最近未作答题。
  两个对话各有一道待答题时，答第 1 张会被判成第 2 张。对话内题卡提交时前端**已经把 `questionId` 丢掉了**
  （`ChatArea.vue` `@submit-quiz="(p) => store.send(p.answer)"`），后端因此只能猜。
  正确形态：作答走独立通道 `POST /quiz/{id}/answer`，`questionId` 显式带入；`quiz_graded` 走通知。
- [ ] **`_fuzzy_match` 反向包含太松**：`'遍历'` vs 参考 `'二叉树遍历'` 判对。
  收紧会破坏 `test_fuzzy_match_reverse`（该用例断言 `'栈'` 对 `'栈是一种数据结构'` 判对），需要先定口径。
- [ ] **难度不是行字段**：`difficulty` 只能整批传参（`save_questions(difficulty=...)`），一套 500 题全是 `medium`。
  出题器因此没有目标函数，做不了 IRT / 错题归因。`_ensure_columns` 已有补列范式可复用。
- [x] **`attempts` 只写不读**：无 `next_review_at` / 无错题队列 / 无任何 API 暴露正确率（`stats()` 只有 `COUNT(*)`）。
  复习调度（`learning/session.py` 刻意留空的 `review` 动作）依赖它。
  - ✅ 2026-11-16 **错题那半已接上读路径**：`QuizStore.wrong_question_ids()` 按「最近一次作答为错」派生错题池
    （不建表/不加列，订正答对自动移出），`GET /knowledge/node/{id}/quizzes` 给每题回填 `wrong`，节点详情侧边栏显示「答错过」。
  - ⏳ 仍缺：`next_review_at` / 到期队列 / 间隔重复（二期，见根 `TODO.md §10 P2`）。
- [ ] **出题不去重跨批次**：`avoid_questions` 只在本节点内生效；12 个题集里同一考点反复出现。
- [ ] **无 judge 环节**：`quality.py` 只有规则过滤（长度 / 外部依赖 / 文本归一化去重），
  没有"这题考的是不是这个节点""干扰项是不是都错得合理"的语义复核。

### P2 —— 需要决策，不排期

- [ ] **题库导入无入口**：`collector/adapters/dataset_quiz.py::import_from_json` 代码 + 配额 + 7 例测试全就绪，
      **零生产调用方**（无 API / 无 CLI / 无 UI）。补一个 `POST /quiz/import` 端点即可。
- [ ] **试卷拆分接不进来**：`collector/quiz_splitter.py::split_quiz` 实现完整但零调用。
      障碍是结构性的：没有 `papers` 实体表、`kb.upload_and_index` 只返回 `int` 节点 id（拿不回解析文本）、
      `MIN_PARSE_TEXT_LEN=200` 会把 5 道题的小测卷直接拒掉。**属 A1/A2 级工作量，别顺手做。**
- [x] ~~**掌握度是计数器不是估计量**：答对 +20、答错不变、全节通过置 100，单向棘轮没有降级路径。~~
      ALEKS / Khan 都允许掉级。改 BKT 后验估计需要先有稳定题库（见 P1 的调度项）。
  - ✅ 2026-11-16 **棘轮已拆**（用户口径「做错了就下调掌握度，最低到『了解』」）：答错 −20、下限 10
    （`kg.add_mastery(..., floor=10)`，只夹扣分、绝不抬高）；小节题按 **20/N** 分摊（`section_wrong_penalty`）；
    新增 `mastery_events.reason = quiz_wrong`。仍是**启发式计数器**（不是 BKT），换估计量那条继续留在本项。

---

## 3. 已知取舍（有意为之，别"优化"掉）

- **`CHAT_QUIZ_TOP_K = 0`**：对话内出题依据**只能**是节点正文。曾经是 3，实测题目会漂到节点之外
  （学生刚学「二叉树性质1」被考「满二叉树的结点数」）。要加检索，必须先解决"多来源时模型该考哪个"。
- **`_public_question` 不带 `answer` / `analysis`**：答案留在后端，前端拿不到就不能本地判分，也不能"背答案"。
- **对话内只出客观题**（`CHAT_QUIZ_TYPES` 无 `short_answer`）：对话里顺手答一句不适合长文本作答 + LLM 判分。
- **简答题 LLM 判分失败降级给半分**：宁可给个不会崩的分，也不让学生看到 500。
  代价是"没判成"和"答了一半"在数据上不可区分。
- **`apply_mastery_after_answer` 用 `caller="human"`**：这是**用户答题**的结果，写 `"ai"` 会被 `_guard_human_content` 拦下，
  人类创建的节点将永远无法通过答题提分。

---

## 4. 改这个模块时的三条硬约束

1. **判分失败绝不能吞掉分数**：图谱/回写任何异常只记 warning，判分结果必须返回。
2. **掌握度只有一个增量入口** `kg.add_mastery`（不要"读出来 + 20 再写绝对值"，多端并发会 lost update）。
3. **新增题目字段 = 写进 `quiz_store._ensure_columns`**：`CREATE TABLE IF NOT EXISTS` 不给老表加列，
   索引必须在补列**之后**建。
