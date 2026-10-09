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
