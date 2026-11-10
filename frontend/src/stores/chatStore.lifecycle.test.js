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
