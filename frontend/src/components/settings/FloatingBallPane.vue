<script setup>
/**
 * FloatingBallPane.vue — 设置 · 悬浮球
 *
 * 真值源 = 服务端用户画像 preferences.floating_ball（跟账号走）。
 * ballPrefs 是全应用共享响应式状态：这里一改，HomeView 里的悬浮球立刻响应。
 */
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { ballPrefs, saveBallPrefs, loadBallPrefs, BALL_SIZES, BALL_CORNERS } from '../../utils/floatingBall.js'

const ball = ballPrefs
const ballSaving = ref(false)
const ballLoaded = ref(false)

/** 选项文案。可选值本身取自 utils/floatingBall.js 的白名单，不在这里另抄一份 */
const CORNER_LABELS = { 'bottom-right': '右下角', 'bottom-left': '左下角' }
const SIZE_LABELS = { compact: '小', medium: '中', large: '大' }
const BALL_CORNER_OPTIONS = BALL_CORNERS.map((value) => ({ value, label: CORNER_LABELS[value] }))
const BALL_SIZE_OPTIONS = BALL_SIZES.map((value) => ({ value, label: SIZE_LABELS[value] }))

onMounted(async () => {
  await loadBallPrefs()
  ballLoaded.value = true
})

/**
 * 改一项悬浮球偏好：乐观更新 → 落库（成功后 saveBallPrefs 内部会同步共享状态）；失败回滚。
 * 用"读完整画像 → 合并 → 全量 PATCH"的既有模式，避免覆盖 usage_mode 等其它偏好。
 */
async function handleBallChange(patch) {
  if (!ballLoaded.value || ballSaving.value) return
  // 点的是已选中项 = 没变化。不 return 的话 revision 会白 +1，
  // 顺手把用户手动拖出来的球位 / 窗位清掉（看着像"点了反而乱了"）。
  if (Object.entries(patch).every(([k, v]) => ball[k] === v)) return
  const prev = { ...ballPrefs }
  ballSaving.value = true
  Object.assign(ballPrefs, patch)
  try {
    await saveBallPrefs(patch)
  } catch (e) {
    Object.assign(ballPrefs, prev)
    ElMessage.error(e.response?.data?.detail || e.message || '保存失败')
  } finally {
    ballSaving.value = false
  }
}
</script>

<template>
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
</template>
