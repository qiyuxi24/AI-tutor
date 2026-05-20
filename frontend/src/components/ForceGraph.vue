<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import * as d3 from 'd3'

const props = defineProps({
  apiUrl: {
    type: String,
    default: '/api/v1/knowledge/graph'
  }
})

const emit = defineEmits(['node-click'])

const containerRef = ref(null)
const loading = ref(true)
const error = ref('')

let simulation = null
let svgSelection = null

const levelColors = {
  '一级': '#60a5fa',
  '二级': '#34d399',
  '三级': '#fbbf24'
}

/* 掌握程度 → 颜色映射：从灰色渐变到绿色 */
function masteryColor(mastery) {
  if (mastery == null || mastery === 0) return '#9ca3af'   // 未掌握-灰
  if (mastery <= 25)  return '#86efac'                      // 入门-浅绿
  if (mastery <= 50)  return '#34d399'                      // 熟悉-中绿
  if (mastery <= 75)  return '#10b981'                      // 熟练-深绿
  return '#059669'                                          // 精通-墨绿
}

function extractLevel(tags) {
  if (!tags) return '一级'
  for (const tag of tags) {
    if (tag === '一级' || tag === '二级' || tag === '三级') {
      return tag
    }
  }
  return '一级'
}

function initForceGraph(nodes, links) {
  if (!containerRef.value) return

  const { width, height } = containerRef.value.getBoundingClientRect()

  d3.select(containerRef.value).select('svg').remove()

  svgSelection = d3.select(containerRef.value)
    .append('svg')
    .attr('width', '100%')
    .attr('height', '100%')
    .style('display', 'block')

  svgSelection.append('defs').append('marker')
    .attr('id', 'arrowhead')
    .attr('viewBox', '-0 -5 10 10')
    .attr('refX', 20)
    .attr('refY', 0)
    .attr('orient', 'auto')
    .attr('markerWidth', 6)
    .attr('markerHeight', 6)
    .append('path')
    .attr('d', 'M 0,-5 L 10,0 L 0,5')
    .attr('fill', '#6b7280')

  simulation = d3.forceSimulation(nodes)
    .force('link', d3.forceLink(links).id(d => d.id).distance(150))
    .force('charge', d3.forceManyBody().strength(-300))
    .force('center', d3.forceCenter(width / 2, height / 2))
    .force('collision', d3.forceCollide().radius(40))

  const link = svgSelection.append('g')
    .attr('class', 'links')
    .selectAll('line')
    .data(links)
    .enter()
    .append('line')
    .attr('stroke', '#4b5563')
    .attr('stroke-width', 2)
    .attr('marker-end', 'url(#arrowhead)')

  const linkLabel = svgSelection.append('g')
    .attr('class', 'link-labels')
    .selectAll('text')
    .data(links)
    .enter()
    .append('text')
    .attr('font-size', 10)
    .attr('fill', '#9ca3af')
    .attr('text-anchor', 'middle')
    .text(d => d.label)

  const node = svgSelection.append('g')
    .attr('class', 'nodes')
    .selectAll('g')
    .data(nodes)
    .enter()
    .append('g')
    .attr('class', 'node')
    .style('cursor', 'grab')
    .call(d3.drag()
      .on('start', dragstarted)
      .on('drag', dragged)
      .on('end', dragended))

  node.append('circle')
    .attr('r', 20)
    .attr('fill', d => masteryColor(d.mastery))
    .attr('stroke', d => masteryColor(d.mastery))
    .attr('stroke-width', 2)
    .attr('stroke-opacity', 0.5)
    .style('filter', 'drop-shadow(0 0 8px currentColor)')

  node.append('text')
    .attr('dy', 35)
    .attr('text-anchor', 'middle')
    .attr('fill', '#e5e7eb')
    .attr('font-size', 12)
    .text(d => d.name)

  node.on('mouseover', function(event, d) {
    d3.select(this).select('circle')
      .attr('r', 24)
      .attr('stroke-width', 3)

    link.attr('stroke', l =>
      (l.source.id === d.id || l.target.id === d.id) ? '#fbbf24' : '#4b5563'
    )
    .attr('stroke-width', l =>
      (l.source.id === d.id || l.target.id === d.id) ? 3 : 2
    )

    node.select('circle')
      .attr('opacity', n => {
        if (n.id === d.id) return 1
        const connected = links.some(l =>
          (l.source.id === d.id && l.target.id === n.id) ||
          (l.target.id === d.id && l.source.id === n.id)
        )
        return connected ? 1 : 0.3
      })
  })

  node.on('mouseout', function() {
    d3.select(this).select('circle')
      .attr('r', 20)
      .attr('stroke-width', 2)

    link.attr('stroke', '#4b5563').attr('stroke-width', 2)
    node.select('circle').attr('opacity', 1)
  })

  node.on('click', function(event, d) {
    emit('node-click', d.id)
  })

  simulation.on('tick', () => {
    link
      .attr('x1', d => d.source.x)
      .attr('y1', d => d.source.y)
      .attr('x2', d => d.target.x)
      .attr('y2', d => d.target.y)

    linkLabel
      .attr('x', d => (d.source.x + d.target.x) / 2)
      .attr('y', d => (d.source.y + d.target.y) / 2)

    node.attr('transform', d => `translate(${d.x},${d.y})`)
  })

  function dragstarted(event, d) {
    if (!event.active) simulation.alphaTarget(0.3).restart()
    d.fx = d.x
    d.fy = d.y
    d3.select(this).style('cursor', 'grabbing')
  }

  function dragged(event, d) {
    d.fx = event.x
    d.fy = event.y
  }

  function dragended(event, d) {
    if (!event.active) simulation.alphaTarget(0).alphaTarget(0)
    d.fx = null
    d.fy = null
    d3.select(this).style('cursor', 'grab')
  }
}

