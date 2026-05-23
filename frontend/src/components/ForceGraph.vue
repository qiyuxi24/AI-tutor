<script setup>
/**
 * ForceGraph.vue — D3 力导向知识图谱组件
 *
 * 功能：
 *   - D3 力场仿真渲染节点和边
 *   - 缩放平移（d3.zoom + 控制按钮）
 *   - 节点拖拽（d3.drag，优先级高于 zoom）
 *   - 鼠标悬停高亮关联节点/边
 *   - 右键菜单（ContextMenu）创建/编辑/删除节点和边
 *   - 编辑弹窗（EditDialog）表单输入
 *   - 自动刷新检测图谱变化
 *
 * Props:
 *   nodes, edges — 图谱数据（由父组件 fetch 后传入）
 *   loading, error — 状态
 *   autoRefresh, refreshInterval — 自动刷新
 *
 * Emits:
 *   node-click, node-dblclick — 节点交互
 *   graph-changed — CRUD 后通知父组件重新 fetch 数据
 */

import { ref, onMounted, onUnmounted, watch, nextTick } from 'vue'
import * as d3 from 'd3'
import ContextMenu from './ContextMenu.vue'
import EditDialog from './EditDialog.vue'

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
})

/* ================================================================
   组件 Events
   ================================================================ */
const emit = defineEmits([
  'node-click',
  'node-dblclick',
  'graph-changed',  // CRUD 操作后通知父组件刷新
])

/* ================================================================
   响应式状态
   ================================================================ */
const containerRef = ref(null)

// 自动刷新
const graphChanged = ref(false)
const changeTimer = ref(null)
const autoRefreshTimer = ref(null)
const currentZoom = ref(1)

// 右键菜单状态
const menuVisible = ref(false)
const menuX = ref(0)
const menuY = ref(0)
const menuTargetType = ref('canvas')
const menuTargetData = ref(null)

// 编辑弹窗状态
const dialogVisible = ref(false)
const dialogMode = ref('create-node')
const dialogData = ref({})

// ── 边连线模式 ──
const drawingEdgeMode = ref(false)     // 是否处于连线模式
const drawingSourceId = ref(null)      // 连线源节点 ID

/* ================================================================
   D3 核心对象引用（不响应式）
   ================================================================ */
let simulation = null
let svgSelection = null
let zoomBehavior = null
let zoomContainer = null

/** 边击中的透明线引用（用于右键检测） */
let edgeHitLines = null

/** 连线模式中的临时虚线 */
let drawingTempLine = null

/** mousemove 回调引用（用于移除监听器） */
let drawingMouseMoveHandler = null

/* ================================================================
   工具函数
   ================================================================ */
function masteryColor(mastery) {
  if (mastery == null || mastery === 0) return '#9ca3af'
  if (mastery <= 25)  return '#86efac'
  if (mastery <= 50)  return '#34d399'
  if (mastery <= 75)  return '#10b981'
  return '#059669'
}

/* ================================================================
   图谱渲染核心
   ================================================================ */
