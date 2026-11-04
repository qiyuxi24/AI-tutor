<script setup>
/**
 * LectureImportView.vue — 视频课程导入（链接 → 讲义 → 知识库）
 *
 * 取词逻辑全在后端（`core/kb/video_import.py`），排队在 `core/kb/import_queue.py`。
 * 本页只有三块：**粘贴 → （可选）预检 → 队列**。
 *
 * 一条硬规矩：**队列是唯一真相**。
 * 旧版同时维护「本地任务卡（步骤条 + 结果区）」和「队列面板」两份状态，靠轮询回写
 * 互相追赶（`syncLocalTask` 就是那个补丁）—— 批量导入时两处说的不一样，刷新后本地
 * 那份直接丢。现在服务端把分集阶段（`stage`）、开始时间（`started_at`）都落库了，
 * 页面只需要一件事：拉 `GET /kb/import/tasks` 渲染。SSE 只负责让它更快看到变化。
 *
 * 进度分三层，各自有归属：
 *   任务级  done+failed / total（进度条）
 *   分集级  第 i 集 / 共 n 集（哪一集在跑）
 *   步骤级  查找字幕 → 下载 → 提取 → 转写 → 整理（那一集在第几步）
 */
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { useChatStore } from '../stores/chatStore'
import {
  biliLoginPoll, biliLoginQrcode, getKbNodeText, importLecture, inspectLecture,
  lectureItemAction, lectureTaskAction, listLectureTasks, probeLectureImport,
  reorderLectureItem,
} from '../api/kb.js'
import { subtitleLanguages, useLectureSettings } from '../utils/lectureSettings.js'
import KbTextPreviewDialog from '../components/KbTextPreviewDialog.vue'

const emit = defineEmits(['go-kb'])

const store = useChatStore()
const settings = useLectureSettings()

// ── 状态字典（顺序 = 执行顺序；key 与后端 video_import.STAGE_* 一一对应）──
const STEPS = [
  { key: 'subtitle', label: '查找字幕' },
  { key: 'download', label: '下载音频' },
  { key: 'extract', label: '提取音轨' },
  { key: 'transcribe', label: '语音转写' },
  { key: 'compose', label: '整理讲义' },
]
const STAGE_LABEL = Object.fromEntries(STEPS.map((s) => [s.key, s.label]))

const STATUS = {
  pending: '排队中', running: '导入中', paused: '已暂停',
  done: '完成', failed: '失败', canceled: '已取消',
}
const OPEN = ['pending', 'running', 'paused']

const url = ref('')
const deps = ref(null)
const submitting = ref(false)
const inspecting = ref(false)
const report = ref(null)       // 预检结果（可选路径；null = 没检查过）
const tasks = ref([])
const expanded = ref(new Set())
const fetchedAt = ref(0)
const tick = ref(0)

let pollTimer = null
let refreshTimer = null
let clockTimer = null

// ── 队列 ──────────────────────────────────────────────

const openTasks = computed(() => tasks.value.filter((t) => OPEN.includes(t.status)))
const closedTasks = computed(() => tasks.value.filter((t) => !OPEN.includes(t.status)))
const hasOpen = computed(() => openTasks.value.length > 0)
const linkCount = computed(() => url.value.split(/\r?\n/).filter((l) => l.trim()).length)
/** 历史区默认只显示最近一个：刚导完的讲义要能立刻点开预览，老任务不该占屏 */
const visibleClosed = computed(() => (
  expanded.value.has('__history') ? closedTasks.value : closedTasks.value.slice(0, 1)
))

function sortTasks(list) {
  const rank = { running: 0, pending: 1, paused: 2 }
  return [...list].sort((a, b) => (rank[a.status] ?? 3) - (rank[b.status] ?? 3))
}

async function refreshQueue() {
  try {
    const { data } = await listLectureTasks()
    tasks.value = sortTasks(data.tasks || [])
    fetchedAt.value = Date.now()
  } catch {
    // 拉取失败不弹错：下一轮（SSE 或兜底轮询）会再来
  }
  syncTimers()
}

