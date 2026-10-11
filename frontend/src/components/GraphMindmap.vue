<script setup>
/**
 * GraphMindmap.vue — 「思维导图」视图（依赖树）
 *
 * 表达的是**依赖**（沿 prerequisite 边：谁是谁的前置），不是归属 —— 树由渲染层纯函数
 * `utils/graphTree.buildDependencyTree` 从同一份 nodes/edges 派生（回边忽略 + 多父取首次到达），
 * 数据层一行不改。想看"谁属于谁"请切「大括号」。
 *
 * 渲染：d3-hierarchy（`d3.tree`）自绘 SVG —— d3@7 已在依赖内，零新增依赖；
 * 缩放/平移沿用 d3-zoom（与 forceGraphEngine 同一套交互习惯）。
 */
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import * as d3 from 'd3'
import { buildDependencyTree } from '../utils/graphTree'
import { nodeFill } from '../utils/forceGraphEngine'

const props = defineProps({
  nodes: { type: Array, default: () => [] },
  edges: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  error: { type: String, default: '' },
  nextNodeId: { type: String, default: '' },
  sourceNodes: { type: Array, default: () => [] },
  // 虚拟根显示名（传当前学科名，让用户知道这张树是哪个切片的）
  rootLabel: { type: String, default: '' },
})

const emit = defineEmits(['open-node'])

const NODE_GAP = 34        // 同层节点间距
const LEVEL_GAP = 220      // 层与层的间距
const INITIAL_DEPTH = 2    // 首屏只铺到第 2 层，更深的先折起来

const containerRef = ref(null)
const svgRef = ref(null)

const tree = computed(() => buildDependencyTree(props.nodes, props.edges, {
  rootName: props.rootLabel || '前置依赖',
}))
const sourceSet = computed(() => new Set(props.sourceNodes))

/** 折叠的节点 id 集合（computed 之外的本地交互态） */
const collapsed = new Set()

let svg = null
let gRoot = null
let zoomBehavior = null
let resizeObserver = null
let rootSignature = ''

const hasKids = (d) => !!(d.data.children && d.data.children.length)

/* ────────────────────────── 渲染 ────────────────────────── */

function render() {
  if (!svg) return
  const el = containerRef.value
  const width = el?.clientWidth || 0
  const height = el?.clientHeight || 0
  if (!width || !height) return
  svg.attr('width', width).attr('height', height)

  const root = d3.hierarchy(tree.value, (d) => d.children)
  root.each((d) => { if (collapsed.has(d.data.id)) d.children = null })

  d3.tree().nodeSize([NODE_GAP, LEVEL_GAP])(root)
  const nodes = root.descendants()
  const links = root.links()

  // ── 边（横向贝塞尔，读起来像"往下游走"） ──
  gRoot.selectAll('path.mm-link')
    .data(links, (d) => d.target.data.id)
    .join('path')
    .attr('class', 'mm-link')
    .attr('d', (d) => {
      const mx = (d.source.y + d.target.y) / 2
      return `M${d.source.y},${d.source.x}C${mx},${d.source.x} ${mx},${d.target.x} ${d.target.y},${d.target.x}`
    })

  // ── 节点（enter 建骨架，merge 后统一刷内容） ──
  const nodeSel = gRoot.selectAll('g.mm-node').data(nodes, (d) => d.data.id)
  const enter = nodeSel.enter().append('g').attr('class', 'mm-node')
  // 折叠开关画在节点左侧（入边那一侧，像个接线柱），点它才折叠；点节点本体是打开详情
  const toggle = enter.append('g').attr('class', 'mm-toggle').attr('transform', 'translate(-19,0)')
  toggle.append('circle').attr('r', 7)
  toggle.append('text').attr('class', 'mm-toggle-sign').attr('dy', '0.32em')
  enter.append('circle').attr('class', 'mm-dot').attr('r', 7)
  enter.append('text').attr('class', 'mm-label').attr('x', 12).attr('dy', '0.32em')

  // 交互只在 enter 上挂一次：datum 由每次 data() join 重新绑定
  toggle.on('click', (event, d) => { event.stopPropagation(); toggleNode(d.data.id) })
  enter.on('click', (event, d) => {
    event.stopPropagation()
    if (d.data.kind === 'node') emit('open-node', d.data.id)
    else toggleNode(d.data.id)
  })

  const all = enter.merge(nodeSel)
  all.attr('transform', (d) => `translate(${d.y},${d.x})`)
    .classed('is-next', (d) => !!props.nextNodeId && d.data.id === props.nextNodeId)
    .classed('is-source', (d) => sourceSet.value.has(d.data.id))
    .classed('is-root', (d) => d.data.kind === 'root')
  all.select('.mm-dot').attr('fill', (d) => (d.data.kind === 'root' ? 'var(--color-accent)' : nodeFill(d.data.mastery)))
  all.select('.mm-label').text((d) => d.data.name)
  all.select('.mm-toggle').style('display', (d) => (hasKids(d) ? null : 'none'))
  all.select('.mm-toggle-sign').text((d) => (collapsed.has(d.data.id) ? '+' : '−'))
}