function initForceGraph(nodes, links) {
  if (!containerRef.value) return

  const { width, height } = containerRef.value.getBoundingClientRect()

  d3.select(containerRef.value).select('svg').remove()

  // ── SVG ──
  svgSelection = d3.select(containerRef.value)
    .append('svg')
    .attr('width', '100%')
    .attr('height', '100%')
    .style('display', 'block')

  // ── 箭头标记 ──
  svgSelection.append('defs').append('marker')
    .attr('id', 'arrowhead')
    .attr('viewBox', '-0 -5 10 10')
    .attr('refX', 20).attr('refY', 0)
    .attr('orient', 'auto')
    .attr('markerWidth', 6).attr('markerHeight', 6)
    .append('path')
    .attr('d', 'M 0,-5 L 10,0 L 0,5')
    .attr('fill', '#6b7280')

  // ── Zoom 容器 ──
  zoomContainer = svgSelection.append('g')
    .attr('class', 'zoom-container')

  // ── Zoom 行为 ──
  zoomBehavior = d3.zoom()
    .scaleExtent([0.1, 4])
    .filter((event) => {
      if (event.type === 'wheel' && event.ctrlKey) return false
      return event.type !== 'dblclick'
    })
    .on('zoom', (event) => {
      zoomContainer.attr('transform', event.transform)
      currentZoom.value = Math.round(event.transform.k * 100) / 100
    })

  svgSelection.call(zoomBehavior)

  // ── 力场仿真 ──
  simulation = d3.forceSimulation(nodes)
    .force('link', d3.forceLink(links).id(d => d.id).distance(150))
    .force('charge', d3.forceManyBody().strength(-300))
    .force('center', d3.forceCenter(width / 2, height / 2))
    .force('collision', d3.forceCollide().radius(40))

  // ── 连线（可视） ──
  const link = zoomContainer.append('g')
    .attr('class', 'links')
    .selectAll('line')
    .data(links)
    .enter()
    .append('line')
    .attr('stroke', '#4b5563')
    .attr('stroke-width', 2)
    .attr('marker-end', 'url(#arrowhead)')
    .style('pointer-events', 'none')

  // ── 连线（透明宽线，用于点击/右键检测） ──
  // ★ 关键：在可视连线上方叠加更宽的透明线，方便鼠标点击边
  edgeHitLines = zoomContainer.append('g')
    .attr('class', 'edge-hit-lines')
    .selectAll('line')
    .data(links)
    .enter()
    .append('line')
    .attr('stroke', 'transparent')
    .attr('stroke-width', 12)   // 12px 宽，容易点到
    .style('cursor', 'pointer')
    .style('pointer-events', 'stroke')
    .on('contextmenu', function (event, d) {
      // 右键边 → 显示边的右键菜单
      event.preventDefault()
      event.stopPropagation()
      const idx = links.indexOf(d)
      showContextMenu(event.clientX, event.clientY, 'edge', { edge: d, index: idx })
    })

  // ── 连线标签 ──
  const linkLabel = zoomContainer.append('g')
    .attr('class', 'link-labels')
    .selectAll('text')
    .data(links)
    .enter()
    .append('text')
    .attr('font-size', 10)
    .attr('fill', '#9ca3af')
    .attr('text-anchor', 'middle')
    .text(d => d.label)
    .style('pointer-events', 'none')
    .style('user-select', 'none')

  // ── 节点 ──
  const node = zoomContainer.append('g')
    .attr('class', 'nodes')
    .selectAll('g')
    .data(nodes)
    .enter()
    .append('g')
    .attr('class', 'node')
    .style('cursor', 'grab')

  // 节点拖拽
  node.call(d3.drag()
    .on('start', dragstarted)
    .on('drag', dragged)
    .on('end', dragended)
    .filter((event) => event.button === 0)
  )

  // ── 节点圆形 ──
  node.append('circle')
    .attr('r', 20)
    .attr('fill', d => masteryColor(d.mastery))
    .attr('stroke', d => masteryColor(d.mastery))
    .attr('stroke-width', 2)
    .attr('stroke-opacity', 0.5)
    .style('filter', 'drop-shadow(0 0 8px currentColor)')

  // ── 节点名称标签 ──
  node.append('text')
    .attr('dy', 35)
    .attr('text-anchor', 'middle')
    .attr('fill', '#e5e7eb')
    .attr('font-size', 12)
    .text(d => d.name)

  // ── 右键：节点 ──
  node.on('contextmenu', function (event, d) {
    event.preventDefault()
    event.stopPropagation()
    showContextMenu(event.clientX, event.clientY, 'node', d)
  })

  // ── 悬停高亮 ──
  node.on('mouseover', function (event, d) {
    event.stopPropagation()
    d3.select(this).select('circle').attr('r', 24).attr('stroke-width', 3)

    link.attr('stroke', l =>
      (l.source.id === d.id || l.target.id === d.id) ? '#fbbf24' : '#4b5563'
    ).attr('stroke-width', l =>
      (l.source.id === d.id || l.target.id === d.id) ? 3 : 2
    )

    node.select('circle').attr('opacity', n => {
      if (n.id === d.id) return 1
      return links.some(l =>
        (l.source.id === d.id && l.target.id === n.id) ||
        (l.target.id === d.id && l.source.id === n.id)
      ) ? 1 : 0.3
    })
  })

  node.on('mouseout', function (event) {
    event.stopPropagation()
    d3.select(this).select('circle').attr('r', 20).attr('stroke-width', 2)
    link.attr('stroke', '#4b5563').attr('stroke-width', 2)
    node.select('circle').attr('opacity', 1)
  })

  // ── 点击 / 双击 ──
  node.on('click', function (event, d) {
    event.stopPropagation()
    // 连线模式下：左键点击节点 = 目标节点
    if (drawingEdgeMode.value) {
      handleDrawingTarget(d.id)
      return
    }
    emit('node-click', d.id)
  })
  node.on('dblclick', function (event, d) {
    event.stopPropagation()
    emit('node-dblclick', d.id)
  })

  // ── 右键：SVG 空白区域 ──
  svgSelection.on('contextmenu', function (event) {
    event.preventDefault()
    // 连线模式下：右键取消连线
    if (drawingEdgeMode.value) {
      clearDrawingMode()
      return
    }
    // 只在点击空白区域时触发（不是节点或边）
    const target = event.target
    if (target.tagName === 'svg' || target === containerRef.value) {
      showContextMenu(event.clientX, event.clientY, 'canvas', null)
    }
  })

  // 左键点击空白：关闭菜单 / 连线模式下不做处理
  svgSelection.on('click', () => {
    if (menuVisible.value) menuVisible.value = false
  })

  // ── Tick 更新 ──
  simulation.on('tick', () => {
    link
      .attr('x1', d => d.source.x).attr('y1', d => d.source.y)
      .attr('x2', d => d.target.x).attr('y2', d => d.target.y)

    // 同步透明击中线的位置
    edgeHitLines
      .attr('x1', d => d.source.x).attr('y1', d => d.source.y)
      .attr('x2', d => d.target.x).attr('y2', d => d.target.y)

    linkLabel
      .attr('x', d => (d.source.x + d.target.x) / 2)
      .attr('y', d => (d.source.y + d.target.y) / 2)

    node.attr('transform', d => `translate(${d.x},${d.y})`)

    // 连线模式：同步临时虚线源节点位置
    if (drawingTempLine && drawingSourceId.value) {
      const src = nodes.find(n => n.id === drawingSourceId.value)
      if (src) drawingTempLine.attr('x1', src.x).attr('y1', src.y)
    }
  })

  // ── 拖拽处理 ──
  function dragstarted(event, d) {
    if (event.sourceEvent) event.sourceEvent.stopPropagation()
    if (!event.active) simulation.alphaTarget(0.3).restart()
    d.fx = d.x; d.fy = d.y
    d3.select(this).style('cursor', 'grabbing')
  }
  function dragged(event, d) {
    if (event.sourceEvent) event.sourceEvent.stopPropagation()
    d.fx = event.x; d.fy = event.y
  }
  function dragended(event, d) {
    if (event.sourceEvent) event.sourceEvent.stopPropagation()
    if (!event.active) simulation.alphaTarget(0)
    d.fx = null; d.fy = null
    d3.select(this).style('cursor', 'grab')
  }
}

