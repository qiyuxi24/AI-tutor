/**
 * 出题页配置区（三模式：自动 / 手动 / 按题型配比）回归。
 *
 * 核心不变量：
 *   1. 题量由内容决定 —— 挂载后必须问一次 `previewQuizPlan` 并把它给的 `reason` 渲染出来；
 *   2. 三种模式提交的 payload 形状各不相同，但**都不许再带 `question_types`**
 *      （它与 `question_plan` 互斥，同时传后端 422）；
 *   3. `question_plan` 的合计必须等于 `question_count` —— 说几道就是几道；
 *   4. 配比合计 0 或 >15 时禁用「开始出题」，前端先挡一道，不让用户白等一次 LLM；
 *   5. preview 失败（未登录 / 后端未实现）静默降级为 5 道，**不弹错误、不抛异常**。
 *
 * 不真的打网络：`../api/quiz.js` 整体 mock。
 * 跑法：cd frontend && npm test
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { createApp, nextTick, h } from 'vue'
import ElementPlus from 'element-plus'

/* ── mock 必须在 import 组件之前声明（vi.mock 被提升）── */
vi.mock('../api/quiz.js', () => ({
  generateQuiz: vi.fn(() => Promise.resolve({ data: {} })),
  previewQuizPlan: vi.fn(() => Promise.resolve({ data: {} })),
  gradeQuizQuestion: vi.fn(() => Promise.resolve({ data: {} })),
  getQuizStats: vi.fn(() => Promise.resolve({ data: { total_questions: 0 } })),
  getQuizQuestion: vi.fn(() => Promise.resolve({ data: null })),
  listQuizBatches: vi.fn(() => Promise.resolve({ data: { batches: [], total: 0 } })),
  getBatchQuestions: vi.fn(() => Promise.resolve({ data: { questions: [] } })),
  fetchQuizBlob: vi.fn(() => Promise.resolve({ blob: new Blob(), filename: 'x', type: '' })),
  saveQuizBlob: vi.fn(() => 'x'),
  readBlobError: vi.fn(() => '导出失败'),
}))

import QuizView from './QuizView.vue'
import { generateQuiz, previewQuizPlan, getQuizStats, listQuizBatches } from '../api/quiz.js'

/** 只渲染 slot 的替身：侧栏/预览弹窗与本题无关，省掉它们的重依赖 */
const SlotStub = { render() { return h('div', this.$slots.default ? this.$slots.default() : []) } }

/**
 * 挂载真实的 QuizView（含 Element Plus，与 main.js 的装配一致）。
 * `@vue/test-utils` 不在依赖里，这里直接用 createApp + jsdom 容器，
 * 好处是断言的就是用户真正看到的 DOM。
 *
 * 改状态走 `$ .setupState`：`<script setup>` 的绑定**不挂在公共实例代理上**
 * （没有 defineExpose），直接读 `vm.subject` 会拿到 undefined。
 */
function mountQuiz() {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const app = createApp(QuizView)
  app.use(ElementPlus)
  app.component('SidePanel', SlotStub)
  app.component('QuizHistorySidebar', SlotStub)
  app.component('KbTextPreviewDialog', SlotStub)
  const vm = app.mount(host)
  return {
    host,
    state: vm.$.setupState,
    destroy() {
      app.unmount()
      host.remove()
    },
  }
}

/** preview 有 400ms 防抖，等它落地 */
const wait = (ms) => new Promise((r) => setTimeout(r, ms))
const flush = async (n = 4) => { for (let i = 0; i < n; i += 1) await nextTick() }

/** 找「开始出题」按钮 */
function submitButton(host) {
  return [...host.querySelectorAll('button')].find((b) => b.textContent.includes('开始出题'))
}

async function clickSubmit(host) {
  const btn = submitButton(host)
  expect(btn).toBeTruthy()
  btn.dispatchEvent(new window.MouseEvent('click', { bubbles: true }))
  await flush()
}

let mounted
beforeEach(() => {
  vi.clearAllMocks()
  generateQuiz.mockResolvedValue({ data: { questions: [], question_ids: [] } })
  getQuizStats.mockResolvedValue({ data: { total_questions: 0 } })
  listQuizBatches.mockResolvedValue({ data: { batches: [], total: 0 } })
})

afterEach(() => {
  if (mounted) { mounted.destroy(); mounted = null }
  document.body.innerHTML = ''
})

