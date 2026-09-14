/**
 * chatStore.js — 全局状态管理（对话 + 知识图谱）
 *
 * 架构原则：
 *   1. 单一数据源：图谱数据（knowledgeNodes / knowledgeEdges）仅在此 Store 中管理
 *   2. 组件通过 props 消费数据，通过 emit 触发操作
 *   3. 所有图谱 CRUD 操作统一走 Store 方法，组件不直接调用 apiClient
 *   4. SSE 实时更新 + 用户操作后 3 秒抑制机制，避免双重刷新
 *
 * 对话持久化策略（双写）：
 *   - localStorage 为主：每次变更立即写入 localStorage
 *   - 后端同步为辅：有内容的对话异步同步到后端
 *   - 恢复优先级：localStorage 有数据 → 直接用；localStorage 为空 → 从后端加载
 *   - 空对话（messages.length === 0）不持久化到 localStorage，也不同步到后端
 *
 * 数据流：
 *   Store.fetchGraph() → knowledgeNodes / knowledgeEdges
 *        ↓ (props)
 *   ForceGraph.vue（纯渲染 + 交互）
 *        ↓ (emit: graph-changed / graph-action)
 *   HomeView.vue（编排层）
 *        ↓ (调用 Store 方法)
 *   Store.xxxGraphAction() → apiClient → 后端 → publish("graph_updated")
 *        ↓                                           ↓
 *   refreshGraph() ←────────────────────── SSE / 轮询
 */

import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { sendMessageStream, apiClient } from '../api/index.js'
import { clientError } from '../utils/errorCodes.js'

// 按 user_id 隔离 localStorage，防止切换账号后对话历史泄露
const _uid = (() => {
  try {
    const u = JSON.parse(localStorage.getItem('ai_tutor_user') || 'null')
    return u?.id || 'guest'
  } catch { return 'guest' }
})()
const STORAGE_KEY_CONVERSATIONS = `ai_tutor_conversations_${_uid}`
const STORAGE_KEY_CURRENT = `ai_tutor_current_${_uid}`
const STORAGE_KEY_MODE = `ai_tutor_mode_${_uid}`

