<script setup>
/**
 * HomeView.vue — 主视图（编排层）
 *
 * 布局：左侧活动栏 + 内容区（对话 / 图谱 / 知识库 / 视频导入 / 出题 / 设置），无顶部栏（沉浸式）。
 * 资源采集已改为后端 AI 工具（`search_resources` / `collect_resources`），前端入口下线：
 * CollectorView.vue 保留但不再挂载。
 * 知识库有两个入口、同一个组件 KbPanel：独立页（KbView）与图谱页右侧可收起的侧栏
 * （宽度由 --kb-w 驱动）。两处功能重复是有意的 —— 侧栏窄，只够边看图边挑资料。
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
import { useGraphLayout } from '../utils/graphLayout.js'
import FloatingBall from '../components/FloatingBall.vue'
import { fetchSourceNodes } from '../api/kb.js'
import ActivityBar from '../components/ActivityBar.vue'
import ConversationSidebar from '../components/ConversationSidebar.vue'
import ChatArea from '../components/ChatArea.vue'
import ForceGraph from '../components/ForceGraph.vue'
import GraphMindmap from '../components/GraphMindmap.vue'
import GraphBrace from '../components/GraphBrace.vue'
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
import KbView from './KbView.vue'
import LectureImportView from './LectureImportView.vue'
import LectureDetailView from './LectureDetailView.vue'
import SettingsView from './SettingsView.vue'
import AppIcon from '../components/AppIcon.vue'

const store = useChatStore()
const authStore = useAuthStore()
const viewMode = ref('chat')

/**
 * 活动栏高亮用哪个 id：课时页是「视频导入」的下钻子页，不是一级页面
 * （它没有自己的导航项）。不做这层映射的话，进课时页时左栏会**一个都不亮**，
 * 看起来像"没选中任何页面"。
 */
const activityView = computed(() => (viewMode.value === 'lecture-detail' ? 'lecture' : viewMode.value))

// ─── 图谱布局（力导向 / 思维导图 / 大括号）───
// 真值源 = utils/graphLayout.js 的模块级单例，**唯一切换入口在设置页「知识图谱 · 布局」**。
// 图谱页只读它、按它挂载对应画法，不再放切换控件（保持"看图"的纯粹）。
const { layout: graphLayout } = useGraphLayout()

// ─── 三处侧栏（统一走 SidePanel）：折叠态 + 宽度 ───
// 宽度与折叠态留在父级，是因为它们要参与布局：对话栏折叠后 ChatArea 自动占满，
// 知识库栏宽度还要驱动搜索栏/缩放控件让位（--kb-w）。持久化由 SidePanel 按 storageKey 负责。
const sidebarCollapsed = ref(false)
const convPanelWidth = ref(260)
const graphNavCollapsed = ref(false)
const graphNavWidth = ref(240)
const kbCollapsed = ref(false)
const kbWidth = ref(320)

// ─── 左侧导航的两个 tab（学科分类 / 学习路径）───
// 同一块位置二选一显示：学习路径原本从右侧弹出、与知识库栏抢同一条右边缘，
// 现在收进左栏，右侧那一条边只留给知识库。
// 语义上是"看关系" vs "看顺序"，同一批知识点的两种读法，所以放同一个容器里切换最自然。
const graphNavTab = ref('subject')   // 'subject' | 'path'

// 学习路径并入左侧导航后，右侧只剩知识库一条边 —— 画布让位只由它决定。
const rightReserve = computed(() => (kbCollapsed.value ? 0 : kbWidth.value))

/**
 * 学习路径数据只在切到「学习路径」tab 时拉；开着的时候切学科要跟着重算
 * （否则表里是上一个学科的知识点，和画布对不上）。
 * 注：曾多监听一个 `store.currentBoard`，但 store 里没这个状态（前端无板块选择器），
 * 恒为 undefined 且会让 fetchPathBoard 内部报错 —— 2026-09-29 一并清掉。
 */
watch(
  () => [graphNavTab.value, store.currentSubject],
  ([tab]) => { if (tab === 'path') store.fetchPathBoard() },
  { immediate: true }
)

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
  notifyInfo('后端已重载')
  console.info('[dev] 检测到后端重载，已局部刷新当前视图')
}

const showUserProfile = ref(false)
const showLoginDialog = ref(false)
const graphSearchRef = ref(null)
const forceGraphRef = ref(null)
const onboardingRef = ref(null)