/* ================================================================
   图谱渲染入口
   ================================================================ */
function renderGraph() {
  const nodes = (props.nodes || []).map(n => ({ ...n }))
  const links = (props.edges || []).map(e => ({
    source: e.source || e.from,
    target: e.target || e.to,
    label: e.label,
    relation: e.relation || '',
  }))
  if (nodes.length > 0 && containerRef.value) {
    initForceGraph(nodes, links)
  }
}

watch(() => [props.nodes, props.edges], () => {
  renderGraph()
}, { deep: true })

/* ================================================================
   右键菜单控制
   ================================================================ */
function showContextMenu(x, y, type, data) {
  menuX.value = x
  menuY.value = y
  menuTargetType.value = type
  menuTargetData.value = data
  menuVisible.value = true
}

function closeMenu() {
  menuVisible.value = false
}

/* ================================================================
   右键菜单事件 → 打开编辑弹窗
   ================================================================ */

function handleCreateNode(payload) {
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
  // 进入连线模式：从该节点拖出一条虚线到鼠标光标
  startEdgeDrawing(nodeId)
}

/* ================================================================
   连线模式（拖线创建边）
   ================================================================ */

/**
 * 进入连线模式：从指定节点出发，显示一条跟随鼠标的虚线
 * 左键点击目标节点 → 创建边并打开编辑弹窗
 * 右键 → 取消连线
 */
