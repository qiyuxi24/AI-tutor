<script setup>
/**
 * HomeView.vue — 主视图（编排层）
 *
 * 职责：
 *   1. 视图切换（对话 ↔ 知识图谱）
 *   2. 编排数据流：Store ↔ 子组件
 *   3. 监听 ForceGraph 的 graph-action 事件，调用 Store CRUD 方法
 *   4. 管理 NodeDetail 弹窗
 *
 * 去耦设计：
 *   - ForceGraph 只负责渲染 + 用户交互，不直接调用 API
 *   - HomeView 是唯一编排层：监听 graph-action → 调用 Store 方法
 *   - Store 统一管理图谱数据和 CRUD 操作
 *   - NodeDetail 的保存操作也通过 emit 回到 HomeView 处理
 */

import { ref, onMounted, computed } from 'vue'
import { useChatStore } from '../stores/chatStore'
import { useAuthStore } from '../stores/authStore'
// ★ 不再直接 import apiClient —— 图谱 CRUD 统一走 Store
import { formatError, clientError } from '../utils/errorCodes.js'
import { useTheme } from '../utils/theme.js'
import Sidebar from '../components/Sidebar.vue'
import ChatArea from '../components/ChatArea.vue'
import ForceGraph from '../components/ForceGraph.vue'
import NodeDetail from '../components/NodeDetail.vue'
import UserProfile from '../components/UserProfile.vue'
import GraphSearch from '../components/GraphSearch.vue'
import OnboardingGuide from '../components/OnboardingGuide.vue'

const store = useChatStore()
const authStore = useAuthStore()
const { toggleTheme, isDark } = useTheme()
const viewMode = ref('chat')
const sidebarCollapsed = ref(false)
const showUserProfile = ref(false)
const isTransitioning = ref(false)
const showUserMenu = ref(false)
const graphSearchRef = ref(null)
const forceGraphRef = ref(null)

const SIDEBAR_WIDTH = 260

const MODE_LABELS = {
  adaptive: { label: '自适应引导', color: '#10b981' },
  free_talk: { label: '自由对话', color: '#f59e0b' },
  recursive: { label: '递归式教学', color: '#8b5cf6' },
}
const modeInfo = computed(() => MODE_LABELS[store.mode] || { label: store.mode, color: '#6b7280' })

// ─── 节点详情弹窗 ───
const nodeDetailModal = ref(null)
const nodeDetailVisible = ref(false)
const nodeDetailLoading = ref(false)

onMounted(() => {
  store.init()  // async，内部会调用 fetchGraph() + connectSSE()
})

