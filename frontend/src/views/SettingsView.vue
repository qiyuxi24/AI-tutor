<script setup>
/**
 * SettingsView.vue — 设置页（活动栏第四个入口）
 *
 * 职责：集中管理应用设置
 *   - 外观：主题切换（深色 / 浅色）、Markdown 渲染主题（代码高亮配色）
 *   - 引导：重新查看新手引导
 *   - 账号：用户信息、退出登录
 */

import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { useTheme } from '../utils/theme'
import { useMdTheme, MD_THEMES } from '../utils/mdTheme'
import { renderMarkdown } from '../utils/markdown.js'
import { useAuthStore } from '../stores/authStore'
import { getProfile, saveProfileData } from '../api/index.js'
import { useGraphForces, setGraphForce, resetGraphForces, FORCE_FIELDS } from '../utils/graphForces'
import { useDetailPrefs, setDetailPref, resetDetailPrefs, DETAIL_FIELDS } from '../utils/detailPrefs'
import GraphForcePreview from '../components/GraphForcePreview.vue'
import { ballPrefs, normalizeBallPrefs, saveBallPrefs } from '../utils/floatingBall.js'
import { avatarState, removeAvatar, uploadAvatar } from '../utils/avatar.js'

const { mode, setTheme } = useTheme()
const { mdTheme, setMdTheme } = useMdTheme()

// 渲染主题预览样例：主题只改代码高亮的配色，所以必须给一段带多类 token 的代码；
// 顺带放个行内公式，说明数学公式走 KaTeX、不受主题影响。静态内容，算一次即可。
const MD_PREVIEW_SAMPLE = [
  '#### 渲染预览',
  '',
  '行内代码 `npm run dev`，行内公式 $a^2 + b^2 = c^2$。',
  '',
  '```python',
  '# 计算两数之和',
  'def add(a, b):',
  '    return a + b  # -> 3',
  '```',
].join('\n')

const mdPreviewHtml = renderMarkdown(MD_PREVIEW_SAMPLE)
const authStore = useAuthStore()
const { forces } = useGraphForces()
const { prefs: detailPrefs } = useDetailPrefs()

const emit = defineEmits(['replay-onboarding', 'switch-account'])

function handleThemeChange(value) {
  setTheme(value)
}

// ─── 资源与版权（usage_mode，读写用户画像 preferences）───
const usageMode = ref('personal')   // personal | commercial
const usageSaving = ref(false)
const usageLoaded = ref(false)

const MODE_OPTIONS = [
  { value: 'personal', title: '个人使用', desc: '采集 L0 / L1 / L2 资料，仅用于个人学习（默认）' },
  { value: 'commercial', title: '发布 / 商用', desc: '仅采 L0 开放授权资料，规避商用版权风险' },
]

onMounted(async () => {
  try {
    const { data } = await getProfile()
    const prefs = data?.data?.preferences || {}
    usageMode.value = prefs.usage_mode === 'commercial' ? 'commercial' : 'personal'
    // 悬浮球偏好与 usage_mode 同一次 GET 取回（少一个请求，也不会读到中间态）
    Object.assign(ballPrefs, normalizeBallPrefs(prefs.floating_ball))
  } catch {
    // 读取失败保持默认 personal
  } finally {
    usageLoaded.value = true
    ballLoaded.value = true
  }
})

// ─── 悬浮球（D1：真值源 = 服务端 preferences.floating_ball，跟账号走）───
// ballPrefs 是共享响应式状态：这里一改，HomeView 里的 FloatingBall 立刻响应
const ball = ballPrefs
const ballSaving = ref(false)
const ballLoaded = ref(false)

const BALL_CORNER_OPTIONS = [
  { value: 'bottom-right', label: '右下角' },
  { value: 'bottom-left', label: '左下角' },
]
const BALL_SIZE_OPTIONS = [
  { value: 'compact', label: '小' },
  { value: 'medium', label: '中' },
  { value: 'large', label: '大' },
]

/**
 * 改一项悬浮球偏好：乐观更新 → 落库（成功后 saveBallPrefs 内部会同步共享状态）；失败回滚。
 * 用"读完整画像 → 合并 → 全量 PATCH"的既有模式，避免覆盖 usage_mode 等其它偏好。
 */
