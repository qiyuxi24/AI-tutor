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