// 后端 graph_middleware.SUBJECT_UNCLASSIFIED 的对应值。
// 「未分类」= 无学科归属节点的合成分组名，不是真实学科（不出现在学科列表里，
// 但作为一个可选分组出现在 subjectSummaries 中）。
const UNCLASSIFIED_SUBJECT = '未分类'

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
  // ─── 对话状态 ───
  const conversations = ref([])
  const currentId = ref(null)
  const mode = ref('adaptive')
  const currentNode = ref('')  // 递归模式：当前教学知识点 ID
  const loading = ref(false)
  // 知识库上下文范围（用户选择放进对话上下文的文件/文件夹）
  const kbContext = ref(null)

  // ─── 知识图谱状态 ───
  const knowledgeNodes = ref([])
  const knowledgeEdges = ref([])
  const graphLoaded = ref(false)
  const graphError = ref('')
  // 学科维度：每个学科单独一张图。一次只渲染一个学科（按选中渲染），
  // 不再提供"全量"视图——全量会让图谱随学习不断生长、最终压垮画布与提示词。
  const subjects = ref([])           // 真实学科名列表（不含「未分类」）
  const subjectSummaries = ref([])   // 学科 + 分量统计 [{subject, node_count, mastered_count, mastery_avg}]
  const currentSubject = ref(null)   // 当前选中学科；null = 未选（画布空态，不拉全量）
  // 知识板块维度：学科之下的一级分组，currentBoard=null 表示查看整学科
  const boards = ref([])           // 当前学科的板块列表 [{board, node_count, ...}]
  const currentBoard = ref(null)   // 当前查看的板块名；null = 整学科

  // 学习进度维度：科技树联动数据（拓扑排序路径 + 下一步推荐）
  const learningPath = ref([])     // 按学习顺序排列的节点 [{id, name, mastery, difficulty, ...}]
  const nextToLearn = ref(null)    // 下一步推荐节点 {node_id, name, mastery, reason}

  // 学习进度统计（仪表盘）：聚合自图谱 mastery，单一数据源
  const stats = ref(null)          // {overall, by_subject, weak_points, next_to_learn, total_nodes}
  const statsLoading = ref(false)

  // CRUD 操作后 3 秒内忽略 SSE 的 graph_updated 事件，避免双重刷新
  let sseSuppressTimer = null

  // ─── 后端同步防抖 ───
  // 避免频繁调用同步 API，在 persist 后延迟 500ms 合并一次同步
  let syncDebounceTimer = null
  let syncPending = false

  /**
   * 将当前有内容的对话同步到后端（防抖 500ms）
   * 只同步 messages.length > 0 的对话，空对话不上传
   */
  function syncToBackend() {
    if (syncDebounceTimer) clearTimeout(syncDebounceTimer)
    syncDebounceTimer = setTimeout(async () => {
      if (syncPending) return
      syncPending = true
      try {
        const nonEmpty = conversations.value
          .filter(c => c.messages.length > 0)
          .map(c => ({
            id: c.id,
            title: c.title,
            messages: c.messages,
            createdAt: c.createdAt,
          }))
        // 使用 sync 端点全量同步（后端合并）
        await apiClient.post('/api/v1/conversations/sync', {
          conversations: nonEmpty,
        })
      } catch {
        // 同步失败不影响本地使用，静默忽略
      } finally {
        syncPending = false
      }
    }, 500)
  }

  /**
   * 从后端加载对话列表（当 localStorage 为空时调用）
   * 加载后写入 localStorage，后续以 localStorage 为准
   */
  async function loadFromBackend() {
    try {
      const { data } = await apiClient.get('/api/v1/conversations')
      if (data.conversations && data.conversations.length > 0) {
        // 获取摘要列表后，按需加载每个对话的完整消息
        // 为了减少请求数，直接用 sync 端点一次性拉取
        const syncResp = await apiClient.post('/api/v1/conversations/sync', {
          conversations: [],
        })
        if (syncResp.data.conversations && syncResp.data.conversations.length > 0) {
          conversations.value = syncResp.data.conversations.map(c => ({
            id: c.id,
            title: c.title,
            messages: c.messages || [],
            createdAt: c.createdAt || c.created_at || Date.now(),
          }))
          // 写入 localStorage
          persist()
          return true
        }
      }
      return false
    } catch {
      return false
    }
  }

  /**
   * 删除后端的对话
   * @param {string} convId
   */
  async function deleteFromBackend(convId) {
    try {
      await apiClient.delete(`/api/v1/conversations/${encodeURIComponent(convId)}`)
    } catch {
      // 删除失败不影响本地
    }
  }

  // ════════════════════════════════════════════════════════════════
  //  图谱数据获取
  // ════════════════════════════════════════════════════════════════

  /**
   * 从后端按需获取图谱数据，统一做字段映射。
   * 请求粒度由当前学科/板块状态决定（middleware 按需切片）：
   *   - 未选学科   → 清空画布（不再拉全量，见下方注释）
   *   - 仅学科     → 整学科图
   *   - 学科+板块  → 板块局部子图
   *
   * 边的 source/target 在后端可能是 from_node/to_node，统一转为 source/target。
   *
   * @param {boolean} force - 强制刷新（忽略 graphLoaded 守卫）
   */
  async function fetchGraph(force = false) {
    if (!force && graphLoaded.value) return
    // 一次只渲染一个学科：未选中学科时不请求全量图（避免图谱无限生长），直接清空画布。
    // 选中动作由 ensureSubjectSelected() 在学科列表就绪后自动完成。
    if (!currentSubject.value) {
      knowledgeNodes.value = []
      knowledgeEdges.value = []
      boards.value = []
      graphError.value = ''
      graphLoaded.value = true
      return
    }
    try {
      const params = { subject: currentSubject.value }
      // 板块按需切片：仅当指定了板块才传 board
      if (currentBoard.value) params.board = currentBoard.value
      const { data } = await apiClient.get('/api/v1/knowledge/graph', { params })
      knowledgeNodes.value = (data.nodes || []).map(n => ({
        ...n,
        level: (n.tags || []).find(t => ['一级','二级','三级'].includes(t)) || '一级'
      }))
      // ★ 统一字段映射：后端字段 from_node/to_node → 前端字段 source/target
      // ForceGraph 的 renderGraph 会再次做兼容映射，确保即使 Store 未映射也不会出错
      knowledgeEdges.value = (data.edges || []).map((e, i) => ({
        ...e,
        source: e.from_node || e.from,
        target: e.to_node || e.to,
        label: e.label || '',
        relation: e.relation || '',
        edgeId: e.id,     // 数据库主键 ID（用于精确的 updateEdge/deleteEdge 操作）
      }))
      graphLoaded.value = true
      graphError.value = ''
      // 图谱变更 → 学习路径/下一步推荐/统计随之刷新（单一数据源 = 图谱）
      fetchLearningPath()
      fetchNextToLearn()
      fetchStats(currentSubject.value)
    } catch (e) {
      graphError.value = clientError('GRAPH_LOAD')
    }
  }

  /**
   * 获取按学习顺序排列的拓扑路径（后端 Kahn 算法，mastery<50 优先）。
   * 用于图谱"显示学习路径"高亮 + 仪表盘"下一步学什么"。
   */
  async function fetchLearningPath() {
    try {
      const { data } = await apiClient.get('/api/v1/knowledge/learning-path')
      learningPath.value = data.nodes_detail && data.nodes_detail.length
        ? data.nodes_detail
        : (data.ordered_nodes || []).map(id => ({ id }))
    } catch {
      // 学习路径失败不影响图谱使用，静默降级
      learningPath.value = []
    }
  }

  /**
   * 获取当前最应该学习的下一个知识点（后端拓扑排序 + 掌握度推荐）。
   */
  async function fetchNextToLearn() {
    try {
      const { data } = await apiClient.get('/api/v1/knowledge/next-to-learn')
      nextToLearn.value = data
    } catch {
      nextToLearn.value = null
    }
  }

  /**
   * 获取学习进度聚合统计（仪表盘数据源）。
   * 数据从图谱 mastery 聚合而来；图谱变更后应调用此方法刷新仪表盘。
   * @param {string|null} subject - 学科名；null = 全局统计
   */
  async function fetchStats(subject = null) {
    statsLoading.value = true
    try {
      const { data } = await apiClient.get('/api/v1/knowledge/stats', {
        params: subject ? { subject } : {},
      })
      stats.value = data
    } catch {
      stats.value = null
    } finally {
      statsLoading.value = false
    }
  }

  /**
   * 获取学科列表 + 每个学科的分量统计（学科收藏栏数据源）。
   *
   * 单请求来源：/knowledge/stats 的 by_subject 已含全部学科及聚合，
   * 与 /knowledge/subjects 同源（graph_middleware.compute_stats），
   * 因此不再另打一次 subjects 接口。
   *
   * by_subject 可能含「未分类」（无学科归属节点的合成项）：保留在
   * subjectSummaries 供收藏栏渲染，但从 subjects 剔除——subjects 的契约是
   * "真实学科名列表"，DashboardView 等下拉框直接消费。
   */
  async function fetchSubjects() {
    try {
      const { data } = await apiClient.get('/api/v1/knowledge/stats')
      // unclassified 标记「未分类」合成项，供收藏栏做差异化渲染（无需组件再认字符串）
      subjectSummaries.value = (data.by_subject || []).map(s => ({
        ...s,
        unclassified: s.subject === UNCLASSIFIED_SUBJECT,
      }))
      subjects.value = subjectSummaries.value
        .map(s => s.subject)
        .filter(s => s && s !== UNCLASSIFIED_SUBJECT)
    } catch {
      // 静默失败
    }
  }

  /**
   * 保证「当前选中学科」始终有效——一次只渲染一个学科，不允许停在全量视图。
   *
   * 三种情况：
   *   - 已选且仍存在            → 不动
   *   - 已选但已消失（学科被删或改名）→ 回退到第一个学科
   *   - 未选（首次进入/图从空变非空）→ 自动选中第一个学科
   * 学科列表为空（新用户、图空）→ 保持 null，画布空态由 fetchGraph 处理。
   */
  async function ensureSubjectSelected() {
    const names = subjectSummaries.value.map(s => s.subject)
    if (currentSubject.value && names.includes(currentSubject.value)) return
    currentSubject.value = null   // 置空，避免 setSubject 因"值未变"提前返回
    if (names.length) {
      await setSubject(names[0])
    } else {
      await fetchGraph(true)      // 无学科 → 清空画布
    }
  }

  /**
   * 按需获取指定学科下的知识板块列表（含各板块节点数/掌握度统计）。
   * @param {string} subject - 学科名
   */
  async function fetchBoards(subject) {
    if (!subject) {
      boards.value = []
      return
    }
    try {
      const { data } = await apiClient.get('/api/v1/knowledge/boards', { params: { subject } })
      boards.value = data.boards || []
    } catch {
      boards.value = []
    }
  }

  /**
   * 切换当前查看的学科（每个学科单独一张图），并重置板块到"整学科"。
   * @param {string|null} subject - 学科名（含「未分类」）；null = 未选中（画布空态）
   */
  async function setSubject(subject) {
    if (currentSubject.value === subject) return
    currentSubject.value = subject || null
    currentBoard.value = null            // 切换学科后回到整学科视图
    graphLoaded.value = false
    await fetchBoards(subject || null)   // 按需加载板块列表（学科导航用）
    await fetchGraph(true)
  }

  /**
   * 切换当前查看的知识板块（按需请求该板块局部子图，middleware 切片）。
   * @param {string|null} board - 板块名；null 表示整学科
   */
  async function setBoard(board) {
    if (currentBoard.value === board) return
    currentBoard.value = board || null
    graphLoaded.value = false
    await fetchGraph(true)
  }

  /**
   * 从学科书籍生成知识图谱（AI 直接写库），生成后刷新图谱。
   *
   * @param {string} subject - 学科名（如 '数据结构'）
   * @param {number[]} nodeIds - KB 中的文件/文件夹节点 ID 列表
   * @param {'subject'|'section'} mode - 生成模式
   * @returns {Promise<Object>} 生成结果（created_nodes 等）
   */
  async function generateSubjectGraph(subject, nodeIds, mode = 'subject') {
    const { data } = await apiClient.post('/api/v1/kb/graph/generate', {
      subject,
      mode,
      node_ids: nodeIds,
    }, { timeout: 300000 })  // 生成可能较慢
    await fetchSubjects()
    // 生成后自动切换到该学科视图
    currentSubject.value = subject
    currentBoard.value = null
    graphLoaded.value = false
    await fetchBoards(subject)   // 刷新板块列表（生成后节点可能带板块）
    await fetchGraph(true)
    return data
  }

  /**
   * 刷新图谱数据。
   * 强制重新 fetch，如果是用户操作触发的刷新则抑制 SSE 3 秒避免双重刷新。
   *
   * @param {boolean} fromUserAction - 是否由用户操作（CRUD）触发
   */
  async function refreshGraph(fromUserAction = false) {
    // 学科列表与分量统计可能因 CRUD 变化（新增学科、节点数变动）→ 刷新收藏栏
    await fetchSubjects()
    // 图从空变非空（如对话中新建首个节点）时自动选中学科，否则会停在空态
    await ensureSubjectSelected()
    graphLoaded.value = false
    // 板块计数可能因 CRUD 变化，一并刷新（仅当已选学科时）
    if (currentSubject.value) await fetchBoards(currentSubject.value)
    await fetchGraph(true)
    if (fromUserAction) {
      suppressSSE()
    }
  }

  /**
   * 获取单个节点详情（供 HomeView 双击节点时调用）
   * @param {string} nodeId
   * @returns {Promise<Object>} 节点详情对象
   */
  async function fetchNodeDetail(nodeId) {
    const { data } = await apiClient.get(`/api/v1/knowledge/node/${nodeId}`)
    return data
  }

  // ════════════════════════════════════════════════════════════════
  //  图谱 CRUD 操作（统一入口，组件不应直接调用 apiClient）
  // ════════════════════════════════════════════════════════════════

  /**
   * 创建新节点
   * @param {{ name: string, tags: string[], content?: string }} payload
   * @returns {Promise<Object>} API 响应
   */
  async function createNode(payload) {
    const { data } = await apiClient.post('/api/v1/knowledge/node', payload)
    await refreshGraph(true)
    return data
  }

  /**
   * 更新节点基本信息（名称、标签）
   * @param {string} nodeId
   * @param {{ name?: string, tags?: string[] }} payload
   */
  async function updateNodeInfo(nodeId, payload) {
    await apiClient.put(`/api/v1/knowledge/node/${encodeURIComponent(nodeId)}/info`, payload)
    await refreshGraph(true)
  }

  /**
   * 更新节点内容（Markdown）
   * @param {string} nodeId
   * @param {{ content: string }} payload
   */
  async function updateNodeContent(nodeId, payload) {
    await apiClient.put(`/api/v1/knowledge/node/${nodeId}`, payload)
    await refreshGraph(true)
  }

  /**
   * 更新节点掌握度
   * @param {string} nodeId
   * @param {number} mastery 0-100
   */
  async function updateMastery(nodeId, mastery) {
    await apiClient.put(`/api/v1/knowledge/node/${encodeURIComponent(nodeId)}/mastery`, { mastery })
    await refreshGraph(true)
  }

  /**
   * 删除节点及其所有关联边
   * @param {string} nodeId
   */
  async function deleteNode(nodeId) {
    await apiClient.delete(`/api/v1/knowledge/node/${encodeURIComponent(nodeId)}`)
    await refreshGraph(true)
  }

  /**
   * 创建边
   * @param {{ from: string, to: string, relation?: string, label?: string }} payload
   * @returns {Promise<Object>} API 响应
   */
  async function createEdge(payload) {
    const { data } = await apiClient.post('/api/v1/knowledge/edge', payload)
    await refreshGraph(true)
    return data
  }

  /**
   * 更新边信息（使用数据库 ID，避免索引竞态）
   * @param {number} edgeId - 边的数据库主键 ID
   * @param {{ relation?: string, label?: string }} payload
   */
  async function updateEdge(edgeId, payload) {
    await apiClient.put(`/api/v1/knowledge/edge/${edgeId}`, payload)
    await refreshGraph(true)
  }

  /**
   * 删除边（使用数据库 ID，避免索引竞态）
   * @param {number} edgeId - 边的数据库主键 ID
   */
  async function deleteEdge(edgeId) {
    await apiClient.delete(`/api/v1/knowledge/edge/${edgeId}`)
    await refreshGraph(true)
  }

  // ════════════════════════════════════════════════════════════════
  //  SSE 实时推送
  // ════════════════════════════════════════════════════════════════

  /** 抑制 SSE：CRUD 操作后调用，3 秒内忽略 SSE 事件 */
  function suppressSSE() {
    if (sseSuppressTimer) clearTimeout(sseSuppressTimer)
    sseSuppressTimer = setTimeout(() => { sseSuppressTimer = null }, 3000)
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

  const hasConversations = computed(() =>
    conversations.value.some(c => c.messages.length > 0)
  )

  // ─── 分组历史（按日期）───
  // 只展示有内容的对话，空对话不出现在历史列表中
  const groupedConversations = computed(() => {
    const groups = {}
    for (const c of conversations.value) {
      // 跳过空对话（防御性过滤，正常情况 persist 已过滤）
      if (c.messages.length === 0) continue
      const label = todayLabel(c.createdAt)
      if (!groups[label]) groups[label] = []
      groups[label].push(c)
    }
    return groups
  })

  // ─── 初始化：从 localStorage 恢复（优先），否则从后端加载 ───
  async function init() {
    try {
      const saved = localStorage.getItem(STORAGE_KEY_CONVERSATIONS)
      if (saved) {
        conversations.value = JSON.parse(saved)
        // 过滤掉可能残留的空对话（防御性编程）
        conversations.value = conversations.value.filter(c => c.messages.length > 0)
      }

      // localStorage 有数据 → 直接恢复当前对话
      if (conversations.value.length > 0) {
        const cid = localStorage.getItem(STORAGE_KEY_CURRENT)
        if (cid && conversations.value.some((c) => c.id === cid)) {
          currentId.value = cid
        }
        // 有数据时也同步到后端（双向合并）
        syncToBackend()
      } else {
        // localStorage 为空 → 尝试从后端加载
        const loaded = await loadFromBackend()
        if (loaded && conversations.value.length > 0) {
          // 后端有数据，取最新的一条作为当前对话
          currentId.value = conversations.value[0].id
        }
      }

      // 如果没有当前对话（本地和后端都没有数据），自动创建新对话
      if (!currentId.value) {
        const conv = {
          id: generateId(),
          title: '新对话',
          messages: [],
          createdAt: Date.now(),
        }
        conversations.value.unshift(conv)
        currentId.value = conv.id
      }
    } catch {
      // ignore
    }
    // 一次性加载知识图谱：先取学科列表并自动选中一个
    // （一次只渲染一个学科，不再默认拉全量）
    await fetchSubjects()
    await ensureSubjectSelected()
    await fetchGraph()
    // 连接 SSE，后端数据变更时自动刷新图谱
    connectSSE()
  }

  // ─── SSE：监听后端数据变更，自动刷新图谱 ───
  let sseSource = null
  let sseReconnectTimer = null

  function connectSSE() {
    // 清理旧连接和重连定时器
    disconnectSSE()
    // 必须已登录才连接 SSE（后端强制认证）
    const token = localStorage.getItem('ai_tutor_token')
    if (!token) return
    try {
      const url = `/api/v1/knowledge/events?token=${encodeURIComponent(token)}`
      sseSource = new EventSource(url)
      sseSource.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data)
          if (data.type === 'graph_updated') {
            // 如果刚刚执行过 CRUD 操作（3 秒内），忽略此次 SSE 事件
            if (sseSuppressTimer) return
            refreshGraph()
          } else if (data.type === 'quiz_ready') {
            // 对话内出题完成（后台任务，约 40s 后到达）→ 追加一条题目消息
            handleQuizReady(data)
          } else if (data.type === 'error') {
            console.warn(`[SSE Error] ${data.code} | ${data.module}: ${data.message}`, data.detail || '')
          }
        } catch { /* ignore parse errors */ }
      }
      sseSource.onerror = () => {
        sseSource.close()
        sseSource = null
        // 5 秒后重连（保存引用以便取消）
        sseReconnectTimer = setTimeout(() => connectSSE(), 5000)
      }
    } catch {
      // SSE 不支持时静默失败
    }
  }

  /**
   * 对话内出题完成（后台异步，约 40s 后到达）→ 往当前对话追加一条题目消息。
   *
   * 为什么走这条常驻连接而不是对话 SSE：出题是后台任务，对话的流式响应早已结束，
   * 40 秒后才有结果，只能由 /knowledge/events 这条长连接送达（见后端 quiz_ready 事件）。
   */
  function handleQuizReady(data) {
    const conv = currentConversation.value
    if (!conv) return
    if (!data.ok) {
      // 不能让 AI 说的"稍等片刻"变成永远没有下文，失败也要给个交代
      conv.messages.push({
        role: 'assistant',
        content: `（出题没能完成：${data.message || '请稍后再试'}）`,
        thinking: [], tools: [],
      })
      persist()
      return
    }
    conv.messages.push({
      role: 'assistant',
      content: formatQuizMessage(data),
      thinking: [], tools: [],
      quiz: data.questions || [],   // 留字段：P1 换成可点选项卡片时直接用
    })
    persist()
  }

  /** 把推送来的题目渲染成 markdown（P0 先用纯文本，P1 再换可点卡片） */
  function formatQuizMessage(data) {
    const lines = [`**来，检验一下刚才学的「${data.subject || '这个知识点'}」**`, '']
    for (const q of data.questions || []) {
      lines.push(q.question)
      if (q.options?.length) {
        lines.push('')
        for (const o of q.options) lines.push(`- **${o.value}.** ${o.label}`)
      }
      lines.push('')
    }
    lines.push('> 直接回复你的答案就行（比如 `A`），我来判分。')
    return lines.join('\n')
  }

  /** 断开 SSE 并取消重连定时器 */
  function disconnectSSE() {
    if (sseReconnectTimer) {
      clearTimeout(sseReconnectTimer)
      sseReconnectTimer = null
    }
    if (sseSource) {
      sseSource.close()
      sseSource = null
    }
  }

  // ─── 持久化 ───
  // 核心规则：只有有内容的对话才写入 localStorage + 同步到后端
  // 空对话（messages.length === 0）不存储
  function persist() {
    // 过滤掉空对话，只有有内容的对话才持久化
    const nonEmpty = conversations.value.filter(c => c.messages.length > 0)
    localStorage.setItem(STORAGE_KEY_CONVERSATIONS, JSON.stringify(nonEmpty))
    // 如果当前对话是空对话，不存储 currentId（下次加载时会自动创建新对话）
    const curConv = conversations.value.find(c => c.id === currentId.value)
    if (curConv && curConv.messages.length > 0) {
      localStorage.setItem(STORAGE_KEY_CURRENT, currentId.value)
    } else {
      localStorage.setItem(STORAGE_KEY_CURRENT, '')
    }
    localStorage.setItem(STORAGE_KEY_MODE, mode.value)

    // 双写：同步到后端（防抖，避免频繁请求）
    syncToBackend()
  }

  // ─── 当前对话是否为空（无任何消息） ───
  const isCurrentEmpty = computed(() => {
    const c = currentConversation.value
    return c ? c.messages.length === 0 : true
  })

  // ─── 新对话 ───
  // 核心规则：
  //   1. 只有有内容的对话才存储到 localStorage
  //   2. 新建对话的前提：当前对话不是空对话（已有内容）
  //   3. 创建新对话时，自动删除上一个空对话（如果存在）
  //   4. 空对话不持久化——persist() 内部会过滤掉空对话
  function newConversation() {
    // 规则 2：如果当前对话已经是空的新对话，不创建
    if (currentConversation.value && currentConversation.value.messages.length === 0) {
      return currentConversation.value
    }

    // 清理所有残留的空对话（切换对话时可能留下，或异常情况）
    conversations.value = conversations.value.filter(c => c.messages.length > 0)

    const conv = {
      id: generateId(),
      title: '新对话',
      messages: [],
      createdAt: Date.now(),
    }
    conversations.value.unshift(conv)
    currentId.value = conv.id
    // 注意：空对话不持久化！persist() 会过滤 messages.length === 0 的对话
    persist()
    return conv
  }

  // ─── 切换对话 ───
  // 规则：如果当前对话是空对话，切换时自动删除它
  function switchConversation(id) {
    // 如果切换目标就是当前对话，不做任何事
    if (currentId.value === id) return

    // 当前对话为空 → 删除它（不留空对话在列表中）
    if (currentConversation.value && currentConversation.value.messages.length === 0) {
      const curIdx = conversations.value.findIndex(c => c.id === currentId.value)
      if (curIdx !== -1) conversations.value.splice(curIdx, 1)
    }

    currentId.value = id
    persist()
  }

  // ─── 删除对话 ───
  function deleteConversation(id) {
    const idx = conversations.value.findIndex((c) => c.id === id)
    if (idx === -1) return
    conversations.value.splice(idx, 1)
    if (currentId.value === id) {
      // 如果删除的是当前对话，自动创建新对话
      const conv = {
        id: generateId(),
        title: '新对话',
        messages: [],
        createdAt: Date.now(),
      }
      conversations.value.unshift(conv)
      currentId.value = conv.id
    }
    persist()
    // 同步删除后端数据
    deleteFromBackend(id)
  }

  // ─── 设置模式 ───
  function setMode(newMode) {
    mode.value = newMode
    persist()
  }

  // ─── 设置知识库上下文范围 ───
  function setKbContext(ctx) {
    kbContext.value = ctx // ctx: { nodeIds: [], name: string } 或 null
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
    // 此时对话从"空"变为"有内容"，需要持久化
    if (conv.messages.length === 1) {
      conv.title = text.length > 20 ? text.slice(0, 20) + '…' : text
    }

    // 添加占位 AI 消息（流式填充 + 工具/思考事件挂载）
    conv.messages.push({ role: 'assistant', content: '', tools: [], thinking: [] })
    loading.value = true
    // 对话已有内容，持久化（persist 内部会过滤空对话，此对话现在不会被过滤）
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
            conv.messages.splice(lastIdx, 1, {
              ...lastMsg,
              content: lastMsg.content + token,
            })
          }
        },
        // AI 思考过程
        onThinking: (text) => {
          const lastIdx = conv.messages.length - 1
          const lastMsg = conv.messages[lastIdx]
          if (lastMsg.role === 'assistant') {
            const thinking = lastMsg.thinking || []
            thinking.push(text)
            conv.messages.splice(lastIdx, 1, { ...lastMsg, thinking })
          }
        },
        // 工具开始执行
        onToolStart: (data) => {
          const lastIdx = conv.messages.length - 1
          const lastMsg = conv.messages[lastIdx]
          if (lastMsg.role === 'assistant') {
            const tools = lastMsg.tools || []
            tools.push({ ...data, status: 'running', result: null })
            conv.messages.splice(lastIdx, 1, { ...lastMsg, tools })
          }
        },
        // 工具执行完毕
        onToolResult: (data) => {
          const lastIdx = conv.messages.length - 1
          const lastMsg = conv.messages[lastIdx]
          if (lastMsg.role === 'assistant') {
            const tools = (lastMsg.tools || []).map(t =>
              t.tool === data.tool && t.round === data.round && t.status === 'running'
                ? { ...t, status: data.ok ? 'done' : 'error', result: data }
                : t
            )
            conv.messages.splice(lastIdx, 1, { ...lastMsg, tools })
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
      },
      currentNode.value,
      kbContext.value,
    )
  }

  return {
    // 对话
    conversations,
    currentId,
    mode,
    currentNode,
    loading,
    kbContext,
    currentConversation,
    currentMessages,
    currentTitle,
    hasConversations,
    groupedConversations,
    isCurrentEmpty,
    init,
    newConversation,
    switchConversation,
    deleteConversation,
    setMode,
    setKbContext,
    send,
    // 图谱数据
    knowledgeNodes,
    knowledgeEdges,
    graphLoaded,
    graphError,
    subjects,
    subjectSummaries,
    currentSubject,
    boards,
    currentBoard,
    setBoard,
    ensureSubjectSelected,
    fetchBoards,
    fetchGraph,
    refreshGraph,
    fetchNodeDetail,
    fetchSubjects,
    setSubject,
    generateSubjectGraph,
    // 学习进度（科技树联动）
    learningPath,
    nextToLearn,
    fetchLearningPath,
    fetchNextToLearn,
    // 学习进度统计（仪表盘）
    stats,
    statsLoading,
    fetchStats,
    // 图谱 CRUD（统一入口）
    createNode,
    updateNodeInfo,
    updateNodeContent,
    updateMastery,
    deleteNode,
    createEdge,
    updateEdge,
    deleteEdge,
  }
})
