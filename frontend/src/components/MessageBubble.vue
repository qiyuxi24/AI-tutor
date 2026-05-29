<script setup>
import { computed } from 'vue'
import { marked } from 'marked'

const props = defineProps({
  message: { type: Object, required: true },
})

const isUser = computed(() => props.message.role === 'user')
const isStreaming = computed(() => !isUser.value && !props.message.content)

const renderedContent = computed(() => {
  if (isUser.value) return props.message.content
  // 流式内容直接渲染纯文本（不用 markdown，因为内容不完整时可能格式错乱）
  return marked(props.message.content || '', { breaks: true })
})
</script>

<template>
  <div class="bubble-wrapper" :class="{ 'is-user': isUser }">
    <div class="avatar" :class="{ 'user-avatar': isUser }">
      {{ isUser ? '👤' : '🤖' }}
    </div>
    <div class="bubble" :class="{ 'user-bubble': isUser, 'ai-bubble': !isUser }">
      <div v-if="isUser" class="text">{{ message.content }}</div>
      <!-- 流式填充中：显示打字动画 -->
      <div v-else-if="isStreaming" class="typing-indicator">
        <span></span><span></span><span></span>
      </div>
      <!-- 流式内容渲染 -->
      <div v-else class="markdown-body" v-html="renderedContent"></div>
    </div>
  </div>
</template>

<style scoped>
.bubble-wrapper {
  display: flex;
  gap: 10px;
  margin-bottom: 16px;
  max-width: 85%;
  animation: fadeIn 0.2s ease;
}

@keyframes fadeIn {
  from { opacity: 0; transform: translateY(6px); }
  to   { opacity: 1; transform: translateY(0); }
}

.bubble-wrapper.is-user {
  flex-direction: row-reverse;
  margin-left: auto;
}

.avatar {
  flex-shrink: 0;
  width: 32px;
  height: 32px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  background: #f1f5f9;
}

.user-avatar {
  background: #eef2ff;
}

.bubble {
  padding: 12px 16px;
  border-radius: 18px;
  line-height: 1.6;
  font-size: 14px;
  word-break: break-word;
}

.user-bubble {
  background: #4f46e5;
  color: #ffffff;
  border-bottom-right-radius: 4px;
}

.ai-bubble {
  background: #f1f5f9;
  color: #1e293b;
  border-bottom-left-radius: 4px;
}

.text {
  white-space: pre-wrap;
}

/* Markdown 渲染样式 */
.markdown-body :deep(p) {
  margin: 0 0 8px;
}

.markdown-body :deep(p:last-child) {
  margin-bottom: 0;
}

.markdown-body :deep(strong) {
  font-weight: 600;
}

.markdown-body :deep(code) {
  background: rgba(0, 0, 0, 0.06);
  padding: 2px 6px;
  border-radius: 4px;
  font-family: 'Consolas', 'Courier New', monospace;
  font-size: 13px;
}

.markdown-body :deep(pre) {
  background: #1e293b;
  color: #e2e8f0;
  padding: 14px 16px;
  border-radius: 10px;
  overflow-x: auto;
  margin: 10px 0;
  font-size: 13px;
  line-height: 1.5;
}

.markdown-body :deep(pre code) {
  background: none;
  padding: 0;
  color: inherit;
}

.markdown-body :deep(ul),
.markdown-body :deep(ol) {
  padding-left: 20px;
  margin: 6px 0;
}

.markdown-body :deep(li) {
  margin-bottom: 4px;
}

.markdown-body :deep(blockquote) {
  border-left: 3px solid #4f46e5;
  margin: 8px 0;
  padding: 4px 12px;
  color: #64748b;
  background: rgba(79, 70, 229, 0.04);
  border-radius: 0 6px 6px 0;
}

.markdown-body :deep(h3),
.markdown-body :deep(h4) {
  margin: 12px 0 6px;
}

.markdown-body :deep(a) {
  color: #4f46e5;
  text-decoration: underline;
}

.markdown-body :deep(table) {
  border-collapse: collapse;
  margin: 10px 0;
  width: 100%;
  font-size: 13px;
}

.markdown-body :deep(th),
.markdown-body :deep(td) {
  border: 1px solid #d1d5db;
  padding: 6px 10px;
  text-align: left;
}

.markdown-body :deep(th) {
  background: #f8fafc;
  font-weight: 600;
}

/* 流式打字动画 */
.typing-indicator {
  display: flex;
  gap: 4px;
  padding: 4px 0;
}

.typing-indicator span {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #94a3b8;
  animation: typingBounce 1.2s ease-in-out infinite;
}

.typing-indicator span:nth-child(2) {
  animation-delay: 0.2s;
}

.typing-indicator span:nth-child(3) {
  animation-delay: 0.4s;
}

@keyframes typingBounce {
  0%, 60%, 100% {
    transform: translateY(0);
    opacity: 0.4;
  }
  30% {
    transform: translateY(-6px);
    opacity: 1;
  }
}
</style>