/** 学习路径「在图谱中定位」：把画布视角移到该节点（引擎自带平滑聚焦） */
function handlePathBoardFocus(nodeId) {
  forceGraphRef.value?.focusNode(nodeId)
}

/** 学习路径「详情」：复用节点详情弹窗，不做第二套入口 */
async function handlePathBoardEdit(nodeId) {
  await handleNodeDblClick(nodeId)
}

/**
 * 「去学习」（节点详情 / 学习路径都走这里）：切到对话视图，以该知识点为教学内容开一轮学习。
 *
 * 顺序要求：**先切视图再发消息** —— 消息会立刻渲染在当前对话里，
 * 若还在图谱页，学生只会看到"消息莫名跑到别处去了"。
 */
function handleGoLearn(nodeId) {
  if (!nodeId) return
  const name = store.knowledgeNodes.find((n) => n.id === nodeId)?.name || ''
  viewMode.value = 'chat'
  sidebarCollapsed.value = true
  store.startLearning(nodeId, name)
}

// ─── 视频课时页（导入页「看讲义」/ 节点详情「视频出处」进来）───
// 课时页是从导入页/节点详情下钻的**子页面**，不占左栏导航项：
// 左栏的 'lecture' 仍指向导入页，返回时回那里 —— 用户的心智模型是"我进去看了下细节"，
// 而不是"我换了个一级页面"。
const lectureDocId = ref(0)
const lectureTitle = ref('')
const lectureAt = ref(null)      // 落点（秒）：来自「视频出处」的 locator；null = 从头开始

/**
 * 打开某节课的课时页。
 * @param {number} docId KB 文件节点 id（接口的 {doc_id}）
 * @param {string} [title] 加载期间先显示的标题
 * @param {number|null} [at] 落到第几秒（缺省 = 第一段）
 */
function openLecture(docId, title = '', at = null) {
  if (!docId) return
  lectureDocId.value = Number(docId) || 0
  lectureTitle.value = title || ''
  lectureAt.value = Number.isFinite(at) ? at : null
  viewMode.value = 'lecture-detail'
}

// ─── 节点详情弹窗 ───
const nodeDetailModal = ref(null)
const nodeDetailVisible = ref(false)
const nodeDetailLoading = ref(false)

// ─── 节点详情 → 出题页跳转 ───
// quizTarget = { nodeName, graphNodeId, questionId }
//   graphNodeId：图谱节点 id —— 出题时带给后端，题目入库的 knowledge_point 记成它，
//                判分才能回写这个节点的掌握度（否则判完分回图谱颜色不变）。
//   questionId：为空表示只按节点名预填主题。
const quizTarget = ref(null)
// 目标变化时重建 QuizView（v-if 在外层视图切换时已重挂，此 key 兜住"已在出题页再跳题"）
const quizViewKey = computed(() =>
  quizTarget.value
    ? `${quizTarget.value.nodeName}#${quizTarget.value.graphNodeId ?? ''}#${quizTarget.value.questionId ?? ''}`
    : 'quiz'
)

onMounted(async () => {
  // 登录墙：未登录就只弹 LoginDialog（mandatory），**不拉任何需要 token 的数据** ——
  // init() 里的 fetchSubjects/fetchGraph/connectSSE 全都要 token，没先登录整屏就是 401。
  // 登录成功后由 handleLoginSuccess 重载页面，再走一遍这里。
  const ok = await authStore.ensureSession()
  if (!ok) {
    showLoginDialog.value = true
    return
  }
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
  // 视图卸载 = 本次会话的生命周期结束：断 SSE、清定时器、掐在途流（A3）。
  // 不做的话离开页面后仍在后台重连 / 定时同步。重挂时 init() 会重新接线。
  store.dispose()
})

/** 切换账号：打开登录弹窗（#/login 路由页已取消，也不再提供"退出登录"）*/
function openAccountSwitch() {
  showLoginDialog.value = true
}

/** 登录/注册成功：必须重载，否则 store 里还是上一个账号的图谱与对话记录 */
function handleLoginSuccess() {
  window.location.reload()
}