/** 首屏折叠策略：第 INITIAL_DEPTH 层往下都先折起来（大图别一次铺开几百个节点） */
function applyInitialCollapse(root) {
  collapsed.clear()
  const walk = (n, depth) => {
    if (depth >= INITIAL_DEPTH && (n.children || []).length) {
      collapsed.add(n.id)
      return
    }
    ;(n.children || []).forEach((c) => walk(c, depth + 1))
  }
  walk(root, 0)
}

function toggleNode(id) {
  collapsed.has(id) ? collapsed.delete(id) : collapsed.add(id)
  render()
}

/** 缩放到刚好放下整棵树 */
function fit() {
  if (!svg || !gRoot) return
  const el = containerRef.value
  const box = gRoot.node().getBBox()
  if (!box.width || !box.height || !el?.clientWidth) return
  const scale = Math.min(1, Math.min(el.clientWidth / (box.width + 80), el.clientHeight / (box.height + 80)))
  const tx = el.clientWidth / 2 - (box.x + box.width / 2) * scale
  const ty = el.clientHeight / 2 - (box.y + box.height / 2) * scale
  svg.call(zoomBehavior.transform, d3.zoomIdentity.translate(tx, ty).scale(scale))
}

function pathTo(node, id, acc = []) {
  if (node.id === id) return acc
  for (const c of node.children || []) {
    const hit = pathTo(c, id, [...acc, node.id])
    if (hit) return hit
  }
  return null
}

/** 聚焦某知识点（搜索 / 任务栏「在图谱中定位」）：展开祖先链后把它移到视野中心 */
function focusNode(nodeId) {
  if (!nodeId || !svg) return
  for (const id of pathTo(tree.value, nodeId) || []) collapsed.delete(id)
  render()
  const el = containerRef.value
  const hit = gRoot.selectAll('g.mm-node').filter((d) => d.data.id === nodeId).datum()
  if (!hit || !el) return
  const k = d3.zoomTransform(svgRef.value).k
  svg.call(zoomBehavior.transform, d3.zoomIdentity
    .translate(el.clientWidth / 2 - hit.y * k, el.clientHeight / 2 - hit.x * k)
    .scale(k))
}

defineExpose({ focusNode, fit })

/* ────────────────────────── 生命周期 ────────────────────────── */

onMounted(() => {
  svg = d3.select(svgRef.value)
  svg.append('rect').attr('class', 'mm-bg').attr('width', '100%').attr('height', '100%').attr('fill', 'transparent')
  gRoot = svg.append('g')
  zoomBehavior = d3.zoom().scaleExtent([0.15, 3]).on('zoom', (e) => gRoot.attr('transform', e.transform))
  svg.call(zoomBehavior).on('dblclick.zoom', null)   // 双击留给"打开详情"，不当放大用

  applyInitialCollapse(tree.value)
  rootSignature = (tree.value.children || []).map((c) => c.id).join('|')
  render()
  fit()

  resizeObserver = new ResizeObserver(() => render())
  resizeObserver.observe(containerRef.value)
})

onUnmounted(() => {
  resizeObserver?.disconnect()
  resizeObserver = null
  // 只摘监听：DOM 由 Vue 卸载（d3 画的子元素跟着根节点一起被移除）
  svg?.on('.zoom', null)
  svg = null
  gRoot = null
})