async function handleBallChange(patch) {
  if (!ballLoaded.value || ballSaving.value) return
  const prev = { ...ballPrefs }
  ballSaving.value = true
  Object.assign(ballPrefs, normalizeBallPrefs({ ...prev, ...patch }))
  try {
    await saveBallPrefs(patch)
  } catch (e) {
    Object.assign(ballPrefs, prev)
    ElMessage.error(e.response?.data?.detail || e.message || '保存失败')
  } finally {
    ballSaving.value = false
  }
}

async function handleUsageModeChange(value) {
  if (!usageLoaded.value || usageSaving.value || usageMode.value === value) return
  usageSaving.value = true
  try {
    // 复用 PATCH /profile：读取当前画像 → 合并 usage_mode → 全量保存（避免覆盖其他偏好）
    const { data } = await getProfile()
    const current = data?.data || {}
    const merged = {
      ...current,
      preferences: { ...(current.preferences || {}), usage_mode: value },
    }
    await saveProfileData(merged)
    usageMode.value = value
    ElMessage.success(value === 'commercial' ? '已切换到发布 / 商用模式' : '已切换到个人使用模式')
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || e.message || '保存失败')
    usageMode.value = value === 'commercial' ? 'personal' : 'commercial'
  } finally {
    usageSaving.value = false
  }
}

// ─── 头像（真值源 = 服务端头像图片，见 backend /api/v1/profile/avatar）───
// avatarState 是共享响应式状态：这里上传成功，活动栏与对话里的头像立刻跟着变。
const avatarFileInput = ref(null)
const avatarBusy = ref(false)

/** 打开系统文件选择框（真正的上传在 @change 里做） */
function pickAvatarFile() {
  if (avatarBusy.value) return
  avatarFileInput.value?.click()
}

async function handleAvatarFile(event) {
  const file = event.target.files?.[0]
  // 清空 value：否则"选了同一个文件再传一次"不会触发 change
  event.target.value = ''
  if (!file) return
  avatarBusy.value = true
  try {
    await uploadAvatar(file)
    ElMessage.success('头像已更新')
  } catch (e) {
    // 后端按 413/415/422 分了级，detail 是可直接读的中文说明
    ElMessage.error(e.response?.data?.detail || e.message || '上传失败')
  } finally {
    avatarBusy.value = false
  }
}

async function handleAvatarReset() {
  if (avatarBusy.value || !avatarState.url) return
  avatarBusy.value = true
  try {
    await removeAvatar()
    ElMessage.success('已恢复默认头像')
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || e.message || '操作失败')
  } finally {
    avatarBusy.value = false
  }
}
</script>

