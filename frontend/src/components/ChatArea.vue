<script setup>
import { watch, nextTick, ref } from 'vue'
import { useChatStore } from '../stores/chatStore'
import MessageBubble from './MessageBubble.vue'
import InputArea from './InputArea.vue'

const emit = defineEmits(['navigate-to-node'])

const store = useChatStore()
const listRef = ref(null)

/**
 * 判断消息列表是否贴近底部。
 * @returns {boolean} true 表示用户就在底部附近（120px 容差内），可以跟随滚动
 * 副作用：只读 DOM 尺寸，不改状态。
 */
function isNearBottom() {
  const el = listRef.value
  if (!el) return true
  return el.scrollHeight - el.scrollTop - el.clientHeight < 120
}

/**
 * 滚到底部。
 * @param {boolean} force true = 无条件滚动（切换对话时）
 * 副作用：修改 listRef 元素的 scrollTop。
 * 用直接赋值而非 scrollIntoView({behavior:'smooth'})：流式 chunk 每秒多次触发，
 * 平滑动画会被反复重启，表现为抖动 + 把上翻历史的用户拽回底部。
 */
function scrollToBottom(force = false) {
  // 必须在 DOM 更新前采样：watch(pre) 阶段拿到的仍是上一帧的 scrollHeight，
  // 这样判断的是"用户在看到新内容前是否在底部"，而不是被新内容推远之后的距离。
  if (!force && !isNearBottom()) return
  nextTick(() => {
    const el = listRef.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

// 消息数量变化时滚动
watch(
  () => store.currentMessages.length,
  () => scrollToBottom()
)

// 切换对话时无条件滚到底部
watch(
  () => store.currentId,
  () => scrollToBottom(true)
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

    <!-- 消息列表 -->
    <main class="message-list" ref="listRef">
      <template v-if="store.currentMessages.length > 0">
        <MessageBubble
          v-for="(msg, idx) in store.currentMessages"
          :key="idx"
          :message="msg"
          :knowledge-nodes="store.knowledgeNodes"
          @navigate-to-node="(nodeId) => emit('navigate-to-node', nodeId)"
          @retry="store.retryLast()"
          @submit-quiz="(p) => store.send(p.answer)"
        />
      </template>

      <!-- 空白欢迎 -->
      <div v-else class="welcome">
        <!-- 描边 SVG 图标（lucide sparkles），替代原来的 🧠 emoji -->
        <div class="welcome-icon">
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="1.8"
            stroke-linecap="round"
            stroke-linejoin="round"
            aria-hidden="true"
          >
            <path d="m12 3-1.912 5.813a2 2 0 0 1-1.275 1.275L3 12l5.813 1.912a2 2 0 0 1 1.275 1.275L12 21l1.912-5.813a2 2 0 0 1 1.275-1.275L21 12l-5.813-1.912a2 2 0 0 1-1.275-1.275L12 3Z" />
            <path d="M5 3v4" />
            <path d="M19 17v4" />
            <path d="M3 5h4" />
            <path d="M17 19h4" />
          </svg>
        </div>
        <h3>有什么想学的？</h3>
        <p v-for="(g, i) in greetingMessages" :key="i">{{ g }}</p>
      </div>
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

/* 消息列表：左右最大留白 = max(24px, 剩余空间/2)，把消息列封顶在 760px 并居中
   （长文一行 100+ 字难以阅读；不改 DOM 结构，仅靠 padding 收窄内容区） */
.message-list {
  flex: 1;
  overflow-y: auto;
  padding: 24px max(24px, calc((100% - 760px) / 2)) 8px;
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

/* 欢迎图标：描边 SVG，颜色随主题主色（原为 56px emoji 字号） */
.welcome-icon {
  display: inline-flex;
  margin-bottom: 16px;
  color: var(--color-primary);
  opacity: 0.9;
}

.welcome-icon svg {
  width: 48px;
  height: 48px;
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
