<script setup>
import { ref, nextTick } from 'vue'
import { useChatStore } from '../stores/chatStore'

const store = useChatStore()

const text = ref('')
const textareaRef = ref(null)

const MODE_OPTIONS = [
  { value: 'adaptive', label: '自适应引导' },
  { value: 'free_talk', label: '自由对话' },
  { value: 'recursive', label: '递归式教学' },
]

function handleModeChange(e) {
  store.setMode(e.target.value)
}

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
    <!-- 模式选择 -->
    <div class="mode-bar">
      <span class="mode-label">引导模式：</span>
      <div class="mode-select-wrapper">
        <select
          :value="store.mode"
          @change="handleModeChange"
          class="mode-select"
        >
          <option
            v-for="opt in MODE_OPTIONS"
            :key="opt.value"
            :value="opt.value"
          >
            {{ opt.label }}
          </option>
        </select>
        <svg class="select-arrow" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </div>
    </div>

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
  border-top: 1px solid #e5e7eb;
  padding: 16px 24px 20px;
  background: #ffffff;
}

/* 模式选择 */
.mode-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}

.mode-label {
  font-size: 13px;
  color: #6b7280;
  white-space: nowrap;
}

.mode-select-wrapper {
  position: relative;
  display: inline-flex;
}

.mode-select {
  appearance: none;
  padding: 5px 28px 5px 12px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  font-size: 13px;
  color: #374151;
  background: #f9fafb;
  cursor: pointer;
  outline: none;
  transition: border-color 0.2s, box-shadow 0.2s;
}

.mode-select:focus {
  border-color: #4f46e5;
  box-shadow: 0 0 0 3px rgba(79, 70, 229, 0.1);
}

.select-arrow {
  position: absolute;
  right: 10px;
  top: 50%;
  transform: translateY(-50%);
  pointer-events: none;
  color: #9ca3af;
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
  border: 1px solid #e5e7eb;
  border-radius: 12px;
  padding: 12px 16px;
  font-size: 14px;
  line-height: 1.5;
  font-family: inherit;
  outline: none;
  transition: border-color 0.2s, box-shadow 0.2s;
  max-height: 120px;
  overflow-y: auto;
}

.input-textarea:focus {
  border-color: #4f46e5;
  box-shadow: 0 0 0 3px rgba(79, 70, 229, 0.08);
}

.input-textarea::placeholder {
  color: #9ca3af;
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
  background: #d1d5db;
  color: #ffffff;
}

.send-btn.active {
  background: #4f46e5;
}

.send-btn.active:hover {
  background: #4338ca;
  transform: scale(1.05);
}

.send-btn:disabled {
  cursor: not-allowed;
}
</style>
