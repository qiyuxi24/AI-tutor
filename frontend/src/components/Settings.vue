<script setup>
import { ref } from 'vue'

const emit = defineEmits(['close'])

function closeSettings() {
  emit('close')
}
</script>

<template>
  <div class="settings-overlay" @click.self="closeSettings">
    <div class="settings-panel">
      <div class="settings-header">
        <h2>设置</h2>
        <button class="close-btn" @click="closeSettings">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M18 6L6 18M6 6l12 12"/>
          </svg>
        </button>
      </div>
      
      <div class="settings-content">
        <p>设置页面内容待添加...</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.settings-overlay {
  position: fixed;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  justify-content: center;
  align-items: center;
  z-index: 1000;
}

.settings-panel {
  width: 500px;
  max-width: 90%;
  background: #1e1e2e;
  border-radius: 12px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
}

.settings-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 20px;
  border-bottom: 1px solid #313244;
}

.settings-header h2 {
  font-size: 18px;
  font-weight: 600;
  color: #cdd6f4;
  margin: 0;
}

.close-btn {
  width: 32px;
  height: 32px;
  border: none;
  background: transparent;
  color: #6c7086;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 6px;
  transition: background 0.2s ease;
}

.close-btn:hover {
  background: #313244;
  color: #cdd6f4;
}

.settings-content {
  padding: 20px;
  color: #a6adc8;
  font-size: 14px;
}
</style>