<script setup>
import { useChatStore } from '../stores/chatStore'

const store = useChatStore()

function handleNew() {
  // 如果当前已是空对话，不做任何事
  if (store.isCurrentEmpty) return
  store.newConversation()
}

function handleSwitch(id) {
  store.switchConversation(id)
}

function handleDelete(e, id) {
  e.stopPropagation()
  store.deleteConversation(id)
}
</script>

<template>
  <aside class="sidebar">
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
          @click="handleSwitch(conv.id)"
        >
          <svg class="chat-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
          </svg>
          <span class="item-title">{{ conv.title }}</span>
          <button class="delete-btn" @click="handleDelete($event, conv.id)" title="删除对话">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>
      </template>
      <div v-if="!store.hasConversations" class="empty-hint">暂无历史对话</div>
    </nav>
  </aside>
</template>

<style scoped>
.sidebar {
  width: 260px;
  min-width: 260px;
  height: 100%;
  background: var(--color-bg-primary);
  color: var(--color-text-primary);
  display: flex;
  flex-direction: column;
  user-select: none;
  transform-origin: left;
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
  flex: 1;
  overflow-y: auto;
  padding: 0 8px;
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
  padding: 10px 10px;
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

.delete-btn {
  flex-shrink: 0;
  display: none;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--color-text-muted);
  cursor: pointer;
  transition: color 0.15s, background 0.15s;
}
.history-item:hover .delete-btn { display: flex; }
.delete-btn:hover {
  color: var(--color-red);
  background: var(--color-red-light);
}

.empty-hint {
  text-align: center;
  color: var(--color-text-muted);
  font-size: 13px;
  padding: 30px 0;
}
</style>
