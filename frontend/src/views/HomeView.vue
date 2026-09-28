<script setup>
/**
 * HomeView.vue — 主视图（编排层）
 *
 * 布局：左侧活动栏 + 内容区（对话 / 学习进度 / 图谱 / 出题 / 资源采集 / 设置），无顶部栏（沉浸式）。
 * 知识库不是独立视图：它是图谱页右侧可收起的侧栏（KbPanel，宽度由 --kb-w 驱动）。
 *
 * 职责：
 *   1. 页面切换，由左侧活动栏驱动
 *   2. 编排数据流：Store ↔ 子组件
 *   3. 监听 ForceGraph 的 graph-action 事件，调用 Store CRUD 方法
 *   4. 管理 NodeDetail 弹窗
 *
 * 主题切换 / 用户菜单已下沉到 ActivityBar 组件内部。
 */

import { ref, onMounted, onUnmounted, computed, watch } from 'vue'
import { useChatStore, BACKEND_RELOADED_EVENT } from '../stores/chatStore'
import { useAuthStore } from '../stores/authStore'
import { formatError, clientError } from '../utils/errorCodes.js'
import { notifyError, notifyInfo } from '../utils/feedback'
import { ballPrefs, loadBallPrefs } from '../utils/floatingBall.js'
import { loadAvatar } from '../utils/avatar.js'
import FloatingBall from '../components/FloatingBall.vue'
import { fetchSourceNodes } from '../api/kb.js'
import ActivityBar from '../components/ActivityBar.vue'
import ConversationSidebar from '../components/ConversationSidebar.vue'
import ChatArea from '../components/ChatArea.vue'
import ForceGraph from '../components/ForceGraph.vue'
import GraphSubjectBar from '../components/GraphSubjectBar.vue'
import PathBoard from '../components/PathBoard.vue'
import NodeDetail from '../components/NodeDetail.vue'
import UserProfile from '../components/UserProfile.vue'
import GraphSearch from '../components/GraphSearch.vue'
import SidePanel from '../components/SidePanel.vue'
import OnboardingGuide from '../components/OnboardingGuide.vue'
import LoginDialog from '../components/LoginDialog.vue'
import KbPanel from '../components/KbPanel.vue'
import QuizView from './QuizView.vue'
import CollectorView from './CollectorView.vue'
import SettingsView from './SettingsView.vue'
import DashboardView from './DashboardView.vue'

const store = useChatStore()
const authStore = useAuthStore()
const viewMode = ref('chat')

// ─── 三处侧栏（统一走 SidePanel）：折叠态 + 宽度 ───
// 宽度与折叠态留在父级，是因为它们要参与布局：对话栏折叠后 ChatArea 自动占满，
// 知识库栏宽度还要驱动搜索栏/缩放控件让位（--kb-w）。持久化由 SidePanel 按 storageKey 负责。
const sidebarCollapsed = ref(false)
const convPanelWidth = ref(260)
const graphNavCollapsed = ref(false)
const graphNavWidth = ref(240)
const kbCollapsed = ref(false)
const kbWidth = ref(320)

// 学习任务栏宽度：必须与下方 .graph-path-board 的 width 一致（画布缩放控件按它让位）
const PATH_BOARD_WIDTH = 360

// ─── 开发态：后端代码改动 → 自动局部刷新 ───
// 后端 uvicorn --reload 重启会掐断 SSE（见 chatStore.connectSSE），重连成功即"已重载"；
// 此时把 epoch +1 让子视图重挂 → 各自的 onMounted 重新拉数据（这就是"局部刷新"）。
// 前端改动不用管：Vite HMR 已覆盖。
// 仅开发环境生效：生产环境一次网络抖动不该把用户正在填的作答/勾选冲掉。
const viewEpoch = ref(0)

function handleBackendReloaded() {
  viewEpoch.value += 1
  // 图谱 / 学科列表 / 统计在 store 里，不走子视图重挂，得单独重拉
  store.refreshGraph()
  notifyInfo('后端已重载，页面数据已同步')
  console.info('[dev] 检测到后端重载，已局部刷新当前视图')
}

const showUserProfile = ref(false)
const showLoginDialog = ref(false)
const graphSearchRef = ref(null)
const forceGraphRef = ref(null)
const onboardingRef = ref(null)

