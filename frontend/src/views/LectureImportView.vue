<script setup>
/**
 * LectureImportView.vue — 视频课程导入页（链接 → 讲义 → 知识库）
 *
 * 取词逻辑全在后端（`core/kb/video_import.py`）：平台字幕优先 → yt-dlp 字幕 →
 * 切段语音转写。本页只做三件事：提交、显示进度、给出结果入口。
 *
 * 为什么要有步骤条：导入是分钟级后台任务（下载 → 抽音轨 → 分段转写 → 整理），
 * 提交后请求就返回了。没有进度时用户看到的只有"提交中"一闪，然后长时间毫无反馈，
 * 无法区分"在跑"和"挂了"。进度经 SSE `import_progress` 到达（**瞬态**，不落库），
 * 终态仍走 `kb_updated` / `error`（落库，离线也能补拉）。
 *
 * 状态只有一份：`store.importProgress`（chatStore 的 SSE 分支写入），本页 watch 它。
 * 不在本页自建 EventSource —— 全局只有一条长连接。
 */
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { useChatStore } from '../stores/chatStore'
import {
  biliLoginPoll, biliLoginQrcode, getKbNodeText, importLecture, probeLectureImport,
} from '../api/kb.js'
import KbTextPreviewDialog from '../components/KbTextPreviewDialog.vue'

const emit = defineEmits(['go-kb'])

const store = useChatStore()

/** 步骤条：key 与后端 `video_import.STAGE_*` 一一对应（顺序即执行顺序） */
const STEPS = [
  { key: 'subtitle', label: '查找字幕', hint: 'B站字幕轨 / AI 总结 / 平台字幕文件' },
  { key: 'download', label: '下载音频', hint: 'yt-dlp 拉取音轨（无字幕时才走）' },
  { key: 'extract', label: '提取音轨', hint: 'ffmpeg 转 16kHz 单声道' },
  { key: 'transcribe', label: '语音转写', hint: '按 2 分钟切段，逐段转写' },
  { key: 'compose', label: '整理讲义', hint: '分段 / 时间锚点 / 标题章节' },
]

const url = ref('')
const source = ref('auto')
const showAdvanced = ref(false)
const langInput = ref('')
const asrModelInput = ref('')
const deps = ref(null)
const submitting = ref(false)
const elapsed = ref(0)

/** task = { id, url, stage, phase: running|done|failed, visited[], index, total, error, result } */
const task = ref(null)
let clock = null

const preview = reactive({
  visible: false, loading: false, name: '', markdown: '', chars: 0, totalChars: 0,
  truncated: false,
})

const running = computed(() => task.value?.phase === 'running')

/** 登录态来源的展示文案（后端 probe 的 cookie_source：env / file / browser） */
function cookieText() {
  const d = deps.value
  if (!d) return '未知'
  if (!d.cookie) return '未配置'
  const map = {
    env: '已配置（手工）',
    file: '已配置（文件）',
    saved: '已登录（扫码）',
    browser: `自动（${d.cookie_browser || '浏览器'}，导入时读取）`,
  }
  return map[d.cookie_source] || '已配置'
}

/** 刷新依赖探测（失败当"未知"，不阻断提交） */
async function probe() {
  try {
    const { data } = await probeLectureImport()
    deps.value = data
  } catch {
    deps.value = null
  }
  return deps.value
}

// ── B 站扫码登录（凭证只留在服务端；这里只有二维码与状态）──
const qr = reactive({ visible: false, loading: false, key: '', image: '', status: 'waiting', message: '' })
let qrTimer = null

function stopQrTimer() {
  if (qrTimer) clearInterval(qrTimer)
  qrTimer = null
}

function qrStatusText() {
  const map = {
    waiting: '等待扫码…',
    scanned: '已扫码，请在手机上确认',
    expired: '二维码已失效',
    error: qr.message || '二维码获取失败',
    success: '登录成功',
  }
  return map[qr.status] || qr.message || ''
}

