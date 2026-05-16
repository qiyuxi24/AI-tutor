<script setup>
import { ref } from 'vue'
import { sendMessage } from './api/index.js'

const message = ref('')
const mode = ref('scaffolding')
const reply = ref('')
const loading = ref(false)

async function handleSend() {
  if (!message.value.trim()) return
  loading.value = true
  reply.value = ''
  
  try {
    const res = await sendMessage(message.value, mode.value)
    reply.value = res.data.reply
  } catch (e) {
    reply.value = '❌ 请求失败，请确认后端已启动'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div style="max-width: 600px; margin: 2rem auto; font-family: sans-serif;">
    <h1>AI Tutor 测试</h1>
    
    <!-- 模式选择 -->
    <div style="margin-bottom: 1rem;">
      <label>引导模式：</label>
      <select v-model="mode" style="margin-left: 0.5rem; padding: 0.3rem;">
        <option value="scaffolding">阶梯提问</option>
        <option value="think_first">先思后答</option>
        <option value="reverse_teaching">反向教学</option>
      </select>
    </div>
    
    <!-- 输入区 -->
    <div style="margin-bottom: 1rem;">
      <textarea
        v-model="message"
        placeholder="输入你的问题..."
        rows="4"
        style="width: 100%; padding: 0.5rem; font-size: 1rem;"
      ></textarea>
    </div>
    
    <button
      @click="handleSend"
      :disabled="loading"
      style="padding: 0.5rem 1.5rem; font-size: 1rem; cursor: pointer;"
    >
      {{ loading ? 'AI 思考中...' : '发送' }}
    </button>
    
    <!-- 回复区 -->
    <div v-if="reply" style="margin-top: 2rem; padding: 1rem; background: #f5f5f5; border-radius: 8px;">
      <strong>AI 回复：</strong>
      <p style="white-space: pre-wrap;">{{ reply }}</p>
    </div>
  </div>
</template>