/**
 * forceGraphEngine.js — D3 力导向图谱渲染内核（**不依赖 Vue**）
 *
 * 职责：画布骨架 / 力场 / 增量 join / 拖拽·悬停·缩放 / 连线模式 / 视野保障。
 * 不持有业务状态：节点、边、高亮态、力场参数一律由调用方（`ForceGraph.vue`）喂进来；
 * 需要回传的交互（点节点、右键、缩放、连线完成）通过 `handlers` 回调出去。
 *
 * 为什么独立：原先渲染内核与「右键菜单 / 编辑弹窗 / 覆盖层 UI」挤在同一个 1400+ 行的
 * SFC 里，渲染问题要在 UI 代码里翻找，也没法脱离浏览器验证。拆开后：
 *   · 内核只认 (nodes, links) → 纯函数式驱动，可用 `node scripts/xxx.mjs` 直接断言；
 *   · SFC 只剩"把 store 状态喂进来 + 弹菜单/弹窗"。
 *
 * 数据流（单向）：
 *   Store.displayNodes/displayEdges → (props) → ForceGraph.vue → engine.update()
 *                                                                    ↓ handlers
 *                                                       ForceGraph.vue → emit graph-action → HomeView
 *
 * 设计风格：Obsidian 极简 —— 纯色节点、细线边、无光晕/渐变/装饰。
 *
 * 增量更新不变量（展开/收拢因此是"丝滑"的）：
 *   · 不重建 SVG → 缩放与视野保留（唯一例外见 `ensureExpansionVisible`）；
 *   · 旧节点坐标继承 → 原地不动；新节点从父锚点旁长出；
 *   · 离场节点缩小淡出，而不是瞬间消失。
 */

import * as d3 from 'd3'
import { FORCE_DEFAULTS } from './graphForces'

/* ================================================================
   渲染常量（纯值，与实例无关）
   ================================================================ */

const NODE_RADIUS = 16
const NODE_COLOR_UNSTARTED = 'var(--color-graph-node)'
const NODE_COLOR_WEAK = 'var(--color-red)'
const NODE_COLOR_LEARNING = 'var(--color-yellow)'
const NODE_COLOR_MASTERED = 'var(--color-green)'

/** 展开后锚点周围需要留出的视野余量（占容器短边的比例） */
const EXPAND_MARGIN_RATIO = 0.3

const RELATION_LABELS = {
  prerequisite: '前置知识',
  related: '相关概念',
  confusion: '易混淆',
  extension: '扩展延伸',
}
const RELATION_COLORS = {
  prerequisite: 'var(--color-blue)',
  related: 'var(--color-green)',
  confusion: 'var(--color-orange)',
  extension: 'var(--color-purple)',
}

/**
 * 掌握度分档：0 未开始 / 1 薄弱(1-29) / 2 学习中(30-69) / 3 已掌握(≥70)
 * 阈值契约 = 后端 graph_middleware.mastery_bucket（唯一真值源）；改这里必须同步改后端。
 */
function masteryLevel(mastery) {
  if (mastery == null || mastery === 0) return 0
  if (mastery < 30) return 1
  if (mastery < 70) return 2
  return 3
}

function nodeFill(mastery) {
  const level = masteryLevel(mastery)
  if (level === 0) return NODE_COLOR_UNSTARTED
  if (level === 1) return NODE_COLOR_WEAK
  if (level === 2) return NODE_COLOR_LEARNING
  return NODE_COLOR_MASTERED
}

function nodeOpacity(mastery) {
  if (mastery == null || mastery === 0) return 0.6
  // mastery 0→100 映射 opacity 0.5→1.0
  return 0.5 + Math.min(100, Math.max(1, mastery)) / 200
}

function edgeDisplayLabel(relation) {
  return RELATION_LABELS[relation] || relation || ''
}

function edgeDisplayColor(relation) {
  return RELATION_COLORS[relation] || 'var(--color-text-muted)'
}

/** 节点半径：图谱只画最小知识点节点，固定 NODE_RADIUS（主题层已下线，2026-09-27）。 */
function nodeRadius(d) {
  return NODE_RADIUS
}

/** 边两端取 id（d3.forceLink 解析后 source/target 是节点对象） */
function endpointKey(v) {
  return v && typeof v === 'object' ? v.id : v
}

