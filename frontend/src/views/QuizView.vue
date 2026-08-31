<script setup>
/**
 * QuizView.vue — 出题页（活动栏"出题"入口）
 *
 * 职责：
 *   - 出题配置：主题/知识点、难度、题型、题量
 *   - 调用后端 /quiz/generate，AI 基于教材知识库（RAG 依据）出题
 *   - 题目列表作答：单选/多选/判断/填空/简答
 *   - 判分：客观题规则判分、简答题 LLM 判分，展示解析
 */

import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import {
  generateQuiz,
  gradeQuizQuestion,
  getQuizStats,
} from '../api/quiz.js'

// ─── 出题配置 ───
const subject = ref('')               // 主题/知识点
const difficulty = ref('medium')       // easy / medium / hard
const questionCount = ref(5)
const questionTypes = ref(['single', 'multiple', 'judge', 'fill', 'short_answer'])
const generating = ref(false)
const genError = ref('')

const difficultyOptions = [
  { label: '简单', value: 'easy' },
  { label: '中等', value: 'medium' },
  { label: '困难', value: 'hard' },
]

const typeOptions = [
  { label: '单选题', value: 'single' },
  { label: '多选题', value: 'multiple' },
  { label: '判断题', value: 'judge' },
  { label: '填空题', value: 'fill' },
  { label: '简答题', value: 'short_answer' },
]

// ─── 题目列表与作答 ───
const questions = ref([])              // 已出题目 [{id(本地), type, question, options, ...}]
const answers = ref({})                // { 本地索引: 用户答案 }
const results = ref({})                // { 本地索引: 判分结果 }
const submittingId = ref(null)         // 正在判分的题目
const rejectedCount = ref(0)
const materialsUsed = ref(0)

const stats = ref({ total_questions: 0 })

const typeLabels = {
  single: '单选题',
  multiple: '多选题',
  judge: '判断题',
  fill: '填空题',
  short_answer: '简答题',
}

// 题型图标色
const typeClass = {
  single: 't-single',
  multiple: 't-multiple',
  judge: 't-judge',
  fill: 't-fill',
  short_answer: 't-short',
}

async function loadStats() {
  try {
    const res = await getQuizStats()
    stats.value = { ...stats.value, ...(res.data || {}) }
  } catch (e) {
    // 统计失败不影响出题
  }
}

async function handleGenerate() {
  const subj = subject.value.trim()
  if (!subj) {
    genError.value = '请输入出题主题或知识点，如"二叉树遍历"'
    return
  }
  if (!questionTypes.value.length) {
    genError.value = '请至少选择一种题型'
    return
  }

  generating.value = true
  genError.value = ''
  questions.value = []
  answers.value = {}
  results.value = {}

  try {
    const res = await generateQuiz({
      subject: subj,
      question_count: questionCount.value,
      difficulty: difficulty.value,
      question_types: questionTypes.value,
    })
    const d = res.data || {}
    // 后端返回 questions + question_ids（存库后的数据库 id），一一对应
    const qs = d.questions || []
    const ids = d.question_ids || []
    questions.value = qs.map((q, i) => ({
      ...q,
      localIdx: i,
      dbId: ids[i] != null ? ids[i] : null,  // 数据库 id（用于判分）
    }))
    rejectedCount.value = d.rejected || 0
    materialsUsed.value = d.materials_used || 0
    if (!questions.value.length) {
      ElMessage.warning('AI 未能生成合格题目，请调整主题后重试')
    } else {
      ElMessage.success(`已生成 ${questions.value.length} 道题目`)
      loadStats()
    }
  } catch (e) {
    genError.value = e.response?.data?.detail || e.message || '出题失败'
    ElMessage.error(genError.value)
  } finally {
    generating.value = false
  }
}

// ─── 作答交互 ───
function isAnswered(q) {
  const a = answers.value[q.localIdx]
  if (q.type === 'multiple') return Array.isArray(a) && a.length > 0
  return !!a
}

function handleAnswer(q, val) {
  answers.value[q.localIdx] = val
  delete results.value[q.localIdx]
}

async function handleGrade(q) {
  const a = answers.value[q.localIdx]
  if (!isAnswered(q)) {
    ElMessage.warning('请先作答再判分')
    return
  }
  if (q.dbId == null) {
    ElMessage.error('题目尚未入库，无法判分')
    return
  }
  submittingId.value = q.localIdx
  try {
    const res = await gradeQuizQuestion(q.dbId, normalizeAnswer(q, a))
    results.value[q.localIdx] = {
      score: res.data.score,
      max_score: res.data.max_score,
      correct: res.data.correct,
      comment: res.data.comment,
      analysis: res.data.analysis,
    }
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '判分失败')
  } finally {
    submittingId.value = null
  }
}

/** 将用户答案转成后端判分需要的字符串 */
function normalizeAnswer(q, a) {
  if (q.type === 'multiple') {
    return Array.isArray(a) ? a.join(',') : String(a || '')
  }
  return String(a || '')
}

