import { defineStore } from 'pinia'
import { ref, reactive, computed } from 'vue'
import { sendMessage, sendMessageStream, apiClient } from '../api/index.js'

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

  // ─── 流式请求的 AbortController（用于取消） ───
  let streamController = null

  // ─── 发送消息（流式） ───
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

    // 添加占位 AI 消息（流式填充）
    conv.messages.push({ role: 'assistant', content: '' })
    loading.value = true
    persist()

    // 使用流式 API
    streamController = sendMessageStream(
      conv.messages.slice(0, -1), // 不含占位消息的对话历史
      mode.value,
      {
        // 每收到一个 token，追加到占位消息
        onToken: (token) => {
          const lastIdx = conv.messages.length - 1
          const lastMsg = conv.messages[lastIdx]
          if (lastMsg.role === 'assistant') {
            // 用 splice 替换元素强制触发 Vue 响应式更新
            conv.messages.splice(lastIdx, 1, {
              ...lastMsg,
              content: lastMsg.content + token,
            })
          }
        },
        // 流式完成
        onDone: (fullReply) => {
          loading.value = false
          // 如果流式没给任何内容，移除占位消息
          if (!fullReply) {
            conv.messages.pop()
          }
          persist()
        },
        // 出错
        onError: (errorMsg) => {
          loading.value = false
          // 移除占位消息，替换为错误消息
          conv.messages.pop()
          conv.messages.push({ role: 'assistant', content: errorMsg })
          persist()
        },
      }
    )
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
