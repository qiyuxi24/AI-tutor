<script setup>
/**
 * ForceGraph.vue — 知识图谱视图外壳（UI 层）
 *
 * 渲染内核已独立到 `utils/forceGraphEngine.js`（D3 力场 / 增量 join / 拖拽·缩放 / 连线拖线），
 * 本组件只负责：
 *   1. 把 store 的可见图与高亮态喂给内核；
 *   2. 右键菜单 → 编辑弹窗 / 展开合并 / 建边删边 的交互编排；
 *   3. 覆盖层：加载 / 错误 / 空态、缩放控件、图例 + 学习任务栏入口、更新提示。
 *
 * 设计风格：Obsidian 极简 —— 纯色节点、细线边、无光晕/渐变/装饰。
 *
 * 数据流（单向，去耦合）：
 *   Store.knowledgeNodes/Edges → (props) → ForceGraph → (emit: graph-action) → HomeView
 */

import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import ContextMenu from './ContextMenu.vue'
import EditDialog from './EditDialog.vue'
import { notifyError } from '../utils/feedback'
import { useGraphForces } from '../utils/graphForces'
import { useContextMenu } from '../utils/contextMenu'
import { createForceGraphEngine } from '../utils/forceGraphEngine'

/* ================================================================
   组件 Props
   ================================================================ */
const props = defineProps({
  nodes: { type: Array, default: () => [] },
  edges: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  error: { type: String, default: '' },
  autoRefresh: { type: Boolean, default: false },
  refreshInterval: { type: Number, default: 30000 },
  // ── 科技树联动（学习进度） ──
  // 下一步推荐节点 id（薄弱点脉冲标记）
  nextNodeId: { type: String, default: '' },
  // 右侧「学习任务栏」是否打开（仅用于底部按钮的高亮态，图本身不参与路径表达）
  pathBoardOpen: { type: Boolean, default: false },
})

/* ================================================================
   组件 Events
   ================================================================ */
const emit = defineEmits([
  'node-click',
  'node-dblclick',
  'graph-action',
  'toggle-path-board',
])

/* ================================================================
   响应式状态
   ================================================================ */
const containerRef = ref(null)
const currentZoom = ref(1)
const graphChanged = ref(false)
const changeTimer = ref(null)
const autoRefreshTimer = ref(null)

// 右键菜单状态与开关：与侧栏共用同一套实现（utils/contextMenu.js），
// 菜单条目在模板的 ContextMenu 插槽里按 targetType 给出。
const {
  visible: menuVisible,
  x: menuX,
  y: menuY,
  targetType: menuTargetType,
  targetData: menuTargetData,
  open: showContextMenu,
  close: closeMenu,
} = useContextMenu()

const dialogVisible = ref(false)
const dialogMode = ref('create-node')
const dialogData = ref({})

// 力导向参数（设置页可调；模块级单例，改参数即时生效，见 utils/graphForces.js）
const { forces } = useGraphForces()

/* ================================================================
   渲染内核接线
   ================================================================ */
let engine = null

/** 喂给内核的高亮态（下一步推荐节点的脉冲环） */
function highlightState() {
  return { nextNodeId: props.nextNodeId }
}

onMounted(() => {
  engine = createForceGraphEngine({
    container: containerRef.value,
    forces: forces.value,
    highlight: highlightState(),
    handlers: {
      onNodeClick: (nodeId) => emit('node-click', nodeId),
      onNodeDblClick: (nodeId) => emit('node-dblclick', nodeId),
      onContextMenu: (event, type, data) => showContextMenu(event, type, data),
      onZoomChange: (k) => { currentZoom.value = k },
      // 点空白：收起右键菜单
      onCanvasClick: () => { if (menuVisible.value) menuVisible.value = false },
      onDrawTarget: handleDrawTarget,
    },
  })
  engine.update(props.nodes, props.edges)
  window.addEventListener('resize', handleResize)
  startAutoRefresh()
})

onUnmounted(() => {
  engine?.destroy()
  engine = null
  window.removeEventListener('resize', handleResize)
  stopAutoRefresh()
  if (changeTimer.value) {
    clearTimeout(changeTimer.value)
    changeTimer.value = null
  }
})

