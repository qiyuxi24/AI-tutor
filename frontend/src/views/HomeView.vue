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

import { ref, onMounted, onUnmounted, computed } from 'vue'
import { useChatStore, BACKEND_RELOADED_EVENT } from '../stores/chatStore'
import { useAuthStore } from '../stores/authStore'
import { formatError, clientError } from '../utils/errorCodes.js'
import { notifyError, notifyInfo } from '../utils/feedback'
import ActivityBar from '../components/ActivityBar.vue'
import ConversationSidebar from '../components/ConversationSidebar.vue'
import ChatArea from '../components/ChatArea.vue'
import ForceGraph from '../components/ForceGraph.vue'
import GraphSubjectBar from '../components/GraphSubjectBar.vue'
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
 * NodeDetail 掌握度滑块：通过 Store.updateMastery() 执行。
 */
async function handleNodeDetailMastery({ nodeId, mastery, onResult }) {
  try {
    await store.updateMastery(nodeId, mastery)
    if (onResult) onResult(null)
  } catch (e) {
    const msg = formatError(e, { action: '更新掌握度' })
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
          :style="{ '--kb-w': kbCollapsed ? '0px' : kbWidth + 'px' }"
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
            :learning-path="store.learningPath"
            :next-node-id="store.nextToLearn?.node_id || ''"
            @node-dblclick="handleNodeDblClick"
            @graph-action="handleGraphAction"
          />

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
            <KbPanel :key="viewEpoch" />
          </SidePanel>
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
      @close="closeNodeDetail"
      @refresh="refreshGraph"
      @save-content="handleNodeDetailSave"
      @update-mastery="handleNodeDetailMastery"
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

/* 画布右下角的缩放控件同样要给右栏让位（它原来贴 right:16px） */
.graph-layout :deep(.zoom-controls) {
  right: calc(32px + var(--kb-w, 0px));
  transition: right 0.28s cubic-bezier(0.4, 0, 0.2, 1);
}

.fade-enter-active, .fade-leave-active { transition: opacity 0.2s ease; }
.fade-enter-from, .fade-leave-to { opacity: 0; }
.fade-enter-to, .fade-leave-from { opacity: 1; }
</style>
