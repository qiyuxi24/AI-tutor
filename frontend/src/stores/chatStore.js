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
import { ElMessage } from 'element-plus'
import { sendMessageStream, apiClient } from '../api/index.js'
// 注：空回复文案改用 E-CLIENT-008（`clientError('CHAT_EMPTY')`）——比通用 E-COMM-007
// 更准确，且直接指向「可重试」；因此不再需要 fmt / ErrorDefs。
import { clientError } from '../utils/errorCodes.js'
import { normalizeEdges } from '../utils/graphNormalize.js'

// 按 user_id 隔离 localStorage，防止切换账号后对话历史泄露
const _uid = (() => {
  try {
    const u = JSON.parse(localStorage.getItem('ai_tutor_user') || 'null')
    return u?.id || 'guest'
  } catch { return 'guest' }
})()
const STORAGE_KEY_CONVERSATIONS = `ai_tutor_conversations_${_uid}`
const STORAGE_KEY_CURRENT = `ai_tutor_current_${_uid}`

/**
 * 开发态信号：后端进程已重载（uvicorn --reload 重启）。
 *
 * 前端代码改动由 Vite HMR 负责；后端代码改动没有任何 HMR 通道，
 * 但**进程重启必然掐断所有 TCP 连接** → 常驻 SSE 断线后重连成功，
 * 就是"后端已重载完成"的免费信号（不需要后端加任何钩子/接口）。
 * HomeView 监听本事件 → 局部重挂当前视图 + 重拉图谱。
 */
export const BACKEND_RELOADED_EVENT = 'backend-reloaded'

// SSE 断线重连退避：首断 5s，每次翻倍，60s 封顶。
// 定值重连（原实现固定 5s、无上限）在后端长时间不可用时会以 12 次/分的频率
// 无限刷请求，且**登出后依旧继续**（A3-1）。
const SSE_RECONNECT_BASE_MS = 5000
const SSE_RECONNECT_MAX_MS = 60000

// 后端 graph_middleware.SUBJECT_UNCLASSIFIED 的对应值。
// 「未分类」= 无学科归属节点的合成分组名，不是真实学科（不出现在学科列表里，
// 但作为一个可选分组出现在 subjectSummaries 中）。
const UNCLASSIFIED_SUBJECT = '未分类'