function startEdgeDrawing(sourceNodeId) {
  drawingEdgeMode.value = true
  drawingSourceId.value = sourceNodeId

  // 找到源节点在仿真中的坐标
  const sourceNode = simulation?.nodes()?.find(n => n.id === sourceNodeId)
  const sx = sourceNode?.x ?? 0
  const sy = sourceNode?.y ?? 0

  // 在 zoomContainer 上追加临时虚线
  drawingTempLine = zoomContainer.append('line')
    .attr('class', 'drawing-temp-line')
    .attr('x1', sx).attr('y1', sy)
    .attr('x2', sx).attr('y2', sy)
    .attr('stroke', '#60a5fa')
    .attr('stroke-width', 2)
    .attr('stroke-dasharray', '8,4')
    .style('pointer-events', 'none')

  // 改变 SVG 光标
  svgSelection.style('cursor', 'crosshair')

  // 在 SVG 上监听鼠标移动
  drawingMouseMoveHandler = (event) => {
    if (!drawingEdgeMode.value || !drawingTempLine) return
    // 将屏幕坐标转换为 zoomContainer 坐标
    const [mx, my] = d3.pointer(event, svgSelection.node())
    const transform = d3.zoomTransform(svgSelection.node())
    const zx = (mx - transform.x) / transform.k
    const zy = (my - transform.y) / transform.k
    drawingTempLine.attr('x2', zx).attr('y2', zy)
  }
  svgSelection.on('mousemove.drawing', drawingMouseMoveHandler)
}

/**
 * 退出连线模式，清理临时虚线及事件监听
 */
function clearDrawingMode() {
  drawingEdgeMode.value = false
  drawingSourceId.value = null

  if (drawingTempLine) {
    drawingTempLine.remove()
    drawingTempLine = null
  }

  if (svgSelection) {
    svgSelection.style('cursor', '')
    svgSelection.on('mousemove.drawing', null)  // 移除命名空间监听
  }
}

/**
 * 连线模式下点击目标节点：创建边 → 打开编辑弹窗
 */
async function handleDrawingTarget(targetNodeId) {
  const sourceId = drawingSourceId.value
  if (targetNodeId === sourceId) {
    alert('不能连接到自身')
    clearDrawingMode()
    return
  }

  try {
    // 先用默认值创建边
    const resp = await fetch('/api/v1/knowledge/edge', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        from: sourceId,
        to: targetNodeId,
        relation: 'related',
        label: '',
      }),
    })

    clearDrawingMode()

    if (!resp.ok) {
      const err = await resp.json()
      alert(`添加边失败：${err.detail || resp.statusText}`)
      return
    }

    // 刷新图谱（emit + fetch + 重新渲染）
    await notifyGraphChanged()

    // 再拉一次最新数据，找到刚创建的边索引以打开编辑弹窗
    const graphResp = await fetch('/api/v1/knowledge/graph')
    if (!graphResp.ok) return
    const graphData = await graphResp.json()
    const edges = graphData.edges || []

    // 匹配刚创建的边（from + to + relation）
    const newEdgeIdx = edges.findIndex(e =>
      e.from === sourceId && e.to === targetNodeId && e.relation === 'related'
    )

    if (newEdgeIdx >= 0) {
      // 自动打开编辑边弹窗
      const edge = edges[newEdgeIdx]
      dialogMode.value = 'edit-edge'
      dialogData.value = { ...edge, _index: newEdgeIdx }
      dialogVisible.value = true
    }
  } catch (e) {
    alert(`网络错误：${e.message}`)
    clearDrawingMode()
  }
}