onMounted(() => {
  loadStats()
})
</script>

<template>
  <div class="quiz-layout">
    <main class="quiz-content">
      <div class="quiz-inner">
        <header class="qc-header">
          <h2>AI 出题</h2>
          <p class="qc-subtitle">
            AI 基于你上传的教材 / 例题 / 真题（知识库）出题，只依据真实材料，避免凭空乱出。支持单选 / 多选 / 判断 / 填空 / 简答，自动判分并给出解析。
          </p>
        </header>

        <!-- 出题配置 -->
        <section class="quiz-config">
          <div class="cfg-row">
            <el-input
              v-model="subject"
              placeholder="出题主题 / 知识点，如：二叉树遍历"
              maxlength="100"
              clearable
              class="cfg-subject"
              @keyup.enter="handleGenerate"
            />
            <el-select v-model="difficulty" class="cfg-difficulty">
              <el-option v-for="d in difficultyOptions" :key="d.value" :label="d.label" :value="d.value" />
            </el-select>
            <el-select v-model="questionCount" class="cfg-count">
              <el-option v-for="n in [3, 5, 8, 10]" :key="n" :label="`${n} 题`" :value="n" />
            </el-select>
          </div>
          <div class="cfg-row">
            <div class="cfg-types">
              <el-checkbox-group v-model="questionTypes">
                <el-checkbox v-for="t in typeOptions" :key="t.value" :value="t.value" :label="t.label" />
              </el-checkbox-group>
            </div>
            <el-button
              type="primary"
              :loading="generating"
              @click="handleGenerate"
            >
              {{ generating ? 'AI 出题中...' : '开始出题' }}
            </el-button>
          </div>
          <p v-if="genError" class="cfg-error">{{ genError }}</p>
        </section>

        <!-- 出题说明 / 空态 -->
        <section v-if="!questions.length && !generating" class="quiz-empty">
          <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
            <path d="M9 11l3 3L22 4" />
            <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" />
          </svg>
          <p>输入主题并点击「开始出题」，AI 会先从你的教材知识库检索依据，再生成题目。</p>
          <p class="empty-tip">共 {{ stats.total_questions || 0 }} 道已存题目</p>
        </section>

        <!-- 出题结果统计 -->
        <div v-if="questions.length" class="quiz-meta">
          <span>本次生成 <b>{{ questions.length }}</b> 道</span>
          <span v-if="rejectedCount">，过滤不合格 <b>{{ rejectedCount }}</b> 道</span>
          <span v-if="materialsUsed">，依据知识库 {{ materialsUsed }} 个片段</span>
        </div>

        <!-- 题目列表 -->
        <section class="quiz-questions">
          <div
            v-for="q in questions"
            :key="q.localIdx"
            class="question-card"
            :class="[typeClass[q.type], { 'has-result': results[q.localIdx] }]"
          >
            <div class="q-head">
              <span class="q-type">{{ typeLabels[q.type] || q.type }}</span>
              <span class="q-num">{{ q.localIdx + 1 }}</span>
              <span class="q-points">{{ q.points }}分</span>
            </div>
            <div class="q-body">
              <p class="q-text">{{ q.question }}</p>

              <!-- 单选 -->
              <el-radio-group
                v-if="q.type === 'single'"
                v-model="answers[q.localIdx]"
                @change="handleAnswer(q, $event)"
                :disabled="!!results[q.localIdx]"
              >
                <el-radio v-for="opt in q.options" :key="opt.value" :value="opt.value">
                  {{ opt.label }}
                </el-radio>
              </el-radio-group>

              <!-- 多选 -->
              <el-checkbox-group
                v-else-if="q.type === 'multiple'"
                v-model="answers[q.localIdx]"
                @change="handleAnswer(q, $event)"
                :disabled="!!results[q.localIdx]"
              >
                <el-checkbox v-for="opt in q.options" :key="opt.value" :value="opt.value">
                  {{ opt.label }}
                </el-checkbox>
              </el-checkbox-group>

              <!-- 判断 -->
              <el-radio-group
                v-else-if="q.type === 'judge'"
                v-model="answers[q.localIdx]"
                @change="handleAnswer(q, $event)"
                :disabled="!!results[q.localIdx]"
              >
                <el-radio value="对">正确</el-radio>
                <el-radio value="错">错误</el-radio>
              </el-radio-group>

              <!-- 填空 -->
              <el-input
                v-else-if="q.type === 'fill'"
                v-model="answers[q.localIdx]"
                placeholder="请输入答案"
                clearable
                :disabled="!!results[q.localIdx]"
                class="q-fill-input"
                @keyup.enter="handleGrade(q)"
              />

              <!-- 简答 -->
              <el-input
                v-else-if="q.type === 'short_answer'"
                v-model="answers[q.localIdx]"
                type="textarea"
                :rows="3"
                placeholder="请输入你的回答..."
                :disabled="!!results[q.localIdx]"
                class="q-short-input"
              />
            </div>

            <!-- 判分按钮 + 结果 -->
            <div class="q-foot">
              <el-button
                size="small"
                type="primary"
                plain
                :loading="submittingId === q.localIdx"
                :disabled="!!results[q.localIdx]"
                @click="handleGrade(q)"
              >
                判分
              </el-button>

              <div v-if="results[q.localIdx]" class="q-result" :class="results[q.localIdx].correct ? 'ok' : 'wrong'">
                <div class="q-result-score">
                  {{ results[q.localIdx].correct ? '✓' : '✗' }}
                  {{ results[q.localIdx].score }} / {{ results[q.localIdx].max_score }} 分
                  <span class="q-result-comment">{{ results[q.localIdx].comment }}</span>
                </div>
                <div v-if="results[q.localIdx].analysis" class="q-result-analysis">
                  解析：{{ results[q.localIdx].analysis }}
                </div>
              </div>
            </div>
          </div>
        </section>
      </div>
    </main>
  </div>
