/**
 * chatStore 生命周期回归（A3-1 / A3-2 / A3-3）。
 *
 * 三件事都是同一类问题：「组件卸载 / 登出之后不该继续跑的东西，还在跑」。
 *   · A3-1 SSE 断线退避重连 + disconnectSSE 之后彻底不再连
 *   · A3-2 连发两次时，前一条在途流必须被 abort
 *   · A3-3 dispose() 把所有裸定时器清干净（无残留 timer）
 *
 * 数据层（api）整体替身：这些用例只关心生命周期，不关心网络。
 * 跑法：cd frontend && npm i -D vitest jsdom && npm test
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('../api/index.js', () => ({
  sendMessageStream: vi.fn(() => ({ abort: vi.fn() })),
  apiClient: {
    get: vi.fn(() => Promise.resolve({ data: {} })),
    post: vi.fn(() => Promise.resolve({ data: {} })),
    put: vi.fn(() => Promise.resolve({ data: {} })),
    delete: vi.fn(() => Promise.resolve({ data: {} })),
  },
}))

import { useChatStore } from './chatStore.js'
import { sendMessageStream, apiClient } from '../api/index.js'

/** 最小 EventSource 替身：留实例、暴露三个回调、close() 可断言 */
class FakeEventSource {
  static instances = []
  constructor(url) {
    this.url = url
    this.closed = false
    this.onopen = null
    this.onmessage = null
    this.onerror = null
    FakeEventSource.instances.push(this)
  }
  close() { this.closed = true }
}

/**
 * 内存版 localStorage。
 *
 * **不要用 jsdom 自带的**：`StorageImpl.setItem` 内部会挂一个 0ms 定时器，
 * 于是"清点残留定时器"的断言里永远混着 2 个环境噪声（实测踩过）。
 * 换成纯内存实现后，`vi.getTimerCount()` 只反映 store 自己挂的定时器。
 */
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
  FakeEventSource.instances = []
  vi.stubGlobal('EventSource', FakeEventSource)
  vi.stubGlobal('localStorage', memoryStorage())
  sendMessageStream.mockImplementation(() => ({ abort: vi.fn() }))
  setActivePinia(createPinia())
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.clearAllMocks()
})

describe('SSE 生命周期（A3-1）', () => {
  it('断线按 5s→10s 退避重连；disconnectSSE 之后彻底不再连', async () => {
    vi.useFakeTimers()
    localStorage.setItem('ai_tutor_token', 't')
    const store = useChatStore()

    await store.init()
    expect(FakeEventSource.instances).toHaveLength(1)

    // 断线 → 立即关连接，5s 后重连
    FakeEventSource.instances[0].onerror()
    expect(FakeEventSource.instances[0].closed).toBe(true)
    await vi.advanceTimersByTimeAsync(4999)
    expect(FakeEventSource.instances).toHaveLength(1)
    await vi.advanceTimersByTimeAsync(1)
    expect(FakeEventSource.instances).toHaveLength(2)

    // 再断一次 → 退避翻倍，10s 才重连（原来固定 5s）
    FakeEventSource.instances[1].onerror()
    await vi.advanceTimersByTimeAsync(9000)
    expect(FakeEventSource.instances).toHaveLength(2)
    await vi.advanceTimersByTimeAsync(1000)
    expect(FakeEventSource.instances).toHaveLength(3)

    // 显式断开（登出 / 卸载）→ 连迟到的 onerror 也不该再排重连
    store.disconnectSSE()
    expect(FakeEventSource.instances[2].closed).toBe(true)
    FakeEventSource.instances[2].onerror()
    await vi.advanceTimersByTimeAsync(600000)
    expect(FakeEventSource.instances).toHaveLength(3)
  })

  it('登出（authStore.logout）会断掉 SSE 并清凭据', async () => {
    localStorage.setItem('ai_tutor_token', 't')
    const store = useChatStore()
    await store.init()
    expect(FakeEventSource.instances).toHaveLength(1)

    const { useAuthStore } = await import('./authStore.js')
    useAuthStore().logout()

    expect(localStorage.getItem('ai_tutor_token')).toBeNull()
    expect(FakeEventSource.instances[0].closed).toBe(true)
  })
})

describe('离线通知补拉（A2-3）', () => {
  it('SSE 一建连就拉 pending_notices，补做断线期间的通知', async () => {
    localStorage.setItem('ai_tutor_token', 't')
    const store = useChatStore()
    await store.init()

    FakeEventSource.instances[0].onopen()

    await vi.waitFor(() => {
      expect(apiClient.get).toHaveBeenCalledWith('/api/v1/knowledge/notices/pending')
    })
  })
})

describe('在途流（A3-2）', () => {
  it('连发两次：第一次的流被 abort，第二次不受影响', async () => {
    const controllers = []
    sendMessageStream.mockImplementation(() => {
      const c = { abort: vi.fn() }
      controllers.push(c)
      return c
    })
    const store = useChatStore()

    await store.send('第一条')
    await store.send('第二条')

    expect(controllers).toHaveLength(2)
    expect(controllers[0].abort).toHaveBeenCalledTimes(1)
    expect(controllers[1].abort).not.toHaveBeenCalled()
  })
})

