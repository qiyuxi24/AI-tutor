import { apiClient } from './index.js'

// ═══ 知识库：用户上传文件的目录树管理 ═══

/** 获取目录树（嵌套结构） */
export const getKbTree = () => apiClient.get('/api/v1/kb/tree')

/** 新建文件夹 */
export const createKbFolder = (name, parentId = null) =>
  apiClient.post('/api/v1/kb/folder', { name, parent_id: parentId })

/** 上传文件到指定文件夹（自动解析 + 向量化） */
export const uploadKbFile = (file, parentId = null) => {
  const form = new FormData()
  form.append('file', file)
  const params = parentId ? { parent_id: parentId } : {}
  return apiClient.post('/api/v1/kb/upload', form, {
    params,
    headers: { 'Content-Type': 'multipart/form-data' },
    // 上传 = 解析 + 分块 + 嵌入，扫描版 PDF 会走逐页 OCR，分钟级很正常。
    // 120s 会把请求掐断（后端 CancelledError → 索引半途而废，目录里留下无索引的孤儿文件），
    // 所以给足 10 分钟；真正的进度反馈应由上传接口自己给，不能靠超时。
    timeout: 600000,
  })
}

/** 删除文件/文件夹（递归） */
export const deleteKbNode = (nodeId) =>
  apiClient.delete(`/api/v1/kb/node/${nodeId}`)

/**
 * 读取文件节点已解析的正文（只读预览，不改库）
 * resp: { status, node_id, name, markdown, chars, total_chars, truncated }
 * 超长正文被截断到后端 KB_PREVIEW_MAX_CHARS：chars < total_chars 即"看到的不是全文"
 */
export const getKbNodeText = (nodeId) =>
  apiClient.get(`/api/v1/kb/node/${nodeId}/text`)

/** 在目录范围内语义检索 */
export const searchKb = (q, nodeId = null, topK = 5) => {
  const params = { q, top_k: topK }
  if (nodeId) params.node_id = nodeId
  return apiClient.get('/api/v1/kb/search', { params })
}

/** 收集目录范围内所有文件节点 ID（选择进上下文） */
export const getKbContext = (nodeId, maxDepth = null) => {
  const body = { node_id: nodeId }
  if (maxDepth) body.max_depth = maxDepth
  return apiClient.post('/api/v1/kb/context', body)
}

/** 知识库索引统计 */
export const getKbStats = () => apiClient.get('/api/v1/kb/stats')

// ═══ 节点小节化：小节正文按需读取 / 生成 / 删除 ═══

/**
 * 读取单个小节正文（懒加载：点开哪节读哪节，不一次拉全部）
 * resp: { id, title, kind, status, content }
 */
export const fetchNodeSection = (nodeId, sectionId) =>
  apiClient.get(`/api/v1/knowledge/node/${nodeId}/section/${sectionId}`)

/** 删除单个小节 resp: { deleted: bool } */
export const deleteNodeSection = (nodeId, sectionId) =>
  apiClient.delete(`/api/v1/knowledge/node/${nodeId}/section/${sectionId}`)

/**
 * 列出节点下的试题（懒加载：打开节点详情时才请求，不随图谱列表批量拉）
 * resp: { node_id, quizzes: [{ id, type, question, difficulty, knowledge_point, section_id, created_at }] }
 * 节点不存在 → 404；题库读取异常 → quizzes: []
 */
export const fetchNodeQuizzes = (nodeId) =>
  apiClient.get(`/api/v1/knowledge/node/${nodeId}/quizzes`)
