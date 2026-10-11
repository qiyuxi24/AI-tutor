<script setup>
/**
 * GraphBraceNode.vue — 大括号大纲的递归行（只被 GraphBrace.vue 使用）
 *
 * 递归组件：自己渲染自己（SFC 按文件名自引用）。缩进由嵌套容器 + 左侧引导线给出，
 * 不靠 JS 算坐标 —— 与 VS Code 文件树的 indent guides 同一套思路。
 *
 * 交互：分组行（学科/板块）点行 = 折叠展开；知识点行点行 = 打开详情；
 * 三角是两条路都能走的显式折叠开关。
 */
import { computed } from 'vue'
import { masteryLevel } from '../utils/forceGraphEngine'

const props = defineProps({
  node: { type: Object, required: true },
  // 共享上下文（GraphBrace 传下来的同一对象）：collapsed / toggle / isNext / isSource / open
  ctx: { type: Object, required: true },
})

/** 四档状态类名 —— 与 forceGraphEngine 的 masteryLevel 同一套阈值（后端 mastery_bucket 唯一真值） */
const STATUS = ['st-unstarted', 'st-weak', 'st-learning', 'st-mastered']

const children = computed(() => props.node.children || [])
const isGroup = computed(() => props.node.kind === 'subject' || props.node.kind === 'board')
const collapsed = computed(() => props.ctx.collapsed.has(props.node.id))
const statusClass = computed(() => (isGroup.value ? '' : STATUS[masteryLevel(props.node.mastery)]))
const highlightClass = computed(() => ({
  'is-next': !isGroup.value && props.ctx.isNext(props.node.id),
  'is-source': !isGroup.value && props.ctx.isSource(props.node.id),
}))

function onRowClick() {
  if (isGroup.value) props.ctx.toggle(props.node.id)
  else props.ctx.open(props.node.id)
}
</script>

<template>
  <div class="bn">
    <div
      class="bn-row"
      :class="[statusClass, highlightClass, { group: isGroup }]"
      :data-node-id="node.id"
      :title="isGroup ? `${node.name}（${children.length}）` : node.name"
      @click="onRowClick"
    >
      <span
        class="bn-caret"
        :class="{ open: !collapsed, leaf: !children.length }"
        @click.stop="children.length && ctx.toggle(node.id)"
      >▸</span>
      <span v-if="!isGroup" class="bn-dot" :class="statusClass"></span>
      <span class="bn-name">{{ node.name }}</span>
      <span v-if="isGroup" class="bn-count">{{ children.length }}</span>
      <span v-if="node.extraParents" class="bn-extra" title="还有其他前置（未画进依赖树，切「思维导图」看）">
        +{{ node.extraParents }} 前置
      </span>
    </div>

    <div v-if="children.length && !collapsed" class="bn-kids">
      <!-- 缩进由嵌套的 .bn-kids 自己累积，不需要 depth 数值 -->
      <GraphBraceNode v-for="c in children" :key="c.id" :node="c" :ctx="ctx" />
    </div>
  </div>
</template>

<style scoped>
.bn-row {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 8px 4px 4px;
  border-radius: 6px;
  border-left: 3px solid transparent;
  font-size: 12.5px;
  color: var(--color-text-primary);
  cursor: pointer;
  transition: background 0.12s;
}
.bn-row:hover { background: var(--color-bg-hover); }

/* ── 折叠三角：折叠态 ▸，展开态 ▾；叶子留同宽占位保持对齐 ── */
.bn-caret {
  flex-shrink: 0;
  width: 12px;
  text-align: center;
  font-size: 10px;
  line-height: 1;
  color: var(--color-text-muted);
  transition: transform 0.15s;
}
.bn-caret.open { transform: rotate(90deg); }
.bn-caret.leaf { visibility: hidden; }

.bn-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.bn-count { flex-shrink: 0; font-size: 10.5px; color: var(--color-text-tertiary); }
.bn-extra {
  flex-shrink: 0;
  font-size: 10px;
  padding: 0 5px;
  border-radius: 20px;
  border: 1px dashed var(--color-border-light);
  color: var(--color-text-muted);
}

/* 分组行（学科/板块）：加粗 + 不参与四色 */
.bn-row.group { font-weight: 600; color: var(--color-text-secondary); }
.bn-row.group .bn-name { color: var(--color-text-secondary); }

.bn-dot { flex-shrink: 0; width: 8px; height: 8px; border-radius: 50%; }

/* ── 四档掌握度（同一套色值契约） ── */
.bn-row.st-mastered { border-left-color: var(--color-green); }
.bn-row.st-mastered .bn-dot { background: var(--color-green); }
.bn-row.st-learning { border-left-color: var(--color-yellow); }
.bn-row.st-learning .bn-dot { background: var(--color-yellow); }
.bn-row.st-weak { border-left-color: var(--color-red); }
.bn-row.st-weak .bn-dot { background: var(--color-red); }
.bn-row.st-unstarted { border-left-color: var(--color-border-light); }
.bn-row.st-unstarted .bn-dot { background: var(--color-graph-node); }

/* ── 联动高亮：推荐节点（下一步该学） / 来源命中（「在图谱中显示」） ── */
.bn-row.is-next { background: var(--color-accent-light); border-left-color: var(--color-accent); }
.bn-row.is-source { outline: 1px solid var(--color-accent); outline-offset: -1px; }

/* ── 子级缩进 + 引导线（大括号的连接线的纯 CSS 部分） ── */
.bn-kids {
  margin-left: 9px;
  padding-left: 8px;
  border-left: 1px dashed var(--color-border);
}
</style>
