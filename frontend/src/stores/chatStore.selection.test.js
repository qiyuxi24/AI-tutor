/**
 * 对话上下文选区（选资料 / 选知识点）回归。
 *
 * 核心不变量：选区是**每对话绑定**的，且**空选区 ≠ 不限范围**。
 *
 * 两个曾经真实存在的缺陷：
 * 1. 旧 `kbContext` 是一个全局裸 ref —— 切对话既不清空也不恢复，A 对话选中的资料
 *    会悄悄作用到 B 对话；而且**全仓库没有任何地方渲染它**，用户看不见自己引用了什么。
 * 2. 旧 `api/index.js` 写 `kb_node_ids: kb?.nodeIds || null` —— 空数组被 `||` 变成
 *    `null`，而后端把 `[]` 定义成"选了范围但里面没文件 → 检索不到任何东西"、
 *    `null` 是"不限范围"。于是**清空选区反而变成全库检索**，语义正好反了。
 *
 * 跑法：cd frontend && npm test
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('../api/index.js', () => ({
  sendMessageStream: vi.fn(() => ({ abort: vi.fn() })),
  apiClient: {
    get: vi.fn(() => Promise.resolve({ data: {} })),
    post: vi.fn(() => Promise.resolve({ data: {} })),
    put: vi.fn(() => Promise.resolve({ data: {} })),
    patch: vi.fn(() => Promise.resolve({ data: {} })),
    delete: vi.fn(() => Promise.resolve({ data: {} })),
  },
}))

import { useChatStore } from './chatStore.js'
import { sendMessageStream } from '../api/index.js'

// 存储键带用户后缀（`_uid` 在模块加载时按 ai_tutor_user 推导，测试里是 guest）
const CONV_KEY = 'ai_tutor_conversations_guest'

function memoryStorage() {
  const m = new Map()
  return {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => { m.set(k, String(v)) },
    removeItem: (k) => { m.delete(k) },
    clear: () => { m.clear() },
  }
}

beforeEach(() => {
  vi.stubGlobal('localStorage', memoryStorage())
  sendMessageStream.mockImplementation(() => ({ abort: vi.fn() }))
  setActivePinia(createPinia())
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.clearAllMocks()
})

/** 建两个各有内容的对话，返回已切到 A 的 store。 */
function twoConvs() {
  const store = useChatStore()
  store.conversations.push({
    id: 'a', title: 'A', messages: [{ role: 'user', content: 'x' }], createdAt: 1,
  })
  store.conversations.push({
    id: 'b', title: 'B', messages: [{ role: 'user', content: 'y' }], createdAt: 2,
  })
  store.currentId = 'a'
  return store
}

describe('选区：每对话绑定', () => {
  it('加进来的资料只在当前对话里', () => {
    const store = twoConvs()
    store.toggleSelectionItem('docs', { id: 7, name: '栈.md', type: 'file' })

    expect(store.selection.docs).toHaveLength(1)

    store.switchConversation('b')
    expect(store.selection).toBeNull()          // B 对话没选过 → 不应该继承 A 的

    store.switchConversation('a')
    expect(store.selection.docs[0].id).toBe(7)  // 切回来还在
  })

  it('切对话后发出的请求带的是**目标对话**的选区', async () => {
    const store = twoConvs()
    store.toggleSelectionItem('docs', { id: 7, name: '栈.md', type: 'file' })
    store.switchConversation('b')

    await store.send('提问')

    const sel = sendMessageStream.mock.calls.at(-1)[3]
    expect(sel.kb_node_ids).toBeNull()          // B 对话没有选区 → 不限范围
  })

  it('同一项重复选 = 取消（chip 不会出现两个）', () => {
    const store = twoConvs()
    store.toggleSelectionItem('docs', { id: 7, name: '栈.md', type: 'file' })
    store.toggleSelectionItem('docs', { id: 7, name: '栈.md', type: 'file' })
    expect(store.selection).toBeNull()          // 全空时整体置 null，不是空对象
  })

  it('资料与知识点互不干扰，可同时存在', () => {
    const store = twoConvs()
    store.toggleSelectionItem('docs', { id: 7, name: '栈.md', type: 'file' })
    store.toggleSelectionItem('nodes', { id: 'quicksort', name: '快速排序' })

    expect(store.selection.docs).toHaveLength(1)
    expect(store.selection.nodes).toHaveLength(1)
  })

  it('选区随对话一起落盘（刷新后还在）', () => {
    const store = twoConvs()
    store.toggleSelectionItem('docs', { id: 7, name: '栈.md', type: 'file' })

    const stored = JSON.parse(localStorage.getItem(CONV_KEY))
    const a = stored.find(c => c.id === 'a')
    expect(a.selection.docs[0].id).toBe(7)
  })
})