</template>

<style scoped>
.quiz-layout {
  flex: 1;
  height: 100%;
  min-width: 0;
  overflow-y: auto;
}

.quiz-content {
  height: 100%;
}

.quiz-inner {
  max-width: 960px;
  margin: 0 auto;
  padding: 28px 32px 60px;
  height: 100%;
}

.qc-header {
  margin-bottom: 20px;
}

.qc-header h2 {
  margin: 0 0 6px;
  font-size: 20px;
  font-weight: 600;
  color: var(--color-text-primary);
}

.qc-subtitle {
  margin: 0;
  font-size: 13px;
  color: var(--color-text-tertiary);
  line-height: 1.6;
}

/* 配置区 */
.quiz-config {
  background: var(--color-bg-surface);
  border: 1px solid var(--color-border);
  border-radius: 12px;
  padding: 16px;
  margin-bottom: 20px;
}

.cfg-row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.cfg-row + .cfg-row {
  margin-top: 12px;
}

.cfg-subject {
  flex: 1;
  min-width: 220px;
}

.cfg-difficulty,
.cfg-count {
  width: 110px;
}

.cfg-types {
  flex: 1;
}

.cfg-error {
  margin: 10px 0 0;
  color: var(--color-red);
  font-size: 13px;
}

/* 空态 */
.quiz-empty {
  text-align: center;
  padding: 60px 20px;
  color: var(--color-text-tertiary);
}
.quiz-empty svg {
  color: var(--color-text-tertiary);
  margin-bottom: 12px;
  opacity: 0.6;
}
.quiz-empty p {
  margin: 4px 0;
  font-size: 14px;
}
.quiz-empty .empty-tip {
  font-size: 12px;
  color: var(--color-text-quaternary, var(--color-text-tertiary));
}

/* 出题结果统计 */
.quiz-meta {
  font-size: 13px;
  color: var(--color-text-secondary);
  margin-bottom: 14px;
}
.quiz-meta b {
  color: var(--color-accent);
}

/* 题目卡片 */
.quiz-questions {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.question-card {
  background: var(--color-bg-surface);
  border: 1px solid var(--color-border);
  border-radius: 12px;
  padding: 16px;
  transition: border-color 0.15s;
}

.question-card.has-result {
  border-color: var(--color-border-strong, var(--color-accent));
}

.q-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}

.q-type {
  font-size: 11px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 10px;
  background: var(--color-bg-hover);
  color: var(--color-text-secondary);
}

.t-single .q-type { color: #2563eb; }
.t-multiple .q-type { color: #7c3aed; }
.t-judge .q-type { color: #059669; }
.t-fill .q-type { color: #d97706; }
.t-short .q-type { color: #db2777; }

.q-num {
  font-size: 13px;
  color: var(--color-text-primary);
  font-weight: 600;
}

.q-points {
  font-size: 11px;
  color: var(--color-text-tertiary);
  margin-left: auto;
}

.q-text {
  margin: 0 0 12px;
  font-size: 15px;
  line-height: 1.6;
  color: var(--color-text-primary);
}

.q-fill-input {
  max-width: 320px;
}

.q-short-input {
  width: 100%;
}

.q-foot {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  margin-top: 12px;
}

.q-result {
  flex: 1;
  font-size: 13px;
  line-height: 1.6;
}

.q-result.ok { color: #059669; }
.q-result.wrong { color: #dc2626; }

.q-result-score {
  font-weight: 600;
}

.q-result-comment {
  font-weight: 400;
  margin-left: 6px;
  color: var(--color-text-secondary);
}

.q-result-analysis {
  margin-top: 4px;
  color: var(--color-text-secondary);
  background: var(--color-bg-hover);
  border-radius: 8px;
  padding: 8px 12px;
}
</style>