/** 视频导入页「去知识库」：知识库面板挂在图谱视图右侧（没有独立的 kb 视图） */
/** 视频导入页「去知识库」→ 独立知识库页（不再绕回图谱页） */
function handleGoKb() {
  viewMode.value = 'kb'
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
 * nodeId（图谱节点 id）必须透传 —— 出题与判分靠它把结果回写到这个节点的掌握度。
 * 需先关闭详情弹窗，否则全屏遮罩挡住出题页。
 */
function handleOpenQuiz({ nodeId = '', nodeName, questionId = null }) {
  quizTarget.value = { nodeName, graphNodeId: nodeId, questionId }
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
  // 学习路径 tab 开着时一并重算：改完掌握度要立刻看到建议顺序与追溯结果变化
  if (graphNavTab.value === 'path') store.fetchPathBoard()
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
      notifyInfo(`「${docName}」没有关联的知识点`)
      return
    }

    const count = {}
    for (const n of nodes) if (n.subject) count[n.subject] = (count[n.subject] || 0) + 1
    const subject = Object.keys(count).sort((a, b) => count[b] - count[a])[0] || ''
    const ids = nodes.filter(n => !subject || n.subject === subject).map(n => n.id)
    sourceHighlight.value = { docId, docName, nodeIds: ids, subject, total: nodes.length }

    if (viewMode.value !== 'graph') {
      viewMode.value = 'graph'
      await new Promise(r => setTimeout(r, 450))   // 从知识库页跳过来：等图谱挂载完再聚焦
    }
    if (subject && subject !== store.currentSubject) {
      await store.setSubject(subject)
      await new Promise(r => setTimeout(r, 450))   // 等新学科的力导向图渲染完
    }
    forceGraphRef.value?.focusNode(ids[0])
    notifyInfo(ids.length < nodes.length
      ? `「${docName}」涉及 ${nodes.length} 个知识点，已切到「${subject}」并高亮 ${ids.length} 个`
      : `已高亮 ${ids.length} 个知识点`)
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
    <!-- ═══ 活动栏 + 内容区域（无顶部栏，全沉浸） ═══ -->
    <ActivityBar
      :active-view="activityView"
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
          <!-- 左侧导航：搜索 + （学科列表 / 学习路径）二选一。
               外观/折叠/拖宽都由 SidePanel 统一提供（贴左缘、满高），收起时整个导航一起收起。 -->
          <SidePanel
            class="graph-nav"
            side="left"
            label="学科导航"
            :min="180"
            :max="360"
            storage-key="graphNav"
            v-model:width="graphNavWidth"
            v-model:collapsed="graphNavCollapsed"
          >
            <div class="graph-nav-search">
              <GraphSearch
                ref="graphSearchRef"
                :nodes="store.knowledgeNodes"
                @select-node="handleGraphSearchSelect"
              />
            </div>

            <!-- 两个 tab：同一块位置二选一。
                 「学科分类」= 看关系（有哪些学科、推进到哪）；「学习路径」= 看顺序（先学什么）。
                 学习路径原先是右侧浮动面板（与知识库栏抢同一条右边缘），现已并入此处。 -->
            <div class="graph-nav-tabs" role="tablist">
              <button
                class="graph-nav-tab"
                role="tab"
                :class="{ active: graphNavTab === 'subject' }"
                :aria-selected="graphNavTab === 'subject'"
                title="按学科浏览知识图谱"
                @click="graphNavTab = 'subject'"
              >
                <AppIcon name="layers" :size="14" />
                <span>学科分类</span>
              </button>
              <button
                class="graph-nav-tab"
                role="tab"
                :class="{ active: graphNavTab === 'path' }"
                :aria-selected="graphNavTab === 'path'"
                title="按先修关系分层，看该先学什么"
                @click="graphNavTab = 'path'"
              >
                <AppIcon name="route" :size="14" />
                <span>学习路径</span>
              </button>
            </div>

            <GraphSubjectBar v-if="graphNavTab === 'subject'" class="graph-panel" />

            <!-- 学习路径：与「学科分类」共用这块位置，所以学科跟着当前选中走
                 （切换入口在「学科分类」tab，本面板只读不切） -->
            <div v-else class="graph-nav-path-body">
              <PathBoard
                :items="store.pathBoard.items"
                :recommended="store.pathBoard.recommended"
                :loading="store.pathBoardLoading"
                :subject="store.pathBoard.subject || ''"
                :board="store.pathBoard.board || ''"
                @focus-node="handlePathBoardFocus"
                @edit-node="handlePathBoardEdit"
                @learn-node="handleGoLearn"
                @switch-tab="graphNavTab = 'subject'"
              />
            </div>

            <!-- 掌握度四色图例：从画布左下角的浮动框挪到侧栏底部常驻。
                原来那个框正好被贴左满高的侧栏盖住（重叠）；放这里三种布局都看得到、也不压画布。 -->
            <div v-if="store.displayNodes.length" class="graph-nav-legend">
              <span class="gnl-item"><i class="gnl-dot d-unstarted"></i>未开始</span>
              <span class="gnl-item"><i class="gnl-dot d-weak"></i>薄弱</span>
              <span class="gnl-item"><i class="gnl-dot d-learning"></i>学习中</span>
              <span class="gnl-item"><i class="gnl-dot d-mastered"></i>已掌握</span>
            </div>
          </SidePanel>
          <!-- 三种画法共用同一份 store.displayNodes/Edges：只换渲染层，不换数据（方案 §5.1）。
               ref 三处同名没问题 —— 同一时刻只挂载一个，forceGraphRef 永远指向当前那个，
               所以「在图谱中定位」/ 搜索聚焦在三种布局下都能用。 -->
          <ForceGraph
            v-if="graphLayout === 'force'"
            ref="forceGraphRef"
            :nodes="store.displayNodes"
            :edges="store.displayEdges"
            :loading="!store.graphLoaded"
            :error="store.graphError"
            :source-nodes="sourceNodes"
            @node-dblclick="handleNodeDblClick"
            @graph-action="handleGraphAction"
          />
          <GraphMindmap
            v-else-if="graphLayout === 'mindmap'"
            ref="forceGraphRef"
            :nodes="store.displayNodes"
            :edges="store.displayEdges"
            :loading="!store.graphLoaded"
            :error="store.graphError"
            :source-nodes="sourceNodes"
            :root-label="store.currentSubject || ''"
            @open-node="handleNodeDblClick"
          />
          <GraphBrace
            v-else
            ref="forceGraphRef"
            :nodes="store.displayNodes"
            :loading="!store.graphLoaded"
            :error="store.graphError"
            :source-nodes="sourceNodes"
            @open-node="handleNodeDblClick"
          />

          <!-- 建图中的画布内进度（2026-11-16）：KbPanel 里那条在右栏，收起/切页就看不见，
               而建图是分钟级后台任务，用户在看图时得知道"还在长"。空心节点是骨架态，
               填色 = 正文已写好（口径 filled/total，后端 graph_status）。 -->
          <div v-if="store.graphBuildProgress" class="build-progress-chip">
            <span class="bp-dot"></span>
            <span class="bp-text">正在生成知识图谱</span>
            <span class="bp-count">
              {{ store.graphBuildProgress.filled || 0 }}/{{ store.graphBuildProgress.total || 0 }}
            </span>
            <span class="bp-bar"><i :style="{ width: (store.graphBuildProgress.percent || 0) + '%' }"></i></span>
          </div>

          <!-- 「在图谱中显示」时的来源高亮提示条（可手动取消） -->
          <div v-if="sourceHighlight" class="source-hl-chip">
            <span class="source-hl-text">
              {{ sourceHighlight.docName }}
              · {{ sourceHighlight.nodeIds.length }} 个知识点
            </span>
            <button type="button" class="source-hl-close" title="取消高亮"
                    @click="sourceHighlight = null">
              <AppIcon name="x" :size="12" />
            </button>
          </div>

          <!-- 右侧知识库栏（统一 SidePanel，贴右缘满高）：宽度经 --kb-w 驱动画布缩放控件让位 -->
          <SidePanel
            class="graph-kb"
            side="right"
            label="知识库"
            :min="240"
            :max="560"
            storage-key="kb"
            v-model:width="kbWidth"
            v-model:collapsed="kbCollapsed"
          >
            <KbPanel :key="viewEpoch" @show-in-graph="handleShowInGraph" />
          </SidePanel>
        </div>
      </Transition>

      <!-- 知识库独立页：左栏文件树（KbPanel）+ 右侧常驻阅读/编辑区（DocWorkspace）。
           与图谱页右侧栏共用同一份 KbPanel，但两处职责不同：侧栏窄、只够边看图边挑资料
           （单击选中 / 双击弹只读预览），独立页是集中阅读与修改（单击即送正文到右侧，
           .md/.markdown/.txt 可直接编辑保存）。DocWorkspace 与节点详情弹窗共用同一个组件。 -->
      <KbView
        v-if="viewMode === 'kb'"
        :key="viewEpoch"
        @show-in-graph="handleShowInGraph"
        @open-lecture="(e) => openLecture(e.docId, e.title)"
      />

      <!-- 视频课程导入（链接 → 讲义 → 知识库；依赖探测在组件 setup 里，故挂 viewEpoch） -->
      <LectureImportView
        v-if="viewMode === 'lecture'"
        :key="viewEpoch"
        @go-kb="handleGoKb"
        @open-lecture="(e) => openLecture(e.docId, e.title)"
      />

      <!-- 视频课时页：从导入页「看讲义」或节点详情「视频出处」下钻进来。
           key 里带 docId —— 换一集必须整体重挂（标题/时间轴/选中项都换），
           再带上 viewEpoch 才能享受开发态后端重载后的自动刷新。 -->
      <LectureDetailView
        v-if="viewMode === 'lecture-detail'"
        :key="'lecture-' + lectureDocId + ':' + viewEpoch"
        :doc-id="lectureDocId"
        :title="lectureTitle"
        :at="lectureAt"
        @back="viewMode = 'lecture'"
      />

      <!-- 出题页（可从节点详情侧边栏「试题」跳入并聚焦某题；无跳转时行为与原来一致） -->
      <QuizView
        v-if="viewMode === 'quiz'"
        :key="quizViewKey + ':' + viewEpoch"
        :initial-subject="quizTarget?.nodeName || ''"
        :graph-node-id="quizTarget?.graphNodeId || ''"
        :focus-question-id="quizTarget?.questionId || null"
        @mastery-changed="refreshGraph"
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
      @open-quiz="handleOpenQuiz"
      @go-learn="handleGoLearn"
      @open-lecture="(e) => { nodeDetailVisible = false; openLecture(e.docId, '', e.at) }"
    />

    <!-- 用户画像面板 -->
    <UserProfile
      :visible="showUserProfile"
      @close="showUserProfile = false"
      @profile-updated="store.refreshGraph()"
    />

    <!-- 登录 / 注册弹窗：未登录时是登录墙（不可关），已登录时是"切换账号" -->
    <LoginDialog
      v-model:visible="showLoginDialog"
      :mandatory="!authStore.isLoggedIn"
      @success="handleLoginSuccess"
    />

    <!-- 新手引导：登录后再展示（未登录时挂着会被登录墙遮住，语义也不对） -->
    <OnboardingGuide v-if="authStore.isLoggedIn" ref="onboardingRef" />
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

/* 对话历史侧栏：统一 SidePanel，贴左缘满高，不留外边距 */

/* 图谱布局 */
.graph-layout {
  width: 100%;
  height: 100%;
  position: absolute;
  top: 0;
  left: 0;
}

/* ── 左侧栏顶部的知识节点搜索（挂在卡片内，随侧栏一起收起） ──
   这一行就是侧栏首行，左侧让开折叠按钮（按钮 26px 高、top:9px，居中在 22px；
   搜索框 32px 高 + 6px 上内边距 → 中心也是 22px，两者对得上）。 */
.graph-nav-search {
  flex-shrink: 0;
  padding: 6px 10px 8px var(--sp-head-pad-l, 10px);
  border-bottom: 1px solid var(--color-border);
}

/* ── 侧栏顶部两个 tab（学科分类 / 学习路径）：同一块位置二选一 ──
   与搜索栏一样常驻不缩，下面的内容区自己滚；选中态沿用条目那套「强调色底 + 文字变深」。 */
.graph-nav-tabs {
  flex-shrink: 0;
  display: flex;
  border-bottom: 1px solid var(--color-border);
  padding: 6px 8px;
  gap: 4px;
}
.graph-nav-tab {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  /* 内边距压到刚够：侧栏能拖到 180px，两个 4 字标签 + 图标要挤得下（放不下才省略号） */
  padding: 6px 6px;
  border: none;
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--color-text-secondary);
  font-family: inherit;
  font-size: var(--font-size-xs);
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
}
.graph-nav-tab span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.graph-nav-tab:hover { background: var(--color-bg-hover); color: var(--color-text-primary); }
.graph-nav-tab.active {
  background: var(--color-accent-light);
  color: var(--color-accent);
  font-weight: 600;
}
.graph-nav-tab svg { flex-shrink: 0; opacity: 0.65; }
.graph-nav-tab.active svg { opacity: 1; }

