<script setup>
/**
 * ForceGraph.vue — D3 力导向知识图谱组件
 *
 * 职责：纯渲染 + 用户交互，不直接调用后端 API。
 *
 * 设计风格：Obsidian 极简 —— 纯色节点、细线边、无光晕/渐变/装饰。
 *
 * 数据流（单向，去耦合）：
 *   Store.knowledgeNodes/Edges → (props) → ForceGraph → (emit: graph-action) → HomeView
 */

import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import * as d3 from 'd3'
import ContextMenu from './ContextMenu.vue'
import EditDialog from './EditDialog.vue'
import { notifyError } from '../utils/feedback'

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
  // 按学习顺序排列的节点（拓扑路径），用于"显示学习路径"高亮
  learningPath: { type: Array, default: () => [] },
  // 下一步推荐节点 id（薄弱点脉冲标记）
  nextNodeId: { type: String, default: '' },
  // 外部强制显示学习路径（仪表盘联动用）
  showPath: { type: Boolean, default: false },
})

/* ================================================================
   组件 Events
   ================================================================ */
const emit = defineEmits([
  'node-click',
  'node-dblclick',
  'graph-action',
])

/* ================================================================
   响应式状态
   ================================================================ */
const containerRef = ref(null)

const graphChanged = ref(false)
const changeTimer = ref(null)
const autoRefreshTimer = ref(null)
const currentZoom = ref(1)

const menuVisible = ref(false)
const menuX = ref(0)
const menuY = ref(0)
const menuTargetType = ref('canvas')
const menuTargetData = ref(null)

const dialogVisible = ref(false)
const dialogMode = ref('create-node')
const dialogData = ref({})

const drawingEdgeMode = ref(false)
const drawingSourceId = ref(null)
let drawingWatchStop = null   // 跟踪 handleDrawingTarget 中创建的 watch，防止竞态泄漏

// ── 科技树联动状态 ──
// 本地"显示学习路径"开关；最终生效值 = 本地开关 OR 外部 props.showPath
const pathVisible = ref(false)
const pathVisibleFinal = computed(() => pathVisible.value || props.showPath)

/* ================================================================
   D3 核心对象引用（不响应式）
   ================================================================ */
let simulation = null
let svgSelection = null
let zoomBehavior = null
let zoomContainer = null
let edgeHitLines = null
let drawingTempLine = null
let drawingMouseMoveHandler = null
let nodeSelection = null   // 当前渲染的节点 group（路径高亮 / hover 恢复用）
let linkSelection = null   // 当前渲染的边（路径高亮 / hover 恢复用）

/* ================================================================
   工具函数
   ================================================================ */

/**
 * 节点颜色：掌握度四档（科技树语义）
 *   0      → 灰色  未开始（科技树暗色节点）
 *   1-29   → 红色  薄弱（刚开始学）
 *   30-69  → 黄色  学习中
 *   70-100 → 绿色  已掌握
 */
const NODE_RADIUS = 16
const NODE_COLOR_UNSTARTED = 'var(--color-graph-node)'
const NODE_COLOR_WEAK = 'var(--color-red)'
const NODE_COLOR_LEARNING = 'var(--color-yellow)'
const NODE_COLOR_MASTERED = 'var(--color-green)'

/** 掌握度分档：0 未开始 / 1 薄弱(1-29) / 2 学习中(30-69) / 3 已掌握(≥70)
 *  阈值契约 = 后端 graph_middleware.mastery_bucket（唯一真值源）；改这里必须同步改后端 */
function masteryLevel(mastery) {
  if (mastery == null || mastery === 0) return 0
  if (mastery < 30) return 1
  if (mastery < 70) return 2
  return 3
}

/**
 * 关系类型 → 中文短标签
 */
const RELATION_LABELS = {
  prerequisite: '前置知识',
  related: '相关概念',
  confusion: '易混淆',
  extension: '扩展延伸',
}

/**
 * 关系类型 → 标签颜色（淡色调，区分不同类型）
 */
const RELATION_COLORS = {
  prerequisite: 'var(--color-blue)',
  related: 'var(--color-green)',
  confusion: 'var(--color-orange)',
  extension: 'var(--color-purple)',
}

function edgeDisplayLabel(relation) {
  return RELATION_LABELS[relation] || relation || ''
}

