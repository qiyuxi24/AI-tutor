/**
 * quizPlan.js —— 出题计划（题量 / 题型配比）的纯逻辑。
 *
 * 为什么单独成文件：出题页原本是「下拉选 3/5/8/10 + 5 个题型 checkbox」，
 * 四个数字彼此无关、题量也不由内容决定。改成三模式后，**提交体长什么样**
 * 变成了一条有分支的规则（auto / manual / plan），它必须能被单测直接断言，
 * 而不是塞在 SFC 里只能靠挂载组件间接验证。组件只负责交互与展示。
 *
 * 约定（与后端契约对齐）：
 *   - 后端上限 `QUIZ_MAX_QUESTIONS=15`、下限 `QUIZ_MIN_QUESTIONS=3`。
 *   - `question_plan` 与老字段 `question_types` **互斥**，同时传会 422 —— 这里只产出 plan。
 *   - 只有单选/多选有 `option_count`；判断/填空/简答没有选项。
 */

/** 后端 QUIZ_MIN_QUESTIONS */
export const MIN_QUESTIONS = 3
/** 后端 QUIZ_MAX_QUESTIONS */
export const MAX_QUESTIONS = 15
/** 自动模式拿不到后端估算时的兜底题量（= 改造前写死的默认值） */
export const FALLBACK_COUNT = 5
/** 配比表单里「单行数量」的上限：允许单题型独大，但合计仍受 MAX_QUESTIONS 约束 */
export const MAX_ROW_COUNT = 20
/** 有选项的题型（只有这两类显示「选项数」） */
export const OPTION_TYPES = ['single', 'multiple']

/**
 * 五行固定配比模板（**刻意不做动态增删行**，抄 tldw_server 的做法：
 * 动态行会让 UI 复杂且容易凑出非法组合）。
 * `weight` 同时是 auto/manual 的摊分权重与「按题型配比」的缺省值。
 */
const PLAN_TEMPLATE = [
  { type: 'single', weight: 3, option: 4 },
  { type: 'multiple', weight: 1, option: 4 },
  { type: 'judge', weight: 2, option: 0 },
  { type: 'fill', weight: 1, option: 0 },
  { type: 'short_answer', weight: 0, option: 0 },   // 简答永远不参与自动摊分
]

/** 配比表单缺省值：单选 3×4、多选 1×4、判断 2、填空 1、简答 0（合计 7，不超上限） */
export function defaultPlanRows() {
  return PLAN_TEMPLATE.map((r) => ({ type: r.type, count: r.weight, optionCount: r.option }))
}

/** 把任意输入收敛到 [MIN, MAX] 的整数题量；非法值给 FALLBACK_COUNT */
export function clampCount(total, min = MIN_QUESTIONS, max = MAX_QUESTIONS) {
  const n = Math.round(Number(total))
  if (!Number.isFinite(n)) return FALLBACK_COUNT
  return Math.min(max, Math.max(min, n))
}

/** 一行配比 → 后端 question_plan 项（count<=0 的行由调用方先过滤） */
function rowToPlanItem(row) {
  const item = { type: row.type, count: Math.max(0, Math.round(Number(row.count) || 0)) }
  if (OPTION_TYPES.includes(row.type)) {
    item.option_count = clampCount(row.optionCount, 2, 6)   // 选项数 2~6
  }
  return item
}

/**
 * 把题量摊到题型（auto 与 manual 共用同一套算法）。
 *
 * 算法 = **Hamilton 最大余数法**（配额分配里的标准做法），理由：
 *   1. 保底「合计恰好等于题量」—— 用户/后端给 15，就必须出 15 道，
 *      单纯的按比例 `Math.round` 会漂移成 14 或 16；
 *   2. 稳定可断言 —— 同样的题量永远得到同样的分配，便于单测与排障；
 *   3. 不用整数 DP/优先队列那种"聪明"办法：权重只有 4 个，最大余数法
 *      已经能把误差控制在 1 道以内，再精确没有实际意义。
 *
 * 步骤：
 *   ① 权重 = PLAN_TEMPLATE 的 weight（单选 3 : 多选 1 : 判断 2 : 填空 1，简答权重 0 直接排除）；
 *   ② 每类先拿 `floor(n * w / Σw)`；
 *   ③ 剩下的名额（= n - Σfloor，必 < 行数）按**小数部分从大到小**补；
 *      小数部分相同则权重大的优先，再相同按模板顺序 —— 三轮 tie-break 保证确定性。
 *
 * 例：n=5 → 单选 2、多选 1、判断 1、填空 1（合计 5）；
 *     n=15 → 单选 7、多选 2、判断 4、填空 2（合计 15）。
 *
 * @param {number} total 题量（会被收敛到 [1, MAX_QUESTIONS]）
 * @returns {Array<{type:string,count:number,optionCount:number}>} 只含 count>0 的行
 */