// 数据变化 → 交给内核（内部按指纹去重，未变则不重绘）
watch(() => [props.nodes, props.edges], () => {
  engine?.update(props.nodes, props.edges)
}, { deep: true })

// ── 科技树联动：推荐节点变化时增量刷新样式（不重建布局） ──
watch(() => props.nextNodeId, syncHighlight)
function syncHighlight() {
  engine?.setHighlight(highlightState())
}

// 设置页调参 → 立即作用于当前仿真（不重建、不重置视野）
watch(forces, () => engine?.applyForces(forces.value), { deep: true })

function handleResize() {
  engine?.resize()
}

/* ================================================================
   缩放控制
   ================================================================ */
function zoomIn() { engine?.zoomIn() }
function zoomOut() { engine?.zoomOut() }
function zoomReset() { engine?.zoomReset() }

/* ================================================================
   右键菜单事件 → 编辑弹窗 / 图操作
   ================================================================ */
function handleCreateNode() {
  dialogMode.value = 'create-node'
  dialogData.value = {}
  dialogVisible.value = true
}

function handleEditNode(nodeData) {
  dialogMode.value = 'edit-node'
  dialogData.value = nodeData
  dialogVisible.value = true
}

function handleAddEdgeFromNode(nodeId) {
  engine?.startEdgeDrawing(nodeId)
}

function handleEditEdge(edgeData) {
  dialogMode.value = 'edit-edge'
  dialogData.value = { ...edgeData.edge }
  dialogVisible.value = true
}

/* ================================================================
   连线模式落点 → 建边（成功后补开"编辑边"弹窗）
   ================================================================ */
async function handleDrawTarget(fromId, toId) {
  if (!fromId) return
  if (toId === fromId) {
    notifyError('不能连接到自身')
    return
  }

  emit('graph-action', {
    action: 'create-edge',
    payload: { from: fromId, to: toId, relation: 'related', label: '' },
  })

  await new Promise((resolve) => {
    let resolved = false
    const timeout = setTimeout(() => {
      if (!resolved) { resolved = true; stop(); resolve() }
    }, 5000)
    const stop = watch(() => props.edges, (newEdges) => {
      const found = (newEdges || []).find(e => {
        const eSource = e.source || e.from_node || e.from
        const eTarget = e.target || e.to_node || e.to
        return eSource === fromId && eTarget === toId && e.relation === 'related'
      })
      if (found) {
        resolved = true
        clearTimeout(timeout)
        stop()
        dialogMode.value = 'edit-edge'
        dialogData.value = { ...found }
        dialogVisible.value = true
      }
    }, { deep: true, immediate: true })
  })
}

/* ================================================================
   删除操作
   ================================================================ */
function handleDeleteNode(nodeId) {
  if (!confirm(`确定删除节点「${nodeId}」及其所有关联边吗？此操作不可撤销。`)) return
  emit('graph-action', { action: 'delete-node', payload: { nodeId } })
}

function handleDeleteEdge(edgeData) {
  if (!confirm('确定删除这条边吗？')) return
  emit('graph-action', { action: 'delete-edge', payload: { edgeId: edgeData.edge.edgeId } })
}

/* ================================================================
   编辑弹窗提交
   ================================================================ */
function handleDialogSubmit(formData) {
  switch (dialogMode.value) {
    case 'create-node':
      emit('graph-action', {
        action: 'create-node',
        payload: { name: formData.name, tags: formData.tags, content: formData.content || undefined },
      })
      break
    case 'edit-node': {
      const nodeId = dialogData.value?.id
      if (!nodeId) return
      emit('graph-action', {
        action: 'edit-node',
        payload: { nodeId, name: formData.name, tags: formData.tags },
      })
      break
    }
    case 'edit-edge': {
      const edgeId = dialogData.value?.edgeId
      if (edgeId == null) return
      emit('graph-action', {
        action: 'edit-edge',
        payload: { edgeId, relation: formData.relation, label: formData.label },
      })
      break
    }
  }
}

/* ================================================================
   自动刷新（仅"数量变化"提示，真刷新走 store 的 SSE / refreshGraph）
   ================================================================ */
