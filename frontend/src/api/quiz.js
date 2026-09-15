import { apiClient } from './index.js'

// ═══ 出题测验模块 ═══

/** 生成一份测验（RAG 教材知识库依据出题） */
export const generateQuiz = (data) => {
  return apiClient.post('/api/v1/quiz/generate', data, { timeout: 300000 })
}

/** 列出题库中的题目 */
export const listQuizQuestions = (subject = '', limit = 50, offset = 0) =>
  apiClient.get('/api/v1/quiz/questions', {
    params: { subject, limit, offset },
  })

/** 获取单题详情 */
export const getQuizQuestion = (questionId) =>
  apiClient.get(`/api/v1/quiz/questions/${questionId}`)

/** 判分（客观题规则判分 / 简答题 LLM 判分） */
export const gradeQuizQuestion = (questionId, userAnswer) =>
  apiClient.post(`/api/v1/quiz/${questionId}/grade`, null, {
    params: { user_answer: userAnswer },
    timeout: 120000,
  })

/** 题库统计 */
export const getQuizStats = () => apiClient.get('/api/v1/quiz/stats')

// ═══ 试卷导出（Word / PDF / Markdown）═══

/**
 * 请求导出接口，返回二进制流
 * @param {{format:'docx'|'pdf'|'md', subject?:string, ids?:string,
 *          limit?:number, with_answer?:boolean}} params
 */
export const exportQuiz = (params) =>
  apiClient.get('/api/v1/quiz/export', {
    params,
    responseType: 'blob', // 关键：不加会按 JSON 解析，二进制被破坏
    timeout: 120000,
  })

/** 解析 Content-Disposition 中的文件名（后端用 RFC 5987 的 filename* 传中文名） */
export function parseDispositionFilename(disposition) {
  if (!disposition) return ''
  const star = /filename\*=UTF-8''([^;]+)/i.exec(disposition)
  if (star) {
    try {
      return decodeURIComponent(star[1].trim())
    } catch {
      /* 编码异常则忽略，走兜底文件名 */
    }
  }
  const plain = /filename="?([^";]+)"?/i.exec(disposition)
  return plain ? plain[1].trim() : ''
}

/** 错误响应是 blob（如 404/400 的 JSON）时，把它读回成可读文案 */
export async function readBlobError(error) {
  const data = error?.response?.data
  if (data instanceof Blob) {
    try {
      const parsed = JSON.parse(await data.text())
      return parsed?.detail || parsed?.message || '导出失败'
    } catch {
      /* 不是 JSON，走下面的兜底 */
    }
  }
  return data?.detail || error?.message || '导出失败'
}

/** 触发浏览器下载 */
function saveBlob(blob, filename) {
  const url = window.URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  window.URL.revokeObjectURL(url)
}

/**
 * 导出并直接下载试卷。
 * @returns {Promise<string>} 实际下载的文件名
 */
export async function downloadQuiz(params, fallbackName = 'AI试题') {
  const res = await exportQuiz(params)
  const disposition = res.headers?.['content-disposition'] || res.headers?.['Content-Disposition']
  const ext = params.format || 'docx'
  const filename = parseDispositionFilename(disposition) || `${fallbackName}.${ext}`
  const type = res.headers?.['content-type'] || 'application/octet-stream'
  saveBlob(new Blob([res.data], { type }), filename)
  return filename
}
