import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { sendMessage, apiClient } from '../api/index.js'

const STORAGE_KEY_CONVERSATIONS = 'ai_tutor_conversations'
const STORAGE_KEY_CURRENT = 'ai_tutor_current_id'
const STORAGE_KEY_MODE = 'ai_tutor_mode'

function generateId() {
  return Date.now().toString(36) + Math.random().toString(36).slice(2, 8)
}

function todayLabel(date) {
  const d = new Date(date)
  const now = new Date()
  const sameDay = d.toDateString() === now.toDateString()
  const yesterday = new Date(now)
  yesterday.setDate(yesterday.getDate() - 1)
  const sameYesterday = d.toDateString() === yesterday.toDateString()
  if (sameDay) return '今天'
  if (sameYesterday) return '昨天'
  return `${d.getMonth() + 1}/${d.getDate()}`
}

export const useChatStore = defineStore('chat', () => {
  // ─── 状态 ───
  const conversations = ref([])
  const currentId = ref(null)
  const mode = ref('scaffolding')
  const loading = ref(false)

  // ─── 知识图谱数据（一次性从后端加载）───
  const knowledgeNodes = ref([])
  const knowledgeEdges = ref([])
  const graphLoaded = ref(false)
  const graphError = ref('')

  async function fetchGraph() {
    if (graphLoaded.value) return
    try {
      const { data } = await apiClient.get('/api/v1/knowledge/graph')
      knowledgeNodes.value = (data.nodes || []).map(n => ({
        ...n,
        level: (n.tags || []).find(t => ['一级','二级','三级'].includes(t)) || '一级'
      }))
      knowledgeEdges.value = (data.edges || []).map(e => ({
        source: e.from,
        target: e.to,
        label: e.label
      }))
      graphLoaded.value = true
      graphError.value = ''
    } catch {
      graphError.value = 'ERROR'
    }
  }

  // ─── AI 编辑后刷新图谱 ───
  async function refreshGraph() {
    graphLoaded.value = false
    knowledgeNodes.value = []
    knowledgeEdges.value = []
    await fetchGraph()
  }

  // ─── 计算属性 ───
  const currentConversation = computed(() =>
    conversations.value.find((c) => c.id === currentId.value) || null
  )

  const currentMessages = computed(() => currentConversation.value?.messages ?? [])

  const currentTitle = computed(() => {
    if (!currentConversation.value) return '新对话'
    return currentConversation.value.title
  })

  const hasConversations = computed(() => conversations.value.length > 0)

  // ─── 分组历史（按日期） ───
  const groupedConversations = computed(() => {
    const groups = {}
    for (const c of conversations.value) {
      const label = todayLabel(c.createdAt)
      if (!groups[label]) groups[label] = []
      groups[label].push(c)
    }
    return groups
  })

  // ─── 初始化：从 localStorage 恢复 + 加载图谱 ───
  function init() {
    try {
      const saved = localStorage.getItem(STORAGE_KEY_CONVERSATIONS)
      if (saved) conversations.value = JSON.parse(saved)
      const cid = localStorage.getItem(STORAGE_KEY_CURRENT)
      if (cid && conversations.value.some((c) => c.id === cid)) {
        currentId.value = cid
      }
      const savedMode = localStorage.getItem(STORAGE_KEY_MODE)
      if (savedMode) mode.value = savedMode
    } catch {
      // ignore
    }
    // 一次性加载知识图谱
    fetchGraph()
    // 连接 SSE，后端数据变更时自动刷新图谱
    connectSSE()
  }

  // ─── SSE：监听后端数据变更，自动刷新图谱 ───
  let sseSource = null

  function connectSSE() {
    if (sseSource) return
    try {
      sseSource = new EventSource('/api/v1/knowledge/events')
      sseSource.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data)
          if (data.type === 'graph_updated') {
            refreshGraph()
          } else if (data.type === 'error') {
            // 后端模块错误事件：记录到控制台便于调试
            console.warn(`[SSE Error] ${data.code} | ${data.module}: ${data.message}`, data.detail || '')
          }
        } catch { /* ignore parse errors */ }
      }
      sseSource.onerror = () => {
        // 连接断开时自动重连（EventSource 自带重连机制）
        sseSource.close()
        sseSource = null
        // 5 秒后重试
        setTimeout(() => connectSSE(), 5000)
      }
    } catch {
      // SSE 不支持时静默失败
    }
  }

  // ─── 持久化 ───
  function persist() {
    localStorage.setItem(STORAGE_KEY_CONVERSATIONS, JSON.stringify(conversations.value))
    localStorage.setItem(STORAGE_KEY_CURRENT, currentId.value || '')
    localStorage.setItem(STORAGE_KEY_MODE, mode.value)
  }

  // ─── 新对话 ───
  function newConversation() {
    const conv = {
      id: generateId(),
      title: '新对话',
      messages: [],
      createdAt: Date.now(),
    }
    conversations.value.unshift(conv)
    currentId.value = conv.id
    persist()
    return conv
  }

  // ─── 切换对话 ───
  function switchConversation(id) {
    currentId.value = id
    persist()
  }

  // ─── 删除对话 ───
  function deleteConversation(id) {
    const idx = conversations.value.findIndex((c) => c.id === id)
    if (idx === -1) return
    conversations.value.splice(idx, 1)
    if (currentId.value === id) {
      currentId.value = conversations.value.length > 0 ? conversations.value[0].id : null
    }
    persist()
  }

  // ─── 设置模式 ───
  function setMode(newMode) {
    mode.value = newMode
    persist()
  }

  // ─── 发送消息 ───
  async function send(text) {
    if (!text.trim()) return

    // 如果没有对话则自动创建
    let conv = currentConversation.value
    if (!conv) {
      conv = conversations.value[0]
      if (!conv) conv = newConversation()
      currentId.value = conv.id
    }

    // 追加用户消息
    conv.messages.push({ role: 'user', content: text })

    // 如果是第一条消息，自动用前 20 字设定标题
    if (conv.messages.length === 1) {
      conv.title = text.length > 20 ? text.slice(0, 20) + '…' : text
    }

    loading.value = true
    persist()

    try {
      const res = await sendMessage(conv.messages, mode.value)
      const aiReply = res.data.reply
      conv.messages.push({ role: 'assistant', content: aiReply })
    } catch (err) {
      // ─── 区分错误类型，映射到不同的错误码 ───
      // E-COMM：前后端通信层（axios 超时、网络断连、HTTP 状态码）
      // E-LLM/等：后端模块层（后端返回的 body 中已包含错误码）
      let errorMsg = '❌ 请求失败，请确认后端已启动'

      if (err.response) {
        // 后端正常响应了，但 HTTP 状态码非 2xx
        // 优先使用后端返回体中的错误码（已由后端模块附加）
        const bodyDetail = err.response.data?.detail || ''
        if (bodyDetail && bodyDetail.startsWith('[E-')) {
          // 后端已经给了错误码（如 [E-LLM-001]），直接使用
          errorMsg = bodyDetail
        } else {
          // 后端没有给错误码，按 HTTP 状态码归类为通信层错误
          const status = err.response.status
          if (status === 502) {
            errorMsg = '[E-COMM-003] 后端网关错误 (502)，服务可能未就绪'
          } else if (status === 503) {
            errorMsg = '[E-COMM-004] 后端服务不可用 (503)，请稍后重试'
          } else if (status === 504) {
            errorMsg = '[E-COMM-005] 后端网关超时 (504)，AI调用可能仍在处理中'
          } else if (status >= 500) {
            errorMsg = `[E-COMM-006] 后端内部错误 (HTTP ${status})`
          } else {
            errorMsg = `[E-COMM-007] 后端返回异常 (HTTP ${status})${bodyDetail ? ': ' + bodyDetail : ''}`
          }
        }
      } else if (err.code === 'ECONNABORTED') {
        // axios 超时 → 前后端通信超时
        errorMsg = '[E-COMM-001] 请求超时，后端处理过慢或网络延迟'
      } else if (err.message === 'Network Error') {
        // 网络断连 → 后端可能没启动
        errorMsg = '[E-COMM-002] 无法连接到后端服务器，请确认后端已启动'
      } else {
        errorMsg = `[E-SYS-002] 未知异常: ${err.message || '未知错误'}`
      }
      conv.messages.push({ role: 'assistant', content: errorMsg })
    } finally {
      loading.value = false
      persist()
    }
  }

  return {
    conversations,
    currentId,
    mode,
    loading,
    knowledgeNodes,
    knowledgeEdges,
    graphLoaded,
    graphError,
    currentConversation,
    currentMessages,
    currentTitle,
    hasConversations,
    groupedConversations,
    init,
    fetchGraph,
    refreshGraph,
    newConversation,
    switchConversation,
    deleteConversation,
    setMode,
    send,
  }
})
