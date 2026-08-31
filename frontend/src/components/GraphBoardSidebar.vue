<script setup>
/**
 * GraphBoardSidebar.vue — 知识图谱板块侧栏
 *
 * 职责：
 *   展示当前学科下的知识板块列表（含节点数/掌握度统计），
 *   支持按板块按需加载局部子图（配合后端 graph_middleware 按需切片）。
 *
 * 数据流：
 *   store.boards（当前学科板块列表） → 渲染
 *   store.currentBoard（当前选中板块） → 高亮
 *   store.setBoard()（点击板块） → 按需请求该板块子图
 *
 * 设计风格：与 ConversationSidebar 一致，Obsidian 极简，无边框卡片。
 */

import { useChatStore } from '../stores/chatStore'
import { storeToRefs } from 'pinia'

const store = useChatStore()
const { currentSubject, boards, currentBoard } = storeToRefs(store)

/**
 * 点击板块：按需加载该板块局部子图（null = 整学科）。
 * @param {string|null} board
 */
function handleBoardClick(board) {
  store.setBoard(board)
}

/**
 * 板块节点数文案。
 */
function boardCountText(b) {
  if (!b || b.node_count == null) return ''
  return `${b.node_count} 节点`
}
</script>

<template>
  <aside class="graph-board-sidebar" v-if="currentSubject && boards.length">
    <div class="board-sidebar-header">
      <span class="board-sidebar-title">知识板块</span>
    </div>

    <div class="board-list">
      <!-- 整学科视图（重置板块过滤） -->
      <button
        class="board-item"
        :class="{ active: currentBoard === null }"
        @click="handleBoardClick(null)"
      >
        <span class="board-name">整学科</span>
        <span class="board-count">全部</span>
      </button>

      <!-- 各板块 -->
      <button
        v-for="b in boards"
        :key="b.board || '__ungrouped__'"
        class="board-item"
        :class="{ active: currentBoard === (b.board || '') }"
        @click="handleBoardClick(b.board || '')"
      >
        <span class="board-name">
          <span class="board-dot"></span>
          {{ b.board || '未分组' }}
        </span>
        <span class="board-count" :title="`已掌握 ${b.mastered_count} / ${b.node_count}`">
          {{ boardCountText(b) }}
        </span>
      </button>
    </div>
  </aside>
</template>

<style scoped>
.graph-board-sidebar {
  position: absolute;
  top: 64px;
  left: 12px;
  bottom: 12px;
  width: 200px;
  z-index: 20;
  background: var(--color-bg-secondary);
  border: 1px solid var(--color-border);
  border-radius: 8px;
  overflow-y: auto;
  padding: 8px 6px;
}

.board-sidebar-header {
  padding: 6px 10px 10px;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.02em;
  color: var(--color-text-secondary);
}

.board-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.board-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  width: 100%;
  padding: 7px 10px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--color-text-primary);
  font-size: 13px;
  text-align: left;
  cursor: pointer;
  transition: background 0.15s;
}

.board-item:hover {
  background: var(--color-bg-hover, rgba(127, 127, 127, 0.08));
}

.board-item.active {
  background: var(--color-accent, #5b8ff9);
  color: var(--color-on-accent, #fff);
}

.board-name {
  display: flex;
  align-items: center;
  gap: 6px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.board-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--color-text-tertiary);
  flex-shrink: 0;
}

.board-item.active .board-dot {
  background: var(--color-on-accent, #fff);
}

.board-count {
  font-size: 11px;
  color: var(--color-text-tertiary);
  white-space: nowrap;
  flex-shrink: 0;
}

.board-item.active .board-count {
  color: inherit;
  opacity: 0.8;
}
</style>