/** SSE 已经说明有变化 → 晚 1.5s 拉一次，省掉每来一条事件都打接口 */
function refreshSoon() {
  if (refreshTimer) return
  refreshTimer = setTimeout(() => {
    refreshTimer = null
    refreshQueue()
  }, 1500)
}

function syncTimers() {
  const open = hasOpen.value
  if (open && !pollTimer) pollTimer = setInterval(refreshQueue, 15000)
  if (!open && pollTimer) { clearInterval(pollTimer); pollTimer = null }
}

/** 距上次拉取过了多少秒（依赖 `tick`：秒针每秒 +1 触发重算，而不是每 15 秒跳一格） */
const sinceFetch = computed(() => {
  void tick.value
  return Math.max(0, Math.round((Date.now() - fetchedAt.value) / 1000))
})

function taskElapsed(t) {
  const base = t.elapsed_secs || 0
  return t.status === 'running' ? base + sinceFetch.value : base
}

function taskEta(t) {
  if (t.status !== 'running' || !t.eta_secs) return 0
  return Math.max(0, t.eta_secs - sinceFetch.value)
}

function fmtSecs(secs) {
  const s = Math.max(0, Math.round(secs || 0))
  if (s < 60) return `${s} 秒`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m} 分${s % 60 ? ` ${s % 60} 秒` : ''}`
  return `${Math.floor(m / 60)} 小时 ${m % 60} 分`
}

function progressPercent(t) {
  if (!t.total) return 0
  return Math.min(100, Math.round(((t.done + t.failed) / t.total) * 100))
}

function runningItem(t) {
  return (t.items || []).find((i) => i.status === 'running') || null
}

function itemIndex(t, item) {
  return (t.items || []).findIndex((i) => i.id === item.id) + 1
}

/** 分集在第几步（-1 = 还没有阶段信息） */
function stepIndex(item) {
  return STEPS.findIndex((s) => s.key === item?.stage)
}

function stepState(item, step) {
  if (!item) return 'todo'
  if (item.status === 'failed' && step.key === item.stage) return 'failed'
  if (item.status === 'done') return 'done'
  const cur = stepIndex(item)
  const idx = STEPS.findIndex((s) => s.key === step.key)
  if (cur < 0) return 'todo'
  if (idx === cur) return 'active'
  return idx < cur ? 'done' : 'todo'
}

function itemMeta(t, item) {
  if (item.status === 'running') {
    const label = STAGE_LABEL[item.stage] || '准备中'
    // 段内进度只有 SSE 有（后端不落库：转写一集要报几十次，写库没意义）
    return item._prog ? `${label} · ${item._prog.index}/${item._prog.total} 段` : label
  }
  if (item.status === 'done') {
    const bits = []
    if (item.chars) bits.push(`${item.chars} 字`)
    if (item.duration_secs != null) bits.push(fmtSecs(item.duration_secs))
    if (item.source) bits.push(item.source)
    return bits.join(' · ')
  }
  if (item.status === 'failed') return item.error || '失败'
  if (item.status === 'canceled') return '已取消'
  return '等待中'
}

function toggleItems(taskId) {
  const next = new Set(expanded.value)
  if (next.has(taskId)) next.delete(taskId)
  else next.add(taskId)
  expanded.value = next
}

// ── 操作 ──────────────────────────────────────────────

async function actTask(taskId, action) {
  try {
    await lectureTaskAction(taskId, action)
    await refreshQueue()
  } catch (e) {
    ElMessage.error('操作失败：' + (e.response?.data?.detail || e.message))
  }
}

async function actItem(task, item, action) {
  try {
    await lectureItemAction(task.id, item.id, action)
    await refreshQueue()
  } catch (e) {
    ElMessage.error('操作失败：' + (e.response?.data?.detail || e.message))
  }
}

async function moveItem(task, item, delta) {
  try {
    await reorderLectureItem(task.id, item.id, delta)
    await refreshQueue()
  } catch (e) {
    ElMessage.error('调整顺序失败：' + (e.response?.data?.detail || e.message))
  }
}

// ── 提交 / 预检 ───────────────────────────────────────

function payloadFor(links) {
  const payload = {
    urls: links,
    transcript_source: settings.transcriptSource,
    expand: settings.expand,
    slides: settings.slides,
  }
  const langs = subtitleLanguages()
  if (langs.length) payload.languages = langs
  if (settings.asrModel.trim()) payload.asr_model = settings.asrModel.trim()
  return payload
}

/** 依赖够不够**只由后端判定**（`missing_requirements` 按链接 + 模式逐条查），
 *  前端复制一份判定就是双源漂移：B 站平台字幕本来零依赖，却被 `!d.yt_dlp` 拦死。 */
async function submit() {
  const links = url.value.split(/\r?\n/).map((l) => l.trim()).filter(Boolean)
  if (!links.length || submitting.value) return
  submitting.value = true
  try {
    const { data } = await importLecture(payloadFor(links))
    ElMessage.success(`已加入队列：${data.items} 集`)
    url.value = ''
    report.value = null
    await refreshQueue()
  } catch (e) {
    ElMessage.error('导入失败：' + (e.response?.data?.detail || e.message))
  } finally {
    submitting.value = false
  }
}

/** 可选预检：先看清楚"这链接有几集、要多久、有没有字幕、缺什么"再决定 */
async function doInspect() {
  const links = url.value.split(/\r?\n/).map((l) => l.trim()).filter(Boolean)
  if (!links.length || inspecting.value) return
  inspecting.value = true
  try {
    const { data } = await inspectLecture(payloadFor(links))
    report.value = data
    deps.value = data.deps || deps.value
  } catch (e) {
    ElMessage.error('检查失败：' + (e.response?.data?.detail || e.message))
  } finally {
    inspecting.value = false
  }
}

function reportMeta(row) {
  const bits = []
  if (row.episodes > 1) bits.push(`${row.episodes} 集`)
  if (row.duration_secs) bits.push(`总时长 ${fmtSecs(row.duration_secs)}`)
  if (row.subtitle) {
    bits.push(`有字幕${row.sampled ? '（抽查第 1 集）' : ''}`)
  } else if (row.missing.length) {
    bits.push('缺依赖')
  } else {
    bits.push('无平台字幕 · 将走语音转写')
  }
  return bits.join(' · ')
}

// ── 依赖自检（只在有缺口时提示，不常驻四个 chip）──────────

const depProblems = computed(() => {
  const d = deps.value
  if (!d) return []
  const out = []
  if (!d.yt_dlp) out.push('yt-dlp 缺失（pip install yt-dlp）')
  if (!d.ffmpeg) out.push('ffmpeg 缺失（语音转写/截图需要）')
  if (!d.asr_model) out.push('ASR 模型未配置（无字幕课程需要）')
  if (!d.cookie) out.push('未登录 B 站（会员课 / AI 总结字幕需要）')
  return out
})

function cookieText() {
  const d = deps.value
  if (!d?.cookie) return '未登录'
  return { env: '手工配置', file: '凭证文件', saved: '扫码已登录', browser: '浏览器自动读取' }[
    d.cookie_source] || '已配置'
}

async function probe() {
  try {
    const { data } = await probeLectureImport()
    deps.value = data
  } catch {
    deps.value = null
  }
}

// ── B 站扫码登录（凭证只留在服务端）────────────────────
const qr = reactive({ visible: false, loading: false, key: '', image: '', status: 'waiting', message: '' })
let qrTimer = null

function qrStatusText() {
  return {
    waiting: '等待扫码…', scanned: '已扫码，请在手机上确认',
    expired: '二维码已失效', success: '登录成功', error: qr.message || '获取失败',
  }[qr.status] || ''
}

async function openBiliLogin() {
  stopQrTimer()
  Object.assign(qr, { visible: true, loading: true, image: '', key: '', status: 'waiting', message: '' })
  try {
    const { data } = await biliLoginQrcode()
    qr.key = data.qrcode_key || ''
    qr.image = data.image || ''
    qrTimer = setInterval(pollQr, 2000)
  } catch (e) {
    qr.status = 'error'
    qr.message = e.response?.data?.detail || e.message
  } finally {
    qr.loading = false
  }
}

async function pollQr() {
  if (!qr.key) return
  try {
    const { data } = await biliLoginPoll(qr.key)
    qr.status = data.status
    qr.message = data.message || ''
    if (data.status === 'success') {
      stopQrTimer()
      qr.visible = false
      await probe()
      ElMessage.success('B 站登录成功')
    } else if (['expired', 'error'].includes(data.status)) {
      stopQrTimer()
    }
  } catch { /* 单次轮询失败不终止 */ }
}

function stopQrTimer() {
  if (qrTimer) clearInterval(qrTimer)
  qrTimer = null
}

// ── 讲义预览 ──────────────────────────────────────────
const preview = reactive({
  visible: false, loading: false, name: '', markdown: '', chars: 0, totalChars: 0, truncated: false,
})

async function openPreview(item) {
  if (!item.node_id) return
  preview.visible = true
  preview.loading = true
  preview.name = `课程讲义-${item.title || item.url}.md`
  preview.markdown = ''
  try {
    const { data } = await getKbNodeText(item.node_id)
    preview.markdown = data.markdown || ''
    preview.chars = data.chars || 0
    preview.totalChars = data.total_chars || 0
    preview.truncated = !!data.truncated
  } catch (e) {
    preview.visible = false
    ElMessage.error('读取讲义失败：' + (e.response?.data?.detail || e.message))
  } finally {
    preview.loading = false
  }
}

// ── 生命周期 ──────────────────────────────────────────

// SSE 进度：先就地更新那一行的阶段（不等下一次拉取），再延迟同步一次计数
watch(() => store.importProgress, (p) => {
  if (!p) return
  const task = tasks.value.find((t) => t.id === p.task_id)
  if (task) {
    const item = (task.items || []).find((i) => i.id === p.item_id)
    if (item) {
      if (p.stage && !['done', 'failed'].includes(p.stage)) item.stage = p.stage
      if (p.index && p.total) item._prog = { index: p.index, total: p.total }
      if (p.chars) item.chars = p.chars
    }
    if (p.stage === 'done' || p.stage === 'failed') refreshSoon()
  } else {
    refreshSoon()
  }
})

// 后台任务已落库的通知（kb_updated）→ 立刻重拉
watch(() => store.kbEpoch, refreshSoon)

onMounted(async () => {
  await Promise.all([probe(), refreshQueue()])
  clockTimer = setInterval(() => { tick.value += 1 }, 1000)
})

onBeforeUnmount(() => {
  stopQrTimer()
  if (pollTimer) clearInterval(pollTimer)
  if (refreshTimer) clearTimeout(refreshTimer)
  if (clockTimer) clearInterval(clockTimer)
})
</script>

<template>
  <div class="page-scroll">
    <main class="page-inner page-inner--narrow li">
      <header class="li-head">
        <div>
          <h1 class="page-title">视频课程导入</h1>
          <p class="page-sub">
            粘贴 B 站 / YouTube 链接，先取平台字幕，没有字幕才语音转写；讲义直接进知识库。
          </p>
        </div>
        <button type="button" class="li-ghost" @click="openBiliLogin">
          {{ deps?.cookie ? `B 站：${cookieText()}` : '登录 B 站' }}
        </button>
      </header>

      <!-- 环境：只在有缺口时出现一行，不再常驻四个状态 chip -->
      <div v-if="depProblems.length" class="li-warn">
        <span class="li-warn-dot" aria-hidden="true" />
        <span>{{ depProblems.join('；') }}</span>
        <button type="button" class="li-link" @click="probe">重新探测</button>
      </div>

      <!-- 粘贴 -->
      <section class="li-compose">
        <el-input
          v-model="url"
          type="textarea"
          :rows="3"
          resize="vertical"
          placeholder="粘贴课程链接，一行一条（B站 / YouTube）"
          maxlength="6000"
        />
        <div class="li-compose-foot">
          <span class="li-hint">
            {{ linkCount ? `${linkCount} 条链接` : '把链接粘进来' }} ·
            取词方式 / 合集展开 / 截图在 设置 → 视频导入
          </span>
          <div class="li-compose-actions">
            <button
              type="button"
              class="li-ghost"
              :disabled="!linkCount || inspecting"
              @click="doInspect"
            >
              {{ inspecting ? '检查中…' : '先检查' }}
            </button>
            <el-button
              type="primary"
              :loading="submitting"
              :disabled="!linkCount"
              @click="submit"
            >
              加入队列
            </el-button>
          </div>
        </div>
      </section>

      <!-- 预检结果（可选路径） -->
      <section v-if="report" class="li-card">
        <header class="li-card-head">
          <h3>检查结果</h3>
          <button type="button" class="li-link" @click="report = null">关闭</button>
        </header>
        <ul class="li-report">
          <li v-for="row in report.links" :key="row.url">
            <span class="li-report-title" :title="row.url">{{ row.title || row.url }}</span>
            <span class="li-report-meta">
              {{ row.note || reportMeta(row) }}
            </span>
          </li>
        </ul>
        <footer class="li-card-foot">
          <span class="li-hint">
            共 {{ report.links.reduce((n, r) => n + r.episodes, 0) }} 集会排队，逐集串行处理
          </span>
          <el-button type="primary" :loading="submitting" @click="submit">开始导入</el-button>
        </footer>
      </section>

      <!-- 队列（唯一真相） -->
      <section v-if="openTasks.length" class="li-card li-queue">
        <header class="li-card-head">
          <h3>导入队列</h3>
          <span class="li-hint">同一时间只跑一集 · 暂停/取消在集与集之间生效</span>
        </header>

        <article
          v-for="t in openTasks"
          :key="t.id"
          class="li-task"
          :class="t.status"
        >
          <div class="li-task-head">
            <span class="li-status" :class="t.status">
              <i aria-hidden="true" />{{ STATUS[t.status] || t.status }}
            </span>
            <span class="li-task-title" :title="t.title || t.id">
              {{ t.title || '未命名导入' }}
            </span>
            <span class="li-task-count">{{ t.done + t.failed }} / {{ t.total }} 集</span>
            <span class="li-task-time">
              {{ fmtSecs(taskElapsed(t)) }}
              <template v-if="taskEta(t)">· 约剩 {{ fmtSecs(taskEta(t)) }}</template>
            </span>
            <span class="li-task-actions">
              <button
                v-if="t.status === 'running' || t.status === 'pending'"
                type="button" class="li-link" @click="actTask(t.id, 'pause')"
              >暂停</button>
              <button
                v-if="t.status === 'paused'"
                type="button" class="li-link" @click="actTask(t.id, 'resume')"
              >继续</button>
              <button
                v-if="t.failed"
                type="button" class="li-link" @click="actTask(t.id, 'retry')"
              >重试失败</button>
              <button
                type="button" class="li-link danger" @click="actTask(t.id, 'cancel')"
              >取消</button>
            </span>
          </div>

          <div class="li-bar" role="progressbar" :aria-valuenow="progressPercent(t)"
               aria-valuemin="0" aria-valuemax="100" :aria-label="`导入进度 ${progressPercent(t)}%`">
            <i :style="{ width: progressPercent(t) + '%' }" />
          </div>

          <!-- 正在跑的那一集：三层进度落到一行 -->
          <div v-if="runningItem(t)" class="li-now">
            <span class="li-now-title">
              第 {{ itemIndex(t, runningItem(t)) }} 集
              <b>{{ runningItem(t).title || '（未命名）' }}</b>
            </span>
            <ol class="li-steps">
              <li
                v-for="step in STEPS" :key="step.key"
                :class="stepState(runningItem(t), step)"
              >{{ step.label }}</li>
            </ol>
          </div>

          <!-- 分集明细（默认折叠：队列要能一屏看完） -->
          <button type="button" class="li-items-toggle" @click="toggleItems(t.id)">
            {{ expanded.has(t.id) ? '收起分集' : `展开 ${t.items.length} 集` }}
          </button>
          <ul v-if="expanded.has(t.id)" class="li-items">
            <li v-for="(it, i) in t.items" :key="it.id" :class="it.status">
              <span class="li-item-dot" aria-hidden="true" />
              <span class="li-item-index">{{ i + 1 }}</span>
              <span class="li-item-title" :title="it.url">{{ it.title || it.url }}</span>
              <span class="li-item-meta" :title="itemMeta(t, it)">{{ itemMeta(t, it) }}</span>
              <span class="li-item-actions">
                <button v-if="it.status === 'done' && it.node_id" type="button"
                        class="li-link" @click="openPreview(it)">预览</button>
                <button v-if="it.status === 'failed'" type="button"
                        class="li-link" @click="actItem(t, it, 'retry')">重试</button>
                <button v-if="it.status === 'pending'" type="button"
                        class="li-link" @click="actItem(t, it, 'cancel')">取消</button>
                <template v-if="it.status === 'pending'">
                  <button type="button" class="li-move" :disabled="i === 0"
                          title="上移" @click="moveItem(t, it, -1)">↑</button>
                  <button type="button" class="li-move" :disabled="i === t.items.length - 1"
                          title="下移" @click="moveItem(t, it, 1)">↓</button>
                </template>
              </span>
            </li>
          </ul>
        </article>
      </section>

      <!-- 已完成 / 已取消：最近一个总是露出来（刚导完要能立刻预览），其余折叠 -->
      <section v-if="closedTasks.length" class="li-card li-history">
        <button type="button" class="li-history-head" @click="toggleItems('__history')">
          <span>历史任务</span>
          <span class="li-hint">
            {{ closedTasks.length }} 个 ·
            {{ expanded.has('__history') ? '只看最近一个' : '展开全部' }}
          </span>
        </button>
        <article
          v-for="t in visibleClosed"
          :key="t.id"
          class="li-task"
          :class="t.status"
        >
          <div class="li-task-head">
            <span class="li-status" :class="t.status">
              <i aria-hidden="true" />{{ STATUS[t.status] || t.status }}
            </span>
            <span class="li-task-title" :title="t.title || t.id">
              {{ t.title || '未命名导入' }}
            </span>
            <span class="li-task-count">{{ t.done }} / {{ t.total }} 集</span>
            <span class="li-task-time">{{ fmtSecs(t.elapsed_secs) }}</span>
            <span class="li-task-actions">
              <button v-if="t.failed" type="button" class="li-link"
                      @click="actTask(t.id, 'retry')">重试失败</button>
              <button type="button" class="li-link" @click="toggleItems(t.id)">
                {{ expanded.has(t.id) ? '收起' : '分集' }}
              </button>
            </span>
          </div>
          <ul v-if="expanded.has(t.id)" class="li-items">
            <li v-for="(it, i) in t.items" :key="it.id" :class="it.status">
              <span class="li-item-dot" aria-hidden="true" />
              <span class="li-item-index">{{ i + 1 }}</span>
              <span class="li-item-title" :title="it.url">{{ it.title || it.url }}</span>
              <span class="li-item-meta" :title="itemMeta(t, it)">{{ itemMeta(t, it) }}</span>
              <span class="li-item-actions">
                <button v-if="it.status === 'done' && it.node_id" type="button"
                        class="li-link" @click="openPreview(it)">预览</button>
                <button v-if="it.status === 'failed'" type="button"
                        class="li-link" @click="actItem(t, it, 'retry')">重试</button>
              </span>
            </li>
          </ul>
        </article>
      </section>

      <p v-if="openTasks.length || closedTasks.length" class="li-foot">
        任务在后台继续，可以离开本页；完成时会弹提示。
        <button type="button" class="li-link" @click="emit('go-kb')">去知识库</button>
      </p>
    </main>

    <!-- B 站扫码登录 -->
    <el-dialog v-model="qr.visible" title="登录 B 站" width="300px" align-center
               append-to-body @closed="stopQrTimer">
      <div class="li-qr">
        <img v-if="qr.image" :src="qr.image" alt="B 站登录二维码" class="li-qr-img" />
        <div v-else class="li-qr-empty">二维码生成中…</div>
        <p class="li-qr-status" :class="qr.status">{{ qrStatusText() }}</p>
        <p class="li-hint">
          手机 B 站 App →「我的」→ 扫一扫。凭证只保存在服务端，
          会员课与 AI 总结字幕需要它。
        </p>
        <el-button v-if="['expired', 'error'].includes(qr.status)" size="small"
                   @click="openBiliLogin">刷新二维码</el-button>
      </div>
    </el-dialog>

    <KbTextPreviewDialog
      v-model:visible="preview.visible"
      :name="preview.name"
      file-type=""
      :markdown="preview.markdown"
      :chars="preview.chars"
      :total-chars="preview.totalChars"
      :truncated="preview.truncated"
      :loading="preview.loading"
    />
  </div>
</template>

<style scoped>
.li {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

/* ── 页头 ── */
.li-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}
.li-head .page-sub { max-width: 52ch; }

/* 次要按钮统一形态：无边框文字，hover 才有底色（避免一行里堆三种按钮样式） */
.li-ghost {
  flex-shrink: 0;
  padding: 6px 10px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--color-text-secondary);
  font-family: inherit;
  font-size: var(--font-size-xs);
  cursor: pointer;
  transition: background 0.15s, color 0.15s, border-color 0.15s;
}
.li-ghost:hover:not(:disabled) {
  background: var(--color-bg-hover);
  color: var(--color-text-primary);
  border-color: var(--color-border-strong);
}
.li-ghost:disabled { opacity: 0.45; cursor: default; }

.li-link {
  padding: 0;
  border: none;
  background: transparent;
  color: var(--color-accent);
  font-family: inherit;
  font-size: var(--font-size-xs);
  cursor: pointer;
}
.li-link:hover { text-decoration: underline; }
.li-link.danger { color: var(--color-danger); }

.li-hint {
  font-size: var(--font-size-xs);
  color: var(--color-text-tertiary);
}

/* ── 环境提示：只有缺口才出现 ── */
.li-warn {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-radius: var(--radius-md);
  background: var(--color-warning-soft);
  color: var(--color-text-secondary);
  font-size: var(--font-size-xs);
}
.li-warn-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--color-warning);
  flex-shrink: 0;
}

/* ── 粘贴区 ── */
.li-compose {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  background: var(--color-bg-card);
}
.li-compose-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
.li-compose-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

/* ── 卡片（预检 / 队列） ── */
.li-card {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  background: var(--color-bg-card);
  padding: 14px;
}
.li-card-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}
.li-card-head h3 {
  margin: 0;
  font-size: var(--font-size-base);
  font-weight: 600;
  color: var(--color-text-primary);
}
.li-card-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--color-border-soft);
}

/* ── 预检结果 ── */
.li-report {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.li-report li {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  font-size: var(--font-size-xs);
}
.li-report-title {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--color-text-primary);
}
.li-report-meta {
  flex-shrink: 0;
  color: var(--color-text-tertiary);
}

/* ── 队列：任务行 ── */
.li-task + .li-task {
  margin-top: 14px;
  padding-top: 14px;
  border-top: 1px solid var(--color-border-soft);
}
.li-task-head {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: var(--font-size-xs);
  min-width: 0;
}
.li-status {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  flex-shrink: 0;
  color: var(--color-text-tertiary);
}
.li-status i {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
}
.li-status.running { color: var(--color-accent); }
.li-status.done { color: var(--color-success); }
.li-status.failed { color: var(--color-danger); }
.li-status.paused { color: var(--color-warning); }
.li-task.running .li-task-title { font-weight: 600; }

.li-task-title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--color-text-primary);
}
.li-task-count,
.li-task-time {
  flex-shrink: 0;
  color: var(--color-text-tertiary);
  font-variant-numeric: tabular-nums;
}
.li-task-actions {
  display: inline-flex;
  gap: 10px;
  flex-shrink: 0;
}

.li-bar {
  height: 3px;
  margin: 8px 0;
  border-radius: var(--radius-full);
  background: var(--color-bg-disabled);
  overflow: hidden;
}
.li-bar i {
  display: block;
  height: 100%;
  background: var(--color-accent);
  transition: width 0.3s ease;
}
.li-task.failed .li-bar i { background: var(--color-danger); }
.li-task.paused .li-bar i { background: var(--color-warning); }

/* 正在跑的那一集 */
.li-now {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 8px 10px;
  border-radius: var(--radius-md);
  background: var(--color-bg-hover);
  font-size: var(--font-size-xs);
  color: var(--color-text-secondary);
}
.li-now-title b {
  font-weight: 600;
  color: var(--color-text-primary);
}
.li-steps {
  display: flex;
  align-items: center;
  gap: 0;
  list-style: none;
  margin: 0;
  padding: 0;
  font-size: var(--font-size-xs);
  color: var(--color-text-tertiary);
}
.li-steps li {
  position: relative;
  display: flex;
  align-items: center;
  gap: 4px;
}
.li-steps li + li::before {
  content: '';
  width: 14px;
  height: 1px;
  margin: 0 4px;
  background: var(--color-border);
}
.li-steps li::marker { content: ''; }
/* 状态不只靠颜色区分：当前步加粗、失败步加下划线 */
.li-steps li.active { color: var(--color-accent); font-weight: 600; }
.li-steps li.done { color: var(--color-success); }
.li-steps li.failed { color: var(--color-danger); text-decoration: underline; }

.li-items-toggle {
  margin-top: 2px;
  padding: 0;
  border: none;
  background: transparent;
  color: var(--color-text-tertiary);
  font-family: inherit;
  font-size: var(--font-size-xs);
  cursor: pointer;
}
.li-items-toggle:hover { color: var(--color-text-primary); }

/* 分集列表 */
.li-items {
  list-style: none;
  margin: 8px 0 0;
  padding: 0;
}
.li-items li {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 0;
  font-size: var(--font-size-xs);
  color: var(--color-text-secondary);
}
.li-item-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--color-border-strong);
  flex-shrink: 0;
}
.li-items li.done .li-item-dot { background: var(--color-success); }
.li-items li.failed .li-item-dot { background: var(--color-danger); }
.li-items li.running .li-item-dot { background: var(--color-accent); }
.li-item-index {
  width: 18px;
  flex-shrink: 0;
  color: var(--color-text-tertiary);
  font-variant-numeric: tabular-nums;
}
.li-item-title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.li-item-meta {
  flex-shrink: 0;
  max-width: 40%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--color-text-tertiary);
}
.li-items li.failed .li-item-meta { color: var(--color-danger); }
.li-item-actions {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}
.li-move {
  width: 18px;
  height: 18px;
  padding: 0;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--color-text-tertiary);
  font-size: 11px;
  line-height: 1;
  cursor: pointer;
}
.li-move:hover:not(:disabled) { background: var(--color-bg-hover); color: var(--color-text-primary); }
.li-move:disabled { opacity: 0.35; cursor: default; }

/* ── 历史任务 ── */
.li-history { padding: 10px 14px; }
.li-history-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  padding: 2px 0;
  border: none;
  background: transparent;
  color: var(--color-text-secondary);
  font-family: inherit;
  font-size: var(--font-size-xs);
  cursor: pointer;
}
.li-history-head:hover { color: var(--color-text-primary); }

.li-foot {
  margin: 0;
  font-size: var(--font-size-xs);
  color: var(--color-text-tertiary);
  display: flex;
  gap: 8px;
  align-items: center;
}

/* ── 扫码登录 ── */
.li-qr {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  text-align: center;
}
.li-qr-img {
  width: 200px;
  height: 200px;
  border-radius: var(--radius-md);
  background: #fff;
}
.li-qr-empty {
  width: 200px;
  height: 200px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px dashed var(--color-border);
  border-radius: var(--radius-md);
  font-size: var(--font-size-xs);
  color: var(--color-text-tertiary);
}
.li-qr-status {
  margin: 0;
  font-size: var(--font-size-xs);
  color: var(--color-text-secondary);
}
.li-qr-status.scanned { color: var(--color-accent); }
.li-qr-status.success { color: var(--color-success); }
.li-qr-status.expired,
.li-qr-status.error { color: var(--color-danger); }
</style>
