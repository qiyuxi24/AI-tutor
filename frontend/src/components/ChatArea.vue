<script setup>
import { watch, nextTick, ref } from 'vue'
import { useChatStore } from '../stores/chatStore'
import MessageBubble from './MessageBubble.vue'
import InputArea from './InputArea.vue'

const props = defineProps({
  sidebarCollapsed: Boolean,
})

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
  '可以把问题告诉我，我会根据你选择的引导模式来帮助你思考～',
]
</script>

<template>
  <div class="chat-area">
    <!-- ChatArea 内部不再有顶部栏 — 顶部栏已移到 App.vue 统一管理 -->

    <!-- 消息列表 -->
    <main class="message-list">
      <template v-if="store.currentMessages.length > 0">
        <MessageBubble
          v-for="(msg, idx) in store.currentMessages"
          :key="idx"
          :message="msg"
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
  background: #ffffff;
  min-width: 0;
}

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
  background: #e2e8f0;
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
  color: #94a3b8;
}

.welcome-icon {
  font-size: 56px;
  margin-bottom: 16px;
  opacity: 0.7;
}

.welcome h3 {
  font-size: 22px;
  font-weight: 600;
  color: #334155;
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
  color: #64748b;
  font-size: 14px;
}

.dot-pulse {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #4f46e5;
  animation: pulse 1.2s ease-in-out infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 0.3; transform: scale(0.8); }
  50% { opacity: 1; transform: scale(1.2); }
}
</style>