function edgeDisplayColor(relation) {
  return RELATION_COLORS[relation] || 'var(--color-text-muted)'
}

function nodeFill(mastery) {
  const level = masteryLevel(mastery)
  if (level === 0) return NODE_COLOR_UNSTARTED
  if (level === 1) return NODE_COLOR_WEAK
  if (level === 2) return NODE_COLOR_LEARNING
  return NODE_COLOR_MASTERED
}

/* ── 学习路径（科技树）辅助函数 ──
   props.learningPath 支持两种形态：字符串 id 数组 或 节点对象数组
   isPathEdge 要求边的 source→target 与路径顺序一致（前置在前）才高亮 */
function getPathOrder(id) {
  const lp = props.learningPath || []
  for (let i = 0; i < lp.length; i++) {
    const item = lp[i]
    if (item === id || (item && item.id === id)) return i
  }
  return -1
}

function isPathNode(id) {
  return getPathOrder(id) >= 0
}

function isPathEdge(edge) {
  const s = edge.source?.id ?? edge.source
  const t = edge.target?.id ?? edge.target
  const si = getPathOrder(s)
  const ti = getPathOrder(t)
  // 与路径方向一致（前置 → 后置）的边才属于"该走的路"
  return si >= 0 && ti >= 0 && si < ti
}

/** 路径边/节点默认样式（供初始渲染与 hover 恢复共用） */
function linkDefaultColor(l) {
  return pathVisibleFinal.value && isPathEdge(l) ? 'var(--color-accent)' : 'var(--color-graph-edge)'
}
function linkDefaultWidth(l) {
  return pathVisibleFinal.value && isPathEdge(l) ? 2.6 : 1.2
}
function linkDefaultOpacity(l) {
  return pathVisibleFinal.value && isPathEdge(l) ? 0.9 : 0.4
}
function nodeBodyStroke(d) {
  if (pathVisibleFinal.value && isPathNode(d.id)) return 'var(--color-accent)'
  return nodeFill(d.mastery)
}
function nodeBodyStrokeWidth(d) {
  return pathVisibleFinal.value && isPathNode(d.id) ? 2.6 : 1
}
function nodeBodyStrokeOpacity(d) {
  return pathVisibleFinal.value && isPathNode(d.id) ? 0.95 : 0.3
}

/** 按当前路径状态统一刷新边与节点样式（初始渲染 / 开关切换 / hover 恢复均走这里） */
function applyPathHighlight() {
  if (!nodeSelection || !linkSelection) return
  linkSelection
    .attr('stroke', linkDefaultColor)
    .attr('stroke-width', linkDefaultWidth)
    .attr('stroke-opacity', linkDefaultOpacity)
  nodeSelection.select('.node-body')
    .attr('stroke', nodeBodyStroke)
    .attr('stroke-width', nodeBodyStrokeWidth)
    .attr('stroke-opacity', nodeBodyStrokeOpacity)
}

function nodeOpacity(mastery) {
  if (mastery == null || mastery === 0) return 0.6
  // mastery 0→100 映射 opacity 0.5→1.0
  return 0.5 + Math.min(100, Math.max(1, mastery)) / 200
}

/* ================================================================
   图谱渲染核心
   ================================================================ */