// ─── 右侧「学习任务栏」───
// 路径不画在图上（图上一淡出就丢上下文），改成右侧弹出的分层看板。
const showPathBoard = ref(false)

/**
 * 任务栏与知识库栏争同一条右边缘 → 开任务栏时收起知识库（反之展开知识库时
 * 关任务栏，见下方 watch）。两边都能搌开、又都不遮对方，比堆叠更好预测。
 */
function togglePathBoard() {
  showPathBoard.value = !showPathBoard.value
  if (showPathBoard.value) kbCollapsed.value = true
}

// 用户手动展开知识库 → 让位（否则两张卡片在同一位置重叠）
watch(kbCollapsed, (collapsed) => {
  if (!collapsed) showPathBoard.value = false
})

/**
 * 画布要向右让出多少：知识库栏（或任务栏）占用的那一条。
 * 两边互斥，所以取当前实际占位的那一个；都收起则为 0。
 */
const rightReserve = computed(() => {
  if (!kbCollapsed.value) return kbWidth.value
  return showPathBoard.value ? PATH_BOARD_WIDTH : 0
})

/**
 * 任务栏数据只在面板打开时拉、且打开着的时候切学科/板块要跟着重算
 * （否则表里是上一个学科的知识点，和画布对不上）。
 */
watch(
  () => [showPathBoard.value, store.currentSubject, store.currentBoard],
  ([open]) => { if (open) store.fetchPathBoard() },
  { immediate: true }
)

/** 任务栏「在图谱中定位」：把画布视角移到该节点（引擎自带平滑聚焦） */
function handlePathBoardFocus(nodeId) {
  forceGraphRef.value?.focusNode(nodeId)
}

/** 任务栏「修改掌握度」：复用节点详情弹窗（掌握度滑块在那儿，不做第二套入口） */
async function handlePathBoardEdit(nodeId) {
  await handleNodeDblClick(nodeId)
}

// ─── 节点详情弹窗 ───
const nodeDetailModal = ref(null)
const nodeDetailVisible = ref(false)
const nodeDetailLoading = ref(false)

// ─── 节点详情 → 出题页跳转 ───
// quizTarget = { nodeName, questionId }；questionId 为空表示只按节点名预填主题
const quizTarget = ref(null)
// 目标变化时重建 QuizView（v-if 在外层视图切换时已重挂，此 key 兜住"已在出题页再跳题"）
const quizViewKey = computed(() =>
  quizTarget.value ? `${quizTarget.value.nodeName}#${quizTarget.value.questionId ?? ''}` : 'quiz'
)

onMounted(async () => {
  // 先确保有会话（无 token 时静默登录体验账户）：init() 会立刻打一批需要 token
  // 的接口，没先登录整屏就是 401。
  // ponytail: 失败一律弹登录框，不区分"口令不对"和"后端没起"——弹框里带着真实
  // 错误信息，用户至少知道该干什么。要精确区分就让 ensureSession 返回原因而非 boolean。
  const ok = await authStore.ensureSession()
  if (!ok) showLoginDialog.value = true
  // init() 内部依次：fetchSubjects() → ensureSubjectSelected() → fetchGraph() → connectSSE()
  store.init()
  // 悬浮球开关（真值源 = 服务端账号偏好，跟账号走）→ 写入共享状态，供 FloatingBall 使用
  loadBallPrefs()
  // 用户头像（真值源 = 服务端的头像图片）→ 写入共享状态，供 ActivityBar / MessageBubble 使用
  loadAvatar()
  // 后端重载 → 局部刷新（开发态，见 handleBackendReloaded）
  if (import.meta.env.DEV) {
    window.addEventListener(BACKEND_RELOADED_EVENT, handleBackendReloaded)
  }
})

onUnmounted(() => {
  if (import.meta.env.DEV) {
    window.removeEventListener(BACKEND_RELOADED_EVENT, handleBackendReloaded)
  }
})

/** 切换账号：打开登录弹窗（#/login 路由页已取消，也不再提供"退出登录"）*/
function openAccountSwitch() {
  showLoginDialog.value = true
}

/** 登录/注册成功：必须重载，否则 store 里还是上一个账号的图谱与对话记录 */
function handleLoginSuccess() {
  window.location.reload()
}

