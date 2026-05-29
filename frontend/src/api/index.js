import axios from 'axios'

// 创建axios实例，指向后端地址
// timeout 设为 300s，因为 LLM API 调用可能需要较长时间（含 function calling 多轮）
export const apiClient = axios.create({
  baseURL: 'http://localhost:8000',
  timeout: 300000,
})

// 获取或生成用户ID
export const getUserId = () => {
  let userId = localStorage.getItem('ai_tutor_user_id')
  if (!userId) {
    userId = crypto.randomUUID()
    localStorage.setItem('ai_tutor_user_id', userId)
  }
  return userId
}

// 健康检查
export const healthCheck = () => apiClient.get('/api/health')

// 发送对话消息
export const sendMessage = (messages, mode) =>
  apiClient.post('/api/v1/chat', {
    user_id: getUserId(),
    messages,
    mode,
  })