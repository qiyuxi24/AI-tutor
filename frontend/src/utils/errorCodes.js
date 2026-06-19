/**
 * 前端统一错误码体系
 *
 * 与后端 backend/app/core/error_codes.py 保持一致的模块划分。
 *
 * 模块:
 *   E-COMM-xxx  : 前后端通信（网络、超时、HTTP 状态码）— 前端生成
 *   E-AUTH-xxx  : 用户认证 — 后端返回，前端透传
 *   E-LLM-xxx   : 大模型调用 — 后端返回，前端透传
 *   E-KG-xxx    : 知识图谱操作 — 后端返回，前端透传
 *   E-CLIENT-xxx: 前端特有（表单校验、组件异常）— 前端生成
 *
 * 使用方式:
 *   import { formatError, clientError, ErrorDefs } from '@/utils/errorCodes'
 *   const msg = formatError(err)                    // 自动识别 axios/fetch 错误
 *   const msg = clientError('VALIDATION', '用户名过短') // 前端校验错误
 */

/* ================================================================
   错误码定义（与后端保持同步）
   ================================================================ */

export const ErrorDefs = {
  // ── 前后端通信层 (E-COMM) — 前端根据 HTTP/网络状态生成 ──
  COMM: {
    TIMEOUT:          { code: 'E-COMM-001', msg: '请求超时，后端处理过慢或网络延迟' },
    NETWORK_ERROR:    { code: 'E-COMM-002', msg: '无法连接到后端服务器，请确认后端已启动' },
    BAD_GATEWAY:      { code: 'E-COMM-003', msg: '后端网关错误 (502)，服务可能未就绪' },
    SERVICE_UNAVAIL:  { code: 'E-COMM-004', msg: '后端服务不可用 (503)，请稍后重试' },
    GATEWAY_TIMEOUT:  { code: 'E-COMM-005', msg: '后端网关超时 (504)，AI调用可能仍在处理中' },
    SERVER_ERROR:     { code: 'E-COMM-006', msg: '后端内部错误 (5xx)，请查看服务端日志' },
    UNKNOWN_RESPONSE: { code: 'E-COMM-007', msg: '后端返回了未预期的响应' },
  },

  // ── 前端客户端层 (E-CLIENT) — 前端生成 ──
  CLIENT: {
    VALIDATION:       { code: 'E-CLIENT-001', msg: '输入内容不符合要求' },
    GRAPH_LOAD:       { code: 'E-CLIENT-002', msg: '知识图谱加载失败，请确认后端服务已启动' },
    NODE_SAVE:        { code: 'E-CLIENT-003', msg: '节点保存失败' },
    NODE_DELETE:      { code: 'E-CLIENT-004', msg: '节点删除失败' },
    EDGE_OP:          { code: 'E-CLIENT-005', msg: '边操作失败' },
    NODE_LOAD:        { code: 'E-CLIENT-006', msg: '节点详情加载失败' },
    UNKNOWN:          { code: 'E-CLIENT-007', msg: '发生未知错误，请刷新页面后重试' },
  },
}

/* ================================================================
   核心函数
   ================================================================ */

/**
 * 将 ErrorDefs 中的定义格式化为显示消息
 * @param {{ code: string, msg: string }} def - ErrorDefs 中的条目
 * @param {Object} [extras] - 附加信息 { status?, detail?, action? }
 * @returns {string} 如 "[E-COMM-001] 请求超时，后端处理过慢或网络延迟"
 */
export function fmt(def, extras = {}) {
  let s = `[${def.code}] ${def.msg}`
  if (extras.status) s += ` (HTTP ${extras.status})`
  if (extras.action) s = `[${def.code}] ${extras.action}: ${def.msg}`
  if (extras.detail) s += ` — ${extras.detail}`
  return s
}

/**
 * 根据 axios/fetch 错误自动映射到对应的错误码。
 * 这是前端 catch 块的统一入口。
 *
 * @param {Error} err - axios 或 fetch 抛出的错误对象
 * @param {Object} [opts] - 选项
 * @param {string} [opts.action] - 操作名称（如"删除节点"），用于替换默认消息
 * @param {Object} [opts.fallback] - 无法识别时使用的回退定义
 * @returns {string} 格式化的错误消息
 */
export function formatError(err, opts = {}) {
  const { action, fallback = ErrorDefs.CLIENT.UNKNOWN } = opts

  // 1. 后端返回了带错误码的 detail（优先透传）
  if (err?.response?.data?.detail) {
    return err.response.data.detail
  }

  // 2. 根据 HTTP 状态码映射
  if (err?.response) {
    const s = err.response.status
    if (s === 401) return fmt(ErrorDefs.COMM.UNKNOWN_RESPONSE, { status: s, action })
    if (s === 502) return fmt(ErrorDefs.COMM.BAD_GATEWAY)
    if (s === 503) return fmt(ErrorDefs.COMM.SERVICE_UNAVAIL)
    if (s === 504) return fmt(ErrorDefs.COMM.GATEWAY_TIMEOUT)
    if (s >= 500)  return fmt(ErrorDefs.COMM.SERVER_ERROR, { status: s })
    return fmt(ErrorDefs.COMM.UNKNOWN_RESPONSE, { status: s })
  }

  // 3. 网络层错误
  if (err?.code === 'ECONNABORTED') return fmt(ErrorDefs.COMM.TIMEOUT)
  if (err?.name === 'AbortError') return '' // 主动取消，不算错误
  if (!err?.response) return fmt(ErrorDefs.COMM.NETWORK_ERROR)

  // 4. 无法识别
  return fmt(fallback, { action, detail: err?.message?.slice(0, 100) })
}

/**
 * 前端校验类错误的快捷生成
 * @param {string} key - ErrorDefs.CLIENT 的键名
 * @param {string} [detail] - 补充说明
 * @returns {string}
 */
export function clientError(key, detail) {
  const def = ErrorDefs.CLIENT[key] || ErrorDefs.CLIENT.UNKNOWN
  return fmt(def, { detail })
}
