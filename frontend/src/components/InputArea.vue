<script setup>
import { ref, nextTick } from 'vue'
import { useChatStore } from '../stores/chatStore'

const store = useChatStore()

const text = ref('')
const textareaRef = ref(null)

/**
 * textarea 自动增高。
 * 副作用：直接改 DOM 的 style.height（不走响应式，避免每个字符触发重渲染）。
 * 先置 auto 才能拿到真实内容高度，否则 scrollHeight 只会等于当前已撑开的高度。
 * 上限 120px 由 CSS max-height 兜底，超出后内部滚动。
 */
function autoGrow() {
  const el = textareaRef.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = Math.min(el.scrollHeight, 120) + 'px'
}

/** 清空输入后把高度收回单行（rows="1" 决定 auto 时的基准高度） */
function resetHeight() {
  const el = textareaRef.value
  if (el) el.style.height = 'auto'
}

async function handleSend() {
  if (!text.value.trim() || store.loading) return
  const msg = text.value
  text.value = ''
  await nextTick()
  resetHeight()
  await store.send(msg)
  await nextTick()
  textareaRef.value?.focus()
}

function handleKeydown(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    handleSend()
  }
}
</script>

<template>
  <div class="input-area">
    <!-- 输入框 + 发送按钮 -->
    <div class="input-row">
      <textarea
        ref="textareaRef"
        v-model="text"
        placeholder="输入你的问题..."
        rows="1"
        class="input-textarea"
        @input="autoGrow"
        @keydown="handleKeydown"
      ></textarea>
      <button
        class="send-btn"
        :class="{ active: text.trim() && !store.loading }"
        :disabled="!text.trim() || store.loading"
        @click="handleSend"
        title="发送"
      >
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
          <line x1="22" y1="2" x2="11" y2="13" />
          <polygon points="22 2 15 22 11 13 2 9 22 2" />
        </svg>
      </button>
    </div>

    <!-- 快捷键提示：直接挂在 .input-area 下，不额外包容器 -->
    <p class="input-hint">Enter 发送 · Shift + Enter 换行</p>
  </div>
</template>

<style scoped>
.input-area {
  border-top: 1px solid var(--color-border);
  padding: 16px 24px 20px;
  background: var(--color-bg-primary);
}

/* 输入行 */
.input-row {
  display: flex;
  align-items: flex-end;
  gap: 10px;
}

.input-textarea {
  flex: 1;
  resize: none;
  border: 1px solid var(--color-border);
  border-radius: 12px;
  padding: 12px 16px;
  font-size: 14px;
  line-height: 1.5;
  font-family: inherit;
  outline: none;
  color: var(--color-text-primary);
  background: var(--color-bg-secondary);
  transition: border-color 0.2s, box-shadow 0.2s;
  max-height: 120px;
  overflow-y: auto;
}

.input-textarea:focus {
  border-color: var(--color-accent);
  box-shadow: 0 0 0 3px var(--color-accent-light);
}

.input-textarea::placeholder {
  color: var(--color-text-tertiary);
}

/* 快捷键提示（12px，右对齐，与发送按钮右缘对齐） */
.input-hint {
  margin: 6px 0 0;
  font-size: 12px;
  line-height: 1.4;
  color: var(--color-text-muted);
  text-align: right;
}

/* 发送按钮 */
.send-btn {
  flex-shrink: 0;
  width: 42px;
  height: 42px;
  border-radius: 50%;
  border: none;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: background 0.2s, transform 0.15s;
  background: var(--color-bg-surface);
  color: var(--color-text-secondary);
}

.send-btn.active {
  background: var(--color-accent);
  color: var(--color-text-inverse);
}

.send-btn.active:hover {
  background: var(--color-accent-hover);
  transform: scale(1.05);
}

.send-btn:disabled {
  cursor: not-allowed;
}
</style>
