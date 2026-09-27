<script setup>
import { watch, nextTick, ref } from 'vue'
import { useChatStore } from '../stores/chatStore'
import MessageBubble from './MessageBubble.vue'
import InputArea from './InputArea.vue'

const props = defineProps({
  sidebarCollapsed: Boolean,
})

const emit = defineEmits(['navigate-to-node'])

const store = useChatStore()
const messagesEnd = ref(null)

function scrollToBottom() {
  nextTick(() => {
    messagesEnd.value?.scrollIntoView({ behavior: 'smooth' })
  })
}

// 消息数量变化时滚动
watch(
  () => store.currentMessages.length,
  () => scrollToBottom()
)

// 切换对话时滚动到底部
watch(
  () => store.currentId,
  () => scrollToBottom()
)

// 流式内容变化时也滚动（内容长度变化）
watch(
  () => {
    const msgs = store.currentMessages
    if (msgs.length === 0) return ''
    const last = msgs[msgs.length - 1]
    return last?.content?.length ?? 0
  },
  () => scrollToBottom()
)

const greetingMessages = [
  '你好！我是你的 AI 学习助手，有什么想学的内容吗？',
  '可以把问题告诉我，我来陪你一起把思路理顺～',
]
</script>

<template>
  <div class="chat-area">
    <!-- ChatArea 内部不再有顶部栏 — 顶部栏已移到 App.vue 统一管理 -->

    <!-- 教学焦点上下文条：由图谱节点「去学习」进入；✕ 退出并恢复全图谱范围 -->
    <div v-if="store.currentNode" class="learning-scope">
      <span class="scope-dot"></span>
      <span class="scope-text">
        正在教学：<strong>{{ store.currentNodeName || store.currentNode }}</strong>
      </span>
      <span class="scope-hint">AI 只在此知识点的可行域内教学</span>
      <button
        class="scope-exit"
        title="退出该知识点，恢复全图谱范围"
        @click="store.clearCurrentNode()"
      >✕</button>
    </div>

    <!-- 消息列表 -->
    <main class="message-list">
      <template v-if="store.currentMessages.length > 0">
        <MessageBubble
          v-for="(msg, idx) in store.currentMessages"
          :key="idx"
          :message="msg"
          :knowledge-nodes="store.knowledgeNodes"
          @navigate-to-node="(nodeId) => emit('navigate-to-node', nodeId)"
          @retry="store.retryLast()"
        />
      </template>

      <!-- 空白欢迎 -->
      <div v-else class="welcome">
        <div class="welcome-icon">🧠</div>
        <h3>有什么想学的？</h3>
        <p v-for="(g, i) in greetingMessages" :key="i">{{ g }}</p>
      </div>

      <!-- 加载指示 -->
      <div v-if="store.loading" class="loading-indicator">
        <span class="dot-pulse"></span>
        <span>AI 思考中…</span>
      </div>

      <!-- 底部锚点 -->
      <div ref="messagesEnd"></div>
    </main>

    <!-- 底部输入 -->
    <InputArea />
  </div>
</template>

<style scoped>
.chat-area {
  flex: 1;
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--color-bg-primary);
  min-width: 0;
}

/* 教学焦点上下文条（图谱「去学习」进入后显示） */
.learning-scope {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 12px 24px 0;
  padding: 8px 12px;
  border: 1px solid var(--color-accent-light);
  background: var(--color-accent-light);
  border-radius: 8px;
  font-size: 13px;
  color: var(--color-text-secondary);
  flex-shrink: 0;
}

.scope-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--color-accent);
  flex-shrink: 0;
}

.scope-text strong { color: var(--color-text-primary); font-weight: 600; }

.scope-hint {
  color: var(--color-text-muted);
  font-size: 12px;
}

.scope-exit {
  margin-left: auto;
  border: none;
  background: transparent;
  cursor: pointer;
  color: var(--color-text-muted);
  font-size: 13px;
  line-height: 1;
  padding: 3px 6px;
  border-radius: 4px;
}

.scope-exit:hover { background: var(--color-bg-hover); color: var(--color-text-primary); }

/* 消息列表 */
.message-list {
  flex: 1;
  overflow-y: auto;
  padding: 24px 24px 8px;
}

.message-list::-webkit-scrollbar {
  width: 5px;
}

.message-list::-webkit-scrollbar-thumb {
  background: var(--color-border-light);
  border-radius: 3px;
}

/* 欢迎区 */
.welcome {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  text-align: center;
  color: var(--color-text-muted);
}

.welcome-icon {
  font-size: 56px;
  margin-bottom: 16px;
  opacity: 0.7;
}

.welcome h3 {
  font-size: 22px;
  font-weight: 600;
  color: var(--color-text-primary);
  margin: 0 0 8px;
}

.welcome p {
  margin: 4px 0;
  font-size: 14px;
  line-height: 1.6;
  max-width: 380px;
}

/* 加载动画 */
.loading-indicator {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 16px;
  color: var(--color-text-secondary);
  font-size: 14px;
}

.dot-pulse {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--color-accent);
  animation: pulse 1.2s ease-in-out infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 0.3; transform: scale(0.8); }
  50% { opacity: 1; transform: scale(1.2); }
}
</style>