/* 学习路径面板：吃掉 tab 以下的全部高度，内部自己滚（PathBoard 是 flex 列，不自带卡片外观） */
.graph-nav-path-body {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  overflow: hidden;
}

/* 掌握度图例：侧栏底部常驻条（原是画布左下角浮动框，被侧栏盖住 → 重叠）。
   flex-shrink:0 让它钉在 .sp-content 最下，上方可滚内容吃掉剩余高度。 */
.graph-nav-legend {
  flex-shrink: 0;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 10px;
  padding: 8px 10px;
  border-top: 1px solid var(--color-border);
  font-size: var(--font-size-xs);
  color: var(--color-text-secondary);
  user-select: none;
}
.gnl-item { display: inline-flex; align-items: center; gap: 5px; white-space: nowrap; }
.gnl-dot { width: 9px; height: 9px; border-radius: 50%; flex-shrink: 0; }
.d-unstarted { background: var(--color-graph-node); }
.d-weak { background: var(--color-red); }
.d-learning { background: var(--color-yellow); }
.d-mastered { background: var(--color-green); }

/* ── 图谱左侧导航／右侧知识库 ──
   外观与交互（折叠按钮、拖宽热区）统一由 SidePanel 提供：**贴画布边缘、满高**，
   这里只给定位；卡片内的分区与滚动交给子面板自己。 */