<template>
  <div class="settings-layout">
    <!-- 左侧：设置分类导航 -->
    <aside class="settings-nav">
      <div class="sn-title">设置</div>
      <nav class="sn-list">
        <a class="sn-item active">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M12 20h9" />
            <path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z" />
          </svg>
          外观
        </a>
        <a class="sn-item">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="6" cy="6" r="3" />
            <circle cx="18" cy="6" r="3" />
            <circle cx="12" cy="18" r="3" />
            <line x1="8.6" y1="7.6" x2="10.6" y2="15.5" />
            <line x1="15.4" y1="7.6" x2="13.4" y2="15.5" />
          </svg>
          知识图谱
        </a>
        <a class="sn-item">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M4 7V4h16v3" />
            <line x1="9" y1="20" x2="15" y2="20" />
            <line x1="12" y1="4" x2="12" y2="20" />
          </svg>
          节点详情
        </a>
        <a class="sn-item">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="12" cy="12" r="10" />
            <path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3" />
            <line x1="12" y1="17" x2="12.01" y2="17" />
          </svg>
          引导
        </a>
        <a class="sn-item">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
            <polyline points="7 10 12 15 17 10" />
            <line x1="12" y1="15" x2="12" y2="3" />
          </svg>
          资源与版权
        </a>
        <a class="sn-item">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
            <circle cx="12" cy="7" r="4" />
          </svg>
          账号
        </a>
      </nav>
    </aside>

    <!-- 右侧：设置内容 -->
    <main class="settings-content">
      <!-- 外观 -->
      <section class="sc-section">
        <h3>外观</h3>
        <div class="sc-row">
          <div class="sc-row-info">
            <div class="sc-row-title">主题</div>
            <div class="sc-row-desc">切换深色 / 浅色 / 跟随系统，适配不同环境</div>
          </div>
          <div class="theme-toggle-group">
            <button
              class="theme-option"
              :class="{ active: mode === 'dark' }"
              @click="handleThemeChange('dark')"
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
              </svg>
              深色
            </button>
            <button
              class="theme-option"
              :class="{ active: mode === 'light' }"
              @click="handleThemeChange('light')"
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <circle cx="12" cy="12" r="4" />
                <line x1="12" y1="2" x2="12" y2="4" />
                <line x1="12" y1="20" x2="12" y2="22" />
                <line x1="4.93" y1="4.93" x2="6.34" y2="6.34" />
                <line x1="17.66" y1="17.66" x2="19.07" y2="19.07" />
                <line x1="2" y1="12" x2="4" y2="12" />
                <line x1="20" y1="12" x2="22" y2="12" />
                <line x1="4.93" y1="19.07" x2="6.34" y2="17.66" />
                <line x1="17.66" y1="6.34" x2="19.07" y2="4.93" />
              </svg>
              浅色
            </button>
            <button
              class="theme-option"
              :class="{ active: mode === 'system' }"
              @click="handleThemeChange('system')"
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <rect x="2" y="3" width="20" height="14" rx="2" />
                <line x1="8" y1="21" x2="16" y2="21" />
                <line x1="12" y1="17" x2="12" y2="21" />
              </svg>
              跟随系统
            </button>
          </div>
        </div>

        <div class="sc-row">
          <div class="sc-row-info">
            <div class="sc-row-title">Markdown 渲染主题</div>
            <div class="sc-row-desc">代码高亮配色方案；数学公式由 KaTeX 渲染，不受此影响</div>
          </div>
          <select
            class="md-theme-select"
            :value="mdTheme"
            @change="setMdTheme($event.target.value)"
          >
            <option v-for="t in MD_THEMES" :key="t.value" :value="t.value">{{ t.label }}</option>
          </select>
        </div>

        <!-- 预览：配色由 CSS 决定，切换后立即变化，无需重渲染 -->
        <div class="md-preview markdown-body" v-html="mdPreviewHtml"></div>
      </section>

      <!-- 知识图谱（力导向参数） -->
      <section class="sc-section">
        <h3>知识图谱</h3>
        <p class="usage-hint">调整力导向布局的手感，拖动即生效，并保存到本机浏览器。</p>

        <div class="force-grid">
          <div class="force-controls">
            <div v-for="f in FORCE_FIELDS" :key="f.key" class="force-row">
              <div class="force-row-head">
                <span>{{ f.label }}</span>
                <span class="force-value">{{ +Number(forces[f.key]).toFixed(3) }}</span>
              </div>
              <input
                class="force-slider"
                type="range"
                :min="f.min"
                :max="f.max"
                :step="f.step"
                :value="forces[f.key]"
                @input="setGraphForce(f.key, $event.target.value)"
              />
              <div class="force-hint">{{ f.hint }}</div>
            </div>
            <button class="sc-btn force-reset" @click="resetGraphForces">恢复默认</button>
          </div>

          <div class="force-preview-box">
            <GraphForcePreview />
          </div>
        </div>
      </section>

      <!-- 节点详情（弹窗尺寸 / 正文字号） -->
      <section class="sc-section">
        <h3>节点详情</h3>
        <p class="usage-hint">双击图谱节点弹出的详情面板；拖动即生效，并保存到本机浏览器。</p>

        <div class="force-controls">
          <div v-for="f in DETAIL_FIELDS" :key="f.key" class="force-row">
            <div class="force-row-head">
              <span>{{ f.label }}</span>
              <span class="force-value">{{ f.toDisplay(detailPrefs[f.key]) }}</span>
            </div>
            <input
              class="force-slider"
              type="range"
              :min="f.min"
              :max="f.max"
              :step="f.step"
              :value="detailPrefs[f.key]"
              @input="setDetailPref(f.key, $event.target.value)"
            />
            <div class="force-hint">{{ f.hint }}</div>
          </div>
          <button class="sc-btn force-reset" @click="resetDetailPrefs">恢复默认</button>
        </div>
      </section>

      <!-- 引导 -->
      <section class="sc-section">
        <h3>引导</h3>
        <div class="sc-row">
          <div class="sc-row-info">
            <div class="sc-row-title">新手引导</div>
            <div class="sc-row-desc">重新查看产品功能介绍</div>
          </div>
          <button class="sc-btn" @click="emit('replay-onboarding')">重新查看</button>
        </div>
      </section>

      <!-- 资源与版权 -->
      <section class="sc-section">
        <h3>资源与版权</h3>
        <p class="usage-hint">用于「资源采集」页的授权范围与检索过滤；切换会即时保存到用户画像。</p>
        <div v-for="opt in MODE_OPTIONS" :key="opt.value" class="sc-row">
          <div class="sc-row-info">
            <div class="sc-row-title">{{ opt.title }}</div>
            <div class="sc-row-desc">{{ opt.desc }}</div>
          </div>
          <button
            class="usage-radio"
            :class="{ active: usageMode === opt.value }"
            :disabled="usageSaving || !usageLoaded"
            @click="handleUsageModeChange(opt.value)"
          >
            <span class="usage-dot" :class="{ on: usageMode === opt.value }"></span>
            <span v-if="usageMode === opt.value">使用中</span>
            <span v-else>选择</span>
          </button>
        </div>
      </section>

      <!-- 悬浮球 -->
      <section class="sc-section">
        <h3>悬浮球</h3>
        <p class="usage-hint">
          在本应用里除了对话页之外的页面右下角显示一个小球，点开即可随手问 AI；
          开关跟随你的账号，换设备也生效。
        </p>
        <div class="sc-row">
          <div class="sc-row-info">
            <div class="sc-row-title">启用悬浮球</div>
            <div class="sc-row-desc">改动即时生效，不用刷新页面</div>
          </div>
          <button
            class="usage-radio"
            :class="{ active: ball.enabled }"
            :disabled="ballSaving || !ballLoaded"
            @click="handleBallChange({ enabled: !ball.enabled })"
          >
            <span class="usage-dot" :class="{ on: ball.enabled }"></span>
            <span v-if="ball.enabled">已开启</span>
            <span v-else>已关闭</span>
          </button>
        </div>
        <div class="sc-row">
          <div class="sc-row-info">
            <div class="sc-row-title">打开网页时自动展开小窗</div>
            <div class="sc-row-desc">关闭时只显示小球，点击才展开</div>
          </div>
          <button
            class="usage-radio"
            :class="{ active: ball.default_open }"
            :disabled="ballSaving || !ballLoaded"
            @click="handleBallChange({ default_open: !ball.default_open })"
          >
            <span class="usage-dot" :class="{ on: ball.default_open }"></span>
            <span v-if="ball.default_open">展开</span>
            <span v-else>收起</span>
          </button>
        </div>
        <div class="sc-row">
          <div class="sc-row-info">
            <div class="sc-row-title">小球位置</div>
            <div class="sc-row-desc">也可以在网页上直接拖动小球</div>
          </div>
          <div class="theme-toggle-group">
            <button
              v-for="opt in BALL_CORNER_OPTIONS"
              :key="opt.value"
              class="theme-option"
              :class="{ active: ball.corner === opt.value }"
              :disabled="ballSaving || !ballLoaded"
              @click="handleBallChange({ corner: opt.value })"
            >
              {{ opt.label }}
            </button>
          </div>
        </div>
        <div class="sc-row">
          <div class="sc-row-info">
            <div class="sc-row-title">小窗尺寸</div>
            <div class="sc-row-desc">也可以在小窗右下角拖拽调整</div>
          </div>
          <div class="theme-toggle-group">
            <button
              v-for="opt in BALL_SIZE_OPTIONS"
              :key="opt.value"
              class="theme-option"
              :class="{ active: ball.size === opt.value }"
              :disabled="ballSaving || !ballLoaded"
              @click="handleBallChange({ size: opt.value })"
            >
              {{ opt.label }}
            </button>
          </div>
        </div>
        <div class="sc-row">
          <div class="sc-row-info">
            <div class="sc-row-title">对话页</div>
            <div class="sc-row-desc">对话页不显示悬浮球（那里已经有完整对话区），点开小窗聊的内容与对话页是同一个会话</div>
          </div>
        </div>
      </section>

      <!-- 头像 -->
      <section class="sc-section">
        <h3>头像</h3>
        <p class="usage-hint">
          上传一张本地照片作为头像，会保存在你的账号下（换设备也生效）。
          非方形照片按中心自动裁剪，支持 PNG / JPG / WebP，不超过 2MB。
        </p>
        <div class="sc-row">
          <div class="avatar-preview">
            <img v-if="avatarState.url" class="avatar-preview-img" :src="avatarState.url" alt="当前头像" />
            <svg v-else class="avatar-preview-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
              <circle cx="12" cy="7" r="4" />
            </svg>
          </div>
          <div class="sc-row-info">
            <div class="sc-row-title">{{ avatarState.url ? '当前头像' : '默认头像' }}</div>
            <div class="sc-row-desc">改动即时生效，活动栏和对话里会同步</div>
          </div>
          <div class="avatar-actions">
            <button class="usage-radio" :disabled="avatarBusy" @click="pickAvatarFile">
              <span class="usage-dot" :class="{ on: !!avatarState.url }"></span>
              <span>{{ avatarBusy ? '处理中…' : (avatarState.url ? '更换' : '上传') }}</span>
            </button>
            <button class="usage-radio" :disabled="avatarBusy || !avatarState.url" @click="handleAvatarReset">
              <span>恢复默认</span>
            </button>
          </div>
          <input
            ref="avatarFileInput"
            class="avatar-file-input"
            type="file"
            accept="image/png,image/jpeg,image/webp"
            @change="handleAvatarFile"
          />
        </div>
      </section>

      <!-- 账号 -->
      <section class="sc-section">
        <h3>账号</h3>
        <div class="sc-row">
          <div class="sc-row-info">
            <div class="sc-row-title">当前用户</div>
            <div class="sc-row-desc">{{ authStore.username || '未登录' }}</div>
          </div>
        </div>
        <div class="sc-row">
          <div class="sc-row-info">
            <div class="sc-row-title">切换账号</div>
            <div class="sc-row-desc">登录另一个账号</div>
          </div>
          <button class="sc-btn" @click="emit('switch-account')">切换账号</button>
        </div>
      </section>
    </main>
  </div>
