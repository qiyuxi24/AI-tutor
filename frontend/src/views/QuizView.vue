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

import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import AppIcon from '../components/AppIcon.vue'
import SidePanel from '../components/SidePanel.vue'
import QuizHistorySidebar from '../components/QuizHistorySidebar.vue'
import KbTextPreviewDialog from '../components/KbTextPreviewDialog.vue'
import {
  generateQuiz,
  gradeQuizQuestion,
  getQuizStats,
  getQuizQuestion,
  listQuizBatches,
  getBatchQuestions,
  fetchQuizBlob,
  saveQuizBlob,
  readBlobError,
} from '../api/quiz.js'

/**
 * Props（均为可选，默认值即原行为）：
 *   initialSubject: String      — 挂载时预填出题主题（仅在原为空时覆盖，不覆盖用户已输入）
 *   graphNodeId: String         — 图谱节点 id（从节点详情跳来时带）。出题时透传给后端，
 *                                 题目入库的 knowledge_point 记成它 → 判分能把掌握度写回该节点
 *   focusQuestionId: Number|null — 有值时挂载后拉取该题并直接渲染（供"从节点详情跳转看某题"）
 *
 * emits:
 *   mastery-changed — 判分后掌握度/小节状态确实被写回时触发（父级据此刷新图谱颜色）
 */
const props = defineProps({
  initialSubject: { type: String, default: '' },
  graphNodeId: { type: String, default: '' },
  focusQuestionId: { type: Number, default: null },
})

