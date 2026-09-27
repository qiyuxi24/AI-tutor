<script setup>
/**
 * GraphForcePreview.vue — 设置页的力导向参数实时预览
 *
 * 一张固定的小图（12 节点 / 14 边），力场参数与 ForceGraph 读同一份
 * utils/graphForces.js 状态：拖滑杆 → 这里立即重排，所见即所得。
 *
 * ponytail: 不复用 ForceGraph —— 那边挂着右键菜单 / 缩放控件 / 主题折叠整车逻辑，
 * 只为预览而加一排开关反而更贵。这里 12 个点足以看出四个参数的手感差异。
 * 距离/碰撞按 0.5 缩放以适配小画布，与主图是等比例的，滑杆方向一致。
 */
import { ref, onMounted, onUnmounted, watch } from 'vue'
import * as d3 from 'd3'
import { useGraphForces } from '../utils/graphForces'

const { forces } = useGraphForces()
const containerRef = ref(null)

// 三个"簇"（每簇 1 中心 + 3 卫星）—— 能同时看出重力、排斥、连线长度与碰撞
const SAMPLE_NODES = Array.from({ length: 12 }, (_, i) => ({ id: i }))
const SAMPLE_LINKS = [
  { source: 0, target: 1 }, { source: 0, target: 2 }, { source: 0, target: 3 },
  { source: 4, target: 5 }, { source: 4, target: 6 }, { source: 4, target: 7 },
  { source: 8, target: 9 }, { source: 8, target: 10 }, { source: 8, target: 11 },
  { source: 0, target: 4 }, { source: 4, target: 8 },
  { source: 1, target: 5 }, { source: 3, target: 7 }, { source: 9, target: 11 },
]

let simulation = null
let linkSel = null
let nodeSel = null

function applyParams() {
  if (!simulation) return
  const f = forces.value
  const el = containerRef.value
  const w = el?.clientWidth || 200
  const h = el?.clientHeight || 200
  simulation.force('center').x(w / 2).y(h / 2).strength(f.center)
  simulation.force('x').x(w / 2).strength(f.center / 3)
  simulation.force('y').y(h / 2).strength(f.center / 3)
  simulation.force('charge').strength(f.repel)
  simulation.force('collide').radius(f.collide * 0.5 + 10)
  simulation.force('link')
    .distance(f.linkDistance * 0.5)
    .strength(f.linkStrength)
  simulation.alpha(0.6).restart()
}

onMounted(() => {
  const el = containerRef.value
  if (!el) return
  const svg = d3.select(el).append('svg')
    .attr('width', '100%')
    .attr('height', '100%')
    .style('display', 'block')
  const g = svg.append('g')

  linkSel = g.selectAll('line')
    .data(SAMPLE_LINKS)
    .enter()
    .append('line')
    .attr('stroke', 'var(--color-graph-edge)')
    .attr('stroke-opacity', 0.55)
    .attr('stroke-width', 1.2)

  nodeSel = g.selectAll('circle')
    .data(SAMPLE_NODES)
    .enter()
    .append('circle')
    .attr('r', 7)
    .attr('fill', 'var(--color-accent)')
    .attr('fill-opacity', 0.85)

  simulation = d3.forceSimulation(SAMPLE_NODES)
    .force('link', d3.forceLink(SAMPLE_LINKS).id(d => d.id))
    .force('charge', d3.forceManyBody().distanceMax(400))
    .force('center', d3.forceCenter())
    .force('x', d3.forceX())
    .force('y', d3.forceY())
    .force('collide', d3.forceCollide())
    .on('tick', () => {
      linkSel
        .attr('x1', d => d.source.x).attr('y1', d => d.source.y)
        .attr('x2', d => d.target.x).attr('y2', d => d.target.y)
      nodeSel.attr('cx', d => d.x).attr('cy', d => d.y)
    })

  applyParams()
})

// 滑杆拖动 → 立即重排
watch(forces, applyParams, { deep: true })

onUnmounted(() => {
  if (simulation) {
    simulation.stop()
    simulation = null
  }
  if (containerRef.value) d3.select(containerRef.value).select('svg').remove()
})
</script>

<template>
  <div ref="containerRef" class="force-preview"></div>
</template>

<style scoped>
.force-preview {
  width: 100%;
  height: 100%;
  background: var(--color-bg-primary);
  border: 1px solid var(--color-border);
  border-radius: 10px;
  overflow: hidden;
}
</style>