function linkKey(l) {
  return `${endpointKey(l.source)}->${endpointKey(l.target)}`
}

/** 归一化入参 + 指纹（任一变化都必须触发重绘：mastery / 关系 / 折叠态 / 边权） */
function normalizeNodes(rawNodes) {
  return (rawNodes || []).map((n) => ({ ...n }))
}

function normalizeLinks(rawEdges, nodeIdSet) {
  return (rawEdges || [])
    .map((e) => ({
      source: e.source || e.from_node || e.from,
      target: e.target || e.to_node || e.to,
      label: e.label || '',
      relation: e.relation || '',
      edgeId: e.edgeId,
    }))
    // 过滤悬空边：d3.forceLink 要求边两端节点必须存在，否则直接抛 "node not found"。
    // 按学科/板块切片时后端会保留跨切片边（保证子图连通性可见），必须在此丢弃。
    .filter((l) => nodeIdSet.has(l.source) && nodeIdSet.has(l.target))
}

function graphFingerprint(nodes, links) {
  const nodeIds = nodes
    .map((n) => `${n.id}:${n.mastery}`)
    .sort()
    .join(',')
  const edgeKeys = links
    .map((l) => `${l.source}->${l.target}:${l.relation}`)
    .sort()
    .join(',')
  return `${nodeIds}|${edgeKeys}`
}

/* ================================================================
   引擎
   ================================================================ */

/**
 * @param {Object}   p
 * @param {Element}  p.container 图谱容器（引擎只往它里面 append 一个 svg）
 * @param {Object}   p.forces    力场参数（缺省用 FORCE_DEFAULTS）
 * @param {Object}   p.highlight 初始高亮态 {pathVisible, learningPath, nextNodeId}
 * @param {Object}   p.handlers  交互回调（全部可选）
 *        - onNodeClick(id)                  单击普通知识点
 *        - onNodeDblClick(id)               双击普通知识点
 *        - onContextMenu(event, type, data) 右键：type = 'node' | 'edge' | 'canvas'
 *        - onCanvasClick()                  点空白
 *        - onZoomChange(k)                  缩放比例变化（UI 显示用）
 *        - onDrawTarget(fromId, toId)       连线模式落点（引擎已退出连线模式）
 * @returns {Object} 引擎 API（update / setHighlight / applyForces / resize / 缩放 / focusNode / 连线模式 / destroy）
 */
