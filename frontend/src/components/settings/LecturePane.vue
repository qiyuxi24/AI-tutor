<script setup>
/**
 * LecturePane.vue — 设置 · 视频课程导入
 *
 * 三层内容都收在这里：
 *   ① 导入偏好：取词方式 / 合集展开 / 幻灯片截图 / 字幕语言 / 转写模型
 *      （本机偏好，localStorage，见 `utils/lectureSettings.js`，不入后端）
 *   ② 运行环境自检：yt-dlp / ffmpeg / ASR 模型（后端 `GET /kb/import/probe`）
 *   ③ B 站扫码登录：凭证只落服务端 `data/bili/cookies.txt`，这里只有二维码与状态
 *
 * 为什么都在设置里：这些是"一次配好、长期复用"的事，不是每次导入都要重新确认的
 * 决定 —— 导入页只留「贴链接 → 加入队列」。能否提交仍由后端按链接判定，这里只展示。
 */
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { biliLoginPoll, biliLoginQrcode, probeLectureImport } from '../../api/kb.js'
import { TRANSCRIPT_SOURCES, useLectureSettings } from '../../utils/lectureSettings'

const settings = useLectureSettings()

const currentSource = computed(
  () => TRANSCRIPT_SOURCES.find((s) => s.key === settings.transcriptSource) || TRANSCRIPT_SOURCES[0],
)

// ── 运行环境自检（后端 probe；失败当"未知"，不阻断任何操作）──
const deps = ref(null)
const probing = ref(false)

async function probe() {
  probing.value = true
  try {
    const { data } = await probeLectureImport()
    deps.value = data
  } catch {
    deps.value = null
  } finally {
    probing.value = false
  }
}

onMounted(probe)

/** 登录态来源的展示文案（probe 的 cookie_source：env / file / saved / browser） */
function cookieText() {
  const d = deps.value
  if (!d) return '未知'
  if (!d.cookie) return '未登录'
  const map = {
    env: '已配置（手工）',
    file: '已配置（文件）',
    saved: '已登录（扫码）',
    browser: `自动（${d.cookie_browser || '浏览器'}，导入时读取）`,
  }
  return map[d.cookie_source] || '已配置'
}

// ── B 站扫码登录（凭证只留在服务端；这里只有二维码与状态）──
const qr = reactive({ visible: false, key: '', image: '', status: 'waiting', message: '' })
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

onBeforeUnmount(stopQrTimer)
</script>