function handleActivitySelect(id) {
  if (viewMode.value === id) return
  viewMode.value = id
  // 离开对话视图时，仅收起对话侧栏（保留对话状态）
  if (id !== 'chat') sidebarCollapsed.value = true
  // 从活动栏直接进入出题页 → 清掉上次由节点详情带来的聚焦目标，回到空白出题页
  if (id === 'quiz') quizTarget.value = null
}

function replayOnboarding() {
  onboardingRef.value?.show()
}

/**
 * 双击节点：通过 Store.fetchNodeDetail() 获取节点详情（而非直接调 apiClient）
 */
async function handleNodeDblClick(nodeId) {
  if (viewMode.value !== 'graph') return
  nodeDetailVisible.value = true
  nodeDetailLoading.value = true
  try {
    nodeDetailModal.value = await store.fetchNodeDetail(nodeId)
  } catch {
    nodeDetailModal.value = { id: nodeId, name: '加载失败', content: clientError('NODE_LOAD') }
  } finally {
    nodeDetailLoading.value = false
  }
}

function closeNodeDetail() {
  nodeDetailVisible.value = false
}

/**
 * NodeDetail 侧边栏「试题」/「去出题」→ 跳到出题页。
 * 带 questionId 则聚焦该题；不带则只按节点名预填出题主题。
 * 需先关闭详情弹窗，否则全屏遮罩挡住出题页。
 */
function handleOpenQuiz({ nodeName, questionId = null }) {
  quizTarget.value = { nodeName, questionId }
  nodeDetailVisible.value = false
  viewMode.value = 'quiz'
}

/**
 * NodeDetail 保存内容：通过 Store.updateNodeContent() 执行。
 */
async function handleNodeDetailSave({ nodeId, content, onResult }) {
  try {
    await store.updateNodeContent(nodeId, { content })
    if (onResult) onResult(null)
  } catch (e) {
    const msg = formatError(e, { action: '保存节点内容' })
    if (onResult) onResult(msg)
  }
}

/**
 * 刷新图谱 + 同步更新节点详情弹窗（如果打开着）
 */
async function refreshGraph() {
  await store.refreshGraph()
  if (nodeDetailModal.value?.id) {
    try {
      nodeDetailModal.value = await store.fetchNodeDetail(nodeDetailModal.value.id)
    } catch { /* ignore */ }
  }
  // 任务栏打开时一并重算：改完掌握度要立刻看到解锁状态与追溯结果变化
  if (showPathBoard.value) store.fetchPathBoard()
}

// ════════════════════════════════════════════════════════════════
//  图谱 CRUD 编排：监听 ForceGraph 的 graph-action 事件
// ════════════════════════════════════════════════════════════════

/**
 * ForceGraph emit 的 graph-action 统一处理入口。
 */
async function handleGraphAction({ action, payload }) {
  try {
    switch (action) {
      case 'create-node':
        await store.createNode(payload)
        break

      case 'edit-node':
        await store.updateNodeInfo(payload.nodeId, {
          name: payload.name,
          tags: payload.tags,
        })
        break

      case 'delete-node':
        await store.deleteNode(payload.nodeId)
        break

      case 'create-edge':
        await store.createEdge(payload)
        break

      case 'edit-edge':
        await store.updateEdge(payload.edgeId, {
          relation: payload.relation,
          label: payload.label,
        })
        break

      case 'delete-edge':
        await store.deleteEdge(payload.edgeId)
        break

      default:
        console.warn('[HomeView] 未知的 graph-action:', action)
    }
  } catch (e) {
    notifyError(formatError(e, { action: `图谱操作: ${action}` }))
  }
}

// ════════════════════════════════════════════════════════════════
//  「在图谱中显示」：知识库右键文件 → 高亮"用了这份资料"的节点
// ════════════════════════════════════════════════════════════════

/**
 * sourceHighlight = { docId, docName, nodeIds, subject, total }；null = 未高亮。
 * `docId` 同时传给 NodeDetail —— 打开节点时它按 doc_id 本地高亮该文件的小节与题目
 * （`sections[].sources` / `quizzes[].source_docs` 都在详情响应里，零额外请求）。
 */
const sourceHighlight = ref(null)
const sourceNodes = computed(() => sourceHighlight.value?.nodeIds || [])

/**
 * 反查该资料影响的节点 → 切图谱视图 → 切到命中最多的学科 → 高亮 + 聚焦第一个命中节点。
 * 为什么要选学科：图谱一次只渲染一个学科，而一份资料可能横跨多个（见设计文档的学科切片）。
 */
