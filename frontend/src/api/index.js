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

// 发送对话消息（传统一次性回复）
export const sendMessage = (messages, mode) =>
  apiClient.post('/api/v1/chat', {
    user_id: getUserId(),
    messages,
    mode,
  })

/**
 * 流式发送对话消息（两阶段分离）
 *
 * 阶段1：SSE 逐 token 推送文本回复
 * 阶段2：后台自动执行工具调用 + 图谱分析
 *
 * @param {Array} messages - 完整对话历史 [{role, content}, ...]
 * @param {string} mode - 引导模式
 * @param {Object} callbacks - 回调函数集合
 * @param {Function} callbacks.onToken - 收到新 token 时调用 (token: string)
 * @param {Function} callbacks.onDone - 流式完成时调用 (fullReply: string)
 * @param {Function} callbacks.onError - 出错时调用 (error: string)
 * @returns {AbortController} 用于取消请求
 */
export const sendMessageStream = (messages, mode, callbacks = {}) => {
  const controller = new AbortController()
  const { onToken, onDone, onError } = callbacks

  const body = JSON.stringify({
    user_id: getUserId(),
    messages,
    mode,
  })

  fetch('http://localhost:8000/api/v1/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body,
    signal: controller.signal,
  })
    .then(async (response) => {
      if (!response.ok) {
        const text = await response.text().catch(() => '')
        onError?.(`[E-COMM-007] 后端返回异常 (HTTP ${response.status})${text ? ': ' + text : ''}`)
        return
      }

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let fullReply = ''
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })

        // 解析 SSE 数据行
        const lines = buffer.split('\n')
        buffer = lines.pop() || '' // 最后一行可能不完整，保留到下次

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const data = line.slice(6).trim()
          if (data === '[DONE]') {
            onDone?.(fullReply)
            return
          }

          try {
            const parsed = JSON.parse(data)
            if (parsed.token) {
              fullReply += parsed.token
              onToken?.(parsed.token)
            } else if (parsed.error) {
              onError?.(parsed.error)
              return
            }
          } catch {
            // 忽略解析失败的行
          }
        }
      }

      // 流结束但没有 [DONE] 信号
      onDone?.(fullReply)
    })
    .catch((err) => {
      if (err.name === 'AbortError') return
      onError?.(`[E-COMM-002] 无法连接到后端服务器，请确认后端已启动`)
    })

  return controller
}