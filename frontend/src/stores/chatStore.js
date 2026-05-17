import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { sendMessage } from '../api/index.js'

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

  // ─── 知识图谱数据 ───
  const knowledgeNodes = ref([
    { id: 'recursion_def', name: '递归定义', tags: ['核心概念'], level: '一级' },
    { id: 'recursion_base_case', name: '递归终止条件', tags: ['核心概念'], level: '二级' },
    { id: 'recursion_formula', name: '递归递推公式', tags: ['数学'], level: '二级' }
  ])
  const knowledgeEdges = ref([
    { from: 'recursion_base_case', to: 'recursion_def', label: '前置依赖' },
    { from: 'recursion_formula', to: 'recursion_base_case', label: '前置依赖' }
  ])

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

  // ─── 初始化：从 localStorage 恢复 ───
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
    } catch {
      conv.messages.push({
        role: 'assistant',
        content: '❌ 请求失败，请确认后端已启动',
      })
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
    currentConversation,
    currentMessages,
    currentTitle,
    hasConversations,
    groupedConversations,
    init,
    newConversation,
    switchConversation,
    deleteConversation,
    setMode,
    send,
  }
})
