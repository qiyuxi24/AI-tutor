import { apiClient } from './index.js'

// ═══ 教育资源采集（Collector）═══
//
// 与后端契约（对齐 TODO_Collector B1.4，字段 snake_case）：
//   POST /api/v1/collector/search
//     body: { subject: "数据结构与算法", mode: "personal"|"commercial" }
//     resp: { candidates: CollectCandidate[] }
//   POST /api/v1/collector/tasks
//     body: { subject, mode, candidates: CollectCandidate[] }   // 用户勾选的候选
//     resp: { task: CollectTask }
//   GET  /api/v1/collector/tasks/{id}
//     resp: { task: CollectTask }
//   POST /api/v1/collector/tasks/{id}/cancel
//     resp: { task: CollectTask }
//   GET  /api/v1/collector/stats
//     resp: { coverage: [ { stage, stage_name, total, collected,
//                           subjects: [ { subject, boards_total, boards_collected } ] } ] }
//
// CollectCandidate: { title, source_url, license_level(L0-L3), description, size_bytes }
// CollectTask:      { id, subject, status, processed_count, total_count,
//                     cursor, error, created_at, finished_at }
//   status: queued | running | completed | cancelled | failed

/** 搜索候选资源（按学科 + 版权模式，后端会按 mode 过滤授权等级） */
export const searchCollector = (subject, mode) =>
  apiClient.post('/api/v1/collector/search', { subject, mode }, { timeout: 120000 })

/** 创建采集任务（把勾选的候选入库，返回任务对象，前端轮询其进度） */
export const createCollectorTask = (subject, mode, candidates) =>
  apiClient.post('/api/v1/collector/tasks', { subject, mode, candidates })

/** 查询采集任务进度 */
export const getCollectorTask = (taskId) =>
  apiClient.get(`/api/v1/collector/tasks/${taskId}`)

/** 取消采集任务 */
export const cancelCollectorTask = (taskId) =>
  apiClient.post(`/api/v1/collector/tasks/${taskId}/cancel`)

/** 采集覆盖率概览（学段 → 学科 → 板块 已采/缺口） */
export const getCollectorStats = () => apiClient.get('/api/v1/collector/stats')