</template>

<style scoped>
.settings-layout {
  display: flex;
  width: 100%;
  height: 100%;
}

/* 左侧设置导航 */
.settings-nav {
  width: 200px;
  min-width: 200px;
  height: 100%;
  background: var(--color-bg-primary);
  border-right: 1px solid var(--color-border);
  padding: 16px 12px;
  overflow-y: auto;
  flex-shrink: 0;
}

.sn-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--color-text-muted);
  padding: 4px 10px 12px;
  letter-spacing: 0.3px;
}

.sn-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.sn-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 10px;
  border-radius: 8px;
  font-size: 13px;
  color: var(--color-text-secondary);
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
  text-decoration: none;
}

.sn-item:hover {
  background: var(--color-bg-hover);
  color: var(--color-text-primary);
}

.sn-item.active {
  background: var(--color-accent-light);
  color: var(--color-accent);
  font-weight: 500;
}

/* 右侧内容 */
.settings-content {
  flex: 1;
  overflow-y: auto;
  padding: 32px 40px;
  background: var(--color-bg-primary);
}

.settings-content::-webkit-scrollbar { width: 5px; }
.settings-content::-webkit-scrollbar-thumb {
  background: var(--color-border-light);
  border-radius: 3px;
}

.sc-section {
  max-width: 640px;
  margin-bottom: 28px;
}