let lastNodeCount = 0
let lastEdgeCount = 0
function checkGraphUpdate() {
  const nn = (props.nodes || []).length
  const ne = (props.edges || []).length
  if (lastNodeCount && lastEdgeCount && (nn !== lastNodeCount || ne !== lastEdgeCount)) {
    showChangeNotification()
  }
  lastNodeCount = nn
  lastEdgeCount = ne
}

function showChangeNotification() {
  graphChanged.value = true
  if (changeTimer.value) clearTimeout(changeTimer.value)
  changeTimer.value = setTimeout(() => { graphChanged.value = false }, 3000)
}

function startAutoRefresh() {
  if (!props.autoRefresh) return
  stopAutoRefresh()
  autoRefreshTimer.value = setInterval(checkGraphUpdate, props.refreshInterval)
}

function stopAutoRefresh() {
  if (autoRefreshTimer.value) {
    clearInterval(autoRefreshTimer.value)
    autoRefreshTimer.value = null
  }
}

watch(() => props.autoRefresh, (val) => {
  if (val) startAutoRefresh()
  else stopAutoRefresh()
})

/* ================================================================
   暴露方法
   ================================================================ */
/**
 * 聚焦指定节点：平滑移动到该节点并高亮
 * @param {string} nodeId - 节点 ID
 */
function focusNode(nodeId) {
  engine?.focusNode(nodeId)
}

defineExpose({ focusNode })
</script>

<template>
  <div ref="containerRef" class="force-graph-container">
    <!-- 加载状态 -->
    <div v-if="loading && props.nodes.length === 0" class="loading-overlay">
      <div class="spinner"></div>
      <p>图谱加载中...</p>
    </div>

    <!-- 错误状态 -->
    <div v-else-if="error" class="error-overlay">
      <p>{{ error }}</p>
      <span class="error-hint">请确认后端服务已启动</span>
    </div>

    <!-- 空状态 -->
    <div v-else-if="!loading && props.nodes.length === 0 && !error" class="empty-overlay">
      <div class="empty-icon">◉</div>
      <p>知识图谱暂无内容</p>
      <p class="empty-hint">右键空白区域创建第一个节点</p>
    </div>

    <!-- 缩放控制 -->
    <div v-if="props.nodes.length > 0" class="zoom-controls">
      <button class="zoom-btn" title="放大" @click="zoomIn">+</button>
      <span class="zoom-level">{{ currentZoom }}x</span>
      <button class="zoom-btn" title="缩小" @click="zoomOut">−</button>
      <button class="zoom-btn zoom-reset" title="重置视图" @click="zoomReset">↺</button>
    </div>

    <!-- 科技树图例 + 学习路径开关（底部中央） -->
    <div v-if="props.nodes.length > 0" class="graph-legend">
      <span class="legend-item"><span class="legend-dot dot-unstarted"></span>未开始</span>
      <span class="legend-item"><span class="legend-dot dot-weak"></span>薄弱</span>
      <span class="legend-item"><span class="legend-dot dot-learning"></span>学习中</span>
      <span class="legend-item"><span class="legend-dot dot-mastered"></span>已掌握</span>
      <span class="legend-divider"></span>
      <button
        class="path-toggle"
        :class="{ active: pathBoardOpen }"
        :title="pathBoardOpen ? '收起学习任务栏' : '打开学习任务栏（分层列出知识点与前置）'"
        @click="emit('toggle-path-board')"
      >
        <span class="path-toggle-dot"></span>学习路径
      </button>
    </div>

    <!-- 图谱更新提示 -->
    <transition name="fade">
      <div v-if="graphChanged" class="update-toast">
        图谱已更新
      </div>
    </transition>

    <!-- 右键菜单：容器（定位/遮罩）由 ContextMenu 提供，条目在这里按目标类型给出 -->
    <ContextMenu :visible="menuVisible" :x="menuX" :y="menuY" @close="closeMenu">
      <template #default="{ close }">
        <!-- ── 空白区域 ── -->
        <template v-if="menuTargetType === 'canvas'">
          <div class="menu-item" @click="handleCreateNode(); close()">
            <span class="menu-icon">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
                <line x1="12" y1="5" x2="12" y2="19" />
                <line x1="5" y1="12" x2="19" y2="12" />
              </svg>
            </span>
            创建新节点
          </div>
        </template>

        <!-- ── 节点 ── -->
        <template v-else-if="menuTargetType === 'node'">
          <!-- 普通知识点 -->
          <div class="menu-item" @click="handleEditNode(menuTargetData); close()">
              <span class="menu-icon">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z" />
                </svg>
              </span>
              编辑节点
            </div>
            <div class="menu-item" @click="handleAddEdgeFromNode(menuTargetData?.id); close()">
              <span class="menu-icon">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
                  <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
                </svg>
              </span>
              添加关联边
            </div>
            <div class="menu-divider"></div>
            <div class="menu-item menu-item-danger" @click="handleDeleteNode(menuTargetData?.id); close()">
              <span class="menu-icon">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M3 6h18" />
                  <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6" />
                  <path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
                  <line x1="10" y1="11" x2="10" y2="17" />
                  <line x1="14" y1="11" x2="14" y2="17" />
                </svg>
              </span>
              删除节点
            </div>
        </template>

        <!-- ── 边 ── -->
        <template v-else-if="menuTargetType === 'edge'">
          <div class="menu-item" @click="handleEditEdge(menuTargetData); close()">
            <span class="menu-icon">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z" />
              </svg>
            </span>
            编辑边标签
          </div>
          <div class="menu-divider"></div>
          <div class="menu-item menu-item-danger" @click="handleDeleteEdge(menuTargetData); close()">
            <span class="menu-icon">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M3 6h18" />
                <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6" />
                <path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
                <line x1="10" y1="11" x2="10" y2="17" />
                <line x1="14" y1="11" x2="14" y2="17" />
              </svg>
            </span>
            删除边
          </div>
        </template>
      </template>
    </ContextMenu>

    <!-- 编辑弹窗 -->
    <EditDialog
      :visible="dialogVisible"
      :mode="dialogMode"
      :data="dialogData"
      @close="dialogVisible = false"
      @submit="handleDialogSubmit"
    />
  </div>