async function handleShowInGraph({ docId, docName }) {
  try {
    const { data } = await fetchSourceNodes(docId)
    const nodes = data?.nodes || []
    if (!nodes.length) {
      notifyInfo(`「${docName}」还没有关联的知识点（只有用它建过图才会有）`)
      return
    }

    const count = {}
    for (const n of nodes) if (n.subject) count[n.subject] = (count[n.subject] || 0) + 1
    const subject = Object.keys(count).sort((a, b) => count[b] - count[a])[0] || ''
    const ids = nodes.filter(n => !subject || n.subject === subject).map(n => n.id)
    sourceHighlight.value = { docId, docName, nodeIds: ids, subject, total: nodes.length }

    if (viewMode.value !== 'graph') viewMode.value = 'graph'
    if (subject && subject !== store.currentSubject) {
      await store.setSubject(subject)
      await new Promise(r => setTimeout(r, 450))   // 等新学科的力导向图渲染完（同 switchSubjectAndFocus）
    }
    forceGraphRef.value?.focusNode(ids[0])
    notifyInfo(ids.length < nodes.length
      ? `「${docName}」涉及 ${nodes.length} 个知识点，已切到「${subject}」并高亮其中 ${ids.length} 个`
      : `已高亮「${docName}」关联的 ${ids.length} 个知识点`)
  } catch (e) {
    notifyError(formatError(e, { action: '在图谱中显示' }))
  }
}

// 用户手动切到别的学科 → 高亮节点不在画布上了，清掉（程序自己切到 subject 的那次不触发）
watch(() => store.currentSubject, (s) => {
  if (sourceHighlight.value && s && s !== sourceHighlight.value.subject) {
    sourceHighlight.value = null
  }
})

/**
 * 搜索选中节点 → 切换到图谱视图并聚焦该节点
 */
async function handleGraphSearchSelect(nodeId) {
  if (viewMode.value !== 'graph') {
    viewMode.value = 'graph'
    await new Promise(r => setTimeout(r, 450))
  }
  forceGraphRef.value?.focusNode(nodeId)
}

// ════════════════════════════════════════════════════════════════
//  仪表盘联动：点击学科/薄弱点/推荐节点 → 切图谱并聚焦
// ════════════════════════════════════════════════════════════════

/**
 * 切到目标节点所在学科（若与当前不同），等待重渲染后再聚焦。
 *
 * 图谱一次只渲染一个学科，跨学科聚焦（仪表盘推荐/薄弱点、节点详情里的
 * 前置与关联节点）必须先切学科，否则目标节点根本不在画布上。
 *
 * @param {string} nodeId
 * @param {string} [subject] - 目标学科；空值表示未知，不切换
 */
async function switchSubjectAndFocus(nodeId, subject) {
  if (subject && subject !== store.currentSubject) {
    await store.setSubject(subject)
    // 等新学科的力导向图完成渲染
    await new Promise(r => setTimeout(r, 450))
  }
  forceGraphRef.value?.focusNode(nodeId)
}

/** 仪表盘点击学科 → 切换学科并进入图谱视图 */
async function handleDashboardGoGraph(subject) {
  viewMode.value = 'graph'
  if (subject) {
    await store.setSubject(subject)
  } else {
    // 空值 = "不指定学科"（如空状态页的"前往知识图谱"）→ 沿用/自动选中，不停在空画布
    await store.ensureSubjectSelected()
  }
}

/** 仪表盘点击节点（薄弱点/推荐）→ 进入图谱并聚焦该节点 */
async function handleDashboardGoNode(nodeId, subject) {
  if (viewMode.value !== 'graph') {
    viewMode.value = 'graph'
    // 切视图后等待布局/渲染完成再聚焦
    await new Promise(r => setTimeout(r, 450))
  }
  await switchSubjectAndFocus(nodeId, subject)
}

/**
 * NodeDetail 中点击前置知识/相关节点 → 关闭弹窗并聚焦目标节点
 */
async function handleNodeDetailNavigate(nodeId) {
  nodeDetailVisible.value = false
  nodeDetailLoading.value = true
  let targetSubject = ''
  try {
    nodeDetailModal.value = await store.fetchNodeDetail(nodeId)
    targetSubject = nodeDetailModal.value.subject || ''
    nodeDetailVisible.value = true
  } catch {
    nodeDetailModal.value = { id: nodeId, name: '加载失败', content: clientError('NODE_LOAD') }
    nodeDetailVisible.value = true
  } finally {
    nodeDetailLoading.value = false
  }
  if (viewMode.value === 'graph') {
    await switchSubjectAndFocus(nodeId, targetSubject)
  }
}

