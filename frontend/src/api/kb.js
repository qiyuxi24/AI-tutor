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

/**
 * 读取文件节点上传时的**原件字节**（真预览：PDF / Word / Excel / 图片用它渲染）
 * resp: Blob（responseType 指定，字符串直链拿不到 Authorization 头）
 * 历史文件（2026-09-28 前上传）没存原件 → 404，调用方回退 getKbNodeText 的正文预览
 */
export const getKbNodeRaw = (nodeId) =>
  apiClient.get(`/api/v1/kb/node/${nodeId}/raw`, {
    responseType: 'blob',
    // 扫描版 PDF 几十 MB，默认 300s 够用；这里不额外放大
  })

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
 * resp: { node_id, quizzes: [{ id, type, question, difficulty, knowledge_point,
 *         section_id, source_docs, created_at }] }
 * `source_docs` = 题目来源文件 `[{doc_id, doc_name}]`；`section_id` 非空即挂在该小节下。
 * 节点不存在 → 404；题库读取异常 → quizzes: []
 */
export const fetchNodeQuizzes = (nodeId) =>
  apiClient.get(`/api/v1/knowledge/node/${nodeId}/quizzes`)

/**
 * 针对某个小节出一道题（后台生成，约 40s 后推 quiz_ready；题目自动挂到该节）
 * resp: { status, node_id, section_id }；节点/小节不存在 → 404；已在出题 → 409
 */
export const generateSectionQuiz = (nodeId, sectionId) =>
  apiClient.post(`/api/v1/knowledge/node/${nodeId}/section/${sectionId}/quiz`)

/**
 * 反查「这份资料影响了哪些图谱节点」（右键文件 → 在图谱中显示）
 * docId = KB 的 file 节点 id（与来源条目里的 doc_id 同一个命名空间）
 * resp: { doc_id, subjects: [...], nodes: [{id, name, subject}] }
 * 没建过图的文件 → nodes: []（正常，不是错误）
 */
export const fetchSourceNodes = (docId) =>
  apiClient.get(`/api/v1/knowledge/source/${docId}/nodes`)
