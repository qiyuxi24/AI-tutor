<script setup>
import { ref, nextTick } from 'vue'
import { useChatStore } from '../stores/chatStore'

const store = useChatStore()

const text = ref('')
const textareaRef = ref(null)

async function handleSend() {
  if (!text.value.trim() || store.loading) return
  const msg = text.value
  text.value = ''
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