describe('选区 → 请求参数', () => {
  it('选了资料：kb_node_ids 是**数字数组**，name 是名字拼接', async () => {
    const store = twoConvs()
    store.toggleSelectionItem('docs', { id: 7, name: '栈.md', type: 'file' })
    store.toggleSelectionItem('docs', { id: 9, name: '队列.md', type: 'file' })

    await store.send('提问')

    const sel = sendMessageStream.mock.calls.at(-1)[3]
    expect(sel.kb_node_ids).toEqual([7, 9])
    expect(sel.kb_node_name).toContain('栈.md')
  })

  it('选了知识点：context_node_ids 是**字符串数组**', async () => {
    const store = twoConvs()
    store.toggleSelectionItem('nodes', { id: 'quicksort', name: '快速排序' })

    await store.send('提问')

    const sel = sendMessageStream.mock.calls.at(-1)[3]
    expect(sel.context_node_ids).toEqual(['quicksort'])
    expect(sel.kb_node_ids).toBeNull()
  })

  it('空选区发 null（= 不限范围），不是空数组', () => {
    const store = twoConvs()
    const p = store.buildSelectionPayload()
    expect(p.kb_node_ids).toBeNull()
    expect(p.context_node_ids).toBeNull()
  })

  it('清空选区后回到 null（不再残留上一批 node_id）', async () => {
    const store = twoConvs()
    store.toggleSelectionItem('docs', { id: 7, name: '栈.md', type: 'file' })
    store.clearSelection()

    await store.send('提问')

    expect(sendMessageStream.mock.calls.at(-1)[3].kb_node_ids).toBeNull()
  })
})

describe('停止生成', () => {
  it('停止后 loading=false，已流出的正文保留（不抹掉用户已经看到的内容）', async () => {
    const store = twoConvs()
    sendMessageStream.mockImplementation((_msgs, cbs) => {
      cbs.onToken('已生成的一部分')
      return { abort: vi.fn() }
    })

    // send 会 await 到流结束；这里在它还没回来时手动停
    const p = store.send('提问')
    store.stopGenerating()
    await p

    expect(store.loading).toBe(false)
    const conv = store.conversations.find(c => c.id === 'a')
    const last = conv.messages[conv.messages.length - 1]
    expect(last.content).toContain('已生成的一部分')
  })

  it('一个 token 都没收到就停止 → 留一句可见提示，不留气泡', async () => {
    const store = twoConvs()
    sendMessageStream.mockImplementation(() => ({ abort: vi.fn() }))

    const p = store.send('提问')
    store.stopGenerating()
    await p

    const conv = store.conversations.find(c => c.id === 'a')
    const last = conv.messages[conv.messages.length - 1]
    expect(last.role).toBe('assistant')
    expect(last.content).toBe('（已停止生成）')
    expect(last.failed).toBeFalsy()      // 不是失败：不该出现「重试」按钮
  })

  it('没在生成时调用是空操作（不会误改最后一条消息）', () => {
    const store = twoConvs()
    const before = JSON.stringify(store.conversations)
    store.stopGenerating()
    expect(JSON.stringify(store.conversations)).toBe(before)
  })
})

describe('空数组语义（api 层不得被 || 吞掉）', () => {
  it('kb_node_ids: [] 必须原样发出去（后端据此表示"范围内没有文件"）', async () => {
    // 直接测 api 层：这是旧实现 `kb?.nodeIds || null` 出错的唯一位置。
    // 本文件顶部把 api 模块 mock 掉了，所以这里取**真实**实现（importActual）。
    //
    // ⚠ fetch 的替身**故意永不 resolve**：body 是在调用 sendMessageStream 时同步组装的，
    // 拿到就够了。若让它 resolve 一个假响应，真实实现会继续走错误分流 → 动态 import
    // authStore → 在**别的测试文件**里冒出未处理的 rejection（实测偶发把
    // authStore 的用例打红）。
    const bodies = []
    vi.stubGlobal('fetch', vi.fn((_url, opts) => {
      bodies.push(JSON.parse(opts.body))
      return new Promise(() => {})
    }))

    const real = await vi.importActual('../api/index.js')
    real.sendMessageStream(
      [{ role: 'user', content: 'x' }], {}, '', { kb_node_ids: [], context_node_ids: [] }, 'c1',
    )
    await Promise.resolve()

    expect(bodies).toHaveLength(1)
    expect(bodies[0].kb_node_ids).toEqual([])        // 不是 null
    expect(bodies[0].context_node_ids).toEqual([])
  })
})
