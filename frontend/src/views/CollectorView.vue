<script setup>
/**
 * CollectorView.vue — 资源采集页（活动栏「资源」入口）
 *
 * 流程（对齐 TODO_Collector B1.7 / 设计文档 §7）：
 *   选学科 → 搜索候选（license 徽标 / 预览 / 勾选）→ 建任务 → 轮询进度（可取消）→ 覆盖率概览
 *
 * 版权模式：读取用户画像 preferences.usage_mode（个人 / 商用），搜索与勾选均受其约束；
 *           切换入口在 设置 → 资源与版权。
 */
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import { getProfile } from '../api/index.js'
import {
  searchCollector,
  createCollectorTask,
  getCollectorTask,
  cancelCollectorTask,
  getCollectorStats,
} from '../api/collector.js'

// ─── 版权模式 ───
const mode = ref('personal')            // personal | commercial，来自用户画像
const modeLabel = { personal: '个人使用', commercial: '发布 / 商用' }

// 各授权等级说明（徽标配色沿用 CSS 变量）
const LICENSE_META = {
  L0: { label: 'L0 开放授权', cls: 'lic-l0' },
  L1: { label: 'L1 授权转载', cls: 'lic-l1' },
  L2: { label: 'L2 个人合理使用', cls: 'lic-l2' },
  L3: { label: 'L3 版权不明', cls: 'lic-l3' },
}

/** 当前模式允许勾选的授权等级（个人：L0-L2；商用：仅 L0） */
const ALLOWED_LICENSE = { personal: ['L0', 'L1', 'L2'], commercial: ['L0'] }

function isLicenseAllowed(level) {
  return ALLOWED_LICENSE[mode.value]?.includes(level) ?? false
}

// ─── 第一步：学科选择与搜索 ───
const subject = ref('')
const searching = ref(false)
const searchError = ref('')
const searched = ref(false)

// ─── 第二步：候选列表 ───
const candidates = ref([])
const selected = ref([])                // 勾选中的候选（完整对象）

// ─── 第三步：采集任务（轮询） ───
const creating = ref(false)
const task = ref(null)
const pollTimer = ref(null)
const taskStatusLabel = {
  queued: '排队中', running: '采集中', completed: '已完成',
  cancelled: '已取消', failed: '失败',
}

// ─── 第四步：覆盖率概览 ───
const coverage = ref([])
const statsLoading = ref(true)

const coverageEmpty = computed(() => !coverage.value.length && !statsLoading.value)

/** 从覆盖率数据提取可选学科（支持手工输入新学科） */
const subjectOptions = computed(() => {
  const names = []
  for (const stage of coverage.value) {
    for (const s of stage.subjects || []) {
      if (s.subject || s.name) names.push(s.subject || s.name)
    }
  }
  return [...new Set(names)]
})

const currentModeTip = computed(() =>
  mode.value === 'commercial'
    ? '当前为发布 / 商用模式：仅可采集 L0 开放授权资料，其余来源将灰化不可选。'
    : '当前为个人使用模式：可采集 L0 / L1 / L2 资料，仅限个人学习用途。'
)

// ═══ 数据加载 ═══

async function loadStats() {
  statsLoading.value = true
  try {
    const { data } = await getCollectorStats()
    coverage.value = data?.coverage || []
  } catch {
    // 覆盖率加载失败不阻塞采集主流程
  } finally {
    statsLoading.value = false
  }
}

async function loadMode() {
  try {
    const { data } = await getProfile()
    const prefs = data?.data?.preferences || {}
    mode.value = prefs.usage_mode === 'commercial' ? 'commercial' : 'personal'
  } catch {
    // 画像读取失败时保持默认 personal
  }
}

// ═══ 搜索候选 ═══

async function handleSearch() {
  const subj = subject.value.trim()
  if (!subj) {
    searchError.value = '请输入要采集的学科，如「数据结构与算法」'
    return
  }
  if (task.value && ['queued', 'running'].includes(task.value.status)) {
    ElMessage.warning('有任务正在采集中，请先等待完成或取消')
    return
  }
  searching.value = true
  searchError.value = ''
  searched.value = false
  candidates.value = []
  selected.value = []
  try {
    const { data } = await searchCollector(subj, mode.value)
    const list = data?.candidates || []
    candidates.value = list
    searched.value = true
    if (!list.length) ElMessage.info('未找到可采集的候选资源')
  } catch (e) {
    searchError.value = e.response?.data?.detail || e.message || '搜索失败'
  } finally {
    searching.value = false
  }
}

