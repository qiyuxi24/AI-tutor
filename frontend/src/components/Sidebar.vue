<script setup>
import { useChatStore } from '../stores/chatStore'

const store = useChatStore()

function handleNew() {
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
    <button class="new-chat-btn" @click="handleNew">
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
  background: #1e1e2e;
  color: #cdd6f4;
  display: flex;
  flex-direction: column;
  user-select: none;
  transform-origin: left;
}

/* 品牌区 */
.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 20px 18px 16px;
}

.logo-icon {
  font-size: 24px;
  line-height: 1;
}

.brand-text {
  font-size: 18px;
  font-weight: 700;
  color: #ffffff;
  letter-spacing: 0.3px;
}
.new-chat-btn {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0 14px 14px;
  padding: 10px 14px;
  border: 1px solid rgba(255, 255, 255, 0.15);
  border-radius: 10px;
  background: transparent;
  color: #cdd6f4;
  font-size: 14px;
  cursor: pointer;
  transition: background 0.2s, border-color 0.2s;
}

.new-chat-btn:hover {
  background: rgba(255, 255, 255, 0.06);
  border-color: rgba(255, 255, 255, 0.25);
}

/* 历史列表 */
.history-list {
  flex: 1;
  overflow-y: auto;
  padding: 0 8px;
}

.history-list::-webkit-scrollbar {
  width: 4px;
}

.history-list::-webkit-scrollbar-thumb {
  background: rgba(255, 255, 255, 0.15);
  border-radius: 2px;
}

.date-label {
  font-size: 11px;
  font-weight: 600;
  color: rgba(205, 214, 244, 0.45);
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

.history-item:hover {
  background: rgba(255, 255, 255, 0.06);
}

.history-item.active {
  background: rgba(79, 70, 229, 0.18);
  border-left-color: #4f46e5;
}

.chat-icon {
  flex-shrink: 0;
  opacity: 0.5;
}

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
  color: rgba(205, 214, 244, 0.4);
  cursor: pointer;
  transition: color 0.15s, background 0.15s;
}

.history-item:hover .delete-btn {
  display: flex;
}

.delete-btn:hover {
  color: #f87171;
  background: rgba(248, 113, 113, 0.12);
}

.empty-hint {
  text-align: center;
  color: rgba(205, 214, 244, 0.3);
  font-size: 13px;
  padding: 30px 0;
}
</style>