const emit = defineEmits(['mastery-changed'])

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
// 请求的题数（分批出题后可能因截断/重复凑不满，用来提示差额）
const requestedCount = ref(0)

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
      // 从节点详情进来时带上图谱节点 id：入库的 knowledge_point 记成它，
      // 判分才能把掌握度回写到这个节点（不带则是纯出题，行为与原来一致）。
      graph_node_id: props.graphNodeId || '',
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
    activeBatchId.value = ''   // 新生成的一套 = 草稿，清掉历史选中态
    rejectedCount.value = d.rejected || 0
    materialsUsed.value = d.materials_used || 0
    requestedCount.value = d.requested || questions.value.length
    if (!questions.value.length) {
      ElMessage.warning('AI 未能生成合格题目，请调整主题后重试')
    } else {
      const short = requestedCount.value - questions.value.length
      if (short > 0) {
        // 分批出题后仍凑不满（多轮补题也没补上），明确告知差额而不是静默少给
        ElMessage.warning(`请求 ${requestedCount.value} 道，实际生成 ${questions.value.length} 道（差 ${short} 道）`)
      } else {
        ElMessage.success(`已生成 ${questions.value.length} 道题目`)
      }
      loadStats()
      loadBatches()   // 新出的这套题立即计入历史角标
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
    const d = res.data || {}
    results.value[q.localIdx] = {
      score: d.score,
      max_score: d.max_score,
      correct: d.correct,
      comment: d.comment,
      analysis: d.analysis,
      // 掌握度回写结果（只有题目关联了图谱节点时后端才返回这些字段）
      masteryNote: d.mastery_note || '',
      sectionNote: d.section_note || '',
    }
    // 图谱真被改了才让父级刷新 —— 纯出题（无 graphNodeId）永远不带这些字段
    if (d.mastery_note || d.section_note) emit('mastery-changed')
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

// ─── 试卷导出（Word / PDF / Markdown / Anki）───
const exportOptions = [
  { fmt: 'docx', label: 'Word', tip: '可继续编辑的 .docx' },
  { fmt: 'pdf', label: 'PDF', tip: '适合打印/打印预览' },
  { fmt: 'md', label: 'Markdown', tip: '纯文本，便于二次加工' },
  { fmt: 'tsv', label: 'Anki', tip: '导入 Anki 的卡片（正面题干/背面答案解析，始终含答案）' },
]
const exportingFmt = ref('')        // 正在请求/预览的格式，用于按钮 loading
const withAnswer = ref(true)        // false = 学生卷（不含答案解析）
const exportFmt = ref('pdf')        // 预览默认 PDF（浏览器原生阅读器，最忠实）

/** 导出参数：预览与下载共用同一份，保证「所见即所得」 */
function exportParams(fmt) {
  const ids = questions.value
    .map((q) => q.dbId)
    .filter((id) => id != null)
    .join(',')
  const subj = subject.value.trim()
  return {
    params: {
      format: fmt,
      ids,                              // 有当前题目 → 只导出这套
      subject: ids ? '' : subj,         // 否则按主题导出题库
      limit: 200,
      with_answer: withAnswer.value,
    },
    fallbackName: `${subj || 'AI试题'}${withAnswer.value ? '_试题' : '_学生卷'}`,
  }
}

/**
 * 预览：只请求一次并持有字节（后端每次导出都写「导出时间」，分两次请求会是两份）。
 * docx/pdf 交原件渲染器，md/tsv 是纯文本、读到 markdown 字符串里再渲染。
 */
const previewVisible = ref(false)
const previewName = ref('')
const previewFileType = ref('')
const previewRawBlob = ref(null)
const previewMarkdown = ref('')
const previewHeld = ref(null)       // { blob, filename, type }：预览的就是可下载的那一份

const previewFileTypes = { docx: '.docx', pdf: '.pdf', md: '.md', tsv: '.tsv' }

async function handlePreview(fmt) {
  const { params, fallbackName } = exportParams(fmt)
  if (!params.ids && !params.subject) {
    ElMessage.warning('请先「开始出题」，或填写主题以导出该主题的题库')
    return
  }
  exportingFmt.value = fmt
  try {
    const held = await fetchQuizBlob(params, fallbackName)
    previewName.value = held.filename
    previewFileType.value = previewFileTypes[fmt] || '.md'
    previewMarkdown.value = ''
    previewRawBlob.value = null
    if (fmt === 'md' || fmt === 'tsv') {
      previewMarkdown.value = await held.blob.text()
    } else {
      previewRawBlob.value = held.blob
    }
    previewHeld.value = held
    previewVisible.value = true
  } catch (e) {
    ElMessage.error(await readBlobError(e))
  } finally {
    exportingFmt.value = ''
  }
}

/** 下载当前预览的那一份字节；未预览时退化为一次请求即下载 */
async function handleExport(fmt) {
  if (previewHeld.value && previewVisible.value) {
    const name = saveQuizBlob(previewHeld.value)
    ElMessage.success(`已下载 ${name}`)
    return
  }
  const { params, fallbackName } = exportParams(fmt)
  if (!params.ids && !params.subject) {
    ElMessage.warning('请先「开始出题」，或填写主题以导出该主题的题库')
    return
  }
  exportingFmt.value = fmt
  try {
    const name = saveQuizBlob(await fetchQuizBlob(params, fallbackName))
    ElMessage.success(`已下载 ${name}`)
  } catch (e) {
    ElMessage.error(await readBlobError(e))
  } finally {
    exportingFmt.value = ''
  }
}

function closePreview() {
  previewVisible.value = false
  previewRawBlob.value = null
  previewMarkdown.value = ''
  previewHeld.value = null
}

/**
 * 从节点详情跳入：按题 id 拉取单题并直接渲染。
 * 形状对齐 handleGenerate 的 questions 项（含 localIdx / dbId），使其可正常渲染与判分；
 * 题目已在库中，故 dbId 即 q.id。失败仅 ElMessage 提示，不影响出题等其余功能。
 */
async function focusQuestion(id) {
  try {
    const res = await getQuizQuestion(id)
    const q = res.data
    if (!q) {
      ElMessage.error('未找到该题目')
      return
    }
    questions.value = [{ ...q, localIdx: 0, dbId: q.id }]
    answers.value = {}
    results.value = {}
    rejectedCount.value = 0
    materialsUsed.value = 0
    requestedCount.value = 1
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '加载题目失败')
  }
}

// ─── 出题历史（常驻左侧栏，统一走 SidePanel）───
// 每次出题 = 一个题集；侧栏列出全部题集，点一条即载入主区（可回看/判分/导出），
// 选中态高亮，避免生成的题散在题库里「找不到」。
const batches = ref([])
const batchesTotal = ref(0)
const historyLoading = ref(false)
const openingBatch = ref('')
const activeBatchId = ref('')         // '' = 当前是新生成的草稿 / 聚焦单题（无历史选中）
const historyCollapsed = ref(false)
const historyWidth = ref(240)

async function loadBatches() {
  historyLoading.value = true
  try {
    const res = await listQuizBatches(100, 0)
    batches.value = res.data.batches || []
    batchesTotal.value = res.data.total || 0
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '加载出题历史失败')
  } finally {
    historyLoading.value = false
  }
}

/** 载入某个历史题集：形状对齐 handleGenerate 的 questions 项，使其可判分/导出 */
async function openBatch(b) {
  openingBatch.value = b.batch_id
  try {
    const res = await getBatchQuestions(b.batch_id)
    const qs = res.data.questions || []
    if (!qs.length) {
      ElMessage.warning('该记录没有题目')
      return
    }
    questions.value = qs.map((q, i) => ({ ...q, localIdx: i, dbId: q.id }))
    answers.value = {}
    results.value = {}
    rejectedCount.value = 0
    materialsUsed.value = 0
    requestedCount.value = qs.length
    if (b.subject && !subject.value.trim()) subject.value = b.subject
    activeBatchId.value = b.batch_id
    ElMessage.success(`已载入历史题目 ${qs.length} 道`)
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '加载历史题目失败')
  } finally {
    openingBatch.value = ''
  }
}