export function distributeQuestionPlan(total) {
  const n = clampCount(total, 1, MAX_QUESTIONS)
  const rows = PLAN_TEMPLATE.filter((r) => r.weight > 0)
  const weightSum = rows.reduce((s, r) => s + r.weight, 0)

  const exacts = rows.map((r) => (n * r.weight) / weightSum)
  const seats = exacts.map((x) => Math.floor(x))

  let left = n - seats.reduce((a, b) => a + b, 0)
  const order = rows
    .map((r, i) => ({ i, rem: exacts[i] - seats[i], weight: r.weight }))
    .sort((a, b) => (b.rem - a.rem) || (b.weight - a.weight) || (a.i - b.i))

  // 余数法保证 left <= 行数；循环而不是单轮，防浮点把 left 算大时漏名额
  for (let k = 0; left > 0; k = (k + 1) % order.length) {
    seats[order[k].i] += 1
    left -= 1
  }

  return rows
    .map((r, i) => rowToPlanItem({ type: r.type, count: seats[i], optionCount: r.option }))
    .filter((p) => p.count > 0)
}

/** 配比表单 → question_plan（过滤 count=0 的行，这是契约要求的「空行不传」） */
export function questionPlanFromRows(rows = []) {
  return rows.map(rowToPlanItem).filter((p) => p.count > 0)
}

/** 配比表单合计题量 */
export function sumPlanRows(rows = []) {
  return rows.reduce((s, r) => s + Math.max(0, Math.round(Number(r.count) || 0)), 0)
}

/** 配比是否可提交：1~15 道（0 = 什么也不出，>15 = 后端必拒） */
export function isPlanSubmittable(total) {
  return total >= 1 && total <= MAX_QUESTIONS
}

/**
 * 组装 `POST /api/v1/quiz/generate` 的请求体。
 *
 * - auto  → 题量取「按内容估算」的自动题量，按比例摊到题型；
 * - manual → 摊法相同，题量取用户输入；
 * - plan  → 直接用表单五行（空行过滤），并带上 `strict` 开关；
 * - 三种模式都带 `count_mode`；**绝不带 `question_types`**（与 plan 互斥，同传 422）。
 *
 * `question_count` 一律取 `question_plan` 的合计，保证「说几道就是几道」，
 * 不会出现 count=15 而 plan 只摊出 14 的自我矛盾。
 */
export function buildQuizPayload({
  mode = 'auto',
  subject = '',
  difficulty = 'medium',
  graphNodeId = '',
  autoCount = FALLBACK_COUNT,
  manualCount = FALLBACK_COUNT,
  rows = [],
  strict = false,
} = {}) {
  const question_plan = mode === 'plan'
    ? questionPlanFromRows(rows)
    : distributeQuestionPlan(mode === 'auto' ? autoCount : manualCount)

  const payload = {
    subject,
    difficulty,
    graph_node_id: graphNodeId,
    count_mode: mode,
    question_count: question_plan.reduce((s, p) => s + p.count, 0),
    question_plan,
  }
  if (mode === 'plan') payload.strict = !!strict
  return payload
}

/**
 * 自动模式题量：后端 preview 的 `count`；拿不到就给兜底 5 道。
 * （preview 失败时调用方把 data 传 null，这里自然回落 —— 降级只有这一条路径）
 */
export function autoCountFromPreview(data) {
  const n = Math.round(Number(data?.count))
  if (!Number.isFinite(n) || n <= 0) return FALLBACK_COUNT
  return Math.min(MAX_QUESTIONS, Math.max(1, n))
}

/**
 * 逐节计划摘要：「机械运动的概念与意义 2 道 / 质点 2 道 / … 等 8 节」。
 * 最多显示 `max` 节，其余折成「等 N 节」（N 为小节总数，读起来才是"……等共 8 节"）。
 */
export function summarizeSections(sections, max = 6) {
  const list = (Array.isArray(sections) ? sections : []).filter((s) => s && s.title)
  if (!list.length) return ''
  const head = list.slice(0, max).map((s) => `${s.title} ${Math.round(Number(s.planned) || 0)} 道`)
  return list.length > max ? [...head, `等 ${list.length} 节`].join(' / ') : head.join(' / ')
}

/**
 * 「缺 X 个考点：A、B」文案。
 * 后端只给 `shortfall`（道数）没给考点名时不硬凑考点：改用道数说清差额。
 */
export function formatMissingCovers(keys, shortfall = 0) {
  const list = (Array.isArray(keys) ? keys : []).filter(Boolean)
  if (list.length) return `缺 ${list.length} 个考点：${list.join('、')}`
  const gap = Math.round(Number(shortfall) || 0)
  if (gap > 0) return `还差 ${gap} 道没出全`
  return '未凑齐要求的题量'
}

/** 响应里到底有没有 `missing_cover_keys` —— 没有就不显示「补齐缺失」按钮（不臆造契约） */
export function canFillMissing(data) {
  return Array.isArray(data?.missing_cover_keys) && data.missing_cover_keys.length > 0
}