describe('dispose 清定时器（A3-3）', () => {
  it('dispose() 之后不残留任何 store 自己挂的定时器', async () => {
    vi.useFakeTimers()
    const store = useChatStore()

    await store.init()
    expect(vi.getTimerCount()).toBe(0)

    await store.send('你好')          // → 同步防抖 500ms
    expect(vi.getTimerCount()).toBe(1)

    await store.refreshGraph(true)    // → SSE 抑制 3s
    expect(vi.getTimerCount()).toBe(2)

    store.dispose()
    expect(vi.getTimerCount()).toBe(0)
  })
})

describe('对话持久化与流式收口（缺陷 A/B/C 回归）', () => {
  // store 里的 key 由模块加载时的 user id 派生（test 环境恒为 'guest'）。
  const CONV_KEY = 'ai_tutor_conversations_guest'
  const CUR_KEY = 'ai_tutor_current_guest'

  it('C-2：persist 时当前对话为空 → 移除 current 键（非空串），且不把历史写成 []', () => {
    const store = useChatStore()
    // 内存：一条有内容的历史 + 一个空的当前对话
    store.conversations.push({
      id: 'h1', title: '历史', messages: [{ role: 'user', content: 'hi' }],
      createdAt: 1, updatedAt: 1,
    })
    store.conversations.push({ id: 'e1', title: '新对话', messages: [], createdAt: 2 })
    store.currentId = 'h1'
    // 磁盘上已落盘历史（模拟上一轮 persist）
    localStorage.setItem(CONV_KEY, JSON.stringify([
      { id: 'h1', title: '历史', messages: [{ role: 'user', content: 'hi' }], createdAt: 1 },
    ]))
    localStorage.setItem(CUR_KEY, 'h1')

    // switchConversation 内部会调用 persist()，切到空对话 e1
    store.switchConversation('e1')

    // 历史未被 "[]" 覆盖
    const stored = JSON.parse(localStorage.getItem(CONV_KEY))
    expect(stored.map(c => c.id)).toContain('h1')
    // current 被移除，而不是写成空串
    expect(localStorage.getItem(CUR_KEY)).toBeNull()
  })

  it('C-1：startLearning() 之后对话键未被提前 persist 清空，学习对话已落盘', async () => {
    const store = useChatStore()

    await store.startLearning('node-1', '线性表')

    const stored = JSON.parse(localStorage.getItem(CONV_KEY))
    expect(Array.isArray(stored)).toBe(true)
    expect(stored.length).toBeGreaterThan(0)                 // 不是 "[]"
    const conv = stored.find(c => c.nodeId === 'node-1')
    expect(conv).toBeTruthy()                                // 知识点绑定已落盘
    expect(conv.messages.length).toBeGreaterThan(0)          // 提问正文已写入
    expect(localStorage.getItem(CUR_KEY)).toBe(conv.id)      // current 指向它（未被清空）
  })

  it('B：switchConversation 恢复 currentNode；send 的 current_node 取自对话绑定', async () => {
    const store = useChatStore()
    store.conversations.push({
      id: 'a', title: 'A', messages: [{ role: 'user', content: 'x' }], createdAt: 1, nodeId: 'nA',
    })
    store.conversations.push({ id: 'b', title: 'B', messages: [], createdAt: 2, nodeId: 'nB' })
    store.currentId = 'a'

    store.switchConversation('b')
    expect(store.currentNode).toBe('nB')

    await store.send('继续')
    // sendMessageStream(messages, callbacks, currentNode, kb, convId)
    const args = sendMessageStream.mock.calls.at(-1)
    expect(args[2]).toBe('nB')
  })

  it('A：收到 agent_done 后 loading=false，且正文已落盘', async () => {
    sendMessageStream.mockImplementation((msgs, cbs) => {
      cbs.onToken('你好')            // 流式写入正文
      cbs.onAgentDone({ rounds: 1 }) // 后端 loop 结束 → 立即收口
      return { abort: vi.fn() }
    })
    const store = useChatStore()

    await store.send('提问')

    expect(store.loading).toBe(false)
    const stored = JSON.parse(localStorage.getItem(CONV_KEY))
    const conv = stored.find(c => c.messages.some(m => m.role === 'assistant'))
    expect(conv).toBeTruthy()
    const assistant = conv.messages.find(m => m.role === 'assistant')
    expect(assistant.content).toBe('你好')   // 非空 → 刷新不丢正文
  })

  it('C-4：adoptRemote 识别 snake_case 的 updated_at（远程较新时覆盖本地）', async () => {
    vi.useFakeTimers()
    localStorage.setItem(CONV_KEY, JSON.stringify([
      { id: 'r1', title: '本地旧', messages: [{ role: 'user', content: 'old' }], createdAt: 1, updatedAt: 1 },
    ]))
    localStorage.setItem(CUR_KEY, 'r1')
    apiClient.post.mockResolvedValueOnce({
      data: {
        conversations: [{
          id: 'r1', title: '远程新',
          messages: [{ role: 'assistant', content: 'remote' }],
          created_at: 1, updated_at: Date.now() + 100000,
        }],
      },
    })

    const store = useChatStore()
    await store.init()                 // 本地有数据 → syncToBackend（500ms 防抖）
    await vi.advanceTimersByTimeAsync(500)

    const conv = store.conversations.find(c => c.id === 'r1')
    expect(conv.title).toBe('远程新')
    expect(conv.messages).toEqual([{ role: 'assistant', content: 'remote' }])
  })
})