async function openBiliLogin() {
  stopQrTimer()
  qr.visible = true
  qr.loading = true
  qr.image = ''
  qr.key = ''
  qr.status = 'waiting'
  qr.message = ''
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
      ElMessage.success('B 站登录成功：会员课与 AI 总结字幕已可用')
    } else if (data.status === 'expired' || data.status === 'error') {
      stopQrTimer()
    }
  } catch {
    // 单次轮询失败（网络抖动）不终止，下一轮会再来
  }
}

function startClock() {
  stopClock()
  elapsed.value = 0
  clock = setInterval(() => { elapsed.value += 1 }, 1000)
}

function stopClock() {
  if (clock) clearInterval(clock)
  clock = null
}

function elapsedText() {
  const m = Math.floor(elapsed.value / 60)
  const s = elapsed.value % 60
  return m ? `${m} 分 ${s} 秒` : `${s} 秒`
}

/** 该步骤当前的状态：pending / active / done / skipped / failed */
function stepState(step) {
  const t = task.value
  if (!t) return 'pending'
  const idx = STEPS.findIndex((s) => s.key === step.key)
  const visited = t.visited.includes(step.key)
  if (t.phase === 'done') return visited ? 'done' : 'skipped'
  const cur = STEPS.findIndex((s) => s.key === t.stage)
  if (t.phase === 'failed') {
    if (step.key === t.stage) return 'failed'
    return idx < cur && visited ? 'done' : 'pending'
  }

  if (idx === cur) return 'active'
  if (idx < cur) return visited ? 'done' : 'skipped'
  return 'pending'
}

function stepNote(step) {
  const t = task.value
  if (!t) return step.hint
  if (t.phase === 'running' && step.key === t.stage && step.key === 'transcribe' && t.total) {
    return `第 ${t.index} / ${t.total} 段`
  }
  const state = stepState(step)
  if (state === 'skipped') return '跳过（已有字幕）'
  if (state === 'done') return '完成'
  if (state === 'failed') return '失败'
  return step.hint
}

watch(() => store.importProgress, (p) => {
  if (!p || !task.value || (p.task_id && p.task_id !== task.value.id)) return
  const t = task.value
  if (p.stage === 'done') {
    t.phase = 'done'
    t.result = {
      nodeId: p.node_id, filename: p.filename || '',
      title: p.title || '', source: p.source || '', chars: p.chars || 0,
    }
    t.stage = 'compose'
    stopClock()
    url.value = ''
    // 成功提示由 chatStore.handleKbUpdated 统一弹（kb_updated 到达时），这里不再重复
    return
  }
  if (p.stage === 'failed') {
    // 保持 t.stage 不变：失败点就是"当时正在跑的那一步"，步骤条据此标红
    t.phase = 'failed'
    t.error = p.message || '导入失败'
    stopClock()
    return
  }
  t.stage = p.stage
  if (!t.visited.includes(p.stage)) t.visited.push(p.stage)
  if (p.index) t.index = p.index
  if (p.total) t.total = p.total
})

async function submit() {
  const link = url.value.trim()
  if (!link || submitting.value || running.value) return

  submitting.value = true
  try {
    // 依赖够不够**只由后端判定**（`video_import.missing_requirements`：按这条链接 +
    // 这个模式逐条查）。前端这里曾复制一份 `!d.yt_dlp` 硬拦，结果是双源漂移 ——
    // B 站平台字幕直取本来零依赖就能跑，却被判成不可用。真缺东西时后端返 400，
    // detail 由下面的 catch 弹出来。
    const payload = { url: link, transcript_source: source.value }
    const langs = langInput.value.split(/[,，\s]+/).filter(Boolean)
    if (langs.length) payload.languages = langs
    if (asrModelInput.value.trim()) payload.asr_model = asrModelInput.value.trim()

    const { data } = await importLecture(payload)
    const startStage = source.value === 'asr' ? 'download' : 'subtitle'
    task.value = {
      id: data?.task_id || '', url: link, stage: startStage, phase: 'running',
      visited: [startStage], index: 0, total: 0, error: '', result: null,
    }
    startClock()
  } catch (e) {
    ElMessage.error('导入失败：' + (e.response?.data?.detail || e.message))
  } finally {
    submitting.value = false
  }
}

