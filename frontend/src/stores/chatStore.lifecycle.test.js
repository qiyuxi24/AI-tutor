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
    patch: vi.fn(() => Promise.resolve({ data: {} })),
    delete: vi.fn(() => Promise.resolve({ data: {} })),
  },
}))

import { ElMessage } from 'element-plus'

import { useChatStore, BACKEND_RELOADED_EVENT } from './chatStore.js'
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

describe('后端重载判定（TODO §2 P1：重连 ≠ 重载）', () => {
  /**
   * 让 /api/health 依次返回给定的 boot_id 序列（末值重复使用）。
   * 其余请求（pending_notices 等）给空响应。
   */
  function healthReturns(...bootIds) {
    let i = 0
    apiClient.get.mockImplementation((url) => {
      if (url !== '/api/health') return Promise.resolve({ data: {} })
      const boot = bootIds[Math.min(i++, bootIds.length - 1)]
      return Promise.resolve({ data: { status: 'ok', boot_id: boot } })
    })
  }

  function spyReloadEvent() {
    const spy = vi.fn()
    window.addEventListener(BACKEND_RELOADED_EVENT, spy)
    return spy
  }

  /** 走一遍「首连 → 断线 → 重连」，返回重载事件监听器。 */
  async function connectBreakReconnect() {
    localStorage.setItem('ai_tutor_token', 't')
    const onReload = spyReloadEvent()
    const store = useChatStore()
    await store.init()

    FakeEventSource.instances[0].onopen()      // 首连：只记 boot_id
    await vi.advanceTimersByTimeAsync(0)       // 冲掉 _checkBackendBoot 的微任务
    FakeEventSource.instances[0].onerror()     // 断线
    await vi.advanceTimersByTimeAsync(5000)    // 退避后重连
    FakeEventSource.instances[1].onopen()      // 重连成功
    await vi.advanceTimersByTimeAsync(0)
    return onReload
  }

  it('重连成功但 boot_id 没变 → 不发「后端已重载」（网络抖动不该刷页面）', async () => {
    vi.useFakeTimers()
    healthReturns('boot-A', 'boot-A')
    const onReload = await connectBreakReconnect()

    expect(FakeEventSource.instances).toHaveLength(2)
    expect(onReload).not.toHaveBeenCalled()
  })

  it('boot_id 真的变了（uvicorn --reload 换进程）→ 发「后端已重载」', async () => {
    vi.useFakeTimers()
    healthReturns('boot-A', 'boot-B')
    const onReload = await connectBreakReconnect()

    expect(onReload).toHaveBeenCalledTimes(1)
  })

  it('拿不到 boot_id（后端没升级 / 请求失败）→ 不发信号，宁可漏刷也不误报', async () => {
    vi.useFakeTimers()
    localStorage.setItem('ai_tutor_token', 't')
    apiClient.get.mockRejectedValue(new Error('nope'))
    const onReload = spyReloadEvent()
    const store = useChatStore()
    await store.init()

    FakeEventSource.instances[0].onopen()
    await vi.advanceTimersByTimeAsync(0)
    FakeEventSource.instances[0].onerror()
    await vi.advanceTimersByTimeAsync(5000)
    FakeEventSource.instances[1].onopen()
    await vi.advanceTimersByTimeAsync(0)

    expect(onReload).not.toHaveBeenCalled()
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

    // 流式节流落盘（P0-1 后已无"同步防抖 500ms"这个来源）：
    // 第一次 token 立即落盘，窗口内的第二次才挂 setTimeout。
    sendMessageStream.mockImplementation((_msgs, cb) => {
      cb?.onToken?.('半')
      cb?.onToken?.('句')
      return { abort: vi.fn() }
    })
    await store.send('你好')
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

  it('思考逐片下发 → 拼回一块（否则折叠面板里一行一个碎片）', async () => {
    sendMessageStream.mockImplementation((_msgs, cbs) => {
      // 后端 loop 的 on_reasoning 会按 chunk 推多帧 thinking（2026-11-15）
      cbs.onThinking('先看用户问的')
      cbs.onThinking('是二重积分，')
      cbs.onThinking('需要先画区域。')
      cbs.onAgentDone({ rounds: 1 })
      return { abort: vi.fn() }
    })
    const store = useChatStore()

    await store.send('这题怎么做')

    const conv = store.conversations.find(c => c.id === store.currentId)
    const assistant = conv.messages.find(m => m.role === 'assistant')
    expect(assistant.thinking).toEqual(['先看用户问的是二重积分，需要先画区域。'])
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
    await store.init()                 // init 内部直接对齐（P0-1 起无防抖）
    await vi.advanceTimersByTimeAsync(500)

    const conv = store.conversations.find(c => c.id === 'r1')
    expect(conv.title).toBe('远程新')
    expect(conv.messages).toEqual([{ role: 'assistant', content: 'remote' }])
  })

  it('P0-1/S3：服务端墓碑 → 本地副本必须被删掉（否则下次 sync 会把它推回去复活）', async () => {
    localStorage.setItem(CONV_KEY, JSON.stringify([
      { id: 'keep', title: '留', messages: [{ role: 'user', content: 'a' }], createdAt: 1, updatedAt: 1 },
      { id: 'gone', title: '删', messages: [{ role: 'user', content: 'b' }], createdAt: 2, updatedAt: 2 },
    ]))
    localStorage.setItem(CUR_KEY, 'gone')
    apiClient.post.mockResolvedValueOnce({
      data: {
        conversations: [
          { id: 'keep', title: '留', messages: [{ role: 'user', content: 'a' }], created_at: 1, updated_at: 1 },
        ],
        tombstones: ['gone'],
      },
    })

    const store = useChatStore()
    await store.init()

    expect(store.conversations.map(c => c.id)).toEqual(['keep'])
    expect(store.currentId).toBe('keep')      // 当前对话被删 → 落到剩下那条
    const stored = JSON.parse(localStorage.getItem(CONV_KEY))
    expect(stored.map(c => c.id)).toEqual(['keep'])
  })

  // ─── P0 时间戳单位错配（2026-11-05）：秒 vs 毫秒 ───
  // 后端 time.time() 是秒（~1.7e9），前端 Date.now() 是毫秒（~1.7e12）。
  // 旧实现把两者直接比较：采纳 `remoteAt > localAt` 恒假 → 客户端永不采纳服务端
  // 较新版本；上行又用毫秒 → 后端 `current > incoming` 恒假 → 旧缓存盖新历史。
  it('P0 单位错配：服务端秒级 updated_at 较新时必须被采纳，且上行换算成秒', async () => {
    const STALE_LOCAL_MS = 1_600_000_000_000   // 本地旧快照（2020，毫秒）
    const REMOTE_NEWER_SEC = 1_700_000_000     // 服务端较新版本（2023，秒）
    localStorage.setItem(CONV_KEY, JSON.stringify([
      { id: 'u1', title: '本地旧', messages: [{ role: 'user', content: 'old' }],
        createdAt: STALE_LOCAL_MS, updatedAt: STALE_LOCAL_MS },
    ]))
    localStorage.setItem(CUR_KEY, 'u1')
    apiClient.post.mockResolvedValueOnce({
      data: {
        conversations: [{
          id: 'u1', title: '远程新',
          messages: [{ role: 'assistant', content: 'remote' }],
          created_at: 1_600_000_000, updated_at: REMOTE_NEWER_SEC,
        }],
      },
    })

    const store = useChatStore()
    await store.init()

    // 采纳边界：远端秒还原成毫秒后才与本地（毫秒）可比 → 服务端较新版本覆盖旧快照
    const conv = store.conversations.find(c => c.id === 'u1')
    expect(conv.title).toBe('远程新')
    expect(conv.messages).toEqual([{ role: 'assistant', content: 'remote' }])
    expect(conv.updatedAt).toBe(REMOTE_NEWER_SEC * 1000)               // 秒还原成毫秒

    // 上行边界：本地毫秒必须换算成秒（后端单位是秒）
    const sent = apiClient.post.mock.calls.at(-1)[1].conversations
    expect(sent[0].updatedAt).toBe(Math.floor(STALE_LOCAL_MS / 1000))
    expect(sent[0].createdAt).toBe(Math.floor(STALE_LOCAL_MS / 1000))
  })

  it('P0 单位错配：采纳远端新对话时秒级 createdAt 还原成毫秒，分组不落 1970', async () => {
    const REMOTE_SEC = 1_700_000_000
    localStorage.setItem(CONV_KEY, JSON.stringify([]))
    apiClient.post.mockResolvedValueOnce({
      data: {
        conversations: [{
          id: 'remote-only', title: '服务端对话',
          messages: [{ role: 'user', content: 'hi' }],
          created_at: REMOTE_SEC, updated_at: REMOTE_SEC,
        }],
      },
    })

    const store = useChatStore()
    await store.init()

    const conv = store.conversations.find(c => c.id === 'remote-only')
    expect(conv).toBeTruthy()
    expect(conv.createdAt).toBe(REMOTE_SEC * 1000)   // 秒 → 毫秒
    const d = new Date(REMOTE_SEC * 1000)
    const label = `${d.getMonth() + 1}/${d.getDate()}`
    expect(Object.keys(store.groupedConversations)).toContain(label)  // 不是 1970
  })

  it('send 与 onAgentDone 为 user/assistant 消息写入毫秒级时间戳 ts 并落盘', async () => {
    // 用假时钟把「发送时刻」与「收口时刻」拉开 —— 否则两条 ts 都由 send 里的同一个
    // `now` 赋值，`assistant.ts >= user.ts` 恒真，删掉 onAgentDone 的补写仍然全绿
    // （空转）。这里断言 assistant.ts == 收口时刻，能真正锁住那次补写。
    vi.useFakeTimers()
    const SENT_AT = 1_700_000_000_000
    const DONE_AT = SENT_AT + 5_000          // 流了 5 秒
    vi.setSystemTime(SENT_AT)
    sendMessageStream.mockImplementation((_msgs, cbs) => {
      cbs.onToken('回答内容')
      vi.setSystemTime(DONE_AT)
      cbs.onAgentDone({ rounds: 1 })
      return { abort: vi.fn() }
    })
    const store = useChatStore()

    await store.send('你好')

    const stored = JSON.parse(localStorage.getItem(CONV_KEY))
    const conv = stored[0]
    expect(conv.messages).toHaveLength(2)
    // 毫秒级：send 时刻原值，不是 toSec 折成的秒级（1e9 量级）
    expect(conv.messages[0].ts).toBe(SENT_AT)
    // onAgentDone 把占位气泡的 ts 覆盖成收口时刻（这条是本次改动的真正意图）
    expect(conv.messages[1].ts).toBe(DONE_AT)
    expect(conv.messages[1].ts).toBeGreaterThan(conv.messages[0].ts)
  })
})

// ─────────────────────────────────────────────────────────────────────────────
// 建图完成必须由通知收口（2026-11-10）
//
// 现场：点「一键生成图谱」后，后端确实建完了（日志里逐概念成文跑完），前端却纹丝
// 不动，只能手动刷新。根因是旧实现把刷新挂在 **HTTP 响应之后** —— 而建图实测 15 分钟
// 量级，axios 与 Vite 代理都在 300s 超时；更常见的是用户等不下去切走了页面。
//
// 现在的约定（三层，缺一条就退回旧 bug）：
//   ① 请求超时/断线 = "结果还没到"，**不是失败**，不能弹错、不能清掉"生成中"；
//   ② 完成信号只认 graph_updated 通知（后端 finally 里 notify，先落库再推）；
//   ③ 该通知**不受 3 秒 CRUD 抑制** —— 否则用户在等待期间随手改点什么，
//      就会把"建图完成"这条吃掉，看起来又像"没刷新"。
// ─────────────────────────────────────────────────────────────────────────────
describe('建图结果由 graph_updated 通知收口（不靠 HTTP 响应）', () => {
  /** 计数某个 URL 的 GET —— 用作"刷新发生了"的可观测信号（stats 每次刷新必拉）。 */
  function countGets(url) {
    return apiClient.get.mock.calls.filter(([u]) => u === url).length
  }

  async function bootStore() {
    localStorage.setItem('ai_tutor_token', 't')
    const store = useChatStore()
    await store.init()
    return store
  }

  it('请求超时/断线：不弹失败、返回 null、仍保持"生成中"（等通知）', async () => {
    const store = await bootStore()
    const errSpy = vi.spyOn(ElMessage, 'error')
    apiClient.post.mockRejectedValueOnce(Object.assign(new Error('timeout of 300000ms exceeded'),
                                                        { code: 'ECONNABORTED' }))

    const ret = await store.generateSubjectGraph('数据结构', [1])

    expect(ret).toBeNull()                       // 没拿到明细，但不代表失败
    expect(errSpy).not.toHaveBeenCalled()        // 关键：不能把超时报成失败
    expect(store.graphGenerating).toBe('数据结构')  // 后端还在跑，按钮继续转
  })

  it('收到 ok 通知：结束生成中 + 刷新 + 报告新增量', async () => {
    const store = await bootStore()
    const okSpy = vi.spyOn(ElMessage, 'success')
    await store.generateSubjectGraph('数据结构', [1])
    expect(store.graphGenerating).toBe('数据结构')

    const before = countGets('/api/v1/knowledge/stats')
    FakeEventSource.instances[0].onmessage({
      data: JSON.stringify({
        type: 'graph_updated', subject: '数据结构', ok: true,
        created_nodes: 7, created_edges: 9, merged_nodes: 2,
        failed_docs: [], skipped_docs: [],
      }),
    })
    await vi.waitFor(() => expect(countGets('/api/v1/knowledge/stats')).toBeGreaterThan(before))

    expect(store.graphGenerating).toBe('')       // 生成中结束
    expect(okSpy).toHaveBeenCalledTimes(1)
    expect(okSpy.mock.calls[0][0]).toContain('7 个知识点')
    expect(store.currentSubject).toBe('数据结构')  // 切到刚建好的学科
  })

  it('收到 ok=false 通知：结束生成中 + 弹失败原因（不能让按钮永远转圈）', async () => {
    const store = await bootStore()
    const errSpy = vi.spyOn(ElMessage, 'error')
    await store.generateSubjectGraph('物理', [1])

    FakeEventSource.instances[0].onmessage({
      data: JSON.stringify({
        type: 'graph_updated', subject: '物理', ok: false,
        error: '没有可处理的书籍文本',
      }),
    })

    expect(store.graphGenerating).toBe('')
    expect(errSpy).toHaveBeenCalledTimes(1)
    expect(errSpy.mock.calls[0][0]).toContain('没有可处理的书籍文本')
  })

  it('未入图的资料要点名（GQ-25）：否则用户以为全建好了', async () => {
    const store = await bootStore()
    const warnSpy = vi.spyOn(ElMessage, 'warning')
    await store.generateSubjectGraph('数据结构', [1])

    FakeEventSource.instances[0].onmessage({
      data: JSON.stringify({
        type: 'graph_updated', subject: '数据结构', ok: true,
        created_nodes: 1, created_edges: 0, merged_nodes: 0,
        failed_docs: ['坏书.pdf'], skipped_docs: ['已建完.md'],
      }),
    })

    expect(warnSpy).toHaveBeenCalledTimes(1)
    const msg = warnSpy.mock.calls[0][0]
    expect(msg).toContain('2 份资料未入图')
    expect(msg).toContain('坏书.pdf')
  })

  it('建图完成通知**不受**3 秒 CRUD 抑制（用户等待期间随手改点东西也会被吃掉）', async () => {
    const store = await bootStore()
    await store.generateSubjectGraph('数据结构', [1])
    await store.refreshGraph(true)               // 置上 3 秒抑制窗口
    const before = countGets('/api/v1/knowledge/stats')

    FakeEventSource.instances[0].onmessage({
      data: JSON.stringify({
        type: 'graph_updated', subject: '数据结构', ok: true, created_nodes: 1,
      }),
    })
    await vi.waitFor(() => expect(countGets('/api/v1/knowledge/stats')).toBeGreaterThan(before))

    expect(store.graphGenerating).toBe('')
  })

  it('普通 graph_updated（无 ok 字段）在抑制窗口内仍然被跳过（旧行为不变）', async () => {
    const store = await bootStore()
    await store.refreshGraph(true)               // 置上 3 秒抑制窗口
    const before = countGets('/api/v1/knowledge/stats')

    FakeEventSource.instances[0].onmessage({
      data: JSON.stringify({ type: 'graph_updated' }),
    })
    await new Promise(r => setTimeout(r, 0))   // 让可能的异步链走完（真定时器）

    expect(countGets('/api/v1/knowledge/stats')).toBe(before)
  })
})

// ─────────────────────────────────────────────────────────────────────────────
// init 只走一次图：ensureSubjectSelected 已经做过的事不再补一遍（2026-11-16）
//
// 旧 init：`await ensureSubjectSelected()` 之后**无条件** `await fetchGraph()`。
// 首次进入时 ensureSubjectSelected 走 setSubject(names[0]) → 内部已 fetchGraph(true)，
// 于是 fetchGraph 在一次 init 里被调用两次。
//
// ⚠️ 实测澄清（别被"47.9KB 浪费"带偏）：**成功路径上旧代码也只发 1 个请求** ——
// 第二次 fetchGraph 被 `if (!force && graphLoaded.value) return` 守卫直接返回，
// 根本到不了网络。真正多发的只有**失败路径**：fetchGraph 抛错后 graphLoaded 仍是
// false，第二次调用照样进发请求 → 同一张图连打两次、第二次没有退避也没有意义。
//
// 不变量：任何**原本会拉图**的路径改完后仍然拉，且只拉一次。
// ─────────────────────────────────────────────────────────────────────────────
describe('init 加载图谱的请求数', () => {
  /** 让 stats 返回给定学科列表；graph 返回空图（或按需失败）。 */
  function mockBackend({ subjects = [], graphFails = false } = {}) {
    apiClient.get.mockImplementation((url) => {
      if (url === '/api/v1/knowledge/stats') {
        return Promise.resolve({
          data: { by_subject: subjects.map(subject => ({ subject, node_count: 1 })) },
        })
      }
      if (url === '/api/v1/knowledge/graph') {
        if (graphFails) return Promise.reject(new Error('graph down'))
        return Promise.resolve({ data: { nodes: [], edges: [] } })
      }
      return Promise.resolve({ data: {} })
    })
  }

  /** fetchGraph 发出的请求数（唯一可观测信号就是这个端点）。 */
  function graphGets() {
    return apiClient.get.mock.calls.filter(([u]) => u === '/api/v1/knowledge/graph')
  }

  it('首次 init（未选学科、有学科列表）→ 图请求正好 1 次，且带自动选中的学科', async () => {
    mockBackend({ subjects: ['数据结构', '操作系统'] })
    localStorage.setItem('ai_tutor_token', 't')
    const store = useChatStore()
    expect(store.currentSubject).toBeNull()

    await store.init()

    const calls = graphGets()
    expect(calls).toHaveLength(1)
    expect(calls[0][1].params.subject).toBe('数据结构')  // 自动选中第一个学科
    expect(store.currentSubject).toBe('数据结构')
    expect(store.graphLoaded).toBe(true)
  })

  it('已选学科且仍在列表里 → init 仍要拉图 1 次（不许因为这次改动漏拉）', async () => {
    mockBackend({ subjects: ['数据结构', '操作系统'] })
    localStorage.setItem('ai_tutor_token', 't')
    const store = useChatStore()
    store.currentSubject = '操作系统'                    // 上一轮已选、仍然有效

    await store.init()

    const calls = graphGets()
    expect(calls).toHaveLength(1)                        // ensureSubjectSelected 提前返回 → 由 init 负责
    expect(calls[0][1].params.subject).toBe('操作系统')
    expect(store.currentSubject).toBe('操作系统')        // 不被自动选中改写成第一个
  })

  it('学科列表为空（新用户）→ 仍然清空画布，且不发图请求', async () => {
    mockBackend({ subjects: [] })
    localStorage.setItem('ai_tutor_token', 't')
    const store = useChatStore()

    await store.init()

    expect(graphGets()).toHaveLength(0)                  // 无学科 = 不请求，空态
    expect(store.knowledgeNodes).toEqual([])
    expect(store.graphLoaded).toBe(true)
  })

  it('图请求失败 → 不在同一次 init 里立刻重打一遍（旧实现会发 2 次）', async () => {
    // 这是本次改动**唯一**真正省下的请求：成功路径本来就被 graphLoaded 守卫挡住了。
    mockBackend({ subjects: ['数据结构'], graphFails: true })
    localStorage.setItem('ai_tutor_token', 't')
    const store = useChatStore()

    await store.init()

    expect(graphGets()).toHaveLength(1)
    expect(store.graphError).toBeTruthy()                // 失败仍然可见，不静默
  })
})

// ─────────────────────────────────────────────────────────────────────────────
// refreshGraph 也只走一次图：复用 ensureSubjectSelected 的返回值（2026-11-16）
//
// 旧 refreshGraph：`await ensureSubjectSelected()` 之后**无条件** `await fetchGraph(true)`。
// 与 init 不同，这里传的是 force=true，**绕过 graphLoaded 守卫** —— 未选学科时
// ensureSubjectSelected → setSubject(names[0]) → fetchGraph(true) 已经真发过一次，
// 后面那次于是必然再发一次（实测 2 次，第二个约 47.9KB 纯浪费）。
//
// 但 refreshGraph 的定位是「后台数据变了，必须重拉」—— 收敛请求数**不能**把强制语义
// 一起收掉：当前学科本来就有效时 ensureSubjectSelected 返回 false 且什么都没做，
// 那次 force 重拉必须由 refreshGraph 自己补上（就算 graphLoaded 已经是 true）。
// ─────────────────────────────────────────────────────────────────────────────
describe('refreshGraph 加载图谱的请求数', () => {
  /** 让 stats 返回给定学科列表；graph 返回空图（或按需失败）。 */
  function mockBackend({ subjects = [], graphFails = false } = {}) {
    apiClient.get.mockImplementation((url) => {
      if (url === '/api/v1/knowledge/stats') {
        return Promise.resolve({
          data: { by_subject: subjects.map(subject => ({ subject, node_count: 1 })) },
        })
      }
      if (url === '/api/v1/knowledge/graph') {
        if (graphFails) return Promise.reject(new Error('graph down'))
        return Promise.resolve({ data: { nodes: [], edges: [] } })
      }
      return Promise.resolve({ data: {} })
    })
  }

  /** fetchGraph 发出的请求数（唯一可观测信号就是这个端点）。 */
  function graphGets() {
    return apiClient.get.mock.calls.filter(([u]) => u === '/api/v1/knowledge/graph')
  }

  it('未选学科、有学科列表 → refreshGraph 只发 1 次图请求（旧实现 2 次）', async () => {
    mockBackend({ subjects: ['数据结构', '操作系统'] })
    const store = useChatStore()
    expect(store.currentSubject).toBeNull()

    await store.refreshGraph()

    const calls = graphGets()
    expect(calls).toHaveLength(1)                        // 旧实现这里是 2
    expect(calls[0][1].params.subject).toBe('数据结构')   // 自动选中第一个学科
    expect(store.currentSubject).toBe('数据结构')
    expect(store.graphLoaded).toBe(true)
  })

  it('未选学科 + 图请求失败 → 仍只发 1 次（force 不再重打一遍）', async () => {
    mockBackend({ subjects: ['数据结构'], graphFails: true })
    const store = useChatStore()

    await store.refreshGraph()

    expect(graphGets()).toHaveLength(1)                  // 旧实现这里是 2
    expect(store.graphError).toBeTruthy()                // 失败仍然可见，不静默
  })

  it('当前学科仍有效 → refreshGraph 依旧强制拉，且每次刷新都拉 1 次', async () => {
    mockBackend({ subjects: ['数据结构', '操作系统'] })
    const store = useChatStore()
    store.currentSubject = '操作系统'                    // 上一轮已选、仍然有效

    await store.refreshGraph()

    // ensureSubjectSelected 提前返回（没动图）→ 由 refreshGraph 自己补这一次 force
    expect(graphGets()).toHaveLength(1)
    expect(graphGets()[0][1].params.subject).toBe('操作系统')
    expect(store.currentSubject).toBe('操作系统')        // 不被自动选中改写成第一个

    // graphLoaded 已是 true，refreshGraph 的 force 语义要求照样再拉一次
    await store.refreshGraph()
    expect(graphGets()).toHaveLength(2)
  })

  it('学科被删光（列表为空）→ 不发图请求，画布清空', async () => {
    mockBackend({ subjects: [] })
    const store = useChatStore()
    store.currentSubject = '数据结构'                    // 这个学科已经不存在了

    await store.refreshGraph()

    expect(graphGets()).toHaveLength(0)                  // 无学科 = 不请求，空态
    expect(store.currentSubject).toBeNull()
    expect(store.knowledgeNodes).toEqual([])
    expect(store.graphLoaded).toBe(true)
  })
})

describe('刷新后恢复「生成中」（TODO §11.3/§11.4-B）', () => {
  // 背景：建图已后台化（后端 `runtime.spawn`），刷新**不再取消**它。
  // 但前端 `graphGenerating` 是纯内存 ref —— 刷新后归零，按钮显示"一键生成图谱"，
  // 用户再点一次就撞 409「上一次建图尚未完成」，且不知道什么时候能好。
  // 故 init 时问一次后端 GET /kb/graph/status，把"还在跑"接回来。

  async function bootStore() {
    localStorage.setItem('ai_tutor_token', 't')
    const store = useChatStore()
    await store.init()
    return store
  }

  /** 让 status 端点返回指定 payload（其余 GET 走默认空对象）。 */
  function mockStatus(payload) {
    apiClient.get.mockImplementation((url) => {
      if (url === '/api/v1/kb/graph/status') return Promise.resolve({ data: payload })
      return Promise.resolve({ data: {} })
    })
  }

  it('后端仍在建图 → 恢复学科名，按钮继续转（避免再点一次撞 409）', async () => {
    mockStatus({ building: true, subject: '数据结构', progress: { total: 10, filled: 4, percent: 40 } })

    const store = await bootStore()

    expect(store.graphGenerating).toBe('数据结构')
  })

  it('后端已建完 → 保持空闲，不误显示"生成中"', async () => {
    mockStatus({ building: false, subject: '', progress: null })

    const store = await bootStore()

    expect(store.graphGenerating).toBe('')
  })

  it('status 查询失败 → 静默降级（不能把 init 整个挂掉）', async () => {
    apiClient.get.mockImplementation((url) => {
      if (url === '/api/v1/kb/graph/status') return Promise.reject(new Error('network'))
      return Promise.resolve({ data: {} })
    })

    const store = await bootStore()

    expect(store.graphGenerating).toBe('')
    expect(store.knowledgeNodes).toEqual([])   // init 的其余部分照常跑完
  })

  it('学科名缺失（老数据 / 刚占位）→ 仍算生成中，只是不带名字', async () => {
    mockStatus({ building: true, subject: '', progress: null })

    const store = await bootStore()

    // 不能是「生成中」：KbPanel 的按钮文案是 `生成中（${graphGenerating}）`，
    // 套上去会变成"生成中（生成中）"。
    expect(store.graphGenerating).toBe('上次任务')
  })
})

describe('建图进度轮询（TODO §11.4-B 剩余项：进度条）', () => {
  // 背景：`GET /kb/graph/status` 一直下发 progress{total,filled,percent}，
  // 但前端只在 init 问一次 —— 那只是个快照，建图是分钟级任务，用户要看到"还在动"。
  // 轮询**只更新进度数字**，完成判定仍以 graph_updated 通知为准（不越权改按钮状态）。

  async function bootStore() {
    localStorage.setItem('ai_tutor_token', 't')
    const store = useChatStore()
    await store.init()
    return store
  }

  function mockStatus(payload) {
    apiClient.get.mockImplementation((url) => {
      if (url === '/api/v1/kb/graph/status') return Promise.resolve({ data: payload })
      return Promise.resolve({ data: {} })
    })
  }

  it('建图进行中 → 恢复时就有进度，且后续轮询会刷新它', async () => {
    vi.useFakeTimers()
    mockStatus({ building: true, subject: '数据结构', progress: { total: 10, filled: 4, percent: 40 } })
    const store = await bootStore()

    expect(store.graphBuildProgress).toEqual({ total: 10, filled: 4, percent: 40 })

    // 后端继续推进 → 下一拍（3s）刷新
    mockStatus({ building: true, subject: '数据结构', progress: { total: 10, filled: 7, percent: 70 } })
    await vi.advanceTimersByTimeAsync(3000)

    expect(store.graphBuildProgress).toEqual({ total: 10, filled: 7, percent: 70 })
    store.dispose()
  })

  it('轮询发现建完 → 停表并清空进度，但不擅自清「生成中」', async () => {
    vi.useFakeTimers()
    mockStatus({ building: true, subject: '数据结构', progress: { total: 10, filled: 4, percent: 40 } })
    const store = await bootStore()

    mockStatus({ building: false, subject: '', progress: null })
    await vi.advanceTimersByTimeAsync(3000)

    expect(store.graphBuildProgress).toBeNull()
    // 按钮状态归 graph_updated 通知收口：轮询只负责进度，不抢这个职责
    expect(store.graphGenerating).toBe('数据结构')
    store.dispose()
  })

  it('dispose() 停掉轮询（不残留定时器）', async () => {
    vi.useFakeTimers()
    mockStatus({ building: true, subject: '数据结构', progress: { total: 3, filled: 1, percent: 33 } })
    const store = await bootStore()
    expect(vi.getTimerCount()).toBeGreaterThan(0)

    store.dispose()

    expect(vi.getTimerCount()).toBe(0)
  })

  // ── 画布跟着进度长（2026-11-16）：只轮询数字不动画布的话，整个过程画布是静止的，
  //    用户看到的只是"生成中"三个字，和卡死没区别。
  // 观测信号取 `/knowledge/stats`：`refreshGraph()` 必先拉学科列表（新学科要它才出现），
  // 比"图请求"更能代表"画布真的在跟着长"——首次建图时 currentSubject 还是 null，
  // 图请求本来就不会发。
  function statsGets() {
    return apiClient.get.mock.calls.filter(([u]) => u === '/api/v1/knowledge/stats').length
  }

  it('进度推进（total / filled 变化）→ 顺手重拉学科与画布', async () => {
    vi.useFakeTimers()
    mockStatus({ building: true, subject: '数据结构', progress: { total: 10, filled: 4, percent: 40 } })
    const store = await bootStore()
    const before = statsGets()

    mockStatus({ building: true, subject: '数据结构', progress: { total: 10, filled: 5, percent: 50 } })
    await vi.advanceTimersByTimeAsync(3000)

    expect(statsGets()).toBeGreaterThan(before)
    store.dispose()
  })

  it('数字没变 → 不重拉（3 秒一拍，多数拍子没有变化，不能无脑刷）', async () => {
    vi.useFakeTimers()
    mockStatus({ building: true, subject: '数据结构', progress: { total: 10, filled: 4, percent: 40 } })
    const store = await bootStore()
    const before = statsGets()

    await vi.advanceTimersByTimeAsync(9000)   // 三拍，payload 一模一样

    expect(statsGets()).toBe(before)
    store.dispose()
  })

  it('建的是别的学科 → 不动当前画布（别掀掉用户正在看的图）', async () => {
    vi.useFakeTimers()
    mockStatus({ building: true, subject: '数据结构', progress: { total: 10, filled: 4, percent: 40 } })
    const store = await bootStore()
    store.currentSubject = '线性代数'          // 用户看的是另一个学科
    const before = statsGets()

    mockStatus({ building: true, subject: '数据结构', progress: { total: 10, filled: 6, percent: 60 } })
    await vi.advanceTimersByTimeAsync(3000)

    expect(statsGets()).toBe(before)
    store.dispose()
  })
})

describe('回前台补偿（TODO §2 P1：切回来就补拉一次）', () => {
  /** stats 每次 refreshGraph 必拉 → 作为"刷新发生了"的可观测信号 */
  function statsGets() {
    return apiClient.get.mock.calls.filter(([u]) => u === '/api/v1/knowledge/stats').length
  }

  async function bootStore() {
    localStorage.setItem('ai_tutor_token', 't')
    const store = useChatStore()
    await store.init()
    return store
  }

  it('visibilitychange 且可见 → 补拉通知 + 刷新图谱', async () => {
    const store = await bootStore()
    const beforeStats = statsGets()
    const beforeNotices = apiClient.get.mock.calls
      .filter(([u]) => u === '/api/v1/knowledge/notices/pending').length

    document.dispatchEvent(new Event('visibilitychange'))
    await vi.waitFor(() => expect(statsGets()).toBeGreaterThan(beforeStats))

    const afterNotices = apiClient.get.mock.calls
      .filter(([u]) => u === '/api/v1/knowledge/notices/pending').length
    expect(afterNotices).toBeGreaterThan(beforeNotices)
    expect(store).toBeTruthy()
  })

  it('窗口 focus 也触发（切回浏览器时不一定发 visibilitychange）', async () => {
    await bootStore()
    const before = statsGets()

    window.dispatchEvent(new Event('focus'))
    await vi.waitFor(() => expect(statsGets()).toBeGreaterThan(before))
  })

  it('页面不可见时**不**拉（避免后台标签页反复请求）', async () => {
    await bootStore()
    const before = statsGets()
    const spy = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden')

    document.dispatchEvent(new Event('visibilitychange'))
    await new Promise(r => setTimeout(r, 10))

    expect(statsGets()).toBe(before)
    spy.mockRestore()
  })

  it('visibilitychange + focus 成对到达只补一次（2s 去重）', async () => {
    await bootStore()
    const before = statsGets()

    document.dispatchEvent(new Event('visibilitychange'))
    window.dispatchEvent(new Event('focus'))      // 紧接着的成对事件
    await new Promise(r => setTimeout(r, 20))

    expect(statsGets() - before).toBe(1)
  })

  it('dispose() 摘掉监听器：卸载后再切前台不再请求', async () => {
    const store = await bootStore()
    store.dispose()
    const before = statsGets()

    document.dispatchEvent(new Event('visibilitychange'))
    window.dispatchEvent(new Event('focus'))
    await new Promise(r => setTimeout(r, 20))

    expect(statsGets()).toBe(before)
  })

  it('重建 store 实例不会叠加监听器（旧的没 dispose 也只补一次）', async () => {
    // 现场：HMR 重挂 / 同一页建第二个 store 时，旧实例的监听器仍然挂在 document 上。
    // 若每个实例各挂一份，一次切前台就会 N 个实例同时拉 —— 去重窗口只能挡时间上的重复，
    // 挡不住"N 个实例各拉一次"。2026-11-10 就是这么被测试逮到的。
    await bootStore()
    await bootStore()                             // 第二个实例，第一个故意不 dispose
    const before = statsGets()

    document.dispatchEvent(new Event('visibilitychange'))
    await new Promise(r => setTimeout(r, 20))

    expect(statsGets() - before).toBe(1)
  })
})

// ─────────────────────────────────────────────────────────────────────────────
// init 的请求并发（2026-11-16）
//
// 旧实现是一条串行 await 链：会话同步 → 学科列表 → 图谱 → 建图状态 → 连 SSE。
// 但其中**三组互不依赖**，串行等纯属浪费首屏时间：
//   ① 会话组   syncWithServer → 恢复 currentId → 回填 currentNode（只碰 conversations/currentId）
//   ② 学科组   fetchSubjects → ensureSubjectSelected（可能内部 fetchGraph）
//   ③ 建图状态 restoreGraphBuildStatus（只碰 graphGenerating/graphBuildProgress）
//
// 不变量（下面逐条锁死）：
//   · 组**间**不等待：sync 还没回来时，stats / graph-status 就该已经发出（旧实现要等）；
//   · 组**内**顺序不变：②③ 是强依赖 —— subjectSummaries 没回来不许发图请求；
//   · 连上 SSE 仍必须是最后一步（连上就可能立刻收到事件，不能早于状态就位）；
//   · 任一请求失败时的降级行为与串行版一致（四个函数内部各自吞错）。
// ─────────────────────────────────────────────────────────────────────────────
describe('init 的请求并发（三组互不等待）', () => {
  /** 可手动落地的 promise：用来在"A 还没回"的瞬间观察 B 是否已经发出。 */
  function deferred() {
    let resolve, reject
    const promise = new Promise((res, rej) => { resolve = res; reject = rej })
    return { promise, resolve, reject }
  }

  /** 已发出的全部请求 URL（GET + POST，按发出顺序）。 */
  function startedUrls() {
    return [
      ...apiClient.get.mock.calls.map(([u]) => u),
      ...apiClient.post.mock.calls.map(([u]) => u),
    ]
  }

  /** 冲掉已排队的微任务（用真定时器，deferred 不落地也能跑完）。 */
  const flush = () => new Promise((r) => setTimeout(r, 0))

  /**
   * 三组的端点各自绑到一个 deferred（不传则立即 resolve）。
   * 其余端点一律给"空"响应，让 init 能走完全程。
   */
  function mockThreeGroups({ sync, stats, status } = {}) {
    apiClient.post.mockImplementation((url) => {
      if (url === '/api/v1/conversations/sync') {
        return sync ? sync.promise : Promise.resolve({ data: { conversations: [], tombstones: [] } })
      }
      return Promise.resolve({ data: {} })
    })
    apiClient.get.mockImplementation((url) => {
      if (url === '/api/v1/knowledge/stats') {
        return stats ? stats.promise : Promise.resolve({ data: { by_subject: [] } })
      }
      if (url === '/api/v1/kb/graph/status') {
        return status ? status.promise : Promise.resolve({ data: { building: false } })
      }
      if (url === '/api/v1/knowledge/graph') return Promise.resolve({ data: { nodes: [], edges: [] } })
      return Promise.resolve({ data: {} })
    })
  }

  it('会话同步还没回来 → 学科列表与建图状态的请求已经发出（组间不串行等）', async () => {
    const sync = deferred()
    const stats = deferred()
    const status = deferred()
    mockThreeGroups({ sync, stats, status })
    localStorage.setItem('ai_tutor_token', 't')

    const store = useChatStore()
    const inited = store.init()
    await flush()          // 串行实现此刻只发出 sync 一个请求

    const urls = startedUrls()
    expect(urls).toContain('/api/v1/knowledge/stats')   // ② 不等 ①
    expect(urls).toContain('/api/v1/kb/graph/status')   // ④ 不等 ①

    // 收尾：三组都落地，init 才能走到 connectSSE
    sync.resolve({ data: { conversations: [], tombstones: [] } })
    stats.resolve({ data: { by_subject: [] } })
    status.resolve({ data: { building: false } })
    await inited
    expect(FakeEventSource.instances).toHaveLength(1)
  })

  it('②③ 的强依赖不变：stats 未返回前不发图请求，且整次 init 只发一次', async () => {
    const stats = deferred()
    mockThreeGroups({ stats })
    localStorage.setItem('ai_tutor_token', 't')
    const store = useChatStore()

    const inited = store.init()
    await flush()
    // 学科列表还没回来 → ensureSubjectSelected 不能跑，图请求必须还没发
    expect(startedUrls()).not.toContain('/api/v1/knowledge/graph')

    stats.resolve({ data: { by_subject: [{ subject: '数据结构' }] } })
    await inited

    const graphCalls = apiClient.get.mock.calls.filter(([u]) => u === '/api/v1/knowledge/graph')
    expect(graphCalls).toHaveLength(1)
    expect(graphCalls[0][1].params.subject).toBe('数据结构')   // 自动选中第一个学科
    expect(store.currentSubject).toBe('数据结构')
  })

  it('connectSSE 仍在最后：三组都落地后才建连', async () => {
    const status = deferred()
    mockThreeGroups({ status })
    localStorage.setItem('ai_tutor_token', 't')
    const store = useChatStore()

    const inited = store.init()
    await flush()
    expect(FakeEventSource.instances).toHaveLength(0)   // 建图状态还没回 → 不连

    status.resolve({ data: { building: false } })
    await inited
    expect(FakeEventSource.instances).toHaveLength(1)
  })

  it('① 失败（离线/未登录）不拖垮其余组：学科与建图状态照常就位', async () => {
    mockThreeGroups({})
    apiClient.post.mockImplementation((url) => {
      if (url === '/api/v1/conversations/sync') return Promise.reject(new Error('offline'))
      return Promise.resolve({ data: {} })
    })
    apiClient.get.mockImplementation((url) => {
      if (url === '/api/v1/knowledge/stats') {
        return Promise.resolve({ data: { by_subject: [{ subject: '数据结构' }] } })
      }
      if (url === '/api/v1/kb/graph/status') {
        return Promise.resolve({ data: { building: true, subject: '操作系统', progress: null } })
      }
      if (url === '/api/v1/knowledge/graph') return Promise.resolve({ data: { nodes: [], edges: [] } })
      return Promise.resolve({ data: {} })
    })
    localStorage.setItem('ai_tutor_token', 't')
    const store = useChatStore()

    await expect(store.init()).resolves.toBeUndefined()   // syncWithServer 内部吞错 → 不抛
    expect(store.currentSubject).toBe('数据结构')
    expect(store.graphGenerating).toBe('操作系统')

    store.dispose()          // building=true 开了轮询，收尾别留定时器
  })

  it('② 失败（stats 挂了）→ 不抛错，仍清空画布，④ 照常恢复「生成中」', async () => {
    mockThreeGroups({})
    apiClient.get.mockImplementation((url) => {
      if (url === '/api/v1/knowledge/stats') return Promise.reject(new Error('stats down'))
      if (url === '/api/v1/kb/graph/status') {
        return Promise.resolve({ data: { building: true, subject: '数据结构', progress: null } })
      }
      return Promise.resolve({ data: {} })
    })
    localStorage.setItem('ai_tutor_token', 't')
    const store = useChatStore()

    await expect(store.init()).resolves.toBeUndefined()
    expect(store.knowledgeNodes).toEqual([])        // 无学科 → 空画布
    expect(store.graphGenerating).toBe('数据结构')    // 不被 stats 失败牵连

    store.dispose()
  })
})