.sc-section h3 {
  font-size: 16px;
  font-weight: 600;
  color: var(--color-text-primary);
  margin: 0 0 14px;
}

.sc-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 16px 18px;
  background: var(--color-bg-secondary);
  border: 1px solid var(--color-border);
  border-radius: 10px;
  margin-bottom: 10px;
}

.sc-row-info {
  min-width: 0;
}

.sc-row-title {
  font-size: 14px;
  font-weight: 500;
  color: var(--color-text-primary);
}

.sc-row-desc {
  font-size: 12px;
  color: var(--color-text-tertiary);
  margin-top: 3px;
}

.theme-toggle-group {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
  flex-wrap: wrap;
}

.theme-option {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 7px 14px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: transparent;
  color: var(--color-text-secondary);
  font-size: 13px;
  cursor: pointer;
  transition: all 0.15s;
}

.theme-option.active {
  background: var(--color-accent-light);
  border-color: var(--color-accent);
  color: var(--color-accent);
  font-weight: 500;
}

.theme-option:hover:not(.active) {
  background: var(--color-bg-hover);
  color: var(--color-text-primary);
}

/* Markdown 渲染主题下拉：让原生弹层在深色下也是深色 */
.md-theme-select {
  flex-shrink: 0;
  padding: 7px 12px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: var(--color-bg-secondary);
  color: var(--color-text-primary);
  font-size: 13px;
  cursor: pointer;
}
html.dark .md-theme-select {
  color-scheme: dark;
}