// 学习任务栏空值（分层看板）：未选学科 / 拉取失败时用它。
// 冻结：消费方（PathBoard.vue）永远拿到同一结构，不会有人就地改它。
const EMPTY_PATH_BOARD = Object.freeze({
  items: [], recommended: [], stats: {}, subject: null, board: null,
})

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
  const currentNode = ref('')  // 当前教学位置的知识点 ID（可选）
  const loading = ref(false)
  // 知识库上下文范围（用户选择放进对话上下文的文件/文件夹）
  const kbContext = ref(null)
  // 知识库变更序号：视频课程导入等**后台长任务**完成后由 SSE 递增，
  // 知识库面板 watch 它来重新拉目录树（导入是分钟级任务，请求早已返回，只能靠推送）
  const kbEpoch = ref(0)
  // 视频课程导入进度（**瞬态**：后端不落库，晚到就没有意义）→ 导入页 watch 它渲染步骤条。
  // 只保留最后一条：步骤条是"当前状态"而不是历史。
  const importProgress = ref(null)

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

  // 学习进度维度：科技树联动数据（下一步推荐 + 分层看板）
  // 学习任务栏（右侧分层看板）：切片内全部知识点 + 前置 / 解锁 / 向前追溯。
  // 只在面板打开时拉取（fetchPathBoard），不在 fetchGraph 里预取 —— 大图会白跑一次。
  const pathBoard = ref(EMPTY_PATH_BOARD)
  const pathBoardLoading = ref(false)
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
            // 本地修改时刻：后端据此判断这份快照是不是旧的（旧快照会被拒绝写入）
            updatedAt: c.updatedAt || c.createdAt,
          }))
        // 使用 sync 端点全量同步（后端合并）
        const { data } = await apiClient.post('/api/v1/conversations/sync', {
          conversations: nonEmpty,
        })
        adoptRemote(data?.conversations)
      } catch {
        // 同步失败不影响本地使用，静默忽略
      } finally {
        syncPending = false
      }
    }, 500)
  }

  /**
   * 采纳后端返回的权威版本 —— 防覆盖的**另一半**。
   *
   * 后端 sync 返回的是库里真实状态：本地那份若是旧快照会被拒绝写入，此时必须用
   * 后端的版本刷回来，否则用户在另一端看到的是旧消息、以为消息丢了。
   * 正在流式生成的对话不动（本地才是最新的）。
   * 只写 localStorage、不再触发同步（否则会 sync → adopt → sync 无限循环）。
   */
  function adoptRemote(remote) {
    if (!Array.isArray(remote) || remote.length === 0) return
    let changed = false
    for (const r of remote) {
      if (loading.value && r.id === currentId.value) continue
      // 兼容两种命名：本地上行的 payload 是 camelCase，后端 _all_conversations
      // 返回的是 snake_case（updated_at/created_at）——只认一种会让 remoteAt 恒为 0，
      // 远程较新的版本永远判不出来（缺陷 C-4）。
      const remoteAt = Number(r.updatedAt ?? r.updated_at) || 0
      const local = conversations.value.find(c => c.id === r.id)
      if (!local) {
        conversations.value.push({
          id: r.id,
          title: r.title,
          messages: r.messages || [],
          createdAt: r.createdAt ?? r.created_at ?? Date.now(),
          updatedAt: remoteAt,
        })
        changed = true
        continue
      }
      const localAt = Number(local.updatedAt) || Number(local.createdAt) || 0
      if (remoteAt > localAt) {
        local.messages = r.messages || []
        local.title = r.title
        local.updatedAt = remoteAt
        changed = true
      }
    }
    if (changed) persist(true)
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
            updatedAt: c.updatedAt || c.createdAt || Date.now(),
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
      graphError.value = ''
      graphLoaded.value = true
      return
    }
    try {
      const { data } = await apiClient.get('/api/v1/knowledge/graph', {
        params: { subject: currentSubject.value },
      })
      knowledgeNodes.value = (data.nodes || []).map(n => ({
        ...n,
        level: (n.tags || []).find(t => ['一级','二级','三级'].includes(t)) || '一级'
      }))
      // ★ 字段映射的唯一实现 = utils/graphNormalize.normalizeEdge
      // （原先这里 / forceGraphEngine / ForceGraph.vue 各写一遍，漏改一处就画不出边）
      knowledgeEdges.value = normalizeEdges(data.edges)
      graphLoaded.value = true
      graphError.value = ''
      // 图谱变更 → 下一步推荐 / 统计随之刷新（单一数据源 = 图谱）
      // 注：路径类数据（分层看板）改在面板打开时按需拉（fetchPathBoard）——
      // 原先这里无条件拉全量拓扑序（每个节点的 name/mastery/summary），
      // 而消费它的图内高亮已撑销 → 白跑一次请求。
      fetchNextToLearn()
      fetchStats(currentSubject.value)
    } catch (e) {
      graphError.value = clientError('GRAPH_LOAD')
    }
  }

  /**
   * 获取「学习任务栏」数据：当前切片的分层看板。
   *
   * 切片口径**必须与 fetchGraph 一致**（同一 subject/board），否则任务栏里会出现
   * 画布上没有的知识点、而画布上的又不在表里。
   * 只在面板打开时调（见 HomeView 的 watch），不做预取。
   */
  async function fetchPathBoard() {
    if (!currentSubject.value) {
      pathBoard.value = EMPTY_PATH_BOARD
      return
    }
    pathBoardLoading.value = true
    try {
      // 只按学科切片。**不要**在这里引用 currentBoard：store 里根本没有这个状态
      // （前端目前没有板块选择器），引用它会在请求发出前就抛 ReferenceError —— 而下面的
      // catch 会把它当成“拉取失败”静默降级 —— 表现为任务栏永远是空态。
      // 2026-09-29 实测：/knowledge/path-board 本身 200、返回 9 条，store 却始终是 EMPTY_PATH_BOARD。
      // 后端的 board 参数继续保留，将来加了板块选择器再把这里补上。
      const { data } = await apiClient.get('/api/v1/knowledge/path-board', {
        params: { subject: currentSubject.value },
      })
      pathBoard.value = data || EMPTY_PATH_BOARD
    } catch (e) {
      // 拉取失败不影响图谱使用：降级为空表（面板自己显示空态），但要留痕 ——
      // 静默吞错会让这种“永远空态”极难定位。
      console.warn('[path-board] 拉取失败，任务栏降级为空表', e)
      pathBoard.value = EMPTY_PATH_BOARD
    } finally {
      pathBoardLoading.value = false
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
   * 单请求来源：/knowledge/stats 的 by_subject 已含全部学科及聚合
   * （唯一实现 = graph_middleware.compute_stats），因此不必再设 subjects 接口。
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
   * 切换当前查看的学科（每个学科单独一张图）。
   * @param {string|null} subject - 学科名（含「未分类」）；null = 未选中（画布空态）
   */
  async function setSubject(subject) {
    if (currentSubject.value === subject) return
    currentSubject.value = subject || null
    graphLoaded.value = false
    await fetchGraph(true)
  }

  // 图谱只渲染最小节点（原子知识点）与关系边：主题层与聚合下钻已下线（2026-09-27）。
  const displayNodes = computed(() => knowledgeNodes.value)
  const displayEdges = computed(() => knowledgeEdges.value)

  /**
   * 从学科书籍生成知识图谱（AI 直接写库），生成后刷新图谱。
   * 勾选文件夹时，新节点会归入该文件夹同名的知识板块。
   *
   * @param {string} subject - 学科名（如 '数据结构'）
   * @param {number[]} nodeIds - KB 中的文件/文件夹节点 ID 列表
   * @returns {Promise<Object>} 生成结果（created_nodes 等）
   */
  async function generateSubjectGraph(subject, nodeIds) {
    const { data } = await apiClient.post('/api/v1/kb/graph/generate', {
      subject,
      node_ids: nodeIds,
    }, { timeout: 300000 })  // 生成可能较慢
    await fetchSubjects()
    // 生成后自动切换到该学科视图
    currentSubject.value = subject
    graphLoaded.value = false
    await fetchGraph(true)
    return data
  }

  /**
   * 删除整个学科图谱（节点 / 关系 / 主题 / 节点正文），**知识库中的教材原文不受影响**。
   *
   * 删除后走 refreshGraph：重拉学科列表 + ensureSubjectSelected —— 当前学科已消失时
   * 自动回退到剩余的第一个学科（该分支已存在，见 ensureSubjectSelected 注释）。
   *
   * @param {string} subject - 学科名（「未分类」是后端合成分组，会被后端 400 拒绝）
   * @returns {Promise<Object>} API 响应（deleted_nodes / deleted_edges / deleted_themes）
   */
  async function deleteSubjectGraph(subject) {
    const { data } = await apiClient.delete('/api/v1/knowledge/graph', { params: { subject } })
    await refreshGraph(true)
    return data
  }

  /**
   * 学科改名：只改课名（节点 subject + tags 里的旧名 + 主题树归属），知识点内容不动。
   *
   * 正看着的就是被改名的学科时，**先**把本地 currentSubject 换成新名再刷新 —— 否则
   * refreshGraph 里的 ensureSubjectSelected 发现旧名已不存在，会把视图切到别的学科。
   *
   * @param {string} oldName
   * @param {string} newName
   * @returns {Promise<Object>} API 响应（renamed_nodes / renamed_themes）
   */
  async function renameSubject(oldName, newName) {
    const { data } = await apiClient.patch('/api/v1/knowledge/subject', {
      old_name: oldName,
      new_name: newName,
    })
    if (currentSubject.value === oldName) currentSubject.value = newName
    await refreshGraph(true)
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
    // 第 1 步：读 localStorage。解析失败 / 结构异常 → 视为"本地为空"继续，
    // **不再**用一个 try 把后面的「拉后端 / 建新对话」全吞掉（缺陷 C-3）。
    let saved = []
    try {
      const raw = localStorage.getItem(STORAGE_KEY_CONVERSATIONS)
      if (raw) {
        const parsed = JSON.parse(raw)
        saved = Array.isArray(parsed) ? parsed.filter(c => c.messages?.length > 0) : []
      }
    } catch {
      saved = []
    }
    conversations.value = saved

    // 第 2 步：本地有内容 → 恢复当前对话；本地为空 → **一定**尝试从后端加载
    if (conversations.value.length > 0) {
      const cid = localStorage.getItem(STORAGE_KEY_CURRENT)
      if (cid && conversations.value.some((c) => c.id === cid)) {
        currentId.value = cid
      }
      // 有数据时也同步到后端（双向合并）
      syncToBackend()
    } else {
      const loaded = await loadFromBackend()
      if (loaded && conversations.value.length > 0) {
        // 后端有数据，取最新的一条作为当前对话
        currentId.value = conversations.value[0].id
      }
    }

    // 第 3 步：仍没有当前对话（本地和后端都没有数据）→ 自动创建新对话
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

    // 回填当前教学位置（缺陷 B-4）：以当前对话的 nodeId 绑定为准，否则刷新后
    // 学习模式会丢失（currentNode 从不持久化）。
    currentNode.value = currentConversation.value?.nodeId || ''

    // 一次性加载知识图谱：先取学科列表并自动选中一个
    // （一次只渲染一个学科，不再默认拉全量）
    await fetchSubjects()
    await ensureSubjectSelected()
    await fetchGraph()
    // 连接 SSE，后端数据变更时自动刷新图谱
    // （init = "开始工作"的唯一入口，顺带解除 dispose/disconnectSSE 的永久断开，
    //  否则开发态 HMR 重挂 HomeView 后就再也连不上了）
    sseStopped = false
    connectSSE()
  }

  // ─── SSE：监听后端数据变更，自动刷新图谱 ───
  let sseSource = null
  let sseReconnectTimer = null
  let sseWasDown = false   // 断线标记：只认"断过线又连上"= 后端重载，首次连接不算
  let sseStopped = false   // 已显式断开（登出 / 视图卸载）→ 禁止一切（重）连接
  let sseReconnectDelay = SSE_RECONNECT_BASE_MS

  /** 关连接 + 清重连定时器（只清资源，不改"是否允许连接"的意愿） */
  function _closeSSE() {
    if (sseReconnectTimer) {
      clearTimeout(sseReconnectTimer)
      sseReconnectTimer = null
    }
    if (sseSource) {
      sseSource.close()
      sseSource = null
    }
  }

  function connectSSE() {
    if (sseStopped) return
    // 清理旧连接和重连定时器
    _closeSSE()
    // 必须已登录才连接 SSE（后端强制认证）
    const token = localStorage.getItem('ai_tutor_token')
    if (!token) return
    try {
      const url = `/api/v1/knowledge/events?token=${encodeURIComponent(token)}`
      sseSource = new EventSource(url)
      sseSource.onopen = () => {
        sseReconnectDelay = SSE_RECONNECT_BASE_MS   // 连上了 → 退避复位
        // 首连与重连都要补拉离线通知（A2-3）：出题是后台任务（~40s 才回），
        // 断线窗口里产生的 quiz_ready 只能靠后端落库那条路救回来。
        fetchPendingNotices()
        // 首连成功什么都不做；断线后重连成功 = 后端已重载 → 通知页面刷新
        if (!sseWasDown) return
        sseWasDown = false
        // 发射端也只认开发态：生产环境一次网络抖动不该打这个信号
        // （接收端 HomeView 本来就有 DEV 判断，但信号本身也不该进生产）
        if (import.meta.env.DEV) {
          window.dispatchEvent(new CustomEvent(BACKEND_RELOADED_EVENT))
        }
      }
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
          } else if (data.type === 'kb_updated') {
            // 知识库变更完成（视频课程导入等分钟级后台任务）→ 通知知识库面板刷新
            handleKbUpdated(data)
          } else if (data.type === 'import_progress') {
            // 导入进度（瞬态）→ 视频导入页的步骤条消费；其余页面不关心
            importProgress.value = data
          } else if (data.type === 'error') {
            console.warn(`[SSE Error] ${data.code} | ${data.module}: ${data.message}`, data.detail || '')
            // 后台任务（如课程导入）的失败只能经这条通道到达 → 必须浮出来，
            // 否则用户点了「导入」后永远等不到任何结果，也无法区分成功与失败
            if (data.message) ElMessage.error(data.message)
          }
        } catch { /* ignore parse errors */ }
      }
      sseSource.onerror = () => {
        // 断线原因可能是后端重载、网络抖动、后端挂了 —— 一律记为"断过线"，
        // 重连成功时统一刷新（刷新幂等且廉价，无法区分也不必区分）。
        sseWasDown = true
        _closeSSE()
        if (sseStopped) return
        // 退避重连：5s → 10s → … → 60s 封顶（后端久挂时不刷屏、不烧电）
        sseReconnectTimer = setTimeout(() => connectSSE(), sseReconnectDelay)
        sseReconnectDelay = Math.min(sseReconnectDelay * 2, SSE_RECONNECT_MAX_MS)
      }
    } catch {
      // SSE 不支持时静默失败
    }
  }

  /**
   * 对话内出题完成（后台异步，约 40s 后到达）→ 往**对应知识点的学习对话**追加一条题目消息。
   *
   * 为什么走这条常驻连接而不是对话 SSE：出题是后台任务，对话的流式响应早已结束，
   * 40 秒后才有结果，只能由 /knowledge/events 这条长连接送达（见后端 quiz_ready 事件）。
   *
   * ⚠️ **归属校验（2026-09-30 修）**：原实现直接塞进"当前打开的那个对话"。但出题要 ~40s，
   * 用户完全可能在这 40s 内又点了另一个知识点的「去学习」—— 于是 A 的题卡会出现在 B 的对话里
   * （现场：点「最小代价生成树」却收到一张写着「图的存储结构」的题卡，看起来像"题目出错了"）。
   * 现在按 `nodeId` 找它所属的那段学习对话，找不到就不塞（题仍在题库/节点侧边栏里）。
   */
  function handleQuizReady(data) {
    const conv = _quizTargetConversation(data)
    if (!conv) {
      console.warn(`[quiz_ready] 找不到属于知识点 ${data.node_id || '(未知)'} 的学习对话，`
                   + '已忽略这条推送（题目仍可在节点详情侧边栏看到）')
      return
    }
    if (!data.ok) {
      // 不能让 AI 说的"稍等片刻"变成永远没有下文，失败也要给个交代
      renderAssistant(conv, {
        role: 'assistant',
        content: `（出题没能完成：${data.message || '请稍后再试'}）`,
        thinking: [], tools: [],
      })
      return
    }
    renderAssistant(conv, {
      role: 'assistant',
      content: formatQuizMessage(data),
      thinking: [], tools: [],
      quiz: data.questions || [],   // 留字段：P1 换成可点选项卡片时直接用
    })
  }

  /**
   * 这道题该落到哪段对话：①按 `nodeId` 找学习对话；②退到当前对话（仅在节点对得上或事件没带节点时）。
   * 返回 null = 不该显示（没人认领）。
   */
  function _quizTargetConversation(data) {
    const qNode = data.node_id || ''
    if (qNode) {
      const own = conversations.value.find(c => c.nodeId === qNode)
      if (own) return own
    }
    // 没有带节点的事件（老后端）/ 找不到对应对话 → 只在当前对话"正学的就是它"时才收
    if (!qNode || qNode === currentNode.value) return currentConversation.value || null
    return null
  }

  /**
   * 出题消息的**引导语**。题目本身交给 `QuizCard` 卡片渲染
   * （点选项 → 提交，不再让学生手打 "B"），所以这里**不**列题干与选项 ——
   * 否则同一道题会在气泡里出现两遍（文本一遍 + 卡片一遍）。
   *
   * 标题带上**小节名**：一个知识点有多节、每节都可能出题，只写节点名的话连续几张卡
   * 分不清是哪一节的（2026-09-30 用户反馈"怎么一直在出题"时的困惑点之一）。
   */
  function formatQuizMessage(data) {
    const n = (data.questions || []).length
    const where = [data.subject || '这个知识点', data.section_title]
      .filter(Boolean).join(' · ')
    return [
      `**来，检验一下刚才学的「${where}」**`,
      '',
      `下面 ${n} 道题直接在卡片里作答：选好后点「提交」，我来判分。`,
    ].join('\n')
  }

  /**
   * 知识库变更完成（视频课程导入等**分钟级后台任务**）→ 递增 `kbEpoch`。
   *
   * 为什么不用请求响应：导入要下载 + 转写，请求早就返回了（返回的只是"已开始"），
   * 结果只能靠推送。面板 watch `kbEpoch` → 重新拉目录树，无需轮询。
   */
  function handleKbUpdated(data) {
    kbEpoch.value += 1
    if (data?.filename) ElMessage.success(`已导入知识库：${data.filename}`)
  }

  /**
   * 补拉离线期间产生的通知（A2-3）。
   *
   * 为什么需要：出题是后台任务（约 40s 才回），这段时间里用户可能关掉页面或断网，
   * 实时推送就无处可投（后端 notify 找不到订阅队列）。后端会把这类通知先落库
   * （`pending_notices`），本函数在 SSE 建连/重连后取走，复用实时事件的同一套分发分支。
   * 后端"取走即标记已投递"，因此不会与实时推送重复渲染同一张题卡。
   */
  async function fetchPendingNotices() {
    try {
      const { data } = await apiClient.get('/api/v1/knowledge/notices/pending')
      for (const notice of data?.notices || []) {
        if (notice.type === 'quiz_ready') handleQuizReady(notice)
        else if (notice.type === 'graph_updated') refreshGraph()
        else if (notice.type === 'kb_updated') handleKbUpdated(notice)
        else if (notice.type === 'error') {
          console.warn(`[SSE Error] ${notice.code} | ${notice.module}: ${notice.message}`,
                       notice.detail || '')
          if (notice.message) ElMessage.error(notice.message)
        }
      }
    } catch {
      // 补拉失败不影响实时通道：通知仍在库里，下次连上再取
    }
  }

  /**
   * 「去学习」：以某个知识点为教学内容开一轮学习 —— 新建对话 + 记下当前知识点 + 发首条消息。
   *
   * 为什么要发一条消息（而不是只设状态）：后端的小节级教学上下文（【学习模式】段落）
   * 是**组装 system prompt 时**按 `current_node` 现算的（见 chat_service._build_learn_block），
   * 不发请求就不会生成；发一条"开始学习"正好触发它，AI 据此从对应小节开讲。
   */
  async function startLearning(nodeId, name = '') {
    if (!nodeId) return
    newConversation()
    currentNode.value = nodeId      // 放在 newConversation 之后：防止被它重置
    // 给这段对话打上"正在学哪个知识点"的标记：出题是后台任务（~40s），回来时得靠它
    // 把题卡投回**正确的**对话（见 handleQuizReady / _quizTargetConversation）
    const conv = currentConversation.value
    if (conv) {
      // 只打标记，**不**在这里 persist：此刻 messages 仍为空，persist 会把
      // localStorage 的对话键写成 "[]" 覆盖历史（缺陷 C-1）。真正的落盘交给
      // 紧随其后的 send()（它会先写入消息再 persist）。
      conv.nodeId = nodeId
    }
    await send(`开始学习「${name || nodeId}」`)
  }

  /**
   * 断开 SSE 并**禁止再重连**（登出 / 视图卸载调用，A3-1）。
   *
   * 与"_closeSSE"的区别：那个只是换连接，这个是"这条连接不要了"。
   * 想重新连上必须再走 init()（它会复位 sseStopped）——登录成功后本就整页重载，
   * 天然满足；这样就不存在"登出后旧会话仍在后台重连"的窗口。
   */
  function disconnectSSE() {
    sseStopped = true
    sseWasDown = false
    _closeSSE()
  }

  /**
   * 视图卸载 / 登出：释放全部常驻资源（SSE、定时器、在途流）。可重复调用。
   *
   * 为什么必须有一个统一出口：防抖同步（500ms）、SSE 抑制（3s）、SSE 重连
   * 三处各挂一个裸 setTimeout，没有任何地方成组清理 —— 组件卸载后定时器
   * 仍会戳 store（A3-3）。
   */
  function dispose() {
    disconnectSSE()
    if (sseSuppressTimer) {
      clearTimeout(sseSuppressTimer)
      sseSuppressTimer = null
    }
    if (syncDebounceTimer) {
      clearTimeout(syncDebounceTimer)
      syncDebounceTimer = null
    }
    if (streamPersistTimer) {
      clearTimeout(streamPersistTimer)
      streamPersistTimer = null
    }
    if (streamController) {
      streamController.abort()
      streamController = null
    }
    loading.value = false
  }

  // ─── 持久化 ───
  // 核心规则：只有有内容的对话才写入 localStorage + 同步到后端
  // 空对话（messages.length === 0）不存储
  /**
   * @param {boolean} [skipSync] true = 只写 localStorage、不回推后端
   *   （采纳后端权威版本时用，否则会 sync → adopt → sync 递归）
   */
  function persist(skipSync = false) {
    // 过滤掉空对话，只有有内容的对话才持久化
    const nonEmpty = conversations.value.filter(c => c.messages.length > 0)
    localStorage.setItem(STORAGE_KEY_CONVERSATIONS, JSON.stringify(nonEmpty))
    // 如果当前对话是空对话，不存储 currentId（下次加载时会自动创建新对话）
    const curConv = conversations.value.find(c => c.id === currentId.value)
    if (curConv && curConv.messages.length > 0) {
      localStorage.setItem(STORAGE_KEY_CURRENT, currentId.value)
    } else {
      // 当前对话为空 → **移除**键（不再写空串）。空串会被下次 init 当成一个
      // "存在但无效"的 id，既匹配不到对话、又覆盖掉上一个有效选择（缺陷 C-2）。
      localStorage.removeItem(STORAGE_KEY_CURRENT)
    }

    // 双写：同步到后端（防抖，避免频繁请求）
    if (!skipSync) syncToBackend()
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
    // 新对话没有知识点绑定 → 清空当前教学位置（缺陷 B-3）。
    currentNode.value = ''
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
    // 回填"正在学哪个知识点"（缺陷 B-2）：以目标对话的 nodeId 绑定为准。
    currentNode.value = conversations.value.find(c => c.id === id)?.nodeId || ''
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

  // ─── 重命名对话 ───
  /**
   * 只改标题。标题本就在「localStorage 为主 + 后端全量同步」的契约里：persist() 会把新
   * 标题带进 syncToBackend()，后端 upsert 以客户端 title 为准（conversation_store 的
   * ON CONFLICT title = excluded.title）—— 所以**不需要再加一个改名端点**。
   * messages 没变 → 后端不推进 updated_at，列表排序不受影响。
   *
   * @param {string} id
   * @param {string} title - 已 trim 的非空标题
   */
  function renameConversation(id, title) {
    const conv = conversations.value.find((c) => c.id === id)
    if (!conv || conv.title === title) return
    conv.title = title
    persist()
  }

  // ─── 设置知识库上下文范围 ───
  function setKbContext(ctx) {
    kbContext.value = ctx // ctx: { nodeIds: [], name: string } 或 null
  }

  // ─── 流式请求的 AbortController（用于取消） ───
  let streamController = null

  // 每次 send() 递增：流回调据此判断"这个事件是不是属于当前这次流"。
  // 旧流被 abort 后仍可能有一个迟到事件挤进来，序号不匹配就丢弃（缺陷 A 护栏）。
  let streamSeq = 0

  // 流式过程节流落盘（缺陷 A-2）：最少 STREAM_PERSIST_THROTTLE_MS 间隔写一次
  // localStorage/后端，保证学生在流中途 F5 时正文已落盘（而非只在 onDone 才写）。
  const STREAM_PERSIST_THROTTLE_MS = 1500
  let streamPersistTimer = null
  let streamPersistLast = 0

  /** 节流版 persist：首次立即写，之后按窗口合并（尾写不丢最后一次变更）。 */
  function throttledPersist() {
    const now = Date.now()
    const elapsed = now - streamPersistLast
    if (elapsed >= STREAM_PERSIST_THROTTLE_MS) {
      streamPersistLast = now
      persist()
    } else if (!streamPersistTimer) {
      streamPersistTimer = setTimeout(() => {
        streamPersistTimer = null
        streamPersistLast = Date.now()
        persist()
      }, STREAM_PERSIST_THROTTLE_MS - elapsed)
    }
  }

  // ─── 流式渲染：把"事件 → 改 store"收敛成一份（回调只负责转交） ───
  /**
   * 用 patch 产出的字段就地替换对话末尾的 assistant 气泡（渲染的单一出口）。
   * 流式回调（onToken/onThinking/onToolStart/onToolResult）都走这里 —— 回调只把
   * 事件数据交给本函数，不再各自重复"取最后一条 → splice 替换"。
   * @param {object} conv - 目标对话
   * @param {(msg: object) => object} patch - 收到末尾消息，返回要合并进去的字段
   */
  function patchLastAssistant(conv, patch) {
    const idx = conv.messages.length - 1
    const msg = conv.messages[idx]
    if (msg && msg.role === 'assistant') {
      conv.messages.splice(idx, 1, { ...msg, ...patch(msg) })
      // 内容在变 → 提升本地修改时刻（多端防覆盖：让这份快照"够新"）
      conv.updatedAt = Date.now()
    }
  }

  /**
   * 落定一条 assistant 消息并持久化（onDone 空回复 / onError / quiz_ready 共用）。
   * @param {object} conv - 目标对话
   * @param {object} msg - 完整消息对象（字段由调用方按事件语义给出）
   * @param {boolean} [replaceLast] - true=先 pop 掉末尾占位气泡再 push（onDone/onError）
   */
  function renderAssistant(conv, msg, replaceLast = false) {
    if (replaceLast) conv.messages.pop()
    conv.messages.push(msg)
    conv.updatedAt = Date.now()
    persist()
  }

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
    // 内容变化 → 提升本地修改时刻（多端防覆盖：后端据此判断这份快照是不是旧的）
    conv.updatedAt = Date.now()

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

    // 新一次发送前先掐掉上一次在途的流（A3-2）：否则两条流会同时往同一个气泡写
    // token（用户连点/重试时表现为"内容来回跳"），且旧流的 onDone 会把新气泡覆盖掉。
    if (streamController) {
      streamController.abort()
      streamController = null
    }

    // 本次流的序号：回调据此判断事件是否属于当前这次流（缺陷 A 护栏）
    const seq = ++streamSeq

    // 使用流式 API
    streamController = sendMessageStream(
      conv.messages.slice(0, -1), // 不含占位消息的对话历史
      {
        // 每收到一个 token，追加到占位消息；同时**节流落盘**（缺陷 A-2）：
        // 流中途刷新也不会丢正文（原先只有 onDone 才 persist）。
        onToken: (token) => {
          patchLastAssistant(conv, (msg) => ({ content: msg.content + token }))
          throttledPersist()
        },
        // AI 思考过程
        onThinking: (text) => {
          patchLastAssistant(conv, (msg) => {
            const thinking = msg.thinking || []
            thinking.push(text)
            return { thinking }
          })
          throttledPersist()
        },
        // 工具开始执行
        onToolStart: (data) =>
          patchLastAssistant(conv, (msg) => {
            const tools = msg.tools || []
            tools.push({ ...data, status: 'running', result: null })
            return { tools }
          }),
        // 工具执行完毕
        onToolResult: (data) => {
          patchLastAssistant(conv, (msg) => ({
            tools: (msg.tools || []).map((t) =>
              t.tool === data.tool && t.round === data.round && t.status === 'running'
                ? { ...t, status: data.ok ? 'done' : 'error', result: data }
                : t
            ),
          }))
          throttledPersist()
        },
        // Agent 循环结束：后端在 loop 收尾（loop_stop=natural）就发这个事件，
        // 远早于 [DONE]（后者在 _analyze_and_apply 之后才发，可能晚数十秒）。
        // 收到即收口 loading 并把正文落盘（缺陷 A-1：否则「AI 思考中…」一直转）。
        onAgentDone: () => {
          if (seq !== streamSeq) return   // 旧流迟到的事件，忽略
          loading.value = false
          persist()
        },
        // 流式完成
        onDone: (fullReply) => {
          loading.value = false
          streamController = null
          if (fullReply) {
            persist()
          } else {
            // 空回复 = 一个 token 都没收到（事件投递失败 / 收尾丢帧）。旧版在这里静默删掉
            // 占位气泡，结果"对话没有反应"且没有任何线索（2026-09-26 排查成本极高的根因之一）。
            // 失败必须留下可见痕迹：failed=true 让 MessageBubble 渲染「↻ 重试」按钮（配 retryLast）。
            renderAssistant(conv, {
              role: 'assistant',
              content: clientError('CHAT_EMPTY'),
              failed: true,
            }, true)
          }
        },
        // 出错
        onError: (errorMsg) => {
          loading.value = false
          streamController = null
          // 移除占位消息，替换为错误消息
          renderAssistant(conv, { role: 'assistant', content: errorMsg }, true)
        },
      },
      // 以**对话绑定**为准（缺陷 B-1）：否则切走再切回、或刷新后 currentNode 丢失时，
      // 会带着错误的知识点（或空）去请求。
      conv.nodeId || currentNode.value || '',
      kbContext.value,
      conv.id, // 多端串行键：同一对话的请求在后端排队，避免两端同时改同一份历史
    )
  }

  /**
   * 重试上一次失败的回答：撤掉失败气泡与对应的提问，用同一句话重发。
   * 只处理「最后两条是 user + failed assistant」的情形，正常对话不受影响。
   */
  function retryLast() {
    const conv = currentConversation.value
    if (!conv || loading.value) return
    const msgs = conv.messages
    if (msgs.length < 2) return
    const last = msgs[msgs.length - 1]
    const prev = msgs[msgs.length - 2]
    if (last?.role !== 'assistant' || !last.failed || prev?.role !== 'user') return
    const text = prev.content
    msgs.splice(msgs.length - 2, 2)
    send(text)
  }

  return {
    // 对话
    conversations,
    currentId,
    currentNode,
    loading,
    kbContext,
    kbEpoch,
    importProgress,
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
    renameConversation,
    setKbContext,
    send,
    retryLast,
    startLearning,
    // 生命周期（A3）：登出 / 卸载时释放 SSE、定时器与在途流
    disconnectSSE,
    dispose,
    // 图谱数据
    knowledgeNodes,
    knowledgeEdges,
    graphLoaded,
    graphError,
    subjects,
    subjectSummaries,
    currentSubject,
    displayNodes,
    displayEdges,
    ensureSubjectSelected,
    fetchGraph,
    refreshGraph,
    fetchNodeDetail,
    fetchSubjects,
    setSubject,
    generateSubjectGraph,
    deleteSubjectGraph,
    renameSubject,
    // 学习进度（科技树联动）
    pathBoard,
    pathBoardLoading,
    nextToLearn,
    fetchPathBoard,
    fetchNextToLearn,
    // 学习进度统计（仪表盘）
    stats,
    statsLoading,
    fetchStats,
    // 图谱 CRUD（统一入口）
    createNode,
    updateNodeInfo,
    updateNodeContent,
    deleteNode,
    createEdge,
    updateEdge,
    deleteEdge,
  }
})
