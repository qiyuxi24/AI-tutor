<script setup>
/**
 * HomeView.vue — 主视图（编排层）
 *
 * 布局：左侧活动栏 + 内容区（对话 / 图谱 / 知识库 / 设置），无顶部栏（沉浸式）
 *
 * 职责：
 *   1. 页面切换，由左侧活动栏驱动
 *   2. 编排数据流：Store ↔ 子组件
 *   3. 监听 ForceGraph 的 graph-action 事件，调用 Store CRUD 方法
 *   4. 管理 NodeDetail 弹窗
 *
 * 主题切换 / 用户菜单已下沉到 ActivityBar 组件内部。
 */

import { ref, onMounted } from 'vue'
import { useChatStore } from '../stores/chatStore'
import { useAuthStore } from '../stores/authStore'
import { formatError, clientError } from '../utils/errorCodes.js'
import { notifyError } from '../utils/feedback'
import ActivityBar from '../components/ActivityBar.vue'
import ConversationSidebar from '../components/ConversationSidebar.vue'
import ChatArea from '../components/ChatArea.vue'
import ForceGraph from '../components/ForceGraph.vue'
import GraphSubjectBar from '../components/GraphSubjectBar.vue'
import GraphBoardSidebar from '../components/GraphBoardSidebar.vue'
import NodeDetail from '../components/NodeDetail.vue'
import UserProfile from '../components/UserProfile.vue'
import GraphSearch from '../components/GraphSearch.vue'
import OnboardingGuide from '../components/OnboardingGuide.vue'
import KnowledgeView from './KnowledgeView.vue'
import QuizView from './QuizView.vue'
import CollectorView from './CollectorView.vue'
import SettingsView from './SettingsView.vue'
import DashboardView from './DashboardView.vue'

const store = useChatStore()
const authStore = useAuthStore()
const viewMode = ref('chat')
const sidebarCollapsed = ref(false)
const graphNavCollapsed = ref(false)

// ─── 图谱导航：宽度拖拽（手动存 localStorage，宽度不持久化的话拖了也白拖）───
const GRAPH_PANEL_MIN = 140
const GRAPH_PANEL_MAX = 340
const graphPanelWidth = ref(Number(localStorage.getItem('graphPanelWidth')) || 176)
const graphPanelResizing = ref(false)

function startGraphResize(e) {
  const startX = e.clientX
  const startW = graphPanelWidth.value
  graphPanelResizing.value = true
  const onMove = (ev) => {
    graphPanelWidth.value = Math.min(
      GRAPH_PANEL_MAX,
      Math.max(GRAPH_PANEL_MIN, startW + ev.clientX - startX)
    )
  }
  const onUp = () => {
    graphPanelResizing.value = false
    localStorage.setItem('graphPanelWidth', String(graphPanelWidth.value))
    document.removeEventListener('mousemove', onMove)
    document.removeEventListener('mouseup', onUp)
  }
  document.addEventListener('mousemove', onMove)
  document.addEventListener('mouseup', onUp)
}
const showUserProfile = ref(false)
const graphSearchRef = ref(null)
const forceGraphRef = ref(null)
const onboardingRef = ref(null)

const SIDEBAR_WIDTH = 260

// ─── 节点详情弹窗 ───
const nodeDetailModal = ref(null)
const nodeDetailVisible = ref(false)
const nodeDetailLoading = ref(false)

onMounted(() => {
  // init() 内部依次：fetchSubjects() → ensureSubjectSelected() → fetchGraph() → connectSSE()
  store.init()
})

function handleLogout() {
  authStore.logout()
  window.location.hash = '#/login'
}