/* Markdown 渲染主题预览：代码块观感对齐对话框（.markdown-body 全局规则只管 token 配色，
   容器底色在组件里，所以这里补一份）。选中具名主题时，mdTheme.js 那条带
   html[data-md-theme] 前缀的归一化规则特异性更高，会自动接管 <pre>/<code>。 */
.md-preview {
  margin-top: 10px;
  padding: 14px 18px;
  background: var(--color-bg-secondary);
  border: 1px solid var(--color-border);
  border-radius: 10px;
  font-size: 13px;
  line-height: 1.6;
  color: var(--color-text-primary);
}
.md-preview :deep(h4) {
  margin: 0 0 8px;
  font-size: 14px;
  font-weight: 600;
}
.md-preview :deep(p) {
  margin: 0 0 8px;
}
.md-preview :deep(p:last-child) {
  margin-bottom: 0;
}
.md-preview :deep(code) {
  background: var(--color-bg-hover);
  padding: 2px 6px;
  border-radius: 4px;
  font-family: 'Consolas', 'Courier New', monospace;
}
.md-preview :deep(pre) {
  background: var(--color-bg-tertiary);
  color: var(--color-text-primary);
  padding: 14px 16px;
  border-radius: 10px;
  overflow-x: auto;
  margin: 0;
}
.md-preview :deep(pre code) {
  background: none;
  padding: 0;
}

.sc-btn {
  flex-shrink: 0;
  padding: 7px 16px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: transparent;
  color: var(--color-text-secondary);
  font-size: 13px;
  cursor: pointer;
  transition: all 0.15s;
}

.sc-btn:hover {
  background: var(--color-bg-hover);
  color: var(--color-text-primary);
}

/* ── 资源与版权 ── */
.usage-hint {
  font-size: 12px;
  color: var(--color-text-tertiary);
  margin: -6px 0 14px;
}

.usage-radio {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 7px 14px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: transparent;
  color: var(--color-text-secondary);
  font-size: 13px;
  cursor: pointer;
  transition: all 0.15s;
}
.usage-radio:hover:not(.active) {
  background: var(--color-bg-hover);
  color: var(--color-text-primary);
}
.usage-radio.active {
  background: var(--color-accent-light);
  border-color: var(--color-accent);
  color: var(--color-accent);
  font-weight: 500;
}
.usage-radio:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
.usage-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--color-border-light);
}
.usage-dot.on {
  background: var(--color-accent);
}

/* ── 知识图谱（力导向参数） ── */
.force-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 220px;
  gap: 18px;
  align-items: start;
}

/* 滑杆卡片：知识图谱力场与节点详情共用 */
.force-controls {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 16px 18px;
  background: var(--color-bg-secondary);
  border: 1px solid var(--color-border);
  border-radius: 10px;
}

.force-row-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  font-size: 13px;
  color: var(--color-text-primary);
  margin-bottom: 4px;
}

.force-value {
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  color: var(--color-text-tertiary);
}

.force-slider {
  width: 100%;
  margin: 0;
  accent-color: var(--color-accent);
  cursor: pointer;
}

.force-hint {
  font-size: 11px;
  color: var(--color-text-tertiary);
  margin-top: 3px;
}

.force-reset {
  align-self: flex-start;
}

.force-preview-box {
  height: 280px;
}

/* ── 头像 ── */
.avatar-preview {
  flex-shrink: 0;
  width: 56px;
  height: 56px;
  border-radius: 50%;
  background: var(--color-bg-surface);
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
}

.avatar-preview-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.avatar-preview-icon {
  width: 26px;
  height: 26px;
  color: var(--color-text-tertiary);
}

.avatar-actions {
  margin-left: auto;
  flex-shrink: 0;
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

/* 原生文件选择框藏起来，由「上传 / 更换」按钮代为触发 */
.avatar-file-input {
  display: none;
}
</style>