function handleLogout() {
  authStore.logout()
  window.location.hash = '#/login'
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
 * NodeDetail 不直接调 API，通过 emit 将保存意图传递给 HomeView 编排层。
 *
 * 通过 onResult 回调通知 NodeDetail 保存结果：
 *   - 成功 → onResult(null)，NodeDetail 切回阅读模式
 *   - 失败 → onResult(errorMessage)，NodeDetail 保持编辑模式并显示错误
 */
async function handleNodeDetailSave({ nodeId, content, onResult }) {
  try {
    await store.updateNodeContent(nodeId, { content })
    // 保存成功：通知子组件
    if (onResult) onResult(null)
  } catch (e) {
    const msg = formatError(e, { action: '保存节点内容' })
    // 保存失败：通知子组件显示错误（不弹 alert，让 NodeDetail 自己展示）
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
//  所有图谱变更操作统一在此处理，调用 Store 方法。
// ════════════════════════════════════════════════════════════════

/**
 * ForceGraph emit 的 graph-action 统一处理入口。
 *
 * action 类型映射：
 *   create-node  → store.createNode(payload)
 *   edit-node    → store.updateNodeInfo(nodeId, payload)
 *   delete-node  → store.deleteNode(nodeId)
 *   create-edge  → store.createEdge(payload)
 *   edit-edge    → store.updateEdge(index, payload)
 *   delete-edge  → store.deleteEdge(index)
 *
 * 错误处理由 Store 内部完成，此处仅捕获未预期的异常。
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
    // Store 方法内部已调用 refreshGraph，此处仅提示错误
    alert(formatError(e, { action: `图谱操作: ${action}` }))
  }
}

/**
 * 搜索选中节点 → 切换到图谱视图并聚焦该节点
 */
async function handleGraphSearchSelect(nodeId) {
  // 切换到图谱视图
  if (viewMode.value !== 'graph') {
    switchViewMode('graph')
    // 等切换动画完成后聚焦
    await new Promise(r => setTimeout(r, 450))
  }
  // 聚焦节点
  forceGraphRef.value?.focusNode(nodeId)
}

/**
 * NodeDetail 中点击前置知识/相关节点 → 关闭弹窗并聚焦目标节点
 */
async function handleNodeDetailNavigate(nodeId) {
  // 关闭节点详情弹窗
  nodeDetailVisible.value = false
  // 加载目标节点详情
  nodeDetailLoading.value = true
  try {
    nodeDetailModal.value = await store.fetchNodeDetail(nodeId)
    nodeDetailVisible.value = true
  } catch {
    nodeDetailModal.value = { id: nodeId, name: '加载失败', content: clientError('NODE_LOAD') }
    nodeDetailVisible.value = true
  } finally {
    nodeDetailLoading.value = false
  }
  // 同步在图谱中聚焦
  if (viewMode.value === 'graph') {
    await new Promise(r => setTimeout(r, 100))
    forceGraphRef.value?.focusNode(nodeId)
  }
}

function toggleSidebar() {
  sidebarCollapsed.value = !sidebarCollapsed.value
}

function switchViewMode(newMode) {
  if (isTransitioning.value || viewMode.value === newMode) return
  sidebarCollapsed.value = true
  isTransitioning.value = true
  viewMode.value = newMode
  setTimeout(() => {
    isTransitioning.value = false
  }, 300)
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
    <!-- ═══ 顶部栏 ═══ -->
    <header class="app-top-bar">
      <button class="sidebar-toggle-btn" @click="toggleSidebar" title="切换侧边栏">
        <svg v-if="sidebarCollapsed" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round">
          <path d="M9 18l6-6-6-6"/>
        </svg>
        <svg v-else width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round">
          <path d="M15 18l-6-6 6-6"/>
        </svg>
      </button>

      <div class="tb-brand" :class="{ collapsed: sidebarCollapsed }" :style="{ width: SIDEBAR_WIDTH + 'px' }">
        <span class="brand">AI Tutor</span>
      </div>

      <div class="tb-right">
        <div class="view-switch" :class="{ disabled: isTransitioning }">
          <span :class="{ active: viewMode === 'chat' }" @click="switchViewMode('chat')">对话</span>
          <div class="switch-slider" :class="{ switched: viewMode === 'graph' }" @click="switchViewMode(viewMode === 'chat' ? 'graph' : 'chat')">
            <div class="slider-dot"></div>
          </div>
          <span :class="{ active: viewMode === 'graph' }" @click="switchViewMode('graph')">图谱</span>
        </div>

        <div v-if="viewMode === 'chat'" class="mode-tag" :style="{
          background: modeInfo.color + '14',
          color: modeInfo.color,
          borderColor: modeInfo.color + '30'
        }">
          <span class="mode-dot" :style="{ background: modeInfo.color }"></span>
          {{ modeInfo.label }}
        </div>

        <button class="tb-btn" @click="toggleTheme" :title="isDark() ? '切换浅色模式' : '切换深色模式'">
          <!-- 太阳图标（浅色模式） -->
          <svg v-if="!isDark()" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <circle cx="12" cy="12" r="5"/>
            <line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/>
            <line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/>
            <line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/>
            <line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/>
          </svg>
          <!-- 月亮图标（深色模式） -->
          <svg v-else width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>
          </svg>
        </button>

        <!-- 用户菜单 -->
        <div class="user-menu-wrapper" v-click-outside="() => showUserMenu = false">
          <button class="user-btn" @click="showUserMenu = !showUserMenu" title="用户菜单">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/>
              <circle cx="12" cy="7" r="4"/>
            </svg>
            <span class="user-name">{{ authStore.username }}</span>
          </button>
          <Transition name="menu-fade">
            <div v-if="showUserMenu" class="user-dropdown">
              <div class="dropdown-item info">
                <span class="info-label">用户名</span>
                <span class="info-value">{{ authStore.username }}</span>
              </div>
              <div class="dropdown-divider"></div>
              <button class="dropdown-item action" @click="showUserProfile = true; showUserMenu = false">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/>
                  <circle cx="12" cy="7" r="4"/>
                </svg>
                用户画像
              </button>
              <div class="dropdown-divider"></div>
              <button class="dropdown-item logout" @click="handleLogout">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/>
                  <polyline points="16 17 21 12 16 7"/>
                  <line x1="21" y1="12" x2="9" y2="12"/>
                </svg>
                退出登录
              </button>
            </div>
          </Transition>
        </div>
      </div>
    </header>

    <!-- ═══ 主内容区域 ═══ -->
    <div class="main-content">
      <Transition name="view-fade" v-bind="slideTransition">
        <div v-if="viewMode === 'chat'" class="app-layout" data-view="chat">
          <div class="sidebar-panel" :class="{ collapsed: sidebarCollapsed }" :style="{ width: SIDEBAR_WIDTH + 'px' }">
            <Sidebar />
          </div>
          <ChatArea :sidebarCollapsed="sidebarCollapsed" @navigate-to-node="handleGraphSearchSelect" />
        </div>
      </Transition>

      <Transition name="view-fade" v-bind="slideTransition">
        <div v-if="viewMode === 'graph'" class="graph-layout" data-view="graph">
          <!-- 图谱搜索 -->
          <div class="graph-search-bar">
            <GraphSearch
              ref="graphSearchRef"
              :nodes="store.knowledgeNodes"
              @select-node="handleGraphSearchSelect"
            />
          </div>
          <ForceGraph
            ref="forceGraphRef"
            :nodes="store.knowledgeNodes"
            :edges="store.knowledgeEdges"
            :loading="!store.graphLoaded"
            :error="store.graphError"
            @node-dblclick="handleNodeDblClick"
            @graph-action="handleGraphAction"
          />
        </div>
      </Transition>
    </div>

    <!-- 知识图谱节点详情弹窗 -->
    <NodeDetail
      :nodeInfo="nodeDetailModal"
      :visible="nodeDetailVisible"
      @close="closeNodeDetail"
      @refresh="refreshGraph"
      @save-content="handleNodeDetailSave"
      @navigate-to-node="handleNodeDetailNavigate"
    />

    <!-- 用户画像面板 -->
    <UserProfile
      :visible="showUserProfile"
      @close="showUserProfile = false"
      @profile-updated="store.refreshGraph()"
    />

    <!-- 新手引导 -->
    <OnboardingGuide />
  </div>
</template>

<style scoped>
/* ═══ 布局容器 ═══ */
.app-container {
  width: 100vw;
  height: 100vh;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

/* ═══ 顶部栏 ═══ */
.app-top-bar {
  display: flex;
  align-items: center;
  height: 48px;
  background: var(--color-bg-primary);
  border-bottom: 1px solid var(--color-border);
  flex-shrink: 0;
  padding: 0 12px;
  gap: 4px;
  z-index: 10;
}

.sidebar-toggle-btn {
  width: 34px;
  height: 34px;
  flex-shrink: 0;
  border: none;
  background: transparent;
  color: var(--color-text-primary);
  border-radius: 8px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background 0.2s ease;
}
.sidebar-toggle-btn:hover { background: var(--color-bg-surface); }

.tb-brand {
  display: flex;
  align-items: center;
  padding-left: 6px;
  transition: width 0.3s cubic-bezier(0.4, 0.0, 0.2, 1),
              opacity 0.25s cubic-bezier(0.4, 0.0, 0.2, 1);
  overflow: hidden;
  flex-shrink: 0;
  white-space: nowrap;
}
.tb-brand.collapsed {
  width: 0 !important;
  opacity: 0;
}

.brand {
  font-size: 16px;
  font-weight: 700;
  color: var(--color-text-inverse);
  white-space: nowrap;
}

.tb-right {
  display: flex;
  align-items: center;
  gap: 12px;
  flex: 1;
  justify-content: center;
}

.tb-btn {
  width: 34px;
  height: 34px;
  border: none;
  background: var(--color-bg-surface);
  color: var(--color-text-primary);
  border-radius: 8px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background 0.2s ease;
  flex-shrink: 0;
}
.tb-btn:hover { background: var(--color-bg-hover); }

.view-switch {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 13px;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
}
.view-switch span {
  color: var(--color-text-tertiary);
  cursor: pointer;
  transition: color 0.2s ease;
  user-select: none;
}
.view-switch span.active { color: var(--color-text-primary); font-weight: 500; }
.view-switch.disabled span { cursor: not-allowed; opacity: 0.5; }

.switch-slider {
  width: 44px;
  height: 22px;
  background: var(--color-bg-surface);
  border-radius: 11px;
  cursor: pointer;
  position: relative;
  transition: background 0.2s ease;
}
.switch-slider.switched { background: var(--color-blue-light); }
.slider-dot {
  position: absolute; top: 2px; left: 2px;
  width: 18px; height: 18px;
  background: var(--color-text-primary);
  border-radius: 50%;
  transition: transform 0.2s ease;
}
.switch-slider.switched .slider-dot { transform: translateX(22px); }

.mode-tag {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 12px;
  border-radius: 20px;
  font-size: 12px;
  font-weight: 500;
  border: 1px solid;
  white-space: nowrap;
  flex-shrink: 0;
}
.mode-dot { width: 6px; height: 6px; border-radius: 50%; }

/* ═══ 用户菜单 ═══ */
.user-menu-wrapper {
  position: relative;
}

.user-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  height: 34px;
  padding: 0 10px;
  border: none;
  background: var(--color-bg-surface);
  color: var(--color-text-primary);
  border-radius: 8px;
  cursor: pointer;
  font-size: 13px;
  transition: background 0.2s ease;
  flex-shrink: 0;
}
.user-btn:hover { background: var(--color-bg-hover); }
.user-name { max-width: 80px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.user-dropdown {
  position: absolute;
  top: calc(100% + 6px);
  right: 0;
  min-width: 180px;
  background: var(--color-bg-primary);
  border: 1px solid var(--color-border);
  border-radius: 10px;
  box-shadow: var(--shadow-popup);
  z-index: 100;
  overflow: hidden;
}

.dropdown-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  font-size: 13px;
  color: var(--color-text-primary);
}
.dropdown-item.info {
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
}
.info-label { font-size: 11px; color: var(--color-text-tertiary); }
.info-value { font-weight: 500; }

.dropdown-divider {
  height: 1px;
  background: var(--color-border);
}

.dropdown-item.action {
  width: 100%;
  border: none;
  background: transparent;
  cursor: pointer;
  color: var(--color-text-primary);
  transition: background 0.15s ease;
}
.dropdown-item.action:hover { background: var(--color-bg-hover); }

.dropdown-item.logout {
  width: 100%;
  border: none;
  background: transparent;
  cursor: pointer;
  color: var(--color-red);
  transition: background 0.15s ease;
}
.dropdown-item.logout:hover { background: var(--color-red-light); }

.menu-fade-enter-active, .menu-fade-leave-active {
  transition: opacity 0.15s ease, transform 0.15s ease;
}
.menu-fade-enter-from, .menu-fade-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}

/* ═══ 主内容 ═══ */
.main-content {
  flex: 1;
  overflow: hidden;
  position: relative;
}

.app-layout {
  display: flex;
  width: 100%;
  height: 100%;
  position: absolute;
  top: 0;
  left: 0;
}

.sidebar-panel {
  flex-shrink: 0;
  transition: margin-left 0.3s cubic-bezier(0.4, 0.0, 0.2, 1);
  overflow: hidden;
}
.sidebar-panel.collapsed {
  margin-left: -260px;
}

.graph-layout {
  width: 100%;
  height: 100%;
  position: absolute;
  top: 0;
  left: 0;
}

/* ── 图谱搜索栏 ── */
.graph-search-bar {
  position: absolute;
  top: 12px;
  right: 12px;
  z-index: 25;
}

.fade-enter-active, .fade-leave-active { transition: opacity 0.2s ease; }
.fade-enter-from, .fade-leave-to { opacity: 0; }
.fade-enter-to, .fade-leave-from { opacity: 1; }
</style>
