<script setup>
/**
 * ConversationSidebar.vue — 对话页的二级侧边栏
 *
 * 职责：新对话按钮 + 对话历史列表（VSCode 侧边栏风格）
 * 由父组件（HomeView）控制折叠，本组件不持业务状态。
 */

import { nextTick, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { confirmAction } from '../utils/feedback'
import { useChatStore } from '../stores/chatStore'
import { useContextMenu } from '../utils/contextMenu'
import ContextMenu from './ContextMenu.vue'

const store = useChatStore()

// 右键菜单：与图谱侧栏同一套开关（utils/contextMenu.js），targetData = 被右键的对话
const {
  visible: menuVisible,
  x: menuX,
  y: menuY,
  targetData: menuConv,
  open: openMenu,
  close: closeMenu,
} = useContextMenu()

function handleNew() {
  if (store.isCurrentEmpty) return
  store.newConversation()
}

function handleSwitch(id) {
  store.switchConversation(id)
}

// 内联重命名：编辑中的对话 id + 草稿标题（同一时刻只允许一条在编辑）
const editingId = ref(null)
const editingTitle = ref('')
const editInput = ref(null)

/** input 的模板 ref（函数式，避免在 v-for 里收成数组）。 */
function setEditInput(el) {
  editInput.value = el
}

/**
 * 进入内联编辑（右键菜单「重命名对话」入口）。标题本就在「localStorage 为主 + 后端全量
 * 同步」的契约里，改完 persist() 会自动把新标题同步过去，不需要额外请求。
 */
async function startRename(conv) {
  editingId.value = conv.id
  editingTitle.value = conv.title
  await nextTick()
  editInput.value?.focus()
  editInput.value?.select()
}

/** 提交编辑：空标题视为放弃，保持原标题。 */
function commitRename() {
  const id = editingId.value
  if (!id) return
  const title = editingTitle.value.trim()
  editingId.value = null
  if (title) store.renameConversation(id, title)
}

/** 放弃编辑（Esc）。 */
function cancelRename() {
  editingId.value = null
}

/** 删除对话（二次确认）。删的是当前对话时，store 会自动补一个新对话，不会停在空视图。 */
async function handleDelete() {
  const conv = menuConv.value
  if (!conv) return
  const ok = await confirmAction(`确定删除对话「${conv.title}」吗？此操作不可撤销。`)
  if (!ok) return   // 取消
  store.deleteConversation(conv.id)
  ElMessage.success('已删除')
}
</script>

<template>
  <aside class="conv-sidebar">
    <!-- 标题区 -->
    <div class="cs-header">
      <span class="cs-title">对话</span>
    </div>

    <!-- 新对话按钮 -->
    <button
      class="new-chat-btn"
      :class="{ disabled: store.isCurrentEmpty }"
      @click="handleNew"
      :title="store.isCurrentEmpty ? '当前已是新对话' : '新建对话'"
    >
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round">
        <line x1="12" y1="5" x2="12" y2="19" />
        <line x1="5" y1="12" x2="19" y2="12" />
      </svg>
      新对话
    </button>

    <!-- 历史列表 -->
    <nav class="history-list">
      <template v-for="(convs, label) in store.groupedConversations" :key="label">
        <div class="date-label">{{ label }}</div>
        <div
          v-for="conv in convs"
          :key="conv.id"
          class="history-item"
          :class="{ active: conv.id === store.currentId }"
          :title="`${conv.title}（右键可重命名 / 删除）`"
          @click="handleSwitch(conv.id)"
          @contextmenu.prevent="openMenu($event, 'conversation', conv)"
        >
          <svg class="chat-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
          </svg>
          <input
            v-if="editingId === conv.id"
            :ref="setEditInput"
            v-model="editingTitle"
            class="rename-input"
            @click.stop
            @keydown.enter.prevent="commitRename"
            @keydown.esc.prevent="cancelRename"
            @blur="commitRename"
          />
          <span v-else class="item-title">{{ conv.title }}</span>
        </div>
      </template>
      <div v-if="!store.hasConversations" class="empty-hint">暂无历史对话</div>
    </nav>

    <!-- 右键菜单：容器与图谱侧栏共用 -->
    <ContextMenu :visible="menuVisible" :x="menuX" :y="menuY" @close="closeMenu">
      <template #default="{ close }">
        <div class="menu-item" @click="close(); startRename(menuConv)">
          <span class="menu-icon">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                 stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z" />
            </svg>
          </span>
          重命名对话
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
          删除对话
        </div>
      </template>
    </ContextMenu>
  </aside>
</template>

<style scoped>
.conv-sidebar {
  /* 宽度与外观由外层 SidePanel 决定（统一卡片），这里只管内部纵向布局 */
  width: 100%;
  height: 100%;
  background: transparent;
  color: var(--color-text-primary);
  display: flex;
  flex-direction: column;
  user-select: none;
}

.cs-header {
  display: flex;
  align-items: center;
  padding: 12px 14px 8px;
  flex-shrink: 0;
}

.cs-title {
  font-size: 13px;
  font-weight: 600;
  letter-spacing: 0.3px;
  color: var(--color-text-primary);
}

.new-chat-btn {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0 14px 14px;
  padding: 10px 14px;
  border: 1px solid var(--color-border-light);
  border-radius: 10px;
  background: transparent;
  color: var(--color-text-primary);
  font-size: 14px;
  cursor: pointer;
  transition: background 0.2s, border-color 0.2s;
}
.new-chat-btn:hover {
  background: var(--color-bg-hover);
  border-color: var(--color-border);
}
.new-chat-btn.disabled {
  opacity: 0.4;
  cursor: not-allowed;
  pointer-events: none;
}

.history-list {
  flex: 1 1 auto;
  overflow-y: auto;
  padding: 0 8px;
  min-height: 0;
}
.history-list::-webkit-scrollbar { width: 4px; }
.history-list::-webkit-scrollbar-thumb {
  background: var(--color-border-light);
  border-radius: 2px;
}

.date-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--color-text-muted);
  text-transform: uppercase;
  letter-spacing: 0.8px;
  padding: 12px 10px 6px;
}

.history-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px;
  border-radius: 8px;
  cursor: pointer;
  border-left: 3px solid transparent;
  transition: background 0.15s;
  position: relative;
}
.history-item:hover { background: var(--color-bg-hover); }
.history-item.active {
  background: var(--color-accent-light);
  border-left-color: var(--color-accent);
}

.chat-icon { flex-shrink: 0; opacity: 0.5; }

.item-title {
  flex: 1;
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  line-height: 1.3;
}

/* 内联重命名输入框：就地替换标题，不弹窗 */
.rename-input {
  flex: 1;
  min-width: 0;
  font-family: inherit;
  font-size: 13px;
  line-height: 1.3;
  padding: 2px 6px;
  border: 1px solid var(--color-accent);
  border-radius: 4px;
  background: var(--color-bg-primary);
  color: var(--color-text-primary);
  outline: none;
}

.empty-hint {
  text-align: center;
  color: var(--color-text-muted);
  font-size: 13px;
  padding: 30px 0;
}
</style>