function handleEditEdge(edgeData) {
  dialogMode.value = 'edit-edge'
  // edgeData = { edge: {...}, index: N }
  dialogData.value = { ...edgeData.edge, _index: edgeData.index }
  dialogVisible.value = true
}

/* ================================================================
   删除操作（无需弹窗，直接确认后调 API）
   ================================================================ */
async function handleDeleteNode(nodeId) {
  if (!confirm(`确定删除节点「${nodeId}」及其所有关联边吗？此操作不可撤销。`)) return
  try {
    const resp = await fetch(`/api/v1/knowledge/node/${encodeURIComponent(nodeId)}`, {
      method: 'DELETE',
    })
    if (!resp.ok) {
      const err = await resp.json()
      alert(`删除失败：${err.detail || resp.statusText}`)
      return
    }
    const result = await resp.json()
    notifyGraphChanged()
  } catch (e) {
    alert(`网络错误：${e.message}`)
  }
}

async function handleDeleteEdge(edgeData) {
  // edgeData = { edge: {...}, index: N }
  if (!confirm('确定删除这条边吗？')) return
  const idx = edgeData.index
  try {
    const resp = await fetch(`/api/v1/knowledge/edge/${idx}`, { method: 'DELETE' })
    if (!resp.ok) {
      const err = await resp.json()
      alert(`删除失败：${err.detail || resp.statusText}`)
      return
    }
    notifyGraphChanged()
  } catch (e) {
    alert(`网络错误：${e.message}`)
  }
}

/* ================================================================
   编辑弹窗提交 → 调用 API
   ================================================================ */