// ─── 视图切换动画 ───
const slideTransition = {
  onEnter(el, done) {
    const entering = el.dataset.view
    const isChat = entering === 'chat'
    const from = isChat ? '-100%' : '100%'
    el.style.transform = `translateX(${from}) scale(0.96)`
    el.style.opacity = '0.8'
    el.offsetHeight
    el.style.transition = 'transform 400ms cubic-bezier(0.4, 0.0, 0.2, 1), opacity 400ms cubic-bezier(0.4, 0.0, 0.2, 1)'
    el.style.transform = 'translateX(0) scale(1)'
    el.style.opacity = '1'
    setTimeout(done, 400)
  },
  onLeave(el, done) {
    const leaving = el.dataset.view
    const isChat = leaving === 'chat'
    const to = isChat ? '100%' : '-100%'
    el.style.transform = 'translateX(0)'
    el.style.opacity = '1'
    el.offsetHeight
    el.style.transition = 'transform 350ms cubic-bezier(0.4, 0.0, 0.2, 1), opacity 350ms cubic-bezier(0.4, 0.0, 0.2, 1)'
    el.style.transform = `translateX(${to})`
    el.style.opacity = '0.3'
    setTimeout(done, 350)
  }
}
</script>

<template>
  <div class="app-container">
    <!-- 站内悬浮球 + 只放对话的小窗：只在非对话页显示（对话页已有完整对话区） -->
    <FloatingBall :visible="ballPrefs.enabled && viewMode !== 'chat'" />

    <!-- ═══ 活动栏 + 内容区域（无顶部栏，全沉浸） ═══ -->
    <ActivityBar
      :active-view="viewMode"
      @select="handleActivitySelect"
      @open-profile="showUserProfile = true"
      @switch-account="openAccountSwitch"
    />

    <div class="content-region">
      <!-- 对话页 -->
      <Transition name="view-fade" v-bind="slideTransition">
        <div v-if="viewMode === 'chat'" class="chat-layout" data-view="chat">
          <!-- 对话历史二级侧栏（统一 SidePanel：可拖宽 / 可折叠） -->
          <SidePanel
            class="conv-panel"
            side="left"
            label="对话列表"
            :min="200"
            :max="420"
            storage-key="conv"
            v-model:width="convPanelWidth"
            v-model:collapsed="sidebarCollapsed"
          >
            <ConversationSidebar />
          </SidePanel>

          <ChatArea @navigate-to-node="handleGraphSearchSelect" />
        </div>
      </Transition>

      <!-- 知识图谱页 -->
      <Transition name="view-fade" v-bind="slideTransition">
        <div
          v-if="viewMode === 'graph'"
          class="graph-layout"
          data-view="graph"
          :style="{ '--kb-w': rightReserve + 'px' }"
        >
          <!-- 左侧导航：搜索 + 学科列表，一次只渲染一个学科（避免图谱无限生长）。
               整张卡片的外观/折叠/拖宽/拖高都由 SidePanel 统一提供，收起时搜索栏一起收起。 -->
          <SidePanel
            class="graph-nav"
            side="left"
            label="学科导航"
            :min="180"
            :max="360"
            storage-key="graphNav"
            v-model:width="graphNavWidth"
            v-model:collapsed="graphNavCollapsed"
            resizable-height
          >
            <div class="graph-nav-search">
              <GraphSearch
                ref="graphSearchRef"
                :nodes="store.knowledgeNodes"
                @select-node="handleGraphSearchSelect"
              />
            </div>
            <GraphSubjectBar class="graph-panel" />
          </SidePanel>
          <ForceGraph
            ref="forceGraphRef"
            :nodes="store.displayNodes"
            :edges="store.displayEdges"
            :loading="!store.graphLoaded"
            :error="store.graphError"
            :next-node-id="store.nextToLearn?.node_id || ''"
            :path-board-open="showPathBoard"
            :source-nodes="sourceNodes"
            @node-dblclick="handleNodeDblClick"
            @toggle-path-board="togglePathBoard"
            @graph-action="handleGraphAction"
          />

          <!-- 「在图谱中显示」时的来源高亮提示条（可手动取消） -->
          <div v-if="sourceHighlight" class="source-hl-chip">
            <span class="source-hl-text">
              来源高亮：{{ sourceHighlight.docName }}
              · {{ sourceHighlight.nodeIds.length }} 个知识点
            </span>
            <button type="button" class="source-hl-close" title="取消高亮"
                    @click="sourceHighlight = null">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                   stroke-width="2.5" stroke-linecap="round">
                <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
              </svg>
            </button>
          </div>

          <!-- 右侧知识库栏（统一 SidePanel）：宽度经 --kb-w 驱动画布缩放控件让位 -->
          <SidePanel
            class="graph-kb"
            side="right"
            label="知识库"
            :min="240"
            :max="560"
            storage-key="kb"
            v-model:width="kbWidth"
            v-model:collapsed="kbCollapsed"
            resizable-height
          >
            <KbPanel :key="viewEpoch" @show-in-graph="handleShowInGraph" />
          </SidePanel>

          <!-- 右侧：学习任务栏（分层看板；点图例里的「学习路径」开关）
               与知识库栏互斥（见 togglePathBoard） -->
          <Transition name="view-fade">
            <PathBoard
              v-if="showPathBoard"
              class="graph-path-board"
              :items="store.pathBoard.items"
              :recommended="store.pathBoard.recommended"
              :stats="store.pathBoard.stats"
              :loading="store.pathBoardLoading"
              :subject="store.pathBoard.subject || ''"
              :board="store.pathBoard.board || ''"
              @close="showPathBoard = false"
              @focus-node="handlePathBoardFocus"
              @edit-node="handlePathBoardEdit"
            />
          </Transition>
        </div>
      </Transition>

      <!-- 出题页（可从节点详情侧边栏「试题」跳入并聚焦某题；无跳转时行为与原来一致） -->
      <QuizView
        v-if="viewMode === 'quiz'"
        :key="quizViewKey + ':' + viewEpoch"
        :initial-subject="quizTarget?.nodeName || ''"
        :focus-question-id="quizTarget?.questionId || null"
      />

      <!-- 资源采集页 -->
      <CollectorView v-if="viewMode === 'resources'" :key="viewEpoch" />

      <!-- 学习进度仪表盘 -->
      <DashboardView
        v-if="viewMode === 'dashboard'"
        :key="viewEpoch"
        @go-graph="handleDashboardGoGraph"
        @go-node="handleDashboardGoNode"
      />

      <!-- 设置页 -->
      <SettingsView
        v-if="viewMode === 'settings'"
        :key="viewEpoch"
        @replay-onboarding="replayOnboarding"
        @switch-account="openAccountSwitch"
      />
    </div>

    <!-- 知识图谱节点详情弹窗 -->
    <NodeDetail
      :nodeInfo="nodeDetailModal"
      :visible="nodeDetailVisible"
      :source-doc-id="sourceHighlight?.docId || null"
      @close="closeNodeDetail"
      @refresh="refreshGraph"
      @save-content="handleNodeDetailSave"
      @navigate-to-node="handleNodeDetailNavigate"
      @open-quiz="handleOpenQuiz"
    />

    <!-- 用户画像面板 -->
    <UserProfile
      :visible="showUserProfile"
      @close="showUserProfile = false"
      @profile-updated="store.refreshGraph()"
    />

    <!-- 登录 / 注册弹窗（切换账号） -->
    <LoginDialog v-model:visible="showLoginDialog" @success="handleLoginSuccess" />

    <!-- 新手引导 -->
    <OnboardingGuide ref="onboardingRef" />
  </div>