/** 导出提示：当前是新草稿 / 某个历史题集 / 主题题库 */
const sourceLabel = computed(() => {
  if (questions.value.length) {
    const hit = batches.value.find((b) => b.batch_id === activeBatchId.value)
    return hit ? `历史题集「${hit.subject || '未命名主题'}」` : '本次题目'
  }
  return `主题「${subject.value.trim()}」的题库`
})

onMounted(() => {
  loadStats()
  loadBatches()   // 侧栏角标显示历史题集数
  // 从节点详情跳入时：预填主题（仅在原为空时覆盖）+ 聚焦指定题目
  if (props.initialSubject && !subject.value.trim()) {
    subject.value = props.initialSubject
  }
  if (props.focusQuestionId != null) {
    focusQuestion(props.focusQuestionId)
  }
})
</script>

<template>
  <div class="quiz-layout">
    <!-- 出题历史：常驻左侧栏，与对话历史 / 图谱导航共用 SidePanel（拖宽 / 折叠 / 持久化） -->
    <SidePanel
      class="quiz-history-panel"
      side="left"
      label="出题历史"
      :min="200"
      :max="420"
      storage-key="quizHistory"
      v-model:width="historyWidth"
      v-model:collapsed="historyCollapsed"
    >
      <QuizHistorySidebar
        :batches="batches"
        :total="batchesTotal"
        :loading="historyLoading"
        :active-id="activeBatchId"
        :opening-id="openingBatch"
        @select="openBatch"
      />
    </SidePanel>

    <main class="quiz-content page-scroll">
      <div class="page-inner">
        <header class="page-head">
          <div class="qc-header-main">
            <h2 class="page-title">出题</h2>
          </div>
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
          <p>填主题，点「开始出题」。</p>
          <p class="empty-tip">已存 {{ stats.total_questions || 0 }} 道</p>
        </section>

        <!-- 出题结果统计：只留用户关心的数量，过滤/依据等过程信息进 title -->
        <div v-if="questions.length" class="quiz-meta">
          <span>本次 {{ questions.length }} 道</span>
          <span
            v-if="requestedCount > questions.length || rejectedCount || materialsUsed"
            class="meta-shortfall"
            :title="[
              requestedCount > questions.length ? `请求 ${requestedCount} 道，实得 ${questions.length} 道` : '',
              rejectedCount ? `过滤不合格 ${rejectedCount} 道` : '',
              materialsUsed ? `依据知识库 ${materialsUsed} 个片段` : '',
            ].filter(Boolean).join('；')"
          >· 详情</span>
        </div>

        <!-- 试卷导出：选格式 → 项目内预览 / 下载同一份 -->
        <div v-if="questions.length || subject.trim()" class="quiz-export">
          <span class="export-title">导出试卷</span>

          <el-select v-model="exportFmt" class="export-fmt" size="small">
            <el-option
              v-for="opt in exportOptions"
              :key="opt.fmt"
              :label="opt.label"
              :value="opt.fmt"
            />
          </el-select>

          <el-radio-group v-model="withAnswer" size="small" @change="closePreview">
            <el-radio-button :value="true">含答案</el-radio-button>
            <el-radio-button :value="false">仅题目</el-radio-button>
          </el-radio-group>

          <el-tooltip :content="exportOptions.find(o => o.fmt === exportFmt)?.tip" placement="top">
            <el-button
              size="small"
              :loading="exportingFmt === exportFmt"
              :disabled="!!exportingFmt"
              @click="handlePreview(exportFmt)"
            >
              <AppIcon name="eye" :size="14" /> 预览
            </el-button>
          </el-tooltip>

          <el-button
            size="small"
            type="primary"
            :loading="exportingFmt === exportFmt"
            :disabled="!!exportingFmt"
            @click="handleExport(exportFmt)"
          >
            <AppIcon name="download" :size="14" /> 下载
          </el-button>

          <span class="export-hint">{{ sourceLabel }}</span>
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
                  <!-- 对错图标：描边 SVG，颜色由 .q-result.ok/.wrong 的 color 决定 -->
                  <AppIcon
                    class="q-result-icon"
                    :name="results[q.localIdx].correct ? 'check' : 'x'"
                    :size="14"
                  />{{ results[q.localIdx].score }} / {{ results[q.localIdx].max_score }} 分
                  <span class="q-result-comment">{{ results[q.localIdx].comment }}</span>
                </div>
                <div v-if="results[q.localIdx].analysis" class="q-result-analysis">
                  解析：{{ results[q.localIdx].analysis }}
                </div>
                <!-- 掌握度回写：判分不再只留分数，结果要当场反映到图谱上 -->
                <div
                  v-if="results[q.localIdx].masteryNote || results[q.localIdx].sectionNote"
                  class="q-result-mastery"
                >
                  {{ results[q.localIdx].masteryNote || results[q.localIdx].sectionNote }}
                </div>
              </div>
            </div>
          </div>
        </section>
      </div>
    </main>

    <!-- 项目内只读预览：复用知识库原件预览组件，下载的就是预览的这一份 -->
    <KbTextPreviewDialog
      :visible="previewVisible"
      :name="previewName"
      :file-type="previewFileType"
      :raw-blob="previewRawBlob"
      :markdown="previewMarkdown"
      :chars="previewMarkdown.length"
      :total-chars="previewMarkdown.length"
      @update:visible="closePreview"
    >
      <template #actions>
        <button class="quiz-preview-download" title="下载当前文件" @click="handleExport(exportFmt)">
          <AppIcon name="download" :size="15" />
          下载
        </button>
      </template>
    </KbTextPreviewDialog>
  </div>