async function handleDialogSubmit(formData) {
  try {
    switch (dialogMode.value) {
      case 'create-node': {
        const resp = await fetch('/api/v1/knowledge/node', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            name: formData.name,
            tags: formData.tags,
            content: formData.content || undefined,
          }),
        })
        if (!resp.ok) {
          const err = await resp.json()
          alert(`创建失败：${err.detail || resp.statusText}`)
          return
        }
        break
      }

      case 'edit-node': {
        const nodeId = dialogData.value?.id
        if (!nodeId) return
        const resp = await fetch(`/api/v1/knowledge/node/${encodeURIComponent(nodeId)}/info`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            name: formData.name,
            tags: formData.tags,
          }),
        })
        if (!resp.ok) {
          const err = await resp.json()
          alert(`更新失败：${err.detail || resp.statusText}`)
          return
        }
        break
      }

      case 'edit-edge': {
        const idx = dialogData.value?._index
        if (idx == null) return
        const resp = await fetch(`/api/v1/knowledge/edge/${idx}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            relation: formData.relation,
            label: formData.label,
          }),
        })
        if (!resp.ok) {
          const err = await resp.json()
          alert(`更新边失败：${err.detail || resp.statusText}`)
          return
        }
        break
      }
    }

    // 成功后通知父组件刷新
    notifyGraphChanged()

  } catch (e) {
    alert(`网络错误：${e.message}`)
  }
}

/* ================================================================
   通知图谱变化（主动 fetch + 直接重新渲染，不依赖父组件）
   ================================================================ */
async function notifyGraphChanged() {
  // 1. 通知父组件（供其更新自身状态，如节点列表等）
  emit('graph-changed')

  // 2. 主动拉取最新图谱并直接重新渲染
  try {
    const resp = await fetch('/api/v1/knowledge/graph')
    if (!resp.ok) return
    const data = await resp.json()

    const nodes = (data.nodes || []).map(n => ({ ...n }))
    const links = (data.edges || []).map(e => ({
      source: e.source || e.from,
      target: e.target || e.to,
      label: e.label,
      relation: e.relation || '',
    }))

    if (nodes.length > 0 && containerRef.value) {
      initForceGraph(nodes, links)
    }
  } catch {
    // 静默失败：emit 已发出，父组件 watch 可能会兜底
  }
}

/* ================================================================
   窗口 resize
   ================================================================ */
function handleResize() {
  if (simulation && containerRef.value) {
    const { width, height } = containerRef.value.getBoundingClientRect()
    simulation.force('center', d3.forceCenter(width / 2, height / 2))
    simulation.alpha(0.3).restart()
  }
}

/* ================================================================
   自动刷新
   ================================================================ */
async function checkGraphUpdate() {
  try {
    const resp = await fetch('/api/v1/knowledge/graph')
    if (!resp.ok) return
    const data = await resp.json()
    const newNodeCount = (data.nodes || []).length
    const newEdgeCount = (data.edges || []).length
    const currentNodes = (props.nodes || []).length
    const currentEdges = (props.edges || []).length
    if (newNodeCount !== currentNodes || newEdgeCount !== currentEdges) {
      showChangeNotification()
    }
  } catch { /* 静默 */ }
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
   缩放控制
   ================================================================ */
function zoomIn() {
  if (!svgSelection) return
  svgSelection.transition().duration(300).call(zoomBehavior.scaleBy, 1.3)
}
function zoomOut() {
  if (!svgSelection) return
  svgSelection.transition().duration(300).call(zoomBehavior.scaleBy, 0.7)
}
function zoomReset() {
  if (!svgSelection) return
  svgSelection.transition().duration(500).call(zoomBehavior.transform, d3.zoomIdentity)
}

/* ================================================================
   生命周期
   ================================================================ */
onMounted(() => {
  renderGraph()
  window.addEventListener('resize', handleResize)
  startAutoRefresh()
})

onUnmounted(() => {
  if (simulation) simulation.stop()
  window.removeEventListener('resize', handleResize)
  stopAutoRefresh()
  if (changeTimer.value) clearTimeout(changeTimer.value)
})
</script>

<template>
  <div ref="containerRef" class="force-graph-container">
    <!-- 加载状态 -->
    <div v-if="loading && props.nodes.length === 0" class="loading-overlay">
      <div class="spinner"></div>
      <p>图谱加载中...</p>
    </div>

    <!-- 错误状态 -->
    <div v-else-if="error === 'ERROR'" class="error-overlay">
      <p>图谱加载失败</p>
      <span class="error-hint">请确认后端服务已启动</span>
    </div>

    <!-- 空状态 -->
    <div v-else-if="!loading && props.nodes.length === 0 && !error" class="empty-overlay">
      <div class="empty-icon">📭</div>
      <p>知识图谱暂无内容</p>
      <p class="empty-hint">右键空白区域创建第一个节点</p>
    </div>

    <!-- 缩放控制按钮 -->
    <div v-if="props.nodes.length > 0" class="zoom-controls">
      <button class="zoom-btn" title="放大" @click="zoomIn">+</button>
      <span class="zoom-level">{{ currentZoom }}x</span>
      <button class="zoom-btn" title="缩小" @click="zoomOut">−</button>
      <button class="zoom-btn zoom-reset" title="重置视图" @click="zoomReset">↺</button>
    </div>

    <!-- 图谱更新提示 -->
    <transition name="fade">
      <div v-if="graphChanged" class="update-toast">
        🔄 图谱已更新
      </div>
    </transition>

    <!-- 右键菜单 -->
    <ContextMenu
      :visible="menuVisible"
      :x="menuX"
      :y="menuY"
      :targetType="menuTargetType"
      :targetData="menuTargetData"
      @close="closeMenu"
      @create-node="handleCreateNode"
      @edit-node="handleEditNode"
      @delete-node="handleDeleteNode"
      @add-edge-from-node="handleAddEdgeFromNode"
      @edit-edge="handleEditEdge"
      @delete-edge="handleDeleteEdge"
    />

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
  background: #1e1e2e;
  border-radius: 8px;
  overflow: hidden;
  position: relative;
}

.force-graph-container :deep(svg) { display: block; }

/* D3 元素 */
.force-graph-container :deep(.node) { transition: opacity 0.2s ease; }
.force-graph-container :deep(circle) { transition: r 0.2s ease, stroke-width 0.2s ease; }
.force-graph-container :deep(text) {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  user-select: none;
  pointer-events: none;
}
.force-graph-container :deep(.zoom-container) { pointer-events: auto; }

/* 边击中线的光标样式 */
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
  color: #a6adc8; font-size: 14px; gap: 16px; z-index: 10;
}
.spinner {
  width: 40px; height: 40px;
  border: 3px solid #313244;
  border-top-color: #60a5fa;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }

.error-overlay {
  position: absolute; inset: 0;
  display: flex; flex-direction: column;
  align-items: center; justify-content: center;
  color: #f87171; font-size: 14px; gap: 6px; z-index: 10;
}
.error-hint { font-size: 12px; color: #a6adc8; margin-top: 2px; }

.empty-overlay {
  position: absolute; inset: 0;
  display: flex; flex-direction: column;
  align-items: center; justify-content: center;
  color: #a6adc8; font-size: 14px; gap: 6px; z-index: 10;
}
.empty-icon { font-size: 40px; }
.empty-hint { font-size: 12px; color: #6b7280; margin-top: 4px; }

/* ================================================================
   缩放控制
   ================================================================ */
.zoom-controls {
  position: absolute; top: 12px; left: 12px;
  display: flex; flex-direction: column; align-items: center; gap: 4px; z-index: 20;
}
.zoom-btn {
  width: 32px; height: 32px;
  border: 1px solid rgba(255,255,255,0.15);
  border-radius: 6px;
  background: rgba(30,30,46,0.75);
  color: rgba(255,255,255,0.85);
  font-size: 16px; font-weight: bold; line-height: 1;
  cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  transition: background 0.2s, border-color 0.2s;
  backdrop-filter: blur(8px);
}
.zoom-btn:hover { background: rgba(60,60,80,0.85); border-color: rgba(255,255,255,0.3); }
.zoom-btn:active { background: rgba(80,80,100,0.85); }
.zoom-reset { font-size: 14px; margin-top: 4px; border-top: 1px solid rgba(255,255,255,0.1); padding-top: 8px; }
.zoom-level { font-size: 11px; color: rgba(255,255,255,0.5); user-select: none; padding: 2px 0; }

/* ================================================================
   更新提示
   ================================================================ */
.update-toast {
  position: absolute; bottom: 20px; left: 50%;
  transform: translateX(-50%);
  background: rgba(16,185,129,0.9);
  color: #fff; padding: 10px 20px; border-radius: 8px;
  font-size: 13px; font-weight: 500;
  box-shadow: 0 4px 16px rgba(16,185,129,0.3);
  z-index: 30; backdrop-filter: blur(8px); white-space: nowrap;
}
.fade-enter-active { animation: toastIn 0.3s ease; }
.fade-leave-active { animation: toastOut 0.3s ease; }
@keyframes toastIn {
  from { opacity: 0; transform: translateX(-50%) translateY(10px); }
  to   { opacity: 1; transform: translateX(-50%) translateY(0); }
}
@keyframes toastOut {
  from { opacity: 1; transform: translateX(-50%) translateY(0); }
  to   { opacity: 0; transform: translateX(-50%) translateY(10px); }
}
</style>
