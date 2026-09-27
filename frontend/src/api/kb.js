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
    timeout: 120000, // 上传+解析+向量化可能较慢
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

/** 从学科书籍生成知识图谱（AI 直接写库） */
export const generateKbGraph = (subject, nodeIds, mode = 'subject') =>
  apiClient.post('/api/v1/kb/graph/generate', {
    subject,
    mode,
    node_ids: nodeIds,
  }, { timeout: 300000 })  // 生成可能较慢