describe('自动模式：题量由内容决定', () => {
  it('挂载后请求 preview，并把题量和依据渲染出来', async () => {
    previewQuizPlan.mockResolvedValue({
      data: {
        count: 15,
        reason: '15 道（已到上限）· 依据：8 个小节、正文 17377 字',
        count_mode: 'auto',
        capped: true,
        section_count: 8,
        content_chars: 17377,
        sections: [
          { section_id: 's1', title: '机械运动的概念与意义', chars: 2400, planned: 2 },
          { section_id: 's2', title: '质点', chars: 1800, planned: 2 },
        ],
      },
    })
    mounted = mountQuiz()
    await wait(500)
    await flush()

    expect(previewQuizPlan).toHaveBeenCalledTimes(1)
    const body = mounted.host.textContent
    expect(body).toContain('15')
    expect(body).toContain('15 道（已到上限）· 依据：8 个小节、正文 17377 字')
    // 逐节计划数摘要
    expect(body).toContain('机械运动的概念与意义 2 道')
    expect(body).toContain('质点 2 道')
  })

  it('preview 失败时降级为 5 道并标注「未能按内容估算」，不抛错', async () => {
    previewQuizPlan.mockRejectedValue(new Error('404 Not Found'))
    mounted = mountQuiz()
    await wait(500)
    await flush()

    const body = mounted.host.textContent
    expect(body).toContain('5')
    expect(body).toContain('（未能按内容估算）')
    // 题目没出来，也没有错误横幅 —— 降级是静默的
    expect(mounted.host.querySelector('.cfg-error')).toBeNull()
  })
})

describe('提交 payload 形状（三模式）', () => {
  beforeEach(() => {
    previewQuizPlan.mockResolvedValue({ data: { count: 10, reason: '10 道 · 依据：4 个小节', sections: [] } })
  })

  it('auto：按自动题量摊分，合计 == question_count，不含 question_types', async () => {
    mounted = mountQuiz()
    await wait(500)
    await flush()

    // 自动模式要填主题才能提交
    mounted.state.subject = '二叉树遍历'
    await flush()
    await clickSubmit(mounted.host)

    expect(generateQuiz).toHaveBeenCalledTimes(1)
    const payload = generateQuiz.mock.calls[0][0]
    expect(payload.count_mode).toBe('auto')
    expect(payload.subject).toBe('二叉树遍历')
    expect(payload).not.toHaveProperty('question_types')
    expect(payload.question_count).toBe(10)
    expect(payload.question_plan.reduce((s, p) => s + p.count, 0)).toBe(10)
  })

  it('manual：摊法相同但总数用用户输入', async () => {
    mounted = mountQuiz()
    await wait(500)
    await flush()

    mounted.state.subject = '栈与队列'
    mounted.state.countMode = 'manual'
    mounted.state.manualCount = 6
    await flush()
    await clickSubmit(mounted.host)

    const payload = generateQuiz.mock.calls[0][0]
    expect(payload.count_mode).toBe('manual')
    expect(payload).not.toHaveProperty('question_types')
    expect(payload.question_count).toBe(6)
    expect(payload.question_plan.reduce((s, p) => s + p.count, 0)).toBe(6)
  })

  it('plan：直接用五行表单，过滤 0 道行，并传 strict', async () => {
    mounted = mountQuiz()
    await wait(500)
    await flush()

    mounted.state.subject = '排序算法'
    mounted.state.countMode = 'plan'
    mounted.state.strictMode = true
    // 单选 4×5、多选 0（应被过滤）、判断 3、填空 2、简答 1 → 合计 10
    mounted.state.planRows[0].count = 4
    mounted.state.planRows[0].optionCount = 5
    mounted.state.planRows[1].count = 0
    mounted.state.planRows[2].count = 3
    mounted.state.planRows[3].count = 2
    mounted.state.planRows[4].count = 1
    await flush()
    await clickSubmit(mounted.host)

    const payload = generateQuiz.mock.calls[0][0]
    expect(payload.count_mode).toBe('plan')
    expect(payload.strict).toBe(true)
    expect(payload).not.toHaveProperty('question_types')
    expect(payload.question_count).toBe(10)
    expect(payload.question_plan).toEqual([
      { type: 'single', count: 4, option_count: 5 },
      { type: 'judge', count: 3 },
      { type: 'fill', count: 2 },
      { type: 'short_answer', count: 1 },
    ])
  })
})