async function fetchData() {
  loading.value = true
  error.value = ''

  try {
    const res = await fetch(props.apiUrl)
    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`)
    }
    const data = await res.json()

    const nodes = (data.nodes || []).map(n => ({
      ...n,
      level: extractLevel(n.tags)
    }))
    const links = (data.edges || []).map(e => ({
      source: e.from,
      target: e.to,
      label: e.label
    }))

    initForceGraph(nodes, links)
  } catch (e) {
    error.value = 'ERROR'
  } finally {
    loading.value = false
  }
}

function handleResize() {
  if (simulation && containerRef.value) {
    const { width, height } = containerRef.value.getBoundingClientRect()
    simulation.force('center', d3.forceCenter(width / 2, height / 2))
    simulation.alpha(0.3).restart()
  }
}

onMounted(() => {
  fetchData()
  window.addEventListener('resize', handleResize)
})

onUnmounted(() => {
  if (simulation) {
    simulation.stop()
  }
  window.removeEventListener('resize', handleResize)
})
</script>

<template>
  <div ref="containerRef" class="force-graph-container">
    <div v-if="loading" class="loading-overlay">
      <div class="spinner"></div>
      <p>图谱加载中...</p>
    </div>
    <div v-else-if="error" class="error-overlay">
      <p>{{ error }}</p>
    </div>
  </div>
</template>

<style scoped>
.force-graph-container {
  width: 100%;
  height: 100%;
  background: #1e1e2e;
  border-radius: 8px;
  overflow: hidden;
  position: relative;
}

.force-graph-container :deep(svg) {
  display: block;
}

.force-graph-container :deep(.node) {
  transition: opacity 0.2s ease;
}

.force-graph-container :deep(circle) {
  transition: r 0.2s ease, stroke-width 0.2s ease;
}

.force-graph-container :deep(text) {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
  user-select: none;
  pointer-events: none;
}

.loading-overlay {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  color: #a6adc8;
  font-size: 14px;
  gap: 16px;
}

.spinner {
  width: 40px;
  height: 40px;
  border: 3px solid #313244;
  border-top-color: #60a5fa;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.error-overlay {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #f87171;
  font-size: 14px;
}
</style>