export function createForceGraphEngine({ container, forces, highlight, handlers = {} } = {}) {
  /* ── 实例状态（全部不进入 Vue 响应式）── */
  let forceParams = { ...FORCE_DEFAULTS, ...(forces || {}) }
  let hl = { pathVisible: false, learningPath: [], nextNodeId: '', ...(highlight || {}) }

  let simulation = null
  let svgSelection = null
  let zoomBehavior = null
  let zoomContainer = null
  // 四个图层：增量 join 的目标（画布只建一次，见 ensureCanvas）
  let gLinks = null
  let gHit = null
  let gLabels = null
  let gNodes = null
  let edgeHitLines = null
  let labelSelection = null
  let drawingTempLine = null
  let drawingSourceId = null
  let nodeSelection = null
  let linkSelection = null
  let linkForce = null
  let chargeForce = null
  let centerForce = null
  let xForce = null
  let yForce = null
  let collideForce = null
  // 节点坐标缓存：增量更新时复用旧坐标（旧节点原地不动，新节点从父锚点旁长出）
  const lastPositions = new Map()
  let lastFingerprint = ''
  let destroyed = false

  /* ---------- 高亮态（学习路径 / 推荐节点） ---------- */

  /** props.learningPath 支持两种形态：字符串 id 数组 或 节点对象数组
   *  isPathEdge 要求边的 source→target 与路径顺序一致（前置在前）才高亮 */
  function getPathOrder(id) {
    const lp = hl.learningPath || []
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
    const s = endpointKey(edge.source)
    const t = endpointKey(edge.target)
    const si = getPathOrder(s)
    const ti = getPathOrder(t)
    // 与路径方向一致（前置 → 后置）的边才属于"该走的路"
    return si >= 0 && ti >= 0 && si < ti
  }

  function linkIsPath(l) {
    return hl.pathVisible && isPathEdge(l)
  }

  /** 边默认样式（供初始渲染与 hover 恢复共用）
   *  - 归属边（锚点 → 子节点）：细虚线，表达"归属"而非关系；
   *  - 聚合边（多条边卷到同一对主题）：线宽随合并条数增长。 */
  function isBelongLink(l) { return l.kind === 'belong' }

  function linkDefaultColor(l) {
    if (isBelongLink(l)) return 'var(--color-border)'
    return linkIsPath(l) ? 'var(--color-accent)' : 'var(--color-graph-edge)'
  }

  function linkDefaultWidth(l) {
    if (isBelongLink(l)) return 1
    if (l.kind === 'agg') return Math.min(4, 1.2 + Math.log2(l.count || 1) * 0.9)
    return linkIsPath(l) ? 2.6 : 1.2
  }

  function linkDefaultOpacity(l) {
    if (isBelongLink(l)) return 0.55
    return linkIsPath(l) ? 0.9 : 0.4
  }

  function nodeBodyStroke(d) {
    if (hl.pathVisible && isPathNode(d.id)) return 'var(--color-accent)'
    return nodeFill(d.mastery)
  }

  function nodeBodyStrokeWidth(d) {
    return hl.pathVisible && isPathNode(d.id) ? 2.6 : 1
  }

  function nodeBodyStrokeOpacity(d) {
    return hl.pathVisible && isPathNode(d.id) ? 0.95 : 0.3
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

  /** 推荐节点脉冲环 */
  function applyPulse() {
    if (!svgSelection) return
    svgSelection.selectAll('.node-pulse')
      .style('display', (d) => (d.id === hl.nextNodeId ? null : 'none'))
  }

  /* ---------- 画布骨架 ---------- */

  /** 画布骨架：只建一次 */
  function ensureCanvas() {
    if (svgSelection || !container) return

    svgSelection = d3.select(container)
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

    zoomContainer = svgSelection.append('g').attr('class', 'zoom-container')

    // 图层顺序：边 → 边击中区 → 边标签 → 节点
    gLinks = zoomContainer.append('g').attr('class', 'links')
    gHit = zoomContainer.append('g').attr('class', 'edge-hit-lines')
    gLabels = zoomContainer.append('g').attr('class', 'link-labels')
    gNodes = zoomContainer.append('g').attr('class', 'nodes')

    // ── Zoom ──
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
        handlers.onZoomChange?.(Math.round(event.transform.k * 100) / 100)
      })

    svgSelection.call(zoomBehavior)

    // ── 右键事件（原生 capture，绕过 D3 zoom；只绑一次，靠 datum 取最新数据） ──
    svgSelection.node().addEventListener('contextmenu', (event) => {
      event.preventDefault()
      event.stopPropagation()

      if (drawingSourceId) {
        cancelEdgeDrawing()
        return
      }

      const target = event.target
      const tag = target.tagName?.toLowerCase()

      if (tag === 'circle' && target.closest('.node')) {
        const nodeData = d3.select(target.closest('.node')).datum()
        // 主题聚合节点同样走「节点」菜单（菜单内只给展开/合并，不给编辑删除）
        if (nodeData) {
          handlers.onContextMenu?.(event, 'node', nodeData)
          return
        }
      }

      if (target.closest('.edge-hit-lines line')) {
        const lineEl = target.closest('.edge-hit-lines line')
        const edgeData = d3.select(lineEl).datum()
        if (edgeData) {
          handlers.onContextMenu?.(event, 'edge', { edge: edgeData })
          return
        }
      }

      handlers.onContextMenu?.(event, 'canvas', null)
    }, { capture: true })

    svgSelection.on('click', () => handlers.onCanvasClick?.())

    // ── 力场（参数由设置页驱动） ──
    // 归属边（锚点 → 子节点）短且强 → 子节点围绕父节点聚拢，展开像"花开"
    const { width, height } = container.getBoundingClientRect()
    linkForce = d3.forceLink([]).id((d) => d.id)
    chargeForce = d3.forceManyBody().distanceMax(500)
    centerForce = d3.forceCenter(width / 2, height / 2)
    xForce = d3.forceX(width / 2)
    yForce = d3.forceY(height / 2)
    collideForce = d3.forceCollide().strength(0.6)

    simulation = d3.forceSimulation([])
      .alphaDecay(0.02)
      .velocityDecay(0.35)
      .force('link', linkForce)
      .force('charge', chargeForce)
      .force('center', centerForce)
      .force('collide', collideForce)
      .force('x', xForce)
      .force('y', yForce)
      .on('tick', onTick)
  }

  /** 把设置页参数灌进力场（只改参数，不重建仿真） */
  function applyForceParams() {
    if (!simulation) return
    const f = forceParams
    const rect = container?.getBoundingClientRect()
    const w = rect?.width || 0
    const h = rect?.height || 0
    centerForce.x(w / 2).y(h / 2).strength(f.center)
    xForce.x(w / 2).strength(f.center / 3)   // 等价改造前的 center 0.06 + xy 0.02
    yForce.y(h / 2).strength(f.center / 3)
    chargeForce.strength(f.repel)
    collideForce.radius((d) => nodeRadius(d) + f.collide)
    linkForce
      .distance((l) => (isBelongLink(l) ? f.linkDistance * 0.64 : f.linkDistance))
      .strength((l) => (isBelongLink(l) ? f.linkStrength * 3 : f.linkStrength))
  }

  /** 每帧：用最新 join 结果更新元素坐标 */
  function onTick() {
    if (linkSelection) {
      linkSelection
        .attr('x1', (d) => d.source.x).attr('y1', (d) => d.source.y)
        .attr('x2', (d) => d.target.x).attr('y2', (d) => d.target.y)
    }
    if (edgeHitLines) {
      edgeHitLines
        .attr('x1', (d) => d.source.x).attr('y1', (d) => d.source.y)
        .attr('x2', (d) => d.target.x).attr('y2', (d) => d.target.y)
    }
    if (labelSelection) {
      labelSelection
        .attr('x', (d) => (d.source.x + d.target.x) / 2)
        .attr('y', (d) => (d.source.y + d.target.y) / 2)
    }
    if (nodeSelection) {
      nodeSelection.attr('transform', (d) => {
        lastPositions.set(d.id, { x: d.x, y: d.y })   // 供下次增量复用
        return `translate(${d.x},${d.y})`
      })
    }
    if (drawingTempLine && drawingSourceId) {
      const src = simulation.nodes().find((n) => n.id === drawingSourceId)
      if (src) drawingTempLine.attr('x1', src.x).attr('y1', src.y)
    }
  }

  /**
   * 展开后的视野保障：子节点从锚点旁长出来（摊开半径 ≈ 一条归属边长），
   * 锚点若贴着视口边缘，就会有一半子节点落在视口外 —— 用户看到的是
   * "点了展开却看不见子节点"。这里**只在必要时**把锚点平移回视野中心区，
   * 不动缩放比例（保持"展开不跳视野"的手感）。
   */
  function ensureExpansionVisible(ids) {
    if (!ids.length || !svgSelection || !container) return
    const rect = container.getBoundingClientRect()
    const w = rect.width
    const h = rect.height
    if (!w || !h) return
    const marginX = w * EXPAND_MARGIN_RATIO
    const marginY = h * EXPAND_MARGIN_RATIO
    const byId = new Map(simulation.nodes().map((n) => [n.id, n]))
    const nodes = ids
      .map((id) => byId.get(id))
      .filter((n) => n && Number.isFinite(n.x) && Number.isFinite(n.y))
    if (!nodes.length) return
    const cx = nodes.reduce((s, n) => s + n.x, 0) / nodes.length
    const cy = nodes.reduce((s, n) => s + n.y, 0) / nodes.length
    const t = d3.zoomTransform(svgSelection.node())
    const sx = cx * t.k + t.x
    const sy = cy * t.k + t.y
    if (sx >= marginX && sx <= w - marginX && sy >= marginY && sy <= h - marginY) return
    svgSelection.transition().duration(420)
      .call(zoomBehavior.transform, t.translate((w / 2 - sx) / t.k, (h / 2 - sy) / t.k))
  }

  /** 增量更新：节点/边 join + 生长/收缩过渡 */
  function updateGraph(nodes, links) {
    if (!simulation) return
    const rect = container.getBoundingClientRect()
    const cx = rect.width / 2
    const cy = rect.height / 2

    // ── 落点：旧节点原地保留；新节点从「归属边起点（父锚点）」旁长出 ──
    const anchorOf = new Map()
    for (const l of links) {
      if (l.kind === 'belong') anchorOf.set(endpointKey(l.target), endpointKey(l.source))
    }
    const isNewGraph = !nodes.some((n) => lastPositions.has(n.id))
    const addedIds = []
    for (const n of nodes) {
      const p = lastPositions.get(n.id)
      if (p) { n.x = p.x; n.y = p.y; continue }
      addedIds.push(n.id)
      const a = anchorOf.has(n.id) ? lastPositions.get(anchorOf.get(n.id)) : null
      const base = a || { x: cx, y: cy }
      n.x = base.x + (Math.random() - 0.5) * 40
      n.y = base.y + (Math.random() - 0.5) * 40
    }
    if (isNewGraph) {
      // 换了学科/板块（节点集完全不相交）：坐标缓存与视野一起归零
      lastPositions.clear()
      svgSelection.call(zoomBehavior.transform, d3.zoomIdentity)
      handlers.onZoomChange?.(1)
    }

    // ── 换数据并重启仿真 ──
    // ⚠️ 顺序不能反（d3-force 3.x）：`simulation.nodes()` 与 `forceLink.links()` 都会**立即**
    // 用各自持有的节点集解析边的端点。先 links 后 nodes 时，新边会拿**上一批**节点解析
    // → 抛 `node not found`，被 update 的 try 吞掉，表现为「切换学科后画面停在旧图」。
    // 正确顺序 = d3 官方示例：先换节点，再换边（此时 forceLink 已持有新节点集）。
    simulation.nodes(nodes)
    linkForce.links(links)
    applyForceParams()
    simulation.alpha(isNewGraph ? 1 : 0.5).restart()

    // ⚠️ 先清掉上一轮遗留的"离场"过渡：它是 `transition('exit').remove()`（220ms），
    // 若某个节点在这段时间内又回来了（展开→收起→再展开、SSE 刷新），旧过渡仍会把已回到
    // 数据里的元素 remove 掉 → "数据里有、画布上没有"；半途被打断则会停在缩小 + 半透明态。
    // 只中断**命名**过渡 'exit'（不碰入场/悬停用的默认过渡），并清掉离场写下的内联透明度。
    gNodes.selectAll('g.node').interrupt('exit').style('opacity', null)
    gLinks.selectAll('line').interrupt('exit')
    gLabels.selectAll('text').interrupt('exit')

    // ── 关系边（增量 join） ──
    const linkJoin = gLinks.selectAll('line').data(links, linkKey)
    linkJoin.exit().transition('exit').duration(200).attr('stroke-opacity', 0).remove()

    const linkEnter = linkJoin.enter().append('line')
      .attr('stroke-opacity', 0)
      .style('pointer-events', 'none')
      // 新边从源节点"长"出来，而不是横空出现
      .attr('x1', (d) => d.source.x).attr('y1', (d) => d.source.y)
      .attr('x2', (d) => d.source.x).attr('y2', (d) => d.source.y)

    linkSelection = linkEnter.merge(linkJoin)
      .attr('stroke', linkDefaultColor)
      .attr('stroke-width', linkDefaultWidth)
      // 归属边：虚线 + 无箭头（表达层级归属，不是知识点之间的关系）
      .attr('stroke-dasharray', (l) => (isBelongLink(l) ? '4,4' : null))
      .attr('marker-end', (l) => (isBelongLink(l) ? null : 'url(#arrowhead)'))
    linkEnter.transition().duration(240).attr('stroke-opacity', linkDefaultOpacity)

    // ── 连线（透明击中区） ──
    const hitJoin = gHit.selectAll('line').data(links, linkKey)
    hitJoin.exit().remove()
    edgeHitLines = hitJoin.enter().append('line')
      .attr('stroke', 'transparent')
      .attr('stroke-width', 14)
      .style('cursor', 'pointer')
      .style('pointer-events', 'all')
      .merge(hitJoin)

    // ── 连线标签 ──
    const labelJoin = gLabels.selectAll('text').data(links, linkKey)
    labelJoin.exit().remove()
    const labelEnter = labelJoin.enter().append('text')
      .attr('font-size', 10)
      .attr('font-weight', '500')
      .attr('text-anchor', 'middle')
      .style('pointer-events', 'none')
      .style('user-select', 'none')
      .style('opacity', 0)
    labelSelection = labelEnter.merge(labelJoin)
      .attr('fill', (d) => edgeDisplayColor(d.relation))
      .text((d) => edgeDisplayLabel(d.relation))
    labelEnter.transition().duration(240).style('opacity', 1)

    // ── 节点（增量 join） ──
    const nodeJoin = gNodes.selectAll('g.node').data(nodes, (d) => d.id)

    // 离场：缩小淡出（离场节点不在 nodeSelection 里，tick 不会再写它们的 transform）
    nodeJoin.exit()
      .transition('exit').duration(220)
      .style('opacity', 0)
      .attr('transform', (d) => `translate(${d.x},${d.y}) scale(0.25)`)
      .remove()

    const nodeEnter = nodeJoin.enter().append('g')
      .attr('class', 'node')
      .style('cursor', 'grab')
      .attr('opacity', 0)
      .attr('transform', (d) => `translate(${d.x},${d.y})`)

    // 半径从 0 长出来（配下面的 transition → "花开"）
    nodeEnter.append('circle').attr('class', 'node-body').attr('r', 0)

    // 命中区：比圆面略大一圈，且覆盖节点上方的 ± 展开徽标 ——
    // 徽标是 `pointer-events: none` 的文本，右键点它原本会落到画布上（弹出"创建新节点"）。
    nodeEnter.append('circle')
      .attr('class', 'node-hit')
      .attr('fill', 'transparent')
      .style('pointer-events', 'all')

    // ── 薄弱点脉冲环（下一步推荐节点，扩散动画提示"从这里学起"） ──
    nodeEnter.append('circle')
      .attr('class', 'node-pulse')
      .attr('fill', 'none')
      .attr('stroke', 'var(--color-accent)')
      .attr('stroke-width', 2)
      .style('pointer-events', 'none')
      .style('display', 'none')

    // ── 节点名称（标签在节点右侧，Obsidian 风格；聚合节点带成员数） ──
    nodeEnter.append('text')
      .attr('class', 'node-label')
      .attr('text-anchor', 'start')
      .attr('dy', 4)
      .attr('fill', 'var(--color-text-primary)')
      .attr('font-size', 12)
      .attr('font-weight', '500')
      .style('pointer-events', 'none')
      .style('user-select', 'none')
      .style('opacity', 0)

    // 节点拖拽
    nodeEnter.call(d3.drag()
      .on('start', dragstarted)
      .on('drag', dragged)
      .on('end', dragended)
      .filter((event) => event.button === 0)
    )

    nodeSelection = nodeEnter.merge(nodeJoin)

    // ── 节点圆形（科技树四档配色 + 路径描边） ──
    nodeSelection.select('.node-body')
      .attr('fill', (d) => nodeFill(d.mastery))
      .attr('stroke', nodeBodyStroke)
      .attr('stroke-width', nodeBodyStrokeWidth)
      .attr('stroke-opacity', nodeBodyStrokeOpacity)
    // 生长/更新：只对半径与透明度过渡（tick 不写这两个属性，不会与过渡打架）
    nodeSelection.select('.node-body')
      .transition().duration(320)
      .attr('r', (d) => nodeRadius(d))
      .attr('opacity', (d) => nodeOpacity(d.mastery))

    nodeSelection.select('.node-hit')
      .attr('r', (d) => nodeRadius(d) + 7)

    // 入场淡入
    nodeEnter.transition().duration(320).attr('opacity', 1)
    nodeEnter.select('.node-label').transition().duration(320).style('opacity', 1)

    nodeSelection.select('.node-pulse')
      .attr('r', (d) => nodeRadius(d))
      .style('display', (d) => (d.id === hl.nextNodeId ? null : 'none'))

    nodeSelection.select('.node-label')
      .attr('dx', (d) => nodeRadius(d) + 8)
      .text((d) => d.name)

    // 别名：下方的悬停/点击/拖拽逻辑继续用 node 指代当前节点集
    const node = nodeSelection

    // ── 悬停高亮 ──
    node.on('mouseover', function (event, d) {
      event.stopPropagation()

      // 悬停节点：放大 + 亮色描边
      d3.select(this).select('.node-body')
        .transition().duration(150)
        .attr('r', nodeRadius(d) + 4)
        .attr('stroke-width', 2)
        .attr('stroke-opacity', 0.8)
        .attr('stroke', 'var(--color-accent)')

      d3.select(this).select('.node-label')
        .transition().duration(150)
        .attr('font-weight', 600)

      // 关联边高亮
      linkSelection
        .transition().duration(150)
        .attr('stroke', (l) =>
          (l.source.id === d.id || l.target.id === d.id)
            ? 'var(--color-accent)'
            : 'var(--color-graph-edge)'
        )
        .attr('stroke-width', (l) =>
          (l.source.id === d.id || l.target.id === d.id) ? 2 : 1.2
        )
        .attr('stroke-opacity', (l) =>
          (l.source.id === d.id || l.target.id === d.id) ? 0.7 : 0.12
        )

      // 非关联节点淡化
      node.select('.node-body').transition().duration(150)
        .attr('opacity', (n) => {
          if (n.id === d.id) return nodeOpacity(n.mastery)
          const connected = links.some((l) =>
            (l.source.id === d.id && l.target.id === n.id) ||
            (l.target.id === d.id && l.source.id === n.id)
          )
          return connected ? nodeOpacity(n.mastery) : 0.12
        })
      node.select('.node-label').transition().duration(150)
        .attr('opacity', (n) => {
          if (n.id === d.id) return 1
          const connected = links.some((l) =>
            (l.source.id === d.id && l.target.id === n.id) ||
            (l.target.id === d.id && l.source.id === n.id)
          )
          return connected ? 1 : 0.15
        })
    })

    node.on('mouseout', function (event, d) {
      d3.select(this).select('.node-body')
        .transition().duration(200)
        .attr('r', nodeRadius(d))
        .attr('stroke', nodeBodyStroke(d))
        .attr('stroke-width', nodeBodyStrokeWidth(d))
        .attr('stroke-opacity', nodeBodyStrokeOpacity(d))

      d3.select(this).select('.node-label')
        .transition().duration(200)
        .attr('font-weight', 500)

      node.select('.node-body').transition().duration(200)
        .attr('opacity', (n) => nodeOpacity(n.mastery))
      node.select('.node-label').transition().duration(200)
        .attr('opacity', 1)

      // hover 结束后恢复"学习路径"高亮（若已开启），避免被 hover 效果覆盖
      linkSelection.transition().duration(200)
        .attr('stroke', linkDefaultColor)
        .attr('stroke-width', linkDefaultWidth)
        .attr('stroke-opacity', linkDefaultOpacity)
    })

    // ── 点击 / 双击 ──
    node.on('click', function (event, d) {
      event.stopPropagation()
      if (drawingSourceId) {
        handleDrawingTarget(d.id)
        return
      }
      handlers.onNodeClick?.(d.id)
    })
    node.on('dblclick', function (event, d) {
      event.stopPropagation()
      handlers.onNodeDblClick?.(d.id)
    })

    // ── 拖拽 ──
    function dragstarted(event, d) {
      if (event.sourceEvent) event.sourceEvent.stopPropagation()
      if (!event.active) simulation.alphaTarget(0.12).restart()
      d.fx = d.x; d.fy = d.y
      d3.select(this).style('cursor', 'grabbing')
      d3.select(this).select('.node-body')
        .transition().duration(100)
        .attr('r', nodeRadius(d) + 3)
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
        .attr('r', nodeRadius(d))
        .attr('stroke-opacity', 0.3)
    }

    // 本次新增的节点（含展开出来的子节点）若落在视口外，平移视野把它们带回来
    if (!isNewGraph && addedIds.length) ensureExpansionVisible(addedIds)
  }

  /* ---------- 渲染入口 ---------- */

  /** 清空画布（空图 / 卸载）：下次 ensureCanvas 会重建骨架 */
  function destroyCanvas() {
    if (simulation) {
      simulation.stop()
      simulation = null
    }
    if (container) d3.select(container).select('svg').remove()
    svgSelection = null
    zoomContainer = null
    gLinks = gHit = gLabels = gNodes = null
    linkSelection = null
    edgeHitLines = null
    labelSelection = null
    nodeSelection = null
    linkForce = chargeForce = centerForce = xForce = yForce = collideForce = null
    drawingTempLine = null
    lastPositions.clear()
  }

  /**
   * 用最新数据刷新画布。
   * 指纹未变 → 直接返回（学习路径/推荐节点变化走 setHighlight，不必重建布局）。
   */
  function update(rawNodes, rawEdges) {
    if (destroyed) return
    const nodes = normalizeNodes(rawNodes)
    const links = normalizeLinks(rawEdges, new Set(nodes.map((n) => n.id)))
    const fp = graphFingerprint(nodes, links)
    if (fp === lastFingerprint) return
    lastFingerprint = fp
    try {
      if (!container) return
      if (nodes.length === 0) {
        destroyCanvas()      // 空态由调用方的 cover 层呈现
        return
      }
      ensureCanvas()
      updateGraph(nodes, links)
    } catch (e) {
      console.error('[ForceGraph] 渲染失败:', e)
    }
  }

  /** 合并高亮态（学习路径开关 / 路径数据 / 推荐节点）并立即刷新样式 */
  function setHighlight(patch) {
    hl = { ...hl, ...patch }
    applyPathHighlight()
    applyPulse()
  }

  /** 力场参数变化：只改参数 + 重启仿真（不重建、不重置视野） */
  function applyForces(next) {
    forceParams = { ...forceParams, ...(next || {}) }
    if (!simulation) return
    applyForceParams()
    simulation.alpha(0.4).restart()
  }

  /** 容器尺寸变化 */
  function resize() {
    if (!simulation || !container) return
    applyForceParams()   // 重新读取容器尺寸，更新 center / x / y
    simulation.alpha(0.2).restart()
  }

  /* ---------- 缩放 ---------- */

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

  /**
   * 聚焦指定节点：平滑移动到该节点并高亮
   * @param {string} nodeId
   */
  function focusNode(nodeId) {
    if (!simulation || !svgSelection || !container) return
    const node = simulation.nodes().find((n) => n.id === nodeId)
    if (!node) return

    const { width, height } = container.getBoundingClientRect()
    const scale = 1.5
    const tx = width / 2 - node.x * scale
    const ty = height / 2 - node.y * scale

    svgSelection.transition().duration(600)
      .call(zoomBehavior.transform, d3.zoomIdentity.translate(tx, ty).scale(scale))

    // 高亮节点（脉冲效果）
    const nodeGroup = svgSelection.selectAll('.node').filter((d) => d.id === nodeId)
    nodeGroup.select('.node-body')
      .transition().duration(200)
      .attr('r', nodeRadius(node) + 6)
      .attr('stroke', 'var(--color-accent)')
      .attr('stroke-width', 2.5)
      .attr('stroke-opacity', 1)
      .transition().duration(400)
      .attr('r', nodeRadius(node))
      .attr('stroke-width', 1)
      .attr('stroke-opacity', 0.3)
  }

  /* ---------- 连线模式 ---------- */

  function startEdgeDrawing(sourceNodeId) {
    if (!svgSelection || !zoomContainer) return
    cancelEdgeDrawing()
    drawingSourceId = sourceNodeId

    const sourceNode = simulation?.nodes()?.find((n) => n.id === sourceNodeId)
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

    svgSelection.on('mousemove.drawing', (event) => {
      if (!drawingSourceId || !drawingTempLine) return
      const [mx, my] = d3.pointer(event, svgSelection.node())
      const transform = d3.zoomTransform(svgSelection.node())
      drawingTempLine
        .attr('x2', (mx - transform.x) / transform.k)
        .attr('y2', (my - transform.y) / transform.k)
    })
  }

  function cancelEdgeDrawing() {
    drawingSourceId = null
    if (drawingTempLine) {
      drawingTempLine.remove()
      drawingTempLine = null
    }
    if (svgSelection) {
      svgSelection.style('cursor', '')
      svgSelection.on('mousemove.drawing', null)
    }
  }

  /** 连线落点：先退出连线模式，再把结果交给调用方（自连等非法判定留给 UI 层） */
  function handleDrawingTarget(targetNodeId) {
    const from = drawingSourceId
    cancelEdgeDrawing()
    if (!from) return
    handlers.onDrawTarget?.(from, targetNodeId)
  }

  function destroy() {
    destroyed = true
    cancelEdgeDrawing()
    destroyCanvas()
    lastFingerprint = ''
  }

  return {
    update,
    setHighlight,
    applyForces,
    resize,
    zoomIn,
    zoomOut,
    zoomReset,
    focusNode,
    startEdgeDrawing,
    cancelEdgeDrawing,
    isDrawing: () => !!drawingSourceId,
    destroy,
  }
}