</template>

<style scoped>
/* ═══ 布局容器（无顶部栏，活动栏 + 内容区直接占满） ═══ */
.app-container {
  width: 100vw;
  height: 100vh;
  overflow: hidden;
  display: flex;
}

.content-region {
  flex: 1;
  overflow: hidden;
  position: relative;
  min-width: 0;
}

/* 对话布局 */
.chat-layout {
  display: flex;
  width: 100%;
  height: 100%;
  position: absolute;
  top: 0;
  left: 0;
}

/* 对话历史侧栏：统一 SidePanel，外边距即悬浮卡片的留白 */
.conv-panel {
  margin: 12px 0 12px 12px;
}

/* 图谱布局 */
.graph-layout {
  width: 100%;
  height: 100%;
  position: absolute;
  top: 0;
  left: 0;
}

/* ── 左侧栏顶部的知识节点搜索（挂在卡片内，随侧栏一起收起） ── */
.graph-nav-search {
  flex-shrink: 0;
  padding: 10px 10px 8px;
  border-bottom: 1px solid var(--color-border);
}

/* ── 图谱左侧两级导航（学科 + 知识板块）／右侧知识库 ──
   两者都是浮在画布上的卡片：外观与交互（抽屉把手、拖宽热区、折叠动画）统一由
   SidePanel 提供，这里只给各自的定位；卡片内的分区与滚动交给子面板自己。

   高度：**随内容自适应，最多到画布四边各留 12px**（不是无条件撑满）——
   内容少时卡片就是实际需要的高度，不高高地空出一条；内容多到封顶后由内部列表滚动。 */
