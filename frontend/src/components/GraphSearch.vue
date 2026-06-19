<script setup>
/**
 * GraphSearch.vue — 知识图谱搜索组件
 *
 * 职责：纯搜索 UI，不直接调用 API。
 *
 * 数据流（去耦合）：
 *   Store.knowledgeNodes → (props: nodes) → GraphSearch → (emit: select-node) → HomeView
 *
 * 特性：
 *   - 即时搜索（输入即过滤）
 *   - 键盘导航（↑↓ 选择，Enter 确认，Esc 关闭）
 *   - 高亮匹配文本
 *   - 点击节点后聚焦图谱中的对应节点
 */

import { ref, computed, watch, nextTick } from 'vue'

/* ================================================================
   组件 Props
   ================================================================ */
const props = defineProps({
  nodes: { type: Array, default: () => [] },
  placeholder: { type: String, default: '搜索知识点...' },
})

/* ================================================================
   组件 Events
   ================================================================ */
const emit = defineEmits(['select-node'])

/* ================================================================
   响应式状态
   ================================================================ */
const query = ref('')
const selectedIndex = ref(-1)
const focused = ref(false)
const inputRef = ref(null)
const listRef = ref(null)

/* ================================================================
   计算属性
   ================================================================ */
const results = computed(() => {
  const q = query.value.trim().toLowerCase()
  if (!q) return []
  const items = (props.nodes || [])
    .filter(n => n.name && n.name.toLowerCase().includes(q))
    .map(n => ({
      ...n,
      matchStart: n.name.toLowerCase().indexOf(q),
      matchLen: q.length,
    }))
    .slice(0, 10) // 最多显示 10 条
  // 重置选中索引
  if (selectedIndex.value >= items.length) {
    selectedIndex.value = items.length > 0 ? 0 : -1
  }
  return items
})

const showResults = computed(() => focused.value && query.value.trim().length > 0)

/* ================================================================
   键盘导航
   ================================================================ */
function handleKeydown(e) {
  if (!showResults.value) return

  switch (e.key) {
    case 'ArrowDown':
      e.preventDefault()
      selectedIndex.value = Math.min(selectedIndex.value + 1, results.value.length - 1)
      scrollToSelected()
      break
    case 'ArrowUp':
      e.preventDefault()
      selectedIndex.value = Math.max(selectedIndex.value - 1, 0)
      scrollToSelected()
      break
    case 'Enter':
      e.preventDefault()
      if (selectedIndex.value >= 0 && results.value[selectedIndex.value]) {
        selectNode(results.value[selectedIndex.value])
      }
      break
    case 'Escape':
      e.preventDefault()
      closeSearch()
      break
  }
}

function scrollToSelected() {
  nextTick(() => {
    const el = listRef.value?.querySelector('.search-result-item.selected')
    el?.scrollIntoView({ block: 'nearest' })
  })
}

/* ================================================================
   操作
   ================================================================ */
function selectNode(node) {
  emit('select-node', node.id)
  closeSearch()
}

function closeSearch() {
  query.value = ''
  selectedIndex.value = -1
  focused.value = false
  inputRef.value?.blur()
}

function onFocus() {
  focused.value = true
  selectedIndex.value = results.value.length > 0 ? 0 : -1
}

function onBlur() {
  // 延迟关闭，让 click 事件先触发
  setTimeout(() => { focused.value = false }, 200)
}

// 当 query 变化时，自动选中第一个结果
watch(() => query.value, () => {
  selectedIndex.value = 0
})

/* ================================================================
   暴露方法（父组件可调用）
   ================================================================ */
defineExpose({ focus: () => inputRef.value?.focus(), close: closeSearch })
</script>

