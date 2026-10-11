<script setup>
/**
 * GraphBrace.vue — 「大括号大纲」视图（归属树）
 *
 * 表达的是**归属**（学科 → 知识板块 → 知识点，含 / 属），不是依赖顺序 ——
 * 层级只在渲染层派生（`utils/graphTree.buildTaxonomyTree`），nodes/edges 一行不改。
 * 想看"先学什么"请切「思维导图」（那个才是 prerequisite 依赖树）——两个视图语义不许混。
 *
 * 纯展示：不调 api、不碰 store；数据与动作全走 props / emit。
 */
import { computed, nextTick, reactive, ref, watch } from 'vue'
import { buildTaxonomyTree } from '../utils/graphTree'
import GraphBraceNode from './GraphBraceNode.vue'

const props = defineProps({
  nodes: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  error: { type: String, default: '' },
  nextNodeId: { type: String, default: '' },
  sourceNodes: { type: Array, default: () => [] },
})

const emit = defineEmits(['open-node'])

const rootRef = ref(null)
const tree = computed(() => buildTaxonomyTree(props.nodes))
const sourceSet = computed(() => new Set(props.sourceNodes))

/** 折叠的分组 id 集合（默认全展开：大纲本来就是拿来通读的，滚动比折叠更顺手） */
const collapsed = reactive(new Set())

const ctx = {
  collapsed,
  toggle: (id) => { collapsed.has(id) ? collapsed.delete(id) : collapsed.add(id) },
  isNext: (id) => !!props.nextNodeId && id === props.nextNodeId,
  isSource: (id) => sourceSet.value.has(id),
  open: (id) => emit('open-node', id),
}

/** 图谱换了一批节点（切学科 / 重新建图）→ 清掉旧折叠态，避免新视图整片折叠 */
watch(tree, (t) => {
  const alive = new Set()
  const walk = (n) => { alive.add(n.id); (n.children || []).forEach(walk) }
  walk(t)
  for (const id of collapsed) if (!alive.has(id)) collapsed.delete(id)
})

/** 从根到目标节点的祖先链（找不到返回 null） */
function ancestry(node, id, acc = []) {
  if (node.id === id) return acc
  for (const c of node.children || []) {
    const hit = ancestry(c, id, [...acc, node.id])
    if (hit) return hit
  }
  return null
}

/**
 * 聚焦某知识点（供 HomeView 的搜索 / 任务栏「在图谱中定位」复用）：
 * 先展开祖先链，再把它滚进视野。
 */
async function focusNode(nodeId) {
  if (!nodeId) return
  for (const id of ancestry(tree.value, nodeId) || []) collapsed.delete(id)
  await nextTick()
  const row = rootRef.value?.querySelector(`[data-node-id="${nodeId}"]`)
  row?.scrollIntoView({ block: 'center', behavior: 'smooth' })
}

defineExpose({ focusNode })
</script>

<template>
  <div ref="rootRef" class="brace-view">
    <div v-if="loading && !nodes.length" class="brace-overlay">图谱加载中…</div>
    <div v-else-if="error" class="brace-overlay is-error">{{ error }}</div>
    <div v-else-if="!nodes.length" class="brace-overlay">
      <p>该学科暂无知识点</p>
      <p class="brace-hint">在对话里让 AI 建图，或从知识库上传资料建图</p>
    </div>
    <div v-else class="brace-tree">
      <GraphBraceNode v-for="c in tree.children" :key="c.id" :node="c" :ctx="ctx" />
    </div>
  </div>
</template>

<style scoped>
.brace-view {
  width: 100%;
  height: 100%;
  position: relative;
  background: var(--color-bg-primary);
  border-radius: 8px;
  overflow: hidden;
}

.brace-tree {
  height: 100%;
  overflow-y: auto;
  padding: 12px 14px 24px;
}
.brace-tree::-webkit-scrollbar { width: 6px; }
.brace-tree::-webkit-scrollbar-thumb { background: var(--color-border); border-radius: 3px; }

.brace-overlay {
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
}
.brace-overlay.is-error { color: var(--color-red); }
.brace-hint { font-size: 12px; color: var(--color-text-muted); margin: 0; }
</style>