// 根集合变了（切学科 / 重新建图）= 换了一张图 → 重置折叠与视野；只是掌握度变了则原地重绘
watch(tree, (t) => {
  const sig = (t.children || []).map((c) => c.id).join('|')
  if (sig !== rootSignature) {
    rootSignature = sig
    applyInitialCollapse(t)
    render()
    fit()
  } else {
    render()
  }
})

// 高亮态变化只需要刷类名，不必重排
watch([() => props.nextNodeId, () => props.sourceNodes], () => {
  gRoot?.selectAll('g.mm-node')
    .classed('is-next', (d) => !!props.nextNodeId && d.data.id === props.nextNodeId)
    .classed('is-source', (d) => sourceSet.value.has(d.data.id))
})
</script>

<template>
  <div ref="containerRef" class="mindmap-view">
    <svg ref="svgRef" class="mm-canvas"></svg>

    <div v-if="loading && !nodes.length" class="mm-overlay">图谱加载中…</div>
    <div v-else-if="error" class="mm-overlay is-error">{{ error }}</div>
    <div v-else-if="!nodes.length" class="mm-overlay">
      <p>该学科暂无知识点</p>
      <p class="mm-hint">在对话里让 AI 建图，或从知识库上传资料建图</p>
    </div>

    <button v-if="nodes.length" class="mm-reset" title="缩放到刚好放下整棵树" @click="fit">适应窗口</button>
  </div>
</template>

<style scoped>
.mindmap-view {
  width: 100%;
  height: 100%;
  position: relative;
  background: var(--color-bg-primary);
  border-radius: 8px;
  overflow: hidden;
}
.mm-canvas { display: block; cursor: grab; }
.mm-canvas:active { cursor: grabbing; }

.mm-reset {
  position: absolute;
  right: 16px;
  bottom: 20px;
  z-index: 20;
  padding: 5px 10px;
  font-family: inherit;
  font-size: 11px;
  border: 1px solid var(--color-border-subtle);
  border-radius: 4px;
  background: var(--color-bg-primary);
  color: var(--color-text-secondary);
  cursor: pointer;
}
.mm-reset:hover { background: var(--color-bg-surface); color: var(--color-text-primary); }

.mm-overlay {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  font-size: 14px;
  color: var(--color-text-secondary);
  text-align: center;
  padding: 0 24px;
  pointer-events: none;
}
.mm-overlay.is-error { color: var(--color-red); }
.mm-hint { font-size: 12px; color: var(--color-text-muted); margin: 0; }

/* ── d3 画出来的元素：scoped 下必须走 :deep ── */
.mindmap-view :deep(.mm-link) {
  fill: none;
  stroke: var(--color-graph-edge);
  stroke-width: 1.2;
}
.mindmap-view :deep(.mm-label) {
  font-size: 12px;
  fill: var(--color-text-primary);
  cursor: pointer;
  user-select: none;
}
.mindmap-view :deep(g.mm-node) { cursor: pointer; }
.mindmap-view :deep(.mm-dot) { stroke: var(--color-bg-primary); stroke-width: 1.5; }
.mindmap-view :deep(g.is-root .mm-dot) { stroke: var(--color-accent); }
.mindmap-view :deep(g.is-root .mm-label) { font-weight: 600; fill: var(--color-text-secondary); }

/* 折叠开关：默认低调，悬停才明显 —— 它不是主操作（点节点才是） */
.mindmap-view :deep(.mm-toggle) { cursor: pointer; }
.mindmap-view :deep(.mm-toggle circle) {
  fill: var(--color-bg-primary);
  stroke: var(--color-border);
  stroke-width: 1;
}
.mindmap-view :deep(.mm-toggle-sign) {
  font-size: 11px;
  text-anchor: middle;
  fill: var(--color-text-secondary);
  user-select: none;
}
.mindmap-view :deep(.mm-toggle:hover circle) { stroke: var(--color-accent); fill: var(--color-accent-light); }

/* ── 联动高亮：推荐节点 / 来源命中 ── */
.mindmap-view :deep(g.is-next .mm-dot) { stroke: var(--color-accent); stroke-width: 3; }
.mindmap-view :deep(g.is-next .mm-label) { font-weight: 600; }
.mindmap-view :deep(g.is-source .mm-dot) { stroke: var(--color-accent); stroke-width: 2.5; }
.mindmap-view :deep(g.is-source .mm-label) { fill: var(--color-accent); }
</style>