</template>

<style scoped>
/* ================================================================
   容器
   ================================================================ */
.force-graph-container {
  width: 100%;
  height: 100%;
  background: var(--color-bg-primary);
  border-radius: 8px;
  overflow: hidden;
  position: relative;
}

.force-graph-container :deep(svg) { display: block; }

/* ── D3 元素（极简过渡；节点/边由 forceGraphEngine.js 生成） ── */
.force-graph-container :deep(.node-body) {
  transition: r 0.15s ease, opacity 0.15s ease, stroke-width 0.15s ease;
}
.force-graph-container :deep(.node-label) {
  transition: opacity 0.15s ease, font-weight 0.15s ease;
}
.force-graph-container :deep(text) {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  user-select: none;
  pointer-events: none;
}
.force-graph-container :deep(.zoom-container) { pointer-events: auto; }
.force-graph-container :deep(.links line) {
  transition: stroke 0.15s ease, stroke-width 0.15s ease, stroke-opacity 0.15s ease;
}
.force-graph-container :deep(.link-labels text) {
  transition: opacity 0.15s ease;
}

/* ── 边击中区 ── */
.force-graph-container :deep(.edge-hit-lines line) {
  cursor: pointer;
}

/* ================================================================
   覆盖层
   ================================================================ */
.loading-overlay {
  position: absolute; inset: 0;
  display: flex; flex-direction: column;
  align-items: center; justify-content: center;
  color: var(--color-text-secondary); font-size: 14px; gap: 16px; z-index: 10;
}
.spinner {
  width: 32px; height: 32px;
  border: 2px solid var(--color-border);
  border-top-color: var(--color-accent);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }

.error-overlay {
  position: absolute; inset: 0;
  display: flex; flex-direction: column;
  align-items: center; justify-content: center;
  color: var(--color-red); font-size: 14px; gap: 6px; z-index: 10;
}
.error-hint { font-size: 12px; color: var(--color-text-secondary); margin-top: 2px; }

.empty-overlay {
  position: absolute; inset: 0;
  display: flex; flex-direction: column;
  align-items: center; justify-content: center;
  color: var(--color-text-secondary); font-size: 14px; gap: 6px; z-index: 10;
}
.empty-icon { font-size: 36px; opacity: 0.4; }
.empty-hint { font-size: 12px; color: var(--color-text-muted); margin-top: 4px; }