// ═══ 勾选与建任务 ═══

function toggleSelect(cand) {
  if (!isLicenseAllowed(cand.license_level)) return
  const idx = selected.value.findIndex((c) => c.source_url === cand.source_url)
  if (idx >= 0) selected.value.splice(idx, 1)
  else selected.value.push(cand)
}

function isSelected(cand) {
  return selected.value.some((c) => c.source_url === cand.source_url)
}

async function handleStart() {
  if (!selected.value.length) {
    ElMessage.warning('请先勾选至少一个候选资源')
    return
  }
  creating.value = true
  try {
    const { data } = await createCollectorTask(
      subject.value.trim(), mode.value, selected.value,
    )
    task.value = data?.task || null
    if (!task.value) throw new Error('后端未返回任务')
    pollTask()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || e.message || '创建任务失败')
  } finally {
    creating.value = false
  }
}

// ═══ 轮询任务进度 ═══

const TERMINAL_STATUS = ['completed', 'cancelled', 'failed']

function pollTask() {
  stopPoll()
  pollTimer.value = setInterval(async () => {
    if (!task.value) return
    try {
      const { data } = await getCollectorTask(task.value.id)
      const t = data?.task || data
      if (!t) return
      task.value = { ...task.value, ...t }
      if (TERMINAL_STATUS.includes(t.status)) {
        stopPoll()
        if (t.status === 'completed') {
          ElMessage.success('采集完成，已写入知识库')
          loadStats()
        } else if (t.status === 'cancelled') {
          ElMessage.info('任务已取消')
        } else {
          ElMessage.error(t.error || '采集任务失败')
        }
      }
    } catch {
      stopPoll()
      ElMessage.error('查询任务进度失败，请稍后手动刷新')
    }
  }, 1500)
}

function stopPoll() {
  if (pollTimer.value) {
    clearInterval(pollTimer.value)
    pollTimer.value = null
  }
}

async function handleCancelTask() {
  if (!task.value) return
  try {
    const { data } = await cancelCollectorTask(task.value.id)
    task.value = { ...task.value, ...(data?.task || data) }
    stopPoll()
    ElMessage.info('已发送取消请求')
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || e.message || '取消失败')
  }
}

// ═══ 覆盖率统计渲染辅助 ═══

function stageStats(stage) {
  const subs = stage.subjects || []
  let total = 0
  let collected = 0
  for (const s of subs) {
    total += s.boards_total ?? 0
    collected += s.boards_collected ?? 0
  }
  if (!total && stage.total != null) {
    total = stage.total ?? 0
    collected = stage.collected ?? 0
  }
  return { total, collected, pct: total ? Math.round((collected / total) * 100) : 0 }
}