.graph-nav {
  position: absolute;
  top: 0;
  bottom: 0;
  left: 0;
  z-index: 20;
}

.graph-kb {
  position: absolute;
  top: 0;
  bottom: 0;
  right: 0;
  z-index: 20;
}

/* 左栏里有搜索下拉（绝对定位，会超出面板边界），面板与内容都要放开裁切；
   收起时的隐藏靠 SidePanel 的透明度，不依赖裁切。 */
.graph-nav :deep(.sp-body),
.graph-nav :deep(.sp-content) {
  overflow: visible;
}

/* 两级面板只负责内部布局，外观统一交给外层容器。
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

/* 建图中的画布内进度：贴画布底部居中（顶部正上方是来源高亮条、右下角是缩放控件，
   三处互不打架）。水平只让开右栏宽度 —— 左栏是浮动面板且宽度拿不到变量，
   居中比贴左更稳（帖左会压在左栏上面）。 */
.build-progress-chip {
  position: absolute; z-index: 5;
  left: 50%; bottom: 20px;
  transform: translateX(calc(-50% - var(--kb-w, 0px) / 2));
  display: flex; align-items: center; gap: 8px;
  max-width: min(340px, 60vw);
  padding: 6px 10px;
  border-radius: var(--radius-xl);
  background: var(--color-bg-secondary); border: 1px solid var(--color-border);
  color: var(--color-text-secondary); font-size: 12px;
  box-shadow: var(--shadow-popup);
}
/* 呼吸点：没有它这条像是"静止的提示"，有它才看得出还在跑 */
.bp-dot {
  flex-shrink: 0; width: 7px; height: 7px; border-radius: 50%;
  background: var(--color-accent); animation: bp-pulse 1.2s ease-in-out infinite;
}
@keyframes bp-pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.25; } }
.bp-text { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.bp-count { flex-shrink: 0; font-variant-numeric: tabular-nums; color: var(--color-text-primary); }
.bp-bar {
  flex-shrink: 0; width: 64px; height: 3px; border-radius: 2px;
  background: var(--color-border-light); overflow: hidden;
}
.bp-bar i { display: block; height: 100%; background: var(--color-accent); transition: width 0.3s ease; }

/* 来源高亮提示条：浮在画布顶部居中 —— 只让开右栏宽度（左栏宽度是侧栏内部状态，拿不到变量） */
.source-hl-chip {
  position: absolute; z-index: 5;
  left: 50%; top: 16px;
  transform: translateX(calc(-50% - var(--kb-w, 0px) / 2));
  display: flex; align-items: center; gap: 8px;
  padding: 6px 8px 6px 12px; border-radius: var(--radius-xl);
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
   rightReserve 把知识库栏算进去（学习路径已并入左侧导航，右侧只留知识库）。 */
.graph-layout :deep(.zoom-controls) {
  right: calc(32px + var(--kb-w, 0px));
  transition: right 0.28s cubic-bezier(0.4, 0, 0.2, 1);
}

.fade-enter-active, .fade-leave-active { transition: opacity 0.2s ease; }
.fade-enter-from, .fade-leave-to { opacity: 0; }
.fade-enter-to, .fade-leave-from { opacity: 1; }
</style>
