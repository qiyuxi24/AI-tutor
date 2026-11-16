/**
 * quizPlan.js 单测 —— 题量摊分与提交体组装。
 *
 * 最要紧的一条：`question_plan` 的合计**必须等于** `question_count`。
 * 后端按 plan 逐类型出题，前端说 15 道而 plan 只摊出 14 道时，用户看到的是
 * "少了 1 道"却找不到原因，所以把 1~15 每个题量都断言一遍。
 *
 * 跑法：cd frontend && npm test
 */
import { describe, it, expect } from 'vitest'
import {
  MAX_QUESTIONS,
  FALLBACK_COUNT,
  defaultPlanRows,
  distributeQuestionPlan,
  questionPlanFromRows,
  isPlanSubmittable,
  buildQuizPayload,
  autoCountFromPreview,
  summarizeSections,
  formatMissingCovers,
  canFillMissing,
} from './quizPlan.js'

const sum = (plan) => plan.reduce((s, p) => s + p.count, 0)

describe('distributeQuestionPlan：合计恒等于题量', () => {
  it('3~15 每一档的 plan 合计都等于题量', () => {
    for (let n = 3; n <= MAX_QUESTIONS; n += 1) {
      expect(sum(distributeQuestionPlan(n))).toBe(n)
    }
  })

  it('简答永远不参与自动摊分（它就是 0 道）', () => {
    for (let n = 3; n <= MAX_QUESTIONS; n += 1) {
      expect(distributeQuestionPlan(n).some((p) => p.type === 'short_answer')).toBe(false)
    }
  })

  it('权重越大分到越多：单选 ≥ 判断 ≥ 多选 ≥ 填空', () => {
    for (let n = 7; n <= MAX_QUESTIONS; n += 1) {
      const by = Object.fromEntries(distributeQuestionPlan(n).map((p) => [p.type, p.count]))
      expect(by.single).toBeGreaterThanOrEqual(by.judge)
      expect(by.judge).toBeGreaterThanOrEqual(by.multiple)
      expect(by.multiple).toBeGreaterThanOrEqual(by.fill)
    }
  })

  it('只有单选/多选带 option_count，且落在 2~6', () => {
    for (const p of distributeQuestionPlan(12)) {
      if (p.type === 'single' || p.type === 'multiple') {
        expect(p.option_count).toBeGreaterThanOrEqual(2)
        expect(p.option_count).toBeLessThanOrEqual(6)
      } else {
        expect(p).not.toHaveProperty('option_count')
      }
    }
  })

  it('题量超上限会被收敛到 15（不可能摊出 20 道）', () => {
    expect(sum(distributeQuestionPlan(99))).toBe(MAX_QUESTIONS)
  })
})

describe('配比表单', () => {
  it('缺省五行合计 7 道，不超上限', () => {
    const rows = defaultPlanRows()
    expect(rows).toHaveLength(5)
    expect(sum(questionPlanFromRows(rows))).toBe(7)
  })

  it('count=0 的行被过滤，选项数被收敛', () => {
    const plan = questionPlanFromRows([
      { type: 'single', count: 2, optionCount: 99 },
      { type: 'multiple', count: 0, optionCount: 4 },
      { type: 'judge', count: 1, optionCount: 0 },
    ])
    expect(plan).toEqual([
      { type: 'single', count: 2, option_count: 6 },
      { type: 'judge', count: 1 },
    ])
  })

  it('可提交判据：1~15', () => {
    expect(isPlanSubmittable(0)).toBe(false)
    expect(isPlanSubmittable(1)).toBe(true)
    expect(isPlanSubmittable(15)).toBe(true)
    expect(isPlanSubmittable(16)).toBe(false)
  })
})

describe('buildQuizPayload', () => {
  it('三模式都不带 question_types，且都带 count_mode', () => {
    for (const mode of ['auto', 'manual', 'plan']) {
      const p = buildQuizPayload({ mode, subject: 'x', rows: defaultPlanRows() })
      expect(p).not.toHaveProperty('question_types')
      expect(p.count_mode).toBe(mode)
      expect(sum(p.question_plan)).toBe(p.question_count)
    }
  })

  it('只有 plan 模式带 strict', () => {
    expect(buildQuizPayload({ mode: 'auto' })).not.toHaveProperty('strict')
    expect(buildQuizPayload({ mode: 'manual' })).not.toHaveProperty('strict')
    expect(buildQuizPayload({ mode: 'plan', rows: defaultPlanRows(), strict: true }).strict).toBe(true)
  })

  it('auto 用自动题量、manual 用手动题量', () => {
    expect(buildQuizPayload({ mode: 'auto', autoCount: 12 }).question_count).toBe(12)
    expect(buildQuizPayload({ mode: 'manual', manualCount: 4 }).question_count).toBe(4)
  })
})

describe('降级与文案', () => {
  it('preview 响应缺 count 时回落 5 道', () => {
    expect(autoCountFromPreview(null)).toBe(FALLBACK_COUNT)
    expect(autoCountFromPreview({})).toBe(FALLBACK_COUNT)
    expect(autoCountFromPreview({ count: 0 })).toBe(FALLBACK_COUNT)
    expect(autoCountFromPreview({ count: 9 })).toBe(9)
    expect(autoCountFromPreview({ count: 99 })).toBe(MAX_QUESTIONS)
  })

  it('逐节摘要最多 6 节，其余折成「等 N 节」（N = 总节数）', () => {
    const sections = Array.from({ length: 8 }, (_, i) => ({ title: `节${i + 1}`, planned: 2 }))
    const s = summarizeSections(sections)
    expect(s).toContain('节1 2 道')
    expect(s).toContain('节6 2 道')
    expect(s).not.toContain('节7 2 道')
    expect(s).toContain('等 8 节')
    expect(summarizeSections([])).toBe('')
  })

  it('缺考点文案：有 keys 说 keys，只有 shortfall 就说差多少道', () => {
    expect(formatMissingCovers(['A', 'B'], 2)).toBe('缺 2 个考点：A、B')
    expect(formatMissingCovers([], 2)).toBe('还差 2 道没出全')
    expect(formatMissingCovers(undefined, 0)).toBe('未凑齐要求的题量')
  })

  it('canFillMissing 只在响应真的带非空数组时为真', () => {
    expect(canFillMissing({ missing_cover_keys: ['k'] })).toBe(true)
    expect(canFillMissing({ missing_cover_keys: [] })).toBe(false)
    expect(canFillMissing({ shortfall: 2 })).toBe(false)
    expect(canFillMissing(undefined)).toBe(false)
  })
})