<template>
  <div class="lecture-pane">
    <section class="sc-section">
      <h3>视频课程导入</h3>
      <p class="usage-hint">
        这里的选项决定导入页怎么取词、要不要展开合集与截图。改一次即全局生效（仅本机浏览器）。
      </p>

      <div class="sc-row">
        <div class="sc-row-info">
          <div class="sc-row-title">取词方式</div>
          <div class="sc-row-desc">{{ currentSource.desc }}</div>
        </div>
        <div class="theme-toggle-group">
          <button
            v-for="opt in TRANSCRIPT_SOURCES"
            :key="opt.key"
            type="button"
            class="theme-option"
            :class="{ active: settings.transcriptSource === opt.key }"
            :title="opt.desc"
            @click="settings.transcriptSource = opt.key"
          >{{ opt.title }}</button>
        </div>
      </div>

      <div class="sc-row">
        <div class="sc-row-info">
          <div class="sc-row-title">合集全量</div>
          <div class="sc-row-desc">B 站多 P / 合集的链接一次导入全部集数，落进以合集命名的文件夹</div>
        </div>
        <el-switch v-model="settings.expand" />
      </div>

      <div class="sc-row">
        <div class="sc-row-info">
          <div class="sc-row-title">幻灯片截图</div>
          <div class="sc-row-desc">画面变化时抽帧去重，图片插到对应讲解段落前（需下载视频，慢一些）</div>
        </div>
        <el-switch v-model="settings.slides" />
      </div>

      <div class="sc-row sc-row--stack">
        <div class="sc-row-info">
          <div class="sc-row-title">字幕语言优先级</div>
          <div class="sc-row-desc">留空＝中文优先（zh-Hans, zh-CN, zh, en）</div>
        </div>
        <el-input
          v-model="settings.languages"
          size="small"
          class="sc-field"
          placeholder="zh-Hans, zh-CN, zh, en"
        />
      </div>

      <div class="sc-row sc-row--stack">
        <div class="sc-row-info">
          <div class="sc-row-title">转写模型</div>
          <div class="sc-row-desc">留空＝服务端 <code>.env</code> 的 <code>ASR_MODEL</code></div>
        </div>
        <el-input
          v-model="settings.asrModel"
          size="small"
          class="sc-field"
          placeholder="如 whisper-1 / qwen3-asr-flash"
        />
      </div>
    </section>

    <!-- 运行环境自检 -->
    <section class="sc-section">
      <h3>运行环境</h3>
      <p class="usage-hint">
        导入前看一次即可：缺什么这里会写明怎么补。能否提交由后端按链接判定，这里只做展示。
      </p>

      <div class="sc-row">
        <div class="sc-row-info">
          <div class="sc-row-title">yt-dlp</div>
          <div class="sc-row-desc">
            {{ deps
              ? (deps.yt_dlp ? '下载音轨与幻灯片截图需要' : '缺失：安装 pip install yt-dlp')
              : '状态获取失败，点下方「重新探测」' }}
          </div>
        </div>
        <span v-if="deps" class="dep-state" :class="deps.yt_dlp ? 'ok' : 'bad'">
          <i class="dep-dot" />{{ deps.yt_dlp ? '就绪' : '缺失' }}
        </span>
      </div>

      <div class="sc-row">
        <div class="sc-row-info">
          <div class="sc-row-title">ffmpeg</div>
          <div class="sc-row-desc">
            {{ deps
              ? (deps.ffmpeg ? '提取音轨 / 幻灯片截图需要' : '缺失：转写与截图都要（winget install Gyan.FFmpeg）')
              : '状态获取失败，点下方「重新探测」' }}
          </div>
        </div>
        <span v-if="deps" class="dep-state" :class="deps.ffmpeg ? 'ok' : 'warn'">
          <i class="dep-dot" />{{ deps.ffmpeg ? '就绪' : '缺失' }}
        </span>
      </div>

      <div class="sc-row">
        <div class="sc-row-info">
          <div class="sc-row-title">转写模型（ASR）</div>
          <div class="sc-row-desc">{{ deps && deps.asr_model ? '无字幕课程才会用到' : '未配置：无字幕课程需要，在服务端 .env 设 ASR_MODEL' }}</div>
        </div>
        <span v-if="deps" class="dep-state" :class="deps.asr_model ? 'ok' : 'warn'">
          <i class="dep-dot" />{{ deps.asr_model || '未配置' }}
        </span>
      </div>

      <div class="sc-actions">
        <button type="button" class="sc-btn" :disabled="probing" @click="probe">
          {{ probing ? '探测中…' : '重新探测' }}
        </button>
      </div>
    </section>

    <!-- B 站登录 -->
    <section class="sc-section">
      <h3>B 站登录</h3>
      <p class="usage-hint">
        会员课与「AI 总结」字幕需要登录态。凭证只保存在服务端
        <code>data/bili/cookies.txt</code>，删掉该文件即退出登录；浏览器 Cookie 因系统加密已不可读。
      </p>

      <div class="sc-row">
        <div class="sc-row-info">
          <div class="sc-row-title">登录态</div>
          <div class="sc-row-desc">{{ deps ? cookieText() : '状态获取失败，点「重新探测」' }}</div>
        </div>
        <div class="dep-actions">
          <span v-if="deps" class="dep-state" :class="deps.cookie ? 'ok' : 'warn'">
            <i class="dep-dot" />{{ deps.cookie ? '可用' : '未登录' }}
          </span>
          <button type="button" class="sc-btn" @click="openBiliLogin">扫码登录</button>
        </div>
      </div>
    </section>

    <el-dialog
      v-model="qr.visible"
      title="B 站扫码登录"
      width="300px"
      align-center
      append-to-body
      @closed="stopQrTimer"
    >
      <div class="qr-box">
        <img v-if="qr.image" :src="qr.image" alt="B 站登录二维码" class="qr-img" />
        <div v-else class="qr-empty">二维码生成中…</div>
        <p class="qr-status" :class="qr.status">{{ qrStatusText() }}</p>
        <p class="qr-hint">手机 B 站 App →「我的」→ 扫一扫。登录态只保存在服务端。</p>
        <button
          v-if="qr.status === 'expired' || qr.status === 'error'"
          type="button"
          class="sc-btn"
          @click="openBiliLogin"
        >刷新二维码</button>
      </div>
    </el-dialog>
  </div>
</template>

<style scoped>
.sc-row--stack {
  align-items: flex-start;
}

.sc-field {
  width: 240px;
  flex-shrink: 0;
  margin-left: 16px;
}

/* 依赖状态：与导入页原先那排小圆点同一套语义（ok / warn / bad） */
.dep-state {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--color-text-tertiary);
}
.dep-state .dep-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
  opacity: 0.8;
}
.dep-state.ok { color: var(--color-green); }
.dep-state.warn { color: var(--color-orange); }
.dep-state.bad { color: var(--color-red); }

.dep-actions {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  gap: 12px;
}

.sc-actions {
  display: flex;
  justify-content: flex-end;
  margin-top: 10px;
}

/* ── 扫码登录 ── */
.qr-box {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  text-align: center;
}

.qr-img {
  width: 200px;
  height: 200px;
  border-radius: 8px;
  background: #fff;
}

.qr-empty {
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

.qr-status {
  margin: 0;
  font-size: 13px;
  color: var(--color-text-secondary);
}
.qr-status.scanned { color: var(--color-primary); }
.qr-status.success { color: var(--color-green); }
.qr-status.expired,
.qr-status.error { color: var(--color-red); }

.qr-hint {
  margin: 0;
  font-size: 11px;
  line-height: 1.6;
  color: var(--color-text-tertiary);
}
</style>
