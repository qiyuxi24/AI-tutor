import axios from 'axios'
import { fmt, ErrorDefs } from '../utils/errorCodes.js'

// 创建axios实例
// 不设 baseURL，使用相对路径走 Vite 代理（开发环境）或同源部署（生产环境）
// timeout 设为 300s，因为 LLM API 调用可能需要较长时间（含 function calling 多轮）
export const apiClient = axios.create({
  timeout: 300000,
})

// ═══ 公共工具 ═══

/**
 * 401 时的统一处理：清除凭据 + 跳转登录页
 * axios 拦截器和 fetch 流式请求共用此逻辑
 */
function handleUnauthorized() {
  localStorage.removeItem('ai_tutor_token')
  localStorage.removeItem('ai_tutor_user')
  if (window.location.hash !== '#/login') {
    window.location.hash = '#/login'
  }
}

// ═══ 请求拦截器：自动附加 JWT token ═══
apiClient.interceptors.request.use((config) => {
  const token = localStorage.getItem('ai_tutor_token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// ═══ 响应拦截器：401 时清除 token 并跳转登录页 ═══
apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      handleUnauthorized()
    }
    return Promise.reject(error)
  }
)

// 健康检查
export const healthCheck = () => apiClient.get('/api/health')

// ═══ 用户画像（结构化 v2）═══

/** 获取用户画像（渲染 Markdown + 结构化数据 + 完整度） */
export const getProfile = () => apiClient.get('/api/v1/profile')

/** 兼容旧接口：以 Markdown 文本更新画像（全量替换 / 追加） */
export const updateProfile = (content, op = 'replace') =>
  apiClient.put('/api/v1/profile', { content, op })

/** 追加内容到用户画像（解析合并，只补空缺字段 + 追加观察笔记） */
export const appendProfile = (content) =>
  apiClient.put('/api/v1/profile', { content, op: 'append' })

/** 结构化全量更新画像（表单编辑提交） */
export const saveProfileData = (data) =>
  apiClient.patch('/api/v1/profile', { data })

/** 添加 AI 观察笔记（结构化，带时间戳） */
export const addProfileNote = (content) =>
  apiClient.post('/api/v1/profile/notes', { content })

/** 删除观察笔记 */
export const deleteProfileNote = (noteId) =>
  apiClient.delete(`/api/v1/profile/notes/${noteId}`)

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
 * @param {string} currentNode - 递归模式当前节点 ID
 * @param {Object|null} kb - 知识库上下文范围 {nodeIds: [], name: string}
 * @returns {AbortController} 用于取消请求
 */
export const sendMessageStream = (messages, mode, callbacks = {}, currentNode = '', kb = null) => {
  const controller = new AbortController()
  const { onToken, onDone, onError } = callbacks

  const token = localStorage.getItem('ai_tutor_token')

  const body = JSON.stringify({
    messages,
    mode,
    current_node: currentNode,
    kb_node_ids: kb?.nodeIds || null,
    kb_node_name: kb?.name || null,
  })

  const headers = { 'Content-Type': 'application/json' }
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }

  fetch('/api/v1/chat/stream', {
    method: 'POST',
    headers,
    body,
    signal: controller.signal,
  })
    .then(async (response) => {
      if (!response.ok) {
        if (response.status === 401) {
          handleUnauthorized()
          return
        }
        // 流式错误使用统一的错误码映射
        const text = await response.text().catch(() => '')
        const mockErr = { response: { status: response.status, data: { detail: text || undefined } } }
        onError?.(fmt(ErrorDefs.COMM.UNKNOWN_RESPONSE, { status: response.status, detail: text || undefined }))
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
        buffer = lines.pop() || ''

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
              // 后端 SSE 推送的错误已带错误码，直接透传
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
      onError?.(fmt(ErrorDefs.COMM.NETWORK_ERROR))
    })

  return controller
}