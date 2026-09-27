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

import { storeToRefs } from 'pinia'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useChatStore } from '../stores/chatStore'
import { useContextMenu } from '../utils/contextMenu'
import ContextMenu from './ContextMenu.vue'

const store = useChatStore()
const { currentSubject, boards, currentBoard } = storeToRefs(store)

// 右键菜单：与学科栏/对话侧栏同一套开关；targetData = 被右键的板块统计对象
const {
  visible: menuVisible,
  x: menuX,
  y: menuY,
  targetData: menuBoard,
  open: openMenu,
  close: closeMenu,
} = useContextMenu()

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

/** 重命名板块：只改分组名，板块内的知识点、边、掌握度都不动。 */
async function handleRename() {
  const b = menuBoard.value
  if (!b || !b.board) return
  let value
  try {
    ({ value } = await ElMessageBox.prompt('请输入新的板块名（板块内的知识点不受影响）', '重命名板块', {
      inputValue: b.board,
      confirmButtonText: '保存',
      cancelButtonText: '取消',
    }))
  } catch {
    return   // 取消
  }
  const name = (value || '').trim()
  if (!name || name === b.board) return
  // 本地先挡一层重名（与后端同规则，省一次往返；后端仍会再校验）
  if (boards.value.some((x) => x.board === name)) {
    ElMessage.error(`板块「${name}」已存在，请换一个名字`)
    return
  }
  try {
    const r = await store.renameBoard(currentSubject.value, b.board, name)
    ElMessage.success(`已重命名为「${name}」：${r.renamed_nodes} 个知识点`)
  } catch (e) {
    ElMessage.error('重命名失败：' + (e.response?.data?.detail || e.message))
  }
}

/**
 * 删除板块 = **解散分组**：板块内的知识点回到「未分组」，一个都不删。
 * 板块只是 `nodes.board` 上的标签，摘标签不该带走正文、边与掌握度。
 */
async function handleDelete() {
  const b = menuBoard.value
  if (!b || !b.board) return
  try {
    await ElMessageBox.confirm(
      `确定删除「${b.board}」板块吗？板块内的 ${b.node_count} 个知识点会保留，回到「未分组」。`,
      '删除板块',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }
    )
  } catch {
    return   // 取消
  }
  try {
    const r = await store.deleteBoard(currentSubject.value, b.board)
    ElMessage.success(`已删除板块「${b.board}」，${r.moved_nodes} 个知识点回到「未分组」`)
  } catch (e) {
    ElMessage.error('删除失败：' + (e.response?.data?.detail || e.message))
  }
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

      <!-- 各板块：「未分组」（board 为空串）不是真板块，不给右键菜单 -->
      <button
        v-for="b in boards"
        :key="b.board || '__ungrouped__'"
        class="board-item"
        :class="{ active: currentBoard === (b.board || '') }"
        :title="b.board ? `${b.board}（右键可重命名 / 删除）` : '未分组'"
        @click="handleBoardClick(b.board || '')"
        @contextmenu.prevent="b.board && openMenu($event, 'board', b)"
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

    <!-- 右键菜单：容器与学科栏/对话侧栏共用 -->
    <ContextMenu :visible="menuVisible" :x="menuX" :y="menuY" @close="closeMenu">
      <template #default="{ close }">
        <div class="menu-item" @click="close(); handleRename()">
          <span class="menu-icon">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                 stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z" />
            </svg>
          </span>
          重命名板块
        </div>
        <div class="menu-divider"></div>
        <div class="menu-item menu-item-danger" @click="close(); handleDelete()">
          <span class="menu-icon">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                 stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M3 6h18" />
              <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6" />
              <path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
              <line x1="10" y1="11" x2="10" y2="17" />
              <line x1="14" y1="11" x2="14" y2="17" />
            </svg>
          </span>
          删除板块（知识点保留）
        </div>
      </template>
    </ContextMenu>
  </aside>
</template>

<style scoped>
/* 定位交由父容器 .graph-nav（HomeView）统一管理，本组件只负责自身尺寸 */
.graph-board-sidebar {
  display: flex;
  flex-direction: column;
  width: 100%;
  min-height: 0;
  flex-shrink: 1;
  background: var(--color-bg-secondary);
  border: 1px solid var(--color-border);
  border-radius: 8px;
  overflow: hidden;
}

/* 标题区：与 ConversationSidebar / GraphSubjectBar 同款 */
.board-sidebar-header {
  display: flex;
  align-items: center;
  padding: 12px 12px 8px;
  flex-shrink: 0;
}

.board-sidebar-title {
  font-size: 13px;
  font-weight: 600;
  letter-spacing: 0.3px;
  color: var(--color-text-primary);
}

.board-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 0 6px 6px;
  overflow-y: auto;
  min-height: 0;
}

.board-list::-webkit-scrollbar { width: 4px; }
.board-list::-webkit-scrollbar-thumb {
  background: var(--color-border-light);
  border-radius: 2px;
}

/* 条目：与 GraphSubjectBar / ConversationSidebar 同款（无边框 + 左侧色条选中） */
.board-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  width: 100%;
  padding: 8px 10px;
  border: none;
  border-left: 3px solid transparent;
  border-radius: 8px;
  background: transparent;
  color: var(--color-text-primary);
  font-size: 13px;
  text-align: left;
  cursor: pointer;
  transition: background 0.15s;
}

.board-item:hover {
  background: var(--color-bg-hover);
}

.board-item.active {
  background: var(--color-accent-light);
  border-left-color: var(--color-accent);
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
  background: var(--color-accent);
}

.board-count {
  font-size: 11px;
  color: var(--color-text-muted);
  white-space: nowrap;
  flex-shrink: 0;
}

.board-item.active .board-count {
  color: var(--color-accent);
}
</style>