function handleActivitySelect(id) {
  if (viewMode.value === id) return
  viewMode.value = id
  // 离开对话视图时，仅收起对话侧栏（保留对话状态）
  if (id !== 'chat') sidebarCollapsed.value = true
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

function toggleSidebar() {
  sidebarCollapsed.value = !sidebarCollapsed.value
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
      @logout="handleLogout"
    />

    <div class="content-region">
      <!-- 对话页 -->
      <Transition name="view-fade" v-bind="slideTransition">
        <div v-if="viewMode === 'chat'" class="chat-layout" data-view="chat">
          <!-- 对话历史二级侧栏（可折叠） -->
          <div class="conv-panel" :class="{ collapsed: sidebarCollapsed }" :style="{ width: SIDEBAR_WIDTH + 'px' }">
            <ConversationSidebar />
          </div>

          <!-- 统一的折叠切换按钮（展开/收起同一按钮，图标随状态变化） -->
          <button
            class="conv-toggle-btn"
            :class="{ collapsed: sidebarCollapsed }"
            @click="toggleSidebar"
            :title="sidebarCollapsed ? '展开对话列表' : '收起对话列表'"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
              <polyline v-if="!sidebarCollapsed" points="15 18 9 12 15 6" />
              <polyline v-else points="9 18 15 12 9 6" />
            </svg>
          </button>

          <ChatArea
            :sidebarCollapsed="sidebarCollapsed"
            @navigate-to-node="handleGraphSearchSelect"
          />
        </div>
      </Transition>

      <!-- 知识图谱页 -->
      <Transition name="view-fade" v-bind="slideTransition">
        <div v-if="viewMode === 'graph'" class="graph-layout" data-view="graph">
          <div class="graph-topbar">
            <div class="graph-search-bar">
              <GraphSearch
                ref="graphSearchRef"
                :nodes="store.knowledgeNodes"
                @select-node="handleGraphSearchSelect"
              />
            </div>
          </div>
          <!-- 左侧两级导航：学科收藏栏（一级）→ 知识板块（二级）
               一次只渲染一个学科，避免图谱无限生长。
               两级栏合并为一张卡片，右缘把手既可点击折叠、也可拖拽调宽。 -->
          <div
            class="graph-nav"
            :class="{ collapsed: graphNavCollapsed, resizing: graphPanelResizing }"
            :style="{ '--panel-w': graphPanelWidth + 'px' }"
          >
            <div class="graph-panel-stack">
              <GraphSubjectBar class="graph-panel" />
              <GraphBoardSidebar class="graph-panel" />
              <span class="graph-resizer" @mousedown.prevent="startGraphResize"></span>
            </div>
            <button
              class="graph-collapse-btn"
              @click="graphNavCollapsed = !graphNavCollapsed"
              :title="graphNavCollapsed ? '展开学科导航' : '收起学科导航'"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                <polyline v-if="!graphNavCollapsed" points="15 18 9 12 15 6" />
                <polyline v-else points="9 18 15 12 9 6" />
              </svg>
            </button>
          </div>
          <ForceGraph
            ref="forceGraphRef"
            :nodes="store.knowledgeNodes"
            :edges="store.knowledgeEdges"
            :loading="!store.graphLoaded"
            :error="store.graphError"
            :learning-path="store.learningPath"
            :next-node-id="store.nextToLearn?.node_id || ''"
            @node-dblclick="handleNodeDblClick"
            @graph-action="handleGraphAction"
          />
        </div>
      </Transition>

      <!-- 知识库页 -->
      <KnowledgeView v-if="viewMode === 'knowledge'" />

      <!-- 出题页 -->
      <QuizView v-if="viewMode === 'quiz'" />

      <!-- 资源采集页 -->
      <CollectorView v-if="viewMode === 'resources'" />

      <!-- 学习进度仪表盘 -->
      <DashboardView
        v-if="viewMode === 'dashboard'"
        @go-graph="handleDashboardGoGraph"
        @go-node="handleDashboardGoNode"
      />

      <!-- 设置页 -->
      <SettingsView
        v-if="viewMode === 'settings'"
        @replay-onboarding="replayOnboarding"
        @logout="handleLogout"
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
    />

    <!-- 用户画像面板 -->
    <UserProfile
      :visible="showUserProfile"
      @close="showUserProfile = false"
      @profile-updated="store.refreshGraph()"
    />

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

.conv-panel {
  flex-shrink: 0;
  transition: margin-left 0.3s cubic-bezier(0.4, 0.0, 0.2, 1);
  overflow: hidden;
  border-right: 1px solid var(--color-border);
}
.conv-panel.collapsed {
  margin-left: -260px;
}

/* 折叠后的展开条 */
/* 统一的折叠切换按钮（展开态在侧栏右缘，收起态在内容区左缘） */
.conv-toggle-btn {
  position: absolute;
  top: 50%;
  transform: translateY(-50%);
  width: 22px;
  height: 52px;
  border: 1px solid var(--color-border);
  border-radius: 7px;
  background: var(--color-bg-primary);
  color: var(--color-text-tertiary);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 20;
  transition: left 0.3s cubic-bezier(0.4, 0.0, 0.2, 1), background 0.2s, color 0.2s;
}
/* 展开态：按钮贴着侧栏右边缘 */
.conv-toggle-btn {
  left: 260px;
}
/* 收起态：按钮移到内容区左边缘，形状保持不变，仅内部箭头翻转 */
.conv-toggle-btn.collapsed {
  left: 0;
}
.conv-toggle-btn:hover {
  background: var(--color-bg-surface);
  color: var(--color-text-primary);
}

/* 图谱布局 */
.graph-layout {
  width: 100%;
  height: 100%;
  position: absolute;
  top: 0;
  left: 0;
}

/* ── 图谱顶部栏（搜索） ── */
.graph-topbar {
  position: absolute;
  top: 12px;
  left: 16px;
  right: 12px;
  z-index: 25;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 12px;
  pointer-events: none;
}
.graph-topbar > * {
  pointer-events: auto;
}

/* ── 图谱左侧两级导航（学科收藏栏 + 知识板块） ──
   布局：一张圆角卡片（两级栏用细分隔线分区）+ 右缘把手（点击折叠 / 拖拽调宽）。
   宽度由 --panel-w 驱动（拖拽时内联更新），收起时卡片宽度归零并淡出。 */
.graph-nav {
  position: absolute;
  top: 64px;
  left: 12px;
  bottom: 12px;
  z-index: 20;
  display: flex;
  align-items: stretch;
}

.graph-panel-stack {
  position: relative;
  display: flex;
  flex-direction: column;
  width: var(--panel-w, 176px);
  background: var(--color-bg-secondary);
  border: 1px solid var(--color-border);
  border-radius: 12px;
  overflow: hidden;
  transition: width 0.28s cubic-bezier(0.4, 0, 0.2, 1), opacity 0.2s ease;
}

.graph-nav.collapsed .graph-panel-stack {
  width: 0;
  opacity: 0;
  border-width: 0;
  pointer-events: none;
}

/* 拖拽中禁用过渡，否则面板跟不上鼠标 */
.graph-nav.resizing .graph-panel-stack {
  transition: none;
}

/* 两级面板只负责内部布局，外观统一交给外层卡片。
   选择器带 .graph-nav 前缀以覆盖子组件根元素自带的 border/圆角/宽度。 */
.graph-nav .graph-panel {
  width: 100%;
  min-height: 0;
  flex-shrink: 1; /* 子组件自带 flex-shrink:0，改纵向排布后需允许收缩才能内部滚动 */
  background: transparent;
  border: none;
  border-radius: 0;
}
.graph-nav .graph-panel + .graph-panel {
  border-top: 1px solid var(--color-border);
}

/* 右缘拖拽热区：平时隐形，悬停才浮出一条强调色竖线 */
.graph-resizer {
  position: absolute;
  top: 0;
  right: 0;
  bottom: 0;
  width: 7px;
  z-index: 1;
  cursor: col-resize;
}
.graph-resizer::after {
  content: '';
  position: absolute;
  top: 0;
  right: 0;
  bottom: 0;
  width: 2px;
  background: transparent;
  transition: background 0.18s;
}
.graph-resizer:hover::after {
  background: var(--color-accent, #5b8ff9);
}

/* 折叠把手：像抽屉拉手一样贴在卡片右缘，默认半隐、悬停浮现 */
.graph-collapse-btn {
  align-self: center;
  flex-shrink: 0;
  width: 16px;
  height: 46px;
  margin-left: -1px;
  padding: 0;
  border: 1px solid var(--color-border);
  border-left: none;
  border-radius: 0 8px 8px 0;
  background: var(--color-bg-secondary);
  color: var(--color-text-tertiary);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  opacity: 0.5;
  transition: opacity 0.18s, background 0.18s, color 0.18s;
}
.graph-collapse-btn:hover {
  opacity: 1;
  background: var(--color-bg-surface);
  color: var(--color-text-primary);
}

/* 收起后卡片消失，把手脱开浮在原位，补全四边与圆角提示可展开 */
.graph-nav.collapsed .graph-collapse-btn {
  opacity: 0.85;
  border-left: 1px solid var(--color-border);
  border-radius: 8px;
}

/* ── 图谱搜索栏 ── */
.graph-search-bar {
  z-index: 25;
}

.fade-enter-active, .fade-leave-active { transition: opacity 0.2s ease; }
.fade-enter-from, .fade-leave-to { opacity: 0; }
.fade-enter-to, .fade-leave-from { opacity: 1; }
</style>