.graph-nav {
  position: absolute;
  top: 12px;
  left: 12px;
  max-height: calc(100% - 24px);
  z-index: 20;
}

/* ── 图谱右侧「学习任务栏」（分层看板）──
   与 .graph-kb 同一条右边缘、同一套定位口径（top/right 12px + 最多留 12px 边界），
   但两者**互斥**（见 togglePathBoard）：同时开会在同一位置重叠。 */
.graph-path-board {
  position: absolute;
  top: 12px;
  right: 12px;
  max-height: calc(100% - 24px);
  width: 360px;
  z-index: 21;
}

/* 左栏卡片里有搜索下拉（绝对定位，会超出卡片边界），卡片与内容都要放开裁切；
   收起时的隐藏靠 SidePanel 的透明度，不依赖裁切。 */
.graph-nav :deep(.sp-body),
.graph-nav :deep(.sp-content) {
  overflow: visible;
}

.graph-kb {
  position: absolute;
  top: 12px;
  right: 12px;
  max-height: calc(100% - 24px);
  z-index: 20;
}

/* 两级面板只负责内部布局，外观统一交给外层卡片。
   选择器带 .graph-nav 前缀以覆盖子组件根元素自带的 border/圆角/宽度。 */
.graph-nav .graph-panel {
  width: 100%;
  min-height: 0;
  background: transparent;
  border: none;
  border-radius: 0;
}
.graph-nav .graph-panel + .graph-panel {
  border-top: 1px solid var(--color-border);
}

/* 学科区：卡片没封顶时按内容高度，封顶后吃掉剩余高度并自己滚动（上面是固定不缩的搜索栏） */
.graph-nav .graph-subject-bar {
  flex: 1 1 auto;
}

/* 来源高亮提示条：浮在画布顶部居中 —— 只让开右栏宽度（左栏宽度是侧栏内部状态，拿不到变量） */
.source-hl-chip {
  position: absolute; z-index: 5;
  left: 50%; top: 16px;
  transform: translateX(calc(-50% - var(--kb-w, 0px) / 2));
  display: flex; align-items: center; gap: 8px;
  padding: 6px 8px 6px 12px; border-radius: 16px;
  background: var(--color-bg-secondary); border: 1px solid var(--color-accent-light);
  color: var(--color-text-secondary); font-size: 12px;
  box-shadow: var(--shadow-popup);
  max-width: min(420px, 60vw);
}
.source-hl-text { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.source-hl-close {
  flex-shrink: 0; display: inline-flex; align-items: center; justify-content: center;
  width: 20px; height: 20px; padding: 0; border: none; border-radius: 50%;
  background: transparent; color: var(--color-text-muted); cursor: pointer;
}
.source-hl-close:hover { background: var(--color-bg-hover); color: var(--color-text-primary); }

/* 画布右下角的缩放控件同样要给右栏让位（它原来贴 right:16px）；
   rightReserve 把知识库栏与学习任务栏一起算进去（两边互斥）。 */
.graph-layout :deep(.zoom-controls) {
  right: calc(32px + var(--kb-w, 0px));
  transition: right 0.28s cubic-bezier(0.4, 0, 0.2, 1);
}

.fade-enter-active, .fade-leave-active { transition: opacity 0.2s ease; }
.fade-enter-from, .fade-leave-to { opacity: 0; }
.fade-enter-to, .fade-leave-from { opacity: 1; }
</style>