describe('配比合计校验', () => {
  beforeEach(() => {
    previewQuizPlan.mockResolvedValue({ data: { count: 5, reason: '5 道', sections: [] } })
  })

  it('合计 >15 时禁用「开始出题」并给出提示', async () => {
    mounted = mountQuiz()
    await wait(500)
    await flush()

    mounted.state.countMode = 'plan'
    mounted.state.planRows.forEach((r) => { r.count = 20 })   // 20*5 = 100
    await flush()

    const btn = submitButton(mounted.host)
    expect(btn.disabled).toBe(true)
    expect(mounted.host.textContent).toContain('超过上限 15 道')

    await clickSubmit(mounted.host)
    expect(generateQuiz).not.toHaveBeenCalled()
  })

  it('合计为 0 时同样禁用', async () => {
    mounted = mountQuiz()
    await wait(500)
    await flush()

    mounted.state.countMode = 'plan'
    mounted.state.planRows.forEach((r) => { r.count = 0 })
    await flush()

    expect(submitButton(mounted.host).disabled).toBe(true)
    expect(mounted.host.textContent).toContain('至少配 1 道')
  })
})

describe('考点没铺满时的提示', () => {
  it('satisfied=false 且带 missing_cover_keys → 显示缺哪些考点 + 补齐按钮', async () => {
    previewQuizPlan.mockResolvedValue({ data: { count: 5, reason: '5 道', sections: [] } })
    generateQuiz.mockResolvedValue({
      data: {
        questions: [{ type: 'single', question: 'Q1', options: [], points: 5 }],
        question_ids: [1],
        satisfied: false,
        shortfall: 2,
        missing_cover_keys: ['k1', 'k2'],
      },
    })
    mounted = mountQuiz()
    await wait(500)
    await flush()

    mounted.state.subject = '树'
    await flush()
    await clickSubmit(mounted.host)
    await flush()

    const body = mounted.host.textContent
    expect(body).toContain('缺 2 个考点：k1、k2')
    expect([...mounted.host.querySelectorAll('button')].some((b) => b.textContent.includes('补齐缺失'))).toBe(true)
  })

  it('响应里没有 missing_cover_keys → 不显示「补齐缺失」按钮（不臆造契约）', async () => {
    previewQuizPlan.mockResolvedValue({ data: { count: 5, reason: '5 道', sections: [] } })
    generateQuiz.mockResolvedValue({
      data: {
        questions: [{ type: 'single', question: 'Q1', options: [], points: 5 }],
        question_ids: [1],
        satisfied: false,
        shortfall: 2,
      },
    })
    mounted = mountQuiz()
    await wait(500)
    await flush()

    mounted.state.subject = '树'
    await flush()
    await clickSubmit(mounted.host)
    await flush()

    expect([...mounted.host.querySelectorAll('button')].some((b) => b.textContent.includes('补齐缺失'))).toBe(false)
    expect(mounted.host.textContent).toContain('还差 2 道没出全')
  })

  it('点「补齐缺失」→ 带 missing_cover_keys 再请求一次，只补缺口那几道', async () => {
    previewQuizPlan.mockResolvedValue({ data: { count: 5, reason: '5 道', sections: [] } })
    generateQuiz.mockResolvedValueOnce({
      data: {
        questions: [{ type: 'single', question: 'Q1', options: [], points: 5 }],
        question_ids: [1],
        satisfied: false,
        shortfall: 2,
        missing_cover_keys: ['k1', 'k2'],
      },
    })
    generateQuiz.mockResolvedValueOnce({
      data: {
        questions: [
          { type: 'judge', question: 'Q2', points: 5 },
          { type: 'judge', question: 'Q3', points: 5 },
        ],
        question_ids: [2, 3],
        satisfied: true,
        shortfall: 0,
        missing_cover_keys: [],
      },
    })
    mounted = mountQuiz()
    await wait(500)
    await flush()

    mounted.state.subject = '树'
    await flush()
    await clickSubmit(mounted.host)
    await flush()

    const fillBtn = [...mounted.host.querySelectorAll('button')].find((b) => b.textContent.includes('补齐缺失'))
    expect(fillBtn).toBeTruthy()
    fillBtn.dispatchEvent(new window.MouseEvent('click', { bubbles: true }))
    await flush(8)

    expect(generateQuiz).toHaveBeenCalledTimes(2)
    const retry = generateQuiz.mock.calls[1][0]
    expect(retry.missing_cover_keys).toEqual(['k1', 'k2'])
    expect(retry.question_count).toBe(2)                       // 只补缺口，不重发整卷
    expect(retry.question_plan.reduce((s, p) => s + p.count, 0)).toBe(2)
    expect(retry).not.toHaveProperty('question_types')
    // 新题追加在已答题目之后，不覆盖
    expect(mounted.host.textContent).toContain('本次 3 道')
  })
})