<template>
  <div class="graph-search" :class="{ focused: focused }">
    <div class="search-input-wrapper">
      <svg class="search-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round">
        <circle cx="11" cy="11" r="8"/>
        <path d="m21 21-4.35-4.35"/>
      </svg>
      <input
        ref="inputRef"
        v-model="query"
        type="text"
        :placeholder="placeholder"
        class="search-input"
        @keydown="handleKeydown"
        @focus="onFocus"
        @blur="onBlur"
      />
      <button v-if="query" class="search-clear" @mousedown.prevent="closeSearch" title="清除">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round">
          <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
        </svg>
      </button>
    </div>

    <!-- 搜索结果下拉 -->
    <Transition name="search-drop">
      <div v-if="showResults" ref="listRef" class="search-results">
        <div v-if="results.length === 0" class="search-empty">
          未找到匹配的知识点
        </div>
        <div
          v-for="(node, idx) in results"
          :key="node.id"
          class="search-result-item"
          :class="{ selected: idx === selectedIndex }"
          @mousedown.prevent="selectNode(node)"
          @mouseenter="selectedIndex = idx"
        >
          <!-- 节点圆点 -->
          <span class="result-dot" :style="{
            background: (node.mastery == null || node.mastery === 0)
              ? 'var(--color-graph-node)'
              : 'var(--color-accent)',
            opacity: (node.mastery == null || node.mastery === 0) ? 0.6 : 1,
          }"></span>

          <!-- 名称（高亮匹配部分） -->
          <span class="result-name">
            <span>{{ node.name.slice(0, node.matchStart) }}</span>
            <mark>{{ node.name.slice(node.matchStart, node.matchStart + node.matchLen) }}</mark>
            <span>{{ node.name.slice(node.matchStart + node.matchLen) }}</span>
          </span>

          <!-- 掌握度指示 -->
          <span v-if="node.mastery" class="result-mastery" :style="{
            color: node.mastery >= 60 ? 'var(--color-green)' : node.mastery >= 30 ? 'var(--color-orange)' : 'var(--color-text-muted)'
          }">
            {{ node.mastery }}%
          </span>
        </div>

        <!-- 键盘提示 -->
        <div class="search-footer">
          <span class="search-hint">
            <kbd>↑↓</kbd> 导航 <kbd>Enter</kbd> 选择 <kbd>Esc</kbd> 关闭
          </span>
        </div>
      </div>
    </Transition>
  </div>
</template>

<style scoped>
/* ════════════════════════════════════════════
   搜索容器
   ════════════════════════════════════════════ */
.graph-search {
  position: relative;
  width: 240px;
}

.search-input-wrapper {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 0 10px;
  height: 32px;
  background: var(--color-bg-surface);
  border: 1px solid var(--color-border-subtle);
  border-radius: 8px;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}

.graph-search.focused .search-input-wrapper {
  border-color: var(--color-accent);
  box-shadow: 0 0 0 2px var(--color-accent-light, rgba(59, 130, 246, 0.15));
}

.search-icon {
  color: var(--color-text-muted);
  flex-shrink: 0;
}

.search-input {
  flex: 1;
  border: none;
  outline: none;
  background: transparent;
  color: var(--color-text-primary);
  font-size: 13px;
  font-family: inherit;
  min-width: 0;
}
.search-input::placeholder {
  color: var(--color-text-tertiary);
}

.search-clear {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  border: none;
  background: transparent;
  color: var(--color-text-muted);
  cursor: pointer;
  border-radius: 4px;
  flex-shrink: 0;
  padding: 0;
}
.search-clear:hover { color: var(--color-text-primary); background: var(--color-bg-hover); }

/* ════════════════════════════════════════════
   搜索结果下拉
   ════════════════════════════════════════════ */
.search-results {
  position: absolute;
  top: calc(100% + 4px);
  left: 0;
  right: 0;
  background: var(--color-bg-primary);
  border: 1px solid var(--color-border);
  border-radius: 10px;
  box-shadow: var(--shadow-popup);
  z-index: 50;
  overflow: hidden;
  max-height: 320px;
  overflow-y: auto;
}

.search-empty {
  padding: 24px 16px;
  text-align: center;
  color: var(--color-text-muted);
  font-size: 13px;
}

/* ── 结果项 ── */
.search-result-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  cursor: pointer;
  transition: background 0.1s ease;
}

.search-result-item.selected,
.search-result-item:hover {
  background: var(--color-bg-surface);
}

.result-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}

.result-name {
  flex: 1;
  font-size: 13px;
  color: var(--color-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.result-name mark {
  background: var(--color-accent-light, rgba(59, 130, 246, 0.2));
  color: var(--color-accent);
  border-radius: 2px;
  padding: 0 1px;
}

.result-mastery {
  font-size: 11px;
  font-weight: 500;
  flex-shrink: 0;
}

/* ── 底部提示 ── */
.search-footer {
  padding: 6px 12px;
  border-top: 1px solid var(--color-border-subtle);
  background: var(--color-bg-surface);
}

.search-hint {
  font-size: 11px;
  color: var(--color-text-tertiary);
  display: flex;
  align-items: center;
  gap: 4px;
}

.search-hint kbd {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 18px;
  height: 16px;
  padding: 0 4px;
  font-size: 10px;
  font-family: inherit;
  color: var(--color-text-secondary);
  background: var(--color-bg-primary);
  border: 1px solid var(--color-border-subtle);
  border-radius: 3px;
}

/* ════════════════════════════════════════════
   过渡动画
   ════════════════════════════════════════════ */
.search-drop-enter-active {
  transition: opacity 0.12s ease, transform 0.12s ease;
}
.search-drop-leave-active {
  transition: opacity 0.1s ease, transform 0.1s ease;
}
.search-drop-enter-from,
.search-drop-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}
</style>