function initForceGraph(nodes, links) {
  if (!containerRef.value) return

  if (simulation) {
    simulation.stop()
    simulation = null
  }

  const { width, height } = containerRef.value.getBoundingClientRect()

  d3.select(containerRef.value).select('svg').remove()

  // ── SVG ──
  svgSelection = d3.select(containerRef.value)
    .append('svg')
    .attr('width', '100%')
    .attr('height', '100%')
    .style('display', 'block')

  // ── 箭头标记（极简三角） ──
  svgSelection.append('defs')
    .append('marker')
    .attr('id', 'arrowhead')
    .attr('viewBox', '0 -4 8 8')
    .attr('refX', 20).attr('refY', 0)
    .attr('orient', 'auto')
    .attr('markerWidth', 4).attr('markerHeight', 4)
    .append('path')
    .attr('d', 'M 0,-3.5 L 7,0 L 0,3.5')
    .attr('fill', 'var(--color-graph-edge)')

  // ── Zoom ──
  zoomContainer = svgSelection.append('g')
    .attr('class', 'zoom-container')

  zoomBehavior = d3.zoom()
    .scaleExtent([0.08, 5])
    .filter((event) => {
      if (event.type === 'wheel' && event.ctrlKey) return false
      if (event.type === 'dblclick') return false
      if (event.type === 'contextmenu') return false
      return true
    })
    .on('zoom', (event) => {
      zoomContainer.attr('transform', event.transform)
      currentZoom.value = Math.round(event.transform.k * 100) / 100
    })

  svgSelection.call(zoomBehavior)

  // ── 右键事件（原生 capture，绕过 D3 zoom） ──
  const svgNode = svgSelection.node()
  svgNode.addEventListener('contextmenu', (event) => {
    event.preventDefault()
    event.stopPropagation()

    if (drawingEdgeMode.value) {
      clearDrawingMode()
      return
    }

    const target = event.target
    const tag = target.tagName?.toLowerCase()

    if (tag === 'circle' && target.closest('.node')) {
      const nodeGroup = target.closest('.node')
      const nodeData = d3.select(nodeGroup).datum()
      if (nodeData) {
        showContextMenu(event.clientX, event.clientY, 'node', nodeData)
        return
      }
    }

    if (target.closest('.edge-hit-lines line')) {
      const lineEl = target.closest('.edge-hit-lines line')
      const edgeData = d3.select(lineEl).datum()
      if (edgeData) {
        showContextMenu(event.clientX, event.clientY, 'edge', { edge: edgeData })
        return
      }
    }

    showContextMenu(event.clientX, event.clientY, 'canvas', null)
  }, { capture: true })

  // ── 力场仿真 ──
  simulation = d3.forceSimulation(nodes)
    .alphaDecay(0.02)
    .velocityDecay(0.35)
    .force('link', d3.forceLink(links)
      .id(d => d.id)
      .distance(140)
      .strength(0.3)
    )
    .force('charge', d3.forceManyBody()
      .strength(-250)
      .distanceMax(500)
    )
    .force('center', d3.forceCenter(width / 2, height / 2).strength(0.06))
    .force('collision', d3.forceCollide().radius(NODE_RADIUS + 12).strength(0.6))
    .force('x', d3.forceX(width / 2).strength(0.02))
    .force('y', d3.forceY(height / 2).strength(0.02))

  // ── 连线（可视） ──
  const link = zoomContainer.append('g')
    .attr('class', 'links')
    .selectAll('line')
    .data(links)
    .enter()
    .append('line')
    .attr('stroke', linkDefaultColor)
    .attr('stroke-width', linkDefaultWidth)
    .attr('stroke-opacity', linkDefaultOpacity)
    .attr('marker-end', 'url(#arrowhead)')
    .style('pointer-events', 'none')

  linkSelection = link

  // ── 连线（透明击中区） ──
  const hitGroup = zoomContainer.append('g')
    .attr('class', 'edge-hit-lines')

  const hitLines = hitGroup.selectAll('line')
    .data(links)
    .enter()
    .append('line')
    .attr('stroke', 'transparent')
    .attr('stroke-width', 14)
    .style('cursor', 'pointer')
    .style('pointer-events', 'all')

  edgeHitLines = hitLines

  // ── 连线标签 ──
  const linkLabel = zoomContainer.append('g')
    .attr('class', 'link-labels')
    .selectAll('text')
    .data(links)
    .enter()
    .append('text')
    .attr('font-size', 10)
    .attr('font-weight', '500')
    .attr('fill', d => edgeDisplayColor(d.relation))
    .attr('text-anchor', 'middle')
    .text(d => edgeDisplayLabel(d.relation))
    .style('pointer-events', 'none')
    .style('user-select', 'none')

  // ── 节点组 ──
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

  // ── 节点圆形（科技树四档配色 + 路径描边） ──
  node.append('circle')
    .attr('class', 'node-body')
    .attr('r', NODE_RADIUS)
    .attr('fill', d => nodeFill(d.mastery))
    .attr('opacity', d => nodeOpacity(d.mastery))
    .attr('stroke', nodeBodyStroke)
    .attr('stroke-width', nodeBodyStrokeWidth)
    .attr('stroke-opacity', nodeBodyStrokeOpacity)

  // ── 薄弱点脉冲环（下一步推荐节点，扩散动画提示"从这里学起"） ──
  node.append('circle')
    .attr('class', 'node-pulse')
    .attr('r', NODE_RADIUS)
    .attr('fill', 'none')
    .attr('stroke', 'var(--color-accent)')
    .attr('stroke-width', 2)
    .style('pointer-events', 'none')
    .style('display', d => (d.id === props.nextNodeId ? null : 'none'))

  nodeSelection = node

  // ── 节点名称（标签在节点右侧，Obsidian 风格） ──
  node.append('text')
    .attr('class', 'node-label')
    .attr('dx', NODE_RADIUS + 8)
    .attr('dy', 4)
    .attr('text-anchor', 'start')
    .attr('fill', 'var(--color-text-primary)')
    .attr('font-size', 12)
    .attr('font-weight', '500')
    .text(d => d.name)
    .style('pointer-events', 'none')
    .style('user-select', 'none')

  // ── 悬停高亮 ──
  node.on('mouseover', function (event, d) {
    event.stopPropagation()

    // 悬停节点：放大 + 亮色描边
    d3.select(this).select('.node-body')
      .transition().duration(150)
      .attr('r', NODE_RADIUS + 4)
      .attr('stroke-width', 2)
      .attr('stroke-opacity', 0.8)
      .attr('stroke', 'var(--color-accent)')

    d3.select(this).select('.node-label')
      .transition().duration(150)
      .attr('font-weight', '600')

    // 关联边高亮
    link
      .transition().duration(150)
      .attr('stroke', l =>
        (l.source.id === d.id || l.target.id === d.id)
          ? 'var(--color-accent)'
          : 'var(--color-graph-edge)'
      )
      .attr('stroke-width', l =>
        (l.source.id === d.id || l.target.id === d.id) ? 2 : 1.2
      )
      .attr('stroke-opacity', l =>
        (l.source.id === d.id || l.target.id === d.id) ? 0.7 : 0.12
      )

    // 非关联节点淡化
    node.select('.node-body').transition().duration(150)
      .attr('opacity', n => {
        if (n.id === d.id) return nodeOpacity(n.mastery)
        const connected = links.some(l =>
          (l.source.id === d.id && l.target.id === n.id) ||
          (l.target.id === d.id && l.source.id === n.id)
        )
        return connected ? nodeOpacity(n.mastery) : 0.12
      })
    node.select('.node-label').transition().duration(150)
      .attr('opacity', n => {
        if (n.id === d.id) return 1
        const connected = links.some(l =>
          (l.source.id === d.id && l.target.id === n.id) ||
          (l.target.id === d.id && l.source.id === n.id)
        )
        return connected ? 1 : 0.15
      })
  })

  node.on('mouseout', function (event, d) {
    d3.select(this).select('.node-body')
      .transition().duration(200)
      .attr('r', NODE_RADIUS)
      .attr('stroke', nodeBodyStroke(d))
      .attr('stroke-width', nodeBodyStrokeWidth(d))
      .attr('stroke-opacity', nodeBodyStrokeOpacity(d))

    d3.select(this).select('.node-label')
      .transition().duration(200)
      .attr('font-weight', '500')

    node.select('.node-body').transition().duration(200)
      .attr('opacity', n => nodeOpacity(n.mastery))
    node.select('.node-label').transition().duration(200)
      .attr('opacity', 1)

    // hover 结束后恢复"学习路径"高亮（若已开启），避免被 hover 效果覆盖
    link.transition().duration(200)
      .attr('stroke', linkDefaultColor)
      .attr('stroke-width', linkDefaultWidth)
      .attr('stroke-opacity', linkDefaultOpacity)
  })

  // ── 点击 / 双击 ──
  node.on('click', function (event, d) {
    event.stopPropagation()
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

  svgSelection.on('click', () => {
    if (menuVisible.value) menuVisible.value = false
  })

  // ── Tick ──
  simulation.on('tick', () => {
    link
      .attr('x1', d => d.source.x).attr('y1', d => d.source.y)
      .attr('x2', d => d.target.x).attr('y2', d => d.target.y)

    if (edgeHitLines) {
      edgeHitLines
        .attr('x1', d => d.source.x).attr('y1', d => d.source.y)
        .attr('x2', d => d.target.x).attr('y2', d => d.target.y)
    }

    linkLabel
      .attr('x', d => (d.source.x + d.target.x) / 2)
      .attr('y', d => (d.source.y + d.target.y) / 2)

    node.attr('transform', d => `translate(${d.x},${d.y})`)

    if (drawingTempLine && drawingSourceId.value) {
      const src = simulation.nodes().find(n => n.id === drawingSourceId.value)
      if (src) drawingTempLine.attr('x1', src.x).attr('y1', src.y)
    }
  })

  // ── 拖拽 ──
  function dragstarted(event, d) {
    if (event.sourceEvent) event.sourceEvent.stopPropagation()
    if (!event.active) simulation.alphaTarget(0.12).restart()
    d.fx = d.x; d.fy = d.y
    d3.select(this).style('cursor', 'grabbing')
    d3.select(this).select('.node-body')
      .transition().duration(100)
      .attr('r', NODE_RADIUS + 3)
      .attr('stroke-opacity', 0.6)
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
    d3.select(this).select('.node-body')
      .transition().duration(200)
      .attr('r', NODE_RADIUS)
      .attr('stroke-opacity', 0.3)
  }
}

/* ================================================================
   图谱渲染入口
   ================================================================ */
function renderGraph() {
  try {
    const nodes = (props.nodes || []).map(n => ({ ...n }))
    const nodeIdSet = new Set(nodes.map(n => n.id))
    // 过滤悬空边：d3.forceLink 要求边两端节点必须存在，否则初始化直接抛
    // "node not found"。按学科/板块切片时后端会保留跨学科边（保证子图连通
    // 性可见），其中一端节点不在当前节点集中，必须在此丢弃。
    const links = (props.edges || [])
      .map((e) => ({
        source: e.source || e.from_node || e.from,
        target: e.target || e.to_node || e.to,
        label: e.label || '',
        relation: e.relation || '',
        edgeId: e.edgeId,
      }))
      .filter(l => nodeIdSet.has(l.source) && nodeIdSet.has(l.target))
    if (nodes.length > 0 && containerRef.value) {
      initForceGraph(nodes, links)
    }
  } catch (e) {
    console.error('[ForceGraph] renderGraph 失败:', e)
  }
}

let lastGraphFingerprint = ''

function graphFingerprint(nodes, edges) {
  // 纳入 mastery / relation：掌握度或边类型变化也必须触发重绘（科技树四色依赖）
  const nodeIds = (nodes || []).map(n => `${n.id}:${n.mastery}`).sort().join(',')
  const edgeKeys = (edges || []).map(e =>
    `${e.source || e.from_node || e.from}->${e.target || e.to_node || e.to}:${e.relation || ''}`
  ).sort().join(',')
  return `${nodeIds}|${edgeKeys}`
}

watch(() => [props.nodes, props.edges], () => {
  const fp = graphFingerprint(props.nodes, props.edges)
  if (fp !== lastGraphFingerprint) {
    lastGraphFingerprint = fp
    renderGraph()
  }
}, { deep: true })

// ── 科技树联动：路径开关 / 路径数据 / 推荐节点变化时增量刷新样式（不重建布局） ──
watch(pathVisible, () => applyPathHighlight())
watch(() => props.showPath, () => applyPathHighlight())
watch(() => props.learningPath, () => applyPathHighlight())
watch(() => props.nextNodeId, () => {
  svgSelection?.selectAll('.node-pulse')
    .style('display', d => (d.id === props.nextNodeId ? null : 'none'))
})

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
  startEdgeDrawing(nodeId)
}

/* ================================================================
   连线模式
   ================================================================ */
function startEdgeDrawing(sourceNodeId) {
  drawingEdgeMode.value = true
  drawingSourceId.value = sourceNodeId

  const sourceNode = simulation?.nodes()?.find(n => n.id === sourceNodeId)
  const sx = sourceNode?.x ?? 0
  const sy = sourceNode?.y ?? 0

  drawingTempLine = zoomContainer.append('line')
    .attr('class', 'drawing-temp-line')
    .attr('x1', sx).attr('y1', sy)
    .attr('x2', sx).attr('y2', sy)
    .attr('stroke', 'var(--color-accent)')
    .attr('stroke-width', 1.5)
    .attr('stroke-dasharray', '6,4')
    .style('pointer-events', 'none')

  svgSelection.style('cursor', 'crosshair')

  drawingMouseMoveHandler = (event) => {
    if (!drawingEdgeMode.value || !drawingTempLine) return
    const [mx, my] = d3.pointer(event, svgSelection.node())
    const transform = d3.zoomTransform(svgSelection.node())
    const zx = (mx - transform.x) / transform.k
    const zy = (my - transform.y) / transform.k
    drawingTempLine.attr('x2', zx).attr('y2', zy)
  }
  svgSelection.on('mousemove.drawing', drawingMouseMoveHandler)
}

function clearDrawingMode() {
  drawingEdgeMode.value = false
  drawingSourceId.value = null

  if (drawingTempLine) {
    drawingTempLine.remove()
    drawingTempLine = null
  }

  if (svgSelection) {
    svgSelection.style('cursor', '')
    svgSelection.on('mousemove.drawing', null)
  }
}

async function handleDrawingTarget(targetNodeId) {
  const sourceId = drawingSourceId.value
  if (targetNodeId === sourceId) {
    notifyError('不能连接到自身')
    clearDrawingMode()
    return
  }

  clearDrawingMode()

  emit('graph-action', {
    action: 'create-edge',
    payload: { from: sourceId, to: targetNodeId, relation: 'related', label: '' },
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
        return eSource === sourceId && eTarget === targetNodeId && e.relation === 'related'
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

function handleEditEdge(edgeData) {
  dialogMode.value = 'edit-edge'
  dialogData.value = { ...edgeData.edge }
  dialogVisible.value = true
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
   窗口 resize
   ================================================================ */
function handleResize() {
  if (simulation && containerRef.value) {
    const { width, height } = containerRef.value.getBoundingClientRect()
    simulation
      .force('center', d3.forceCenter(width / 2, height / 2).strength(0.06))
      .force('x', d3.forceX(width / 2).strength(0.02))
      .force('y', d3.forceY(height / 2).strength(0.02))
    simulation.alpha(0.2).restart()
  }
}

/* ================================================================
   自动刷新
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
  if (simulation) {
    simulation.stop()
    simulation = null
  }
  if (containerRef.value) {
    d3.select(containerRef.value).select('svg').remove()
  }
  window.removeEventListener('resize', handleResize)
  stopAutoRefresh()
  if (changeTimer.value) {
    clearTimeout(changeTimer.value)
    changeTimer.value = null
  }
  clearDrawingMode()
})

/* ================================================================
   暴露方法
   ================================================================ */

/**
 * 聚焦指定节点：平滑移动到该节点并高亮
 * @param {string} nodeId - 节点 ID
 */
function focusNode(nodeId) {
  if (!simulation || !svgSelection) return
  const node = simulation.nodes().find(n => n.id === nodeId)
  if (!node) return

  const { width, height } = containerRef.value.getBoundingClientRect()
  const scale = 1.5
  const tx = width / 2 - node.x * scale
  const ty = height / 2 - node.y * scale

  svgSelection.transition().duration(600)
    .call(zoomBehavior.transform, d3.zoomIdentity.translate(tx, ty).scale(scale))

  // 高亮节点（脉冲效果）
  const nodeGroup = svgSelection.selectAll('.node').filter(d => d.id === nodeId)
  nodeGroup.select('.node-body')
    .transition().duration(200)
    .attr('r', NODE_RADIUS + 6)
    .attr('stroke', 'var(--color-accent)')
    .attr('stroke-width', 2.5)
    .attr('stroke-opacity', 1)
    .transition().duration(400)
    .attr('r', NODE_RADIUS)
    .attr('stroke-width', 1)
    .attr('stroke-opacity', 0.3)
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
        :class="{ active: pathVisibleFinal }"
        :title="pathVisibleFinal ? '隐藏学习路径' : '显示学习路径（该走的路）'"
        @click="pathVisible = !pathVisible"
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
  background: var(--color-bg-primary);
  border-radius: 8px;
  overflow: hidden;
  position: relative;
}

.force-graph-container :deep(svg) { display: block; }

/* ── D3 元素（极简过渡） ── */
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