/* ================================================================
   缩放控制
   ================================================================ */
.zoom-controls {
  position: absolute; bottom: 20px; right: 16px;
  display: flex; flex-direction: column; align-items: center; gap: 4px; z-index: 20;
}
.zoom-btn {
  width: 28px; height: 28px;
  border: 1px solid var(--color-border-subtle);
  border-radius: 4px;
  background: var(--color-bg-primary);
  color: var(--color-text-secondary);
  font-size: 14px; font-weight: 500; line-height: 1;
  cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  transition: background 0.15s, border-color 0.15s, color 0.15s;
}
.zoom-btn:hover { background: var(--color-bg-surface); color: var(--color-text-primary); border-color: var(--color-border); }
.zoom-reset { font-size: 12px; margin-top: 4px; border-top: 1px solid var(--color-border-subtle); padding-top: 8px; }
.zoom-level { font-size: 10px; color: var(--color-text-muted); user-select: none; padding: 2px 0; }

/* ================================================================
   科技树图例 + 学习路径开关（左下角）
   ================================================================ */
.graph-legend {
  position: absolute; bottom: 16px; left: 16px;
  display: flex; align-items: center; gap: 10px;
  padding: 6px 12px;
  background: var(--color-bg-primary);
  border: 1px solid var(--color-border-subtle);
  border-radius: 6px;
  font-size: 11px;
  color: var(--color-text-secondary);
  z-index: 20;
  user-select: none;
}
.legend-item { display: flex; align-items: center; gap: 5px; }
.legend-dot { width: 9px; height: 9px; border-radius: 50%; flex-shrink: 0; }
.dot-unstarted { background: var(--color-graph-node); }
.dot-weak { background: var(--color-red); }
.dot-learning { background: var(--color-yellow); }
.dot-mastered { background: var(--color-green); }

.legend-divider { width: 1px; height: 14px; background: var(--color-border-subtle); }
.path-toggle {
  display: flex; align-items: center; gap: 6px;
  border: 1px solid var(--color-border-subtle);
  border-radius: 4px;
  background: transparent;
  color: var(--color-text-secondary);
  font-size: 11px;
  padding: 3px 8px;
  cursor: pointer;
  transition: all 0.15s;
}
.path-toggle:hover { border-color: var(--color-accent); color: var(--color-text-primary); }
.path-toggle.active {
  border-color: var(--color-accent);
  background: var(--color-accent);
  color: var(--color-bg-primary);
}
.path-toggle-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--color-graph-edge); }
.path-toggle.active .path-toggle-dot { background: var(--color-bg-primary); }

/* 薄弱点脉冲环：推荐节点扩散动画 */
.node-pulse { transform-origin: center; transform-box: fill-box; animation: nodePulse 2.2s ease-out infinite; }
@keyframes nodePulse {
  0%   { transform: scale(1);    opacity: 0.85; }
  65%  { transform: scale(1.9);  opacity: 0; }
  100% { transform: scale(1.9);  opacity: 0; }
}

/* ================================================================
   更新提示
   ================================================================ */
.update-toast {
  position: absolute; bottom: 20px; left: 50%;
  transform: translateX(-50%);
  background: var(--color-bg-surface);
  color: var(--color-text-primary);
  padding: 8px 18px; border-radius: 6px;
  font-size: 12px; font-weight: 500;
  border: 1px solid var(--color-border-subtle);
  box-shadow: 0 2px 12px rgba(0,0,0,0.15);
  z-index: 30;
  white-space: nowrap;
}
.fade-enter-active { animation: toastIn 0.2s ease; }
.fade-leave-active { animation: toastOut 0.2s ease; }
@keyframes toastIn {
  from { opacity: 0; transform: translateX(-50%) translateY(8px); }
  to   { opacity: 1; transform: translateX(-50%) translateY(0); }
}
@keyframes toastOut {
  from { opacity: 1; transform: translateX(-50%) translateY(0); }
  to   { opacity: 0; transform: translateX(-50%) translateY(8px); }
}
</style>