</template>

<style scoped>
/* 出题页是两栏：常驻历史侧栏（SidePanel）+ 右侧自己滚动的题目主区。
   外层 absolute 铺满视图（同 chat/graph 画布），主区沿用 page-scroll/page-inner，两栏用 flex 并排。 */
.quiz-layout {
  display: flex;
  width: 100%;
  height: 100%;
  position: absolute;
  inset: 0;
  overflow: hidden;
}

.quiz-history-panel {
  flex-shrink: 0;
}

/* 右侧主区：吃掉剩余宽度、自己滚；.page-inner 的限宽/留白照旧 */
.quiz-content {
  flex: 1;
  min-width: 0;
  height: 100%;
}

.qc-header-main {
  flex: 1;
  min-width: 0;
}

/* 配置区 */
.quiz-config {
  background: var(--color-bg-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
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
.quiz-empty p {
  margin: 4px 0;
  font-size: 14px;
}
.quiz-empty .empty-tip {
  font-size: 12px;
  color: var(--color-text-tertiary);
}

/* 出题结果统计 */
.quiz-meta {
  font-size: 13px;
  color: var(--color-text-secondary);
  margin-bottom: 14px;
}
/* 数量不足提示（分批出题后仍未凑够请求题数） */
.quiz-meta .meta-shortfall {
  color: var(--el-color-warning);
}
.quiz-meta b {
  color: var(--color-accent);
}

/* 试卷导出工具条 */
.quiz-export {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  padding: 10px 14px;
  margin-bottom: 16px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  background: var(--color-bg-subtle);
}
.quiz-export .export-title {
  font-size: 13px;
  font-weight: 600;
}
.quiz-export .export-fmt {
  width: 128px;
}
.quiz-export .el-button svg {
  margin-right: 4px;
  vertical-align: -2px;
}
.quiz-export .export-hint {
  font-size: 12px;
  color: var(--color-text-secondary);
  margin-left: auto;
}
.quiz-preview-download {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 10px;
  border: none;
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--color-text-secondary);
  font-family: inherit;
  font-size: 12px;
  cursor: pointer;
}
.quiz-preview-download:hover {
  background: var(--color-bg-hover);
  color: var(--color-text-primary);
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
  border-radius: var(--radius-lg);
  padding: 16px;
  transition: border-color 0.15s;
}

.question-card.has-result {
  border-color: var(--color-border-strong);
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
  border-radius: var(--radius-lg);
  background: var(--color-bg-hover);
  color: var(--color-text-secondary);
}

.t-single .q-type { color: var(--color-info); }
.t-multiple .q-type { color: var(--color-primary); }
.t-judge .q-type { color: var(--color-success); }
.t-fill .q-type { color: var(--color-warning); }
.t-short .q-type { color: var(--color-danger); }

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

.q-result.ok { color: var(--color-success); }
.q-result.wrong { color: var(--color-danger); }

/* ✓ / ✗ 用描边 SVG（stroke=currentColor），随 .ok/.wrong 变色 */
.q-result-icon {
  margin-right: 4px;
  vertical-align: -2px;
}

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
  border-radius: var(--radius-md);
  padding: 8px 12px;
}

/* 掌握度回写提示（判分 → 图谱节点颜色当场变化） */
.q-result-mastery {
  margin-top: 4px;
  font-weight: 400;
  color: var(--color-text-secondary);
}

</style>
