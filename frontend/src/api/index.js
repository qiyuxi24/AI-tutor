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
 * 401 时的统一处理：清除凭据 + 静默重新登录
 * axios 拦截器和 fetch 流式请求共用此逻辑
 *
 * 站点已取消 #/login 路由页（无登录墙），所以这里**不能**再跳登录页，
 * 否则用户被丢到一个不存在的路由 → 白屏。改为直接静默重登体验账户。
 *
 * ⚠️ localStorage 与 Pinia **必须一起清**：authStore 的 token 只在初始化时读过一次
 * localStorage，只清 localStorage 的话 isLoggedIn 仍为 true → ensureSession 拿旧值
 * 判"已登录"而不重新登录，用户侧表现为"发了消息没反应"。
 */
async function handleUnauthorized() {
  // ponytail: 不比对"请求发出时用的 token"，并发旧请求的 401 会把刚换来的新 token
  // 也清掉一次（代价 = 多一次静默登录，不会坏）。要消除就把请求时的 token 记进
  // config，401 时比对不一致直接忽略。
  localStorage.removeItem('ai_tutor_token')
  localStorage.removeItem('ai_tutor_user')
  try {
    // 动态 import 打断 api ↔ store 的静态循环依赖（调用时两边都已加载完毕）
    const { useAuthStore } = await import('../stores/authStore.js')
    const auth = useAuthStore()
    auth.logout()
    await auth.ensureSession()
  } catch {
    // 无活跃 pinia 的场合忽略：localStorage 已清，下次 HomeView 挂载时会再登一次
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

// 登录/注册接口的 401 是"账号密码错"，不是"会话过期"。
// 必须豁免：否则弹窗里输错密码会触发 handleUnauthorized → ensureSession → 再登录
// → 再 401 → 无限递归。（/auth/me 不豁免，它 401 就是 token 真的失效了）
// change-password 也豁免：后端旧密码错误返回 400，但万一返回 401（或 token 恰好过期），
// 静默重登会把用户**换成体验账户**，改密码就改到别人账号上了。
const AUTH_ENDPOINTS = ['/auth/login', '/auth/register', '/auth/change-password']

// ═══ 响应拦截器：401 时清除 token 并静默重登 ═══
apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    const url = error.config?.url || ''
    if (error.response?.status === 401 && !AUTH_ENDPOINTS.some((p) => url.includes(p))) {
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

// ═══ 用户头像 ═══
// 必须走 axios 取二进制：原生 <img src="/api/..."> 带不上 Authorization 头
//（token 在 localStorage，由上面的请求拦截器挂），直接写 URL 会 401。
// 调用方拿 blob 后转 objectURL 交给 <img>，见 utils/avatar.js。

/** 取头像图片；未设置时后端返回 404（调用方按"没有头像"处理） */
export const getAvatarBlob = () =>
  apiClient.get('/api/v1/profile/avatar', { responseType: 'blob' })

/** 上传 / 替换头像（multipart，字段名沿用知识库上传约定：file） */
export const uploadAvatarFile = (file) => {
  const form = new FormData()
  form.append('file', file)
  return apiClient.post('/api/v1/profile/avatar', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 60000,
  })
}

/** 恢复默认头像 */
export const deleteAvatar = () => apiClient.delete('/api/v1/profile/avatar')

// ═══ 用户模型管理（每用户自有对话模型）═══
// Key 只在新建/改 Key 时上行一次，之后后端一律只回掩码（api_key_masked）。

/** 取模型列表 + 当前使用 / 当前生效档位 + 系统默认档 */
export const listLlmModels = () => apiClient.get('/api/v1/llm/models')

/** 新建模型 */
export const createLlmModel = (payload) => apiClient.post('/api/v1/llm/models', payload)

/** 改接入参数（会退回"未验证"）/ 停用恢复 */
export const updateLlmModel = (modelId, payload) =>
  apiClient.patch(`/api/v1/llm/models/${modelId}`, payload)

/** 删除模型 */
export const deleteLlmModel = (modelId) => apiClient.delete(`/api/v1/llm/models/${modelId}`)

/** 连通性测试：后端真打一次最小请求，最长等 20s */
export const testLlmModel = (modelId) =>
  apiClient.post(`/api/v1/llm/models/${modelId}/test`, null, { timeout: 30000 })

/** 设为当前使用的模型 */
export const activateLlmModel = (modelId) =>
  apiClient.post(`/api/v1/llm/models/${modelId}/activate`)

/**
 * 流式发送对话消息（两阶段分离）
 *
 * 阶段1：SSE 逐 token 推送文本回复
 * 阶段2：后台自动执行工具调用 + 图谱分析
 *
 * @param {Array} messages - 完整对话历史 [{role, content}, ...]
 * @param {Object} callbacks - 回调函数集合
 * @param {Function} callbacks.onToken - 收到新 token 时调用 (token: string)
 * @param {Function} callbacks.onThinking - AI 思考过程 (text: string)
 * @param {Function} callbacks.onToolStart - 工具开始执行 (data: {tool, args_head, round})
 * @param {Function} callbacks.onToolResult - 工具执行完毕 (data: {tool, ok, summary, duration_ms, round})
 * @param {Function} callbacks.onAgentStart - Agent 循环开始 (data: {max_rounds})
 * @param {Function} callbacks.onAgentDone - Agent 循环结束 (data: {rounds, total_llm_calls})
 * @param {Function} callbacks.onDone - 流式完成时调用 (fullReply: string)
 * @param {Function} callbacks.onError - 出错时调用 (error: string)
 * @param {string} currentNode - 当前教学位置的知识点 ID（可选）
 * @param {Object|null} kb - 知识库上下文范围 {nodeIds: [], name: string}
 * @returns {AbortController} 用于取消请求
 */
export const sendMessageStream = (messages, callbacks = {}, currentNode = '', kb = null) => {
  const controller = new AbortController()
  const { onToken, onThinking, onToolStart, onToolResult, onAgentStart, onAgentDone, onDone, onError } = callbacks

  const token = localStorage.getItem('ai_tutor_token')

  const body = JSON.stringify({
    messages,
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
          // 必须先给 UI 一个交代：只 return 的话 onError/onDone 都不触发，
          // chatStore.loading 永远为 true（发送按钮永久禁用、气泡停在"AI 思考中…"），
          // 用户侧就是"对话没反应"（2026-09-26 修）。
          onError?.(fmt(ErrorDefs.COMM.UNKNOWN_RESPONSE,
                        { status: response.status, detail: '登录已过期，已自动重新登录，请重发消息' }))
          handleUnauthorized()
          return
        }
        // 流式错误使用统一的错误码映射
        const text = await response.text().catch(() => '')
        onError?.(fmt(ErrorDefs.COMM.UNKNOWN_RESPONSE, { status: response.status, detail: text || undefined }))
        return
      }

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let fullReply = ''
      // 冗余兜底：后端在 [DONE] 前会再带一份最终文本。
      // agent loop 的最终文本是「收尾一次性单帧」下发的（非逐 token），
      // 那一帧若在事件排空窗口丢失，前端会整条消息都拿不到内容。
      let finalReply = ''
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
            // 一帧 token 都没收到 → 用冗余帧兜底（否则 onDone 收到空串会当成空回答）
            if (!fullReply && finalReply) {
              fullReply = finalReply
              onToken?.(finalReply)
            }
            onDone?.(fullReply)
            return
          }

          try {
            const parsed = JSON.parse(data)
            // 旧兼容：token 事件（text_delta 转 token 格式）
            if (parsed.token) {
              fullReply += parsed.token
              onToken?.(parsed.token)
            }
            // 新事件类型：按 type 字段路由
            else if (parsed.type === 'thinking') {
              onThinking?.(parsed.text || '')
            }
            else if (parsed.type === 'tool_start') {
              onToolStart?.(parsed)
            }
            else if (parsed.type === 'tool_result') {
              onToolResult?.(parsed)
            }
            else if (parsed.type === 'agent_start') {
              onAgentStart?.(parsed)
            }
            else if (parsed.type === 'agent_done') {
              onAgentDone?.(parsed)
            }
            else if (parsed.type === 'final') {
              // 冗余最终文本（后端在 [DONE] 前补发）：先存下，仅在"一帧 token 都没收到"
              // 时兜底渲染，见下方 [DONE] 处理。
              // （原 `graph_updated` 忽略分支已在重构中移除，未匹配的事件类型本来就自然落空。）
              finalReply = parsed.text || ''
            }
            else if (parsed.error) {
              onError?.(parsed.error)
              return
            }
          } catch {
            // 忽略解析失败的行
          }
        }
      }

      // 流结束但没有 [DONE] 信号
      if (!fullReply && finalReply) {
        fullReply = finalReply
        onToken?.(finalReply)
      }
      onDone?.(fullReply)
    })
    .catch((err) => {
      if (err.name === 'AbortError') return
      onError?.(fmt(ErrorDefs.COMM.NETWORK_ERROR))
    })

  return controller
}