function fmtSize(bytes) {
  if (bytes == null || !Number.isFinite(bytes)) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`
  return `${(bytes / 1024 ** 3).toFixed(1)} GB`
}

function fmtTime(iso) {
  if (!iso) return ''
  return iso.slice(0, 16).replace('T', ' ')
}

const taskRunning = computed(() =>
  task.value && ['queued', 'running'].includes(task.value.status),
)
const taskPct = computed(() => {
  const t = task.value
  if (!t || !t.total_count) return 0
  return Math.min(100, Math.round(((t.processed_count || 0) / t.total_count) * 100))
})

onMounted(() => {
  loadStats()
  loadMode()
})
onUnmounted(() => stopPoll())
</script>

<template>
  <div class="collector-layout">
    <main class="collector-content">
      <div class="collector-inner">
        <header class="cc-header">
          <h2>资源采集</h2>
          <p class="cc-subtitle">
            按学科从开放来源（维基 / 开放教材等）自动发现学习资料，预览后勾选入库，
            对话即可检索引用。采集范围受「资源与版权」中的使用模式约束。
          </p>
          <span class="mode-pill" :class="mode">
            {{ modeLabel[mode] }}模式
          </span>
        </header>

        <!-- 第一步：学科选择 + 搜索 -->
        <section class="cc-card search-card">
          <div class="cc-row">
            <el-select
              v-model="subject"
              filterable
              allow-create
              default-first-option
              clearable
              placeholder="输入或选择学科，如：数据结构与算法"
              class="subject-select"
              :disabled="!!taskRunning"
              @keyup.enter="handleSearch"
            >
              <el-option v-for="s in subjectOptions" :key="s" :label="s" :value="s" />
            </el-select>
            <el-button type="primary" :loading="searching" :disabled="!!taskRunning" @click="handleSearch">
              {{ searching ? '搜索中...' : '搜索候选资源' }}
            </el-button>
          </div>
          <p class="mode-tip" :class="mode">{{ currentModeTip }}</p>
          <p v-if="searchError" class="error-text">{{ searchError }}</p>

          <!-- 授权等级图例 -->
          <div class="lic-legend">
            <span
              v-for="(m, lv) in LICENSE_META"
              :key="lv"
              class="lic-badge"
              :class="[m.cls, { disabled: !ALLOWED_LICENSE[mode].includes(lv) }]"
            >{{ m.label }}</span>
          </div>
        </section>

        <!-- 第二步：候选列表 -->
        <template v-if="searched || candidates.length">
          <section class="cc-card">
            <div class="cand-head">
              <h3>候选资源（{{ candidates.length }}）</h3>
              <span v-if="selected.length" class="selected-num">已选 {{ selected.length }} 项</span>
            </div>

            <div v-if="!candidates.length" class="empty-state">
              未找到候选资源，可换个学科名或检查使用模式（商用模式仅开放授权源）。
            </div>

            <div v-else class="cand-list">
              <div
                v-for="cand in candidates"
                :key="cand.source_url"
                class="cand-item"
                :class="{ disabled: !isLicenseAllowed(cand.license_level) }"
                @click="toggleSelect(cand)"
              >
                <el-checkbox
                  :model-value="isSelected(cand)"
                  :disabled="!isLicenseAllowed(cand.license_level)"
                  class="cand-check"
                />
                <div class="cand-body">
                  <div class="cand-title-row">
                    <span
                      class="lic-badge small"
                      :class="(LICENSE_META[cand.license_level] || { cls: 'lic-l3' }).cls"
                    >
                      {{ (LICENSE_META[cand.license_level] || { label: cand.license_level }).label }}
                    </span>
                    <span class="cand-title">{{ cand.title }}</span>
                  </div>
                  <p class="cand-desc">{{ cand.description || '（暂无简介）' }}</p>
                  <div class="cand-meta">
                    <span v-if="cand.size_bytes" class="cand-size">{{ fmtSize(cand.size_bytes) }}</span>
                    <span v-if="!isLicenseAllowed(cand.license_level)" class="cand-forbidden">
                      当前模式不可采
                    </span>
                  </div>
                </div>
              </div>
            </div>

            <div v-if="candidates.length" class="cand-actions">
              <el-button
                type="primary"
                :loading="creating"
                :disabled="!selected.length || !!taskRunning"
                @click="handleStart"
              >
                {{ creating ? '创建任务...' : `开始采集（${selected.length} 项）` }}
              </el-button>
              <el-button v-if="selected.length" plain @click="selected = []">清空勾选</el-button>
            </div>
          </section>
        </template>

        <!-- 第三步：任务进度 -->
        <section v-if="task" class="cc-card">
          <div class="task-head">
            <h3>采集任务</h3>
            <span class="task-status" :class="task.status">
              {{ taskStatusLabel[task.status] || task.status }}
            </span>
          </div>
          <div class="task-info">
            学科：<b>{{ task.subject }}</b>
            <span v-if="task.created_at"> · 创建于 {{ fmtTime(task.created_at) }}</span>
          </div>

          <el-progress
            v-if="task.total_count"
            :percentage="taskPct"
            :status="task.status === 'failed' ? 'exception' : task.status === 'completed' ? 'success' : undefined"
          />
          <div class="task-count">
            已处理 {{ task.processed_count || 0 }}
            <template v-if="task.total_count"> / {{ task.total_count }}</template>
            <template v-if="task.cursor"> · 断点：{{ String(task.cursor).slice(0, 60) }}</template>
          </div>
          <p v-if="task.error" class="error-text">{{ task.error }}</p>

          <div v-if="taskRunning" class="task-actions">
            <el-button type="danger" plain :loading="false" @click="handleCancelTask">
              取消采集
            </el-button>
          </div>
        </section>

        <!-- 第四步：覆盖率概览 -->
        <section class="cc-card coverage-card">
          <h3>采集覆盖率</h3>
          <p class="coverage-sub">按 学段 → 学科 → 板块 统计已采 / 缺口，了解哪些板块还没采到资料。</p>

          <div v-if="statsLoading" class="empty-state">加载中...</div>
          <div v-else-if="coverageEmpty" class="empty-state">
            暂无采集记录。搜索并采集一些资源后，这里会显示各板块的覆盖率。
          </div>
          <div v-else class="cov-stages">
            <div v-for="stage in coverage" :key="stage.stage" class="cov-stage">
              <div class="cov-stage-head">
                <span class="cov-stage-name">{{ stage.stage_name || stage.stage }}</span>
                <span v-if="stage.subjects?.length" class="cov-stage-count">
                  {{ stage.subjects.length }} 个学科
                </span>
              </div>

              <!-- 板块粒度明细（B1.4 提供） -->
              <div v-if="stage.subjects?.length" class="cov-subjects">
                <div v-for="s in stage.subjects" :key="s.subject || s.name" class="cov-subject">
                  <div class="cov-subject-row">
                    <span class="cov-subject-name">{{ s.subject || s.name }}</span>
                    <span v-if="s.boards_total" class="cov-subject-stat">
                      {{ s.boards_collected ?? 0 }} / {{ s.boards_total }} 板块
                    </span>
                  </div>
                  <el-progress
                    v-if="s.boards_total"
                    :percentage="s.boards_total ? Math.round(((s.boards_collected ?? 0) / s.boards_total) * 100) : 0"
                  />
                  <el-progress
                    v-else-if="s.collected != null"
                    :percentage="Math.min(100, s.collected || 0)"
                    :format="() => `${s.collected || 0} 份`"
                  />
                </div>
              </div>
              <!-- 仅学段级汇总 -->
              <div v-else class="cov-subject">
                <div class="cov-subject-row">
                  <span class="cov-subject-stat">
                    {{ stageStats(stage).collected }} / {{ stageStats(stage).total }}
                    <template v-if="stageStats(stage).total">板块（{{ stageStats(stage).pct }}%）</template>
                  </span>
                </div>
                <el-progress v-if="stageStats(stage).total" :percentage="stageStats(stage).pct" />
              </div>
            </div>
          </div>
        </section>
      </div>
    </main>
  </div>
</template>

<style scoped>
.collector-layout {
  flex: 1;
  height: 100%;
  min-width: 0;
  overflow-y: auto;
}
.collector-content {
  height: 100%;
}
.collector-inner {
  max-width: 960px;
  margin: 0 auto;
  padding: 28px 32px 60px;
}

/* ── 头部 ── */
.cc-header { margin-bottom: 20px; position: relative; }
.cc-header h2 {
  margin: 0 0 6px;
  font-size: 20px;
  font-weight: 600;
  color: var(--color-text-primary);
}
.cc-subtitle {
  margin: 0;
  font-size: 13px;
  color: var(--color-text-tertiary);
  line-height: 1.6;
  max-width: 720px;
  padding-right: 120px;
}
.mode-pill {
  position: absolute;
  right: 0;
  top: 2px;
  font-size: 12px;
  font-weight: 600;
  padding: 4px 12px;
  border-radius: 999px;
}
.mode-pill.personal {
  background: var(--color-green-light);
  color: var(--color-green);
}
.mode-pill.commercial {
  background: var(--color-accent-light);
  color: var(--color-accent);
}

/* ── 通用卡片 ── */
.cc-card {
  background: var(--color-bg-surface);
  border: 1px solid var(--color-border);
  border-radius: 12px;
  padding: 16px 18px;
  margin-bottom: 18px;
}
.cc-card h3 {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--color-text-primary);
}

.cc-row { display: flex; gap: 10px; align-items: center; }
.subject-select { flex: 1; min-width: 240px; }

.mode-tip { margin: 10px 0 0; font-size: 12px; line-height: 1.6; }
.mode-tip.personal { color: var(--color-text-secondary); }
.mode-tip.commercial { color: var(--color-yellow); }

.error-text {
  margin: 8px 0 0;
  color: var(--color-red);
  font-size: 13px;
}

/* ── 授权等级徽标 ── */
.lic-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
}
.lic-badge {
  display: inline-flex;
  align-items: center;
  font-size: 11px;
  font-weight: 600;
  padding: 2px 9px;
  border-radius: 10px;
  border: 1px solid transparent;
}
.lic-badge.small { font-size: 10px; padding: 1px 7px; }
.lic-l0 { background: var(--color-green-light); color: var(--color-green); }
.lic-l1 { background: var(--color-blue-light); color: var(--color-blue); border-color: rgba(96,165,250,.3); }
.lic-l2 { background: var(--color-yellow); color: #fff; }
.lic-l3 { background: var(--color-red-light); color: var(--color-red); }
.lic-badge.disabled { opacity: 0.35; text-decoration: line-through; }

/* ── 候选列表 ── */
.cand-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}
.selected-num { font-size: 12px; color: var(--color-accent); font-weight: 600; }

.cand-list { display: flex; flex-direction: column; gap: 8px; }
.cand-item {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 10px 12px;
  border: 1px solid var(--color-border-light);
  border-radius: 8px;
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s;
}
.cand-item:hover { background: var(--color-bg-hover); }
.cand-item.disabled { opacity: 0.5; cursor: not-allowed; }

.cand-body { flex: 1; min-width: 0; }
.cand-title-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.cand-title { font-size: 14px; font-weight: 500; color: var(--color-text-primary); }
.cand-desc {
  margin: 6px 0 0;
  font-size: 12px;
  color: var(--color-text-tertiary);
  line-height: 1.6;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.cand-meta { margin-top: 6px; display: flex; gap: 10px; }
.cand-size { font-size: 11px; color: var(--color-text-tertiary); }
.cand-forbidden { font-size: 11px; color: var(--color-red); font-weight: 600; }

.cand-actions {
  display: flex;
  gap: 10px;
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid var(--color-border);
}

/* ── 空态 ── */
.empty-state {
  text-align: center;
  padding: 28px 20px;
  color: var(--color-text-tertiary);
  font-size: 13px;
  line-height: 1.8;
}

/* ── 任务 ── */
.task-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}
.task-status { font-size: 12px; font-weight: 600; padding: 2px 10px; border-radius: 10px; }
.task-status.running, .task-status.queued { background: var(--color-accent-light); color: var(--color-accent); }
.task-status.completed { background: var(--color-green-light); color: var(--color-green); }
.task-status.cancelled { background: var(--color-bg-hover); color: var(--color-text-secondary); }
.task-status.failed { background: var(--color-red-light); color: var(--color-red); }

.task-info { font-size: 13px; color: var(--color-text-secondary); margin-bottom: 12px; }
.task-count {
  margin-top: 8px;
  font-size: 12px;
  color: var(--color-text-tertiary);
  word-break: break-all;
}
.task-actions { margin-top: 12px; }

/* ── 覆盖率 ── */
.coverage-sub { margin: 4px 0 12px; font-size: 12px; color: var(--color-text-tertiary); }
.cov-stages { display: flex; flex-direction: column; gap: 16px; }
.cov-stage {
  border: 1px solid var(--color-border-light);
  border-radius: 8px;
  padding: 12px 14px;
}
.cov-stage-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 10px;
}
.cov-stage-name { font-size: 14px; font-weight: 600; color: var(--color-text-primary); }
.cov-stage-count { font-size: 12px; color: var(--color-text-tertiary); }
.cov-subjects { display: flex; flex-direction: column; gap: 8px; }
.cov-subject { padding: 2px 0; }
.cov-subject-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 4px;
}
.cov-subject-name { font-size: 13px; color: var(--color-text-secondary); }
.cov-subject-stat { font-size: 12px; color: var(--color-text-tertiary); }
</style>