function retry() {
  const link = task.value?.url
  task.value = null
  elapsed.value = 0
  if (link) {
    url.value = link
    submit()
  }
}

function resetTask() {
  task.value = null
  elapsed.value = 0
  stopClock()
}

async function openPreview() {
  const nodeId = task.value?.result?.nodeId
  if (!nodeId) return
  preview.visible = true
  preview.loading = true
  preview.name = task.value.result.filename
  preview.markdown = ''
  try {
    const { data } = await getKbNodeText(nodeId)
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

onBeforeUnmount(() => {
  stopClock()
  stopQrTimer()
})
probe()
</script>

<template>
  <div class="lecture-layout">
    <main class="lecture-inner">
      <header class="lc-header">
        <h1>视频课程导入</h1>
      </header>

      <!-- ── 依赖自检 ── -->
      <div class="lc-deps" :class="{ unknown: !deps }">
        <template v-if="deps">
          <span class="lc-dep" :class="deps.yt_dlp ? 'ok' : 'bad'">
            <i class="dot" />yt-dlp {{ deps.yt_dlp ? '就绪' : '缺失' }}
          </span>
          <span class="lc-dep" :class="deps.ffmpeg ? 'ok' : 'warn'">
            <i class="dot" />ffmpeg {{ deps.ffmpeg ? '就绪' : '缺失（仅转写需要）' }}
          </span>
          <span class="lc-dep" :class="deps.asr_model ? 'ok' : 'warn'">
            <i class="dot" />ASR {{ deps.asr_model || '未配置' }}
          </span>
          <span class="lc-dep" :class="deps.cookie ? 'ok' : 'warn'">
            <i class="dot" />B站登录态 {{ cookieText() }}
          </span>
          <span v-if="!deps.yt_dlp" class="lc-fix">
            安装：<code>pip install yt-dlp</code>
          </span>
          <span v-else-if="!deps.ffmpeg" class="lc-fix">
            转写才需要：<code>winget install Gyan.FFmpeg</code>
          </span>
          <span v-else-if="!deps.asr_model" class="lc-fix">
            无字幕课程需要：<code>.env</code> 里设 <code>ASR_MODEL</code>
          </span>
          <span v-else-if="!deps.cookie" class="lc-fix">
            B 站会员课 / AI 总结字幕需要登录态：<code>.env</code> 设
            <code>BILI_COOKIES_FROM_BROWSER=edge</code>（自动读浏览器）或 <code>BILI_COOKIE</code>
          </span>
        </template>
        <span v-else class="lc-dep warn"><i class="dot" />依赖状态获取失败（不影响提交）</span>
        <span class="lc-deps-actions">
          <button type="button" class="lc-reprobe" @click="openBiliLogin">扫码登录 B 站</button>
          <button type="button" class="lc-reprobe" @click="probe">重新探测</button>
        </span>
      </div>

      <!-- ── 导入 ── -->
      <section class="lc-card">
        <div class="lc-row">
          <el-input
            v-model="url"
            placeholder="粘贴课程链接（B站 / YouTube / b23.tv）"
            maxlength="2000"
            clearable
            size="large"
            :disabled="running"
            @keyup.enter="submit"
          />
          <el-button
            type="primary"
            size="large"
            :loading="submitting"
            :disabled="!url.trim() || running"
            @click="submit"
          >
            {{ running ? '导入中' : '开始导入' }}
          </el-button>
        </div>

        <div class="lc-sources">
          <button
            v-for="opt in [
              { key: 'auto', title: '自动', desc: '字幕优先，没有才转写' },
              { key: 'subtitle', title: '仅字幕', desc: '没有字幕就报错，不花额度' },
              { key: 'asr', title: '语音转写', desc: '跳过字幕，直接从音频转写' },
            ]"
            :key="opt.key"
            type="button"
            class="lc-source"
            :class="{ active: source === opt.key }"
            :disabled="running"
            @click="source = opt.key"
          >
            <span class="lc-source-title">{{ opt.title }}</span>
            <span class="lc-source-desc">{{ opt.desc }}</span>
          </button>
        </div>

        <details class="lc-advanced" :open="showAdvanced" @toggle="showAdvanced = $event.target.open">
          <summary>高级选项</summary>
          <div class="lc-adv-row">
            <label>
              字幕语言优先级
              <el-input v-model="langInput" size="small" placeholder="留空＝中文优先（zh-Hans, zh-CN, zh, en）" />
            </label>
            <label>
              转写模型
              <el-input v-model="asrModelInput" size="small" placeholder="留空＝.env 的 ASR_MODEL" />
            </label>
          </div>
        </details>
      </section>

      <!-- ── 任务进度 ── -->
      <section v-if="task" class="lc-card lc-task" :class="task.phase">
        <header class="lc-task-head">
          <span class="lc-task-title">
            <template v-if="task.phase === 'running'">正在导入</template>
            <template v-else-if="task.phase === 'done'">导入完成</template>
            <template v-else>导入失败</template>
            <span class="lc-task-elapsed">{{ elapsedText() }}</span>
          </span>
          <span class="lc-task-url" :title="task.url">{{ task.url }}</span>
        </header>

        <ol class="lc-steps">
          <li v-for="step in STEPS" :key="step.key" class="lc-step" :class="stepState(step)">
            <span class="lc-step-mark" />
            <span class="lc-step-body">
              <span class="lc-step-label">{{ step.label }}</span>
              <span class="lc-step-note">{{ stepNote(step) }}</span>
            </span>
          </li>
        </ol>

        <p v-if="task.phase === 'running'" class="lc-task-foot">
          任务在后台继续，可以离开本页；完成时会弹提示。
        </p>

        <div v-else-if="task.phase === 'failed'" class="lc-task-foot lc-fail">
          <span>{{ task.error }}</span>
          <el-button size="small" type="primary" plain @click="retry">重试</el-button>
          <el-button size="small" text @click="resetTask">关闭</el-button>
        </div>

        <div v-else class="lc-result">
          <div class="lc-result-main">
            <div class="lc-result-title">{{ task.result.title || task.result.filename }}</div>
            <div class="lc-result-meta">
              <span v-if="task.result.source" class="lc-badge">{{ task.result.source }}</span>
              <span v-if="task.result.chars" class="lc-meta-item">{{ task.result.chars }} 字</span>
              <span class="lc-meta-item">{{ task.result.filename }}</span>
            </div>
          </div>
          <div class="lc-result-actions">
            <el-button size="small" @click="openPreview">预览讲义</el-button>
            <el-button size="small" type="primary" @click="emit('go-kb')">去知识库</el-button>
            <el-button size="small" text @click="resetTask">再导一个</el-button>
          </div>
        </div>
      </section>

      <!-- ── 策略说明 ── -->
      <section class="lc-notes">
        <h3>它会怎么取词</h3>
        <ul>
          <li><b>B 站</b>：直取平台字幕轨；轨为空时再查「AI 总结字幕」（实测经常只有后者有内容）—— 都不下载、不花额度。</li>
          <li><b>其他站点</b>：yt-dlp 拉字幕文件（自动字幕会去掉滚动重复的碎片）。</li>
          <li><b>都没有</b>：下载音频 → ffmpeg 转 16kHz → 按 2 分钟切段 → 逐段转写，每段留 <code>[hh:mm:ss]</code> 锚点。</li>
          <li>讲义统一带标题、来源与 10 分钟章节，方便回视频定位。</li>
        </ul>
      </section>
    </main>

    <!-- B 站扫码登录：凭证只落服务端，前端只拿二维码与状态 -->
    <el-dialog
      v-model="qr.visible"
      title="B 站扫码登录"
      width="300px"
      align-center
      append-to-body
      @closed="stopQrTimer"
    >
      <div class="lc-qr">
        <img v-if="qr.image" :src="qr.image" alt="B 站登录二维码" class="lc-qr-img" />
        <div v-else class="lc-qr-empty">二维码生成中…</div>
        <p class="lc-qr-status" :class="qr.status">{{ qrStatusText() }}</p>
        <p class="lc-qr-hint">手机 B 站 App →「我的」→ 扫一扫。登录态只保存在服务端：会员课与 AI 总结字幕需要它。</p>
        <el-button
          v-if="qr.status === 'expired' || qr.status === 'error'"
          size="small"
          @click="openBiliLogin"
        >刷新二维码</el-button>
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
.lecture-layout {
  flex: 1;
  height: 100%;
  min-width: 0;
  overflow-y: auto;
}

.lecture-inner {
  max-width: 780px;
  margin: 0 auto;
  padding: 28px 32px 60px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.lc-header h2 {
  margin: 0 0 6px;
  font-size: 20px;
  font-weight: 600;
  color: var(--color-text-primary);
}

.lc-subtitle {
  margin: 0;
  font-size: 13px;
  color: var(--color-text-tertiary);
  line-height: 1.6;
}

/* ── 依赖自检 ── */
.lc-deps {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px 16px;
  font-size: 12px;
  color: var(--color-text-tertiary);
}

.lc-dep {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.lc-dep .dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
  opacity: 0.7;
}

.lc-dep.ok { color: var(--color-green); }
.lc-dep.warn { color: var(--color-orange); }
.lc-dep.bad { color: var(--color-red); }

.lc-fix { color: var(--color-text-tertiary); }
.lc-fix code {
  padding: 1px 5px;
  border-radius: 4px;
  background: var(--color-bg-subtle);
  font-size: 11px;
}

.lc-deps-actions {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  gap: 14px;
}

.lc-reprobe {
  border: none;
  background: transparent;
  padding: 0;
  color: var(--color-text-tertiary);
  font-size: 12px;
  font-family: inherit;
  cursor: pointer;
  text-decoration: underline;
}
.lc-reprobe:hover { color: var(--color-text-primary); }

/* ── 扫码登录 ── */
.lc-qr {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  text-align: center;
}

.lc-qr-img {
  width: 200px;
  height: 200px;
  border-radius: 8px;
  background: #fff;
}

.lc-qr-empty {
  width: 200px;
  height: 200px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px dashed var(--color-border);
  border-radius: 8px;
  font-size: 12px;
  color: var(--color-text-tertiary);
}

.lc-qr-status {
  margin: 0;
  font-size: 13px;
  color: var(--color-text-secondary);
}
.lc-qr-status.scanned { color: var(--color-primary); }
.lc-qr-status.success { color: var(--color-green); }
.lc-qr-status.expired,
.lc-qr-status.error { color: var(--color-red); }

.lc-qr-hint {
  margin: 0;
  font-size: 11px;
  line-height: 1.6;
  color: var(--color-text-tertiary);
}

/* ── 卡片与表单 ── */
.lc-card {
  background: var(--color-bg-surface);
  border: 1px solid var(--color-border);
  border-radius: 12px;
  padding: 16px;
}

.lc-row {
  display: flex;
  gap: 8px;
  align-items: center;
}
.lc-row :deep(.el-input) { flex: 1; min-width: 0; }

.lc-sources {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
  margin-top: 14px;
}

.lc-source {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 10px 12px;
  text-align: left;
  border: 1px solid var(--color-border);
  border-radius: 10px;
  background: transparent;
  color: var(--color-text-secondary);
  font-family: inherit;
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s;
}
.lc-source:hover:not(:disabled) { border-color: var(--color-border-strong); }
.lc-source.active {
  border-color: var(--color-primary);
  background: var(--color-primary-subtle);
  color: var(--color-text-primary);
}
.lc-source:disabled { cursor: not-allowed; opacity: 0.6; }

.lc-source-title { font-size: 13px; font-weight: 600; }
.lc-source-desc { font-size: 11px; color: var(--color-text-tertiary); line-height: 1.4; }

.lc-advanced {
  margin-top: 12px;
  font-size: 12px;
  color: var(--color-text-tertiary);
}
.lc-advanced summary { cursor: pointer; width: fit-content; }
.lc-adv-row {
  display: flex;
  gap: 12px;
  margin-top: 10px;
  flex-wrap: wrap;
}
.lc-adv-row label {
  flex: 1 1 240px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

/* ── 任务 ── */
.lc-task-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin-bottom: 14px;
}
.lc-task-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--color-text-primary);
}
.lc-task-elapsed {
  margin-left: 8px;
  font-size: 12px;
  font-weight: 400;
  color: var(--color-text-tertiary);
}
.lc-task-url {
  font-size: 11px;
  color: var(--color-text-tertiary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.lc-steps {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.lc-step {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 6px 0;
  position: relative;
}

/* 竖线：连接相邻步骤（最后一步不画） */
.lc-step:not(:last-child)::before {
  content: '';
  position: absolute;
  left: 4px;
  top: 20px;
  bottom: -4px;
  width: 1px;
  background: var(--color-border);
}

.lc-step-mark {
  flex-shrink: 0;
  width: 9px;
  height: 9px;
  margin-top: 4px;
  border-radius: 50%;
  border: 2px solid var(--color-border-strong);
  background: var(--color-bg-surface);
}

.lc-step-body { display: flex; flex-direction: column; gap: 1px; }
.lc-step-label { font-size: 13px; color: var(--color-text-secondary); }
.lc-step-note { font-size: 11px; color: var(--color-text-tertiary); }

.lc-step.active .lc-step-mark {
  border-color: var(--color-primary);
  background: var(--color-primary);
  box-shadow: 0 0 0 3px var(--color-primary-subtle);
  animation: lc-pulse 1.6s ease-in-out infinite;
}
.lc-step.active .lc-step-label { color: var(--color-text-primary); font-weight: 600; }

.lc-step.done .lc-step-mark {
  border-color: var(--color-green);
  background: var(--color-green);
}
.lc-step.failed .lc-step-mark {
  border-color: var(--color-red);
  background: var(--color-red);
}
.lc-step.skipped .lc-step-label,
.lc-step.skipped .lc-step-note { opacity: 0.5; }

@keyframes lc-pulse {
  0%, 100% { box-shadow: 0 0 0 3px var(--color-primary-subtle); }
  50% { box-shadow: 0 0 0 6px transparent; }
}

.lc-task-foot {
  margin: 12px 0 0;
  font-size: 12px;
  color: var(--color-text-tertiary);
}

.lc-fail {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  color: var(--color-red);
}

/* ── 结果 ── */
.lc-result {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
  margin-top: 14px;
  padding-top: 14px;
  border-top: 1px solid var(--color-border);
}

.lc-result-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--color-text-primary);
  line-height: 1.4;
}

.lc-result-meta {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-top: 5px;
  font-size: 12px;
  color: var(--color-text-tertiary);
}

.lc-badge {
  padding: 1px 7px;
  border-radius: 999px;
  background: var(--color-primary-subtle);
  color: var(--color-primary);
  font-size: 11px;
}

.lc-result-actions { display: flex; gap: 8px; align-items: center; }

/* ── 说明 ── */
.lc-notes {
  padding: 14px 16px;
  border-radius: 12px;
  background: var(--color-bg-subtle);
  font-size: 13px;
  color: var(--color-text-secondary);
  line-height: 1.7;
}

.lc-notes h3 {
  margin: 0 0 6px;
  font-size: 13px;
  font-weight: 600;
  color: var(--color-text-primary);
}

.lc-notes ul { margin: 0; padding-left: 18px; }
.lc-notes code {
  padding: 1px 4px;
  border-radius: 4px;
  background: var(--color-bg-surface);
  font-size: 11px;
}
</style>
