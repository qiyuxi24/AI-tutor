<script setup>
/**
 * FloatingBall.vue — 站内悬浮球 + 只放对话的小窗
 *
 * 显示条件由父级（HomeView）通过 `visible` 决定：**只在非对话页显示**。
 * 对话页不显示，因为那里已经有完整的对话区 —— 这正是悬浮球存在的理由（在别的页面
 * 也能随手问一句），也是需求里"对话界面不能存在"的落地点。
 *
 * 与主对话**共用同一个 chatStore**：小窗里聊的内容切回对话页就在那儿，不需要两套会话。
 * 因此本组件不能同时和主对话视图一起出现（主对话视图在 HomeView 里是 v-if，切走即卸载）。
 *
 * 偏好来自 utils/floatingBall.js 的共享状态（真值源 = 服务端账号偏好，跟账号走）。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import ChatArea from './ChatArea.vue'
import { ballPrefs, ballPrefsMeta, BALL_SIZE_PRESETS } from '../utils/floatingBall.js'
import { BRAND_ICON } from '../utils/brandAssets.js'

const props = defineProps({
  /** 是否允许显示（父级传入：非对话页 且 用户已开启） */
  visible: { type: Boolean, default: false },
})

/* ── 几何常量（px）：这里改就够，组件别处不再出现裸数字 ── */
const BALL_SIZE = 48      // 球的直径（CSS 不再写死，由 ballStyle 下发）
const EDGE = 16           // 球 / 小窗距视口边缘的边距
const SNAP = 16           // 松手时离边缘 < SNAP 就吸附过去
const GAP = 12            // 小窗与球之间的间距
const CLICK_SLOP = 4      // 位移 < CLICK_SLOP 视为点击，否则算拖拽

/** 手动缩放的范围；视口更小则以视口为准。ABS_* = 视口比 MIN_* 还小时的兜底 */
const MIN_W = 320
const MIN_H = 420
const MAX_W = 560
const MAX_H = 760
const ABS_MIN_W = 240
const ABS_MIN_H = 260

const POS_KEY = 'tutorBallPos'
const WIN_POS_KEY = 'tutorBallWinPos'
const WIN_SIZE_KEY = 'tutorBallWinSize'

/** 8 个缩放方向：4 边 + 4 角（n/s/e/w 与组合） */
const RESIZE_DIRS = ['n', 's', 'e', 'w', 'nw', 'ne', 'sw', 'se']

const open = ref(false)

/**
 * 球位置：本机手动拖过就以本机为准，否则跟随设置页的角位 / 边距预设。
 * 预设来自服务端偏好，**到达时间晚于本组件挂载**（HomeView 是异步 loadBallPrefs），
 * 所以下面要 watch 一次重新落位 —— 只在初始化时算一次会让预设角位失效。
 */
const manualPos = readPos()
const hasManualPos = ref(!!manualPos)
const pos = ref(manualPos || defaultPos())

watch(
  () => ballPrefs.corner,
  () => { if (!hasManualPos.value) pos.value = defaultPos() }
)

/**
 * 小窗的手动位置：
 *   null = 跟随球（默认，贴着球展开）
 *   {x,y} = 用户拖过小窗标题栏，之后固定在那儿（存 localStorage）
 * 拖球会清掉它，恢复"贴着球展开"，避免球走了小窗留在原地让人困惑。
 */
const winPos = ref(readWinPos())

/** 小窗的手动尺寸：null = 用设置页的预设档位；拖右下角后固定下来（存 localStorage） */
const winSize = ref(readWinSize())

/** 当前允许的尺寸范围（跟着视口收缩，避免小屏上放不下） */
function sizeBounds() {
  const maxW = Math.max(ABS_MIN_W, Math.min(MAX_W, window.innerWidth - EDGE * 2))
  const maxH = Math.max(ABS_MIN_H, Math.min(MAX_H, window.innerHeight - EDGE * 2))
  return { minW: Math.min(MIN_W, maxW), minH: Math.min(MIN_H, maxH), maxW, maxH }
}

function clampSize(s) {
  const b = sizeBounds()
  return {
    w: Math.round(Math.min(Math.max(b.minW, s.w), b.maxW)),
    h: Math.round(Math.min(Math.max(b.minH, s.h), b.maxH)),
  }
}

/** 当前小窗尺寸：手动缩放优先，否则用设置页的档位 */
const size = computed(() => {
  if (winSize.value) return clampSize(winSize.value)
  const [w, h] = BALL_SIZE_PRESETS[ballPrefs.size] || BALL_SIZE_PRESETS.medium
  return clampSize({ w, h })
})

const ballStyle = computed(() => ({
  left: `${pos.value.x}px`,
  top: `${pos.value.y}px`,
  width: `${BALL_SIZE}px`,
  height: `${BALL_SIZE}px`,
}))

/** 小窗贴着球展开：球在右半屏就右对齐，在下半屏就向上弹 */
function attachPos() {
  const { w, h } = size.value
  const onRight = pos.value.x + BALL_SIZE / 2 > window.innerWidth / 2
  const onBottom = pos.value.y + BALL_SIZE / 2 > window.innerHeight / 2
  const x = onRight ? pos.value.x + BALL_SIZE - w : pos.value.x
  const y = onBottom ? pos.value.y - h - GAP : pos.value.y + BALL_SIZE + GAP
  return clampWin({ x, y })
}

/** 把任意位置钳进视口（按当前小窗尺寸算边界） */
function clampWin(p) {
  const { w, h } = size.value
  return {
    x: Math.min(Math.max(EDGE, p.x), Math.max(EDGE, window.innerWidth - w - EDGE)),
    y: Math.min(Math.max(EDGE, p.y), Math.max(EDGE, window.innerHeight - h - EDGE)),
  }
}

/** 当前小窗位置（手动优先，否则跟随球） */
function currentWinPos() {
  return winPos.value ? clampWin(winPos.value) : attachPos()
}

const winStyle = computed(() => {
  const { w, h } = size.value
  const p = currentWinPos()
  return { left: `${p.x}px`, top: `${p.y}px`, width: `${w}px`, height: `${h}px` }
})

// ─── 位置 ───
function clamp(p) {
  const maxX = Math.max(EDGE, window.innerWidth - BALL_SIZE - EDGE)
  const maxY = Math.max(EDGE, window.innerHeight - BALL_SIZE - EDGE)
  return { x: Math.min(Math.max(EDGE, p.x), maxX), y: Math.min(Math.max(EDGE, p.y), maxY) }
}

/** 按设置页的角位 / 边距算预设位置（右下或左下角） */
function defaultPos() {
  const off = ballPrefs.offset
  const onLeft = ballPrefs.corner === 'bottom-left'
  return clamp({
    x: onLeft ? off.x : window.innerWidth - BALL_SIZE - off.x,
    y: window.innerHeight - BALL_SIZE - off.y,
  })
}

function readPos() {
  try {
    const raw = JSON.parse(localStorage.getItem(POS_KEY) || 'null')
    return raw && Number.isFinite(raw.x) && Number.isFinite(raw.y) ? clamp(raw) : null
  } catch {
    return null
  }
}

/** 存本机球位；未手动摆过（跟随预设）时清掉，避免老位置把新预设盖住 */
function savePos() {
  try {
    if (hasManualPos.value) localStorage.setItem(POS_KEY, JSON.stringify(pos.value))
    else localStorage.removeItem(POS_KEY)
  } catch {
    /* 隐私模式下写不进去，忽略 */
  }
}

function readWinPos() {
  try {
    const raw = JSON.parse(localStorage.getItem(WIN_POS_KEY) || 'null')
    return raw && Number.isFinite(raw.x) && Number.isFinite(raw.y) ? { x: raw.x, y: raw.y } : null
  } catch {
    return null
  }
}

function saveWinPos() {
  try {
    if (winPos.value) localStorage.setItem(WIN_POS_KEY, JSON.stringify(winPos.value))
    else localStorage.removeItem(WIN_POS_KEY)
  } catch {
    /* ignore */
  }
}

function readWinSize() {
  try {
    const raw = JSON.parse(localStorage.getItem(WIN_SIZE_KEY) || 'null')
    return raw && Number.isFinite(raw.w) && Number.isFinite(raw.h) ? { w: raw.w, h: raw.h } : null
  } catch {
    return null
  }
}

function saveWinSize() {
  try {
    if (winSize.value) localStorage.setItem(WIN_SIZE_KEY, JSON.stringify(winSize.value))
    else localStorage.removeItem(WIN_SIZE_KEY)
  } catch {
    /* ignore */
  }
}

// ─── 拖拽：松手吸附最近边；位移 <4px 视为点击 ───
let drag = null
let moved = false

function onPointerDown(e) {
  if (e.button !== 0) return
  moved = false
  drag = { px: e.clientX, py: e.clientY, x: pos.value.x, y: pos.value.y }
  try {
    e.currentTarget.setPointerCapture(e.pointerId)
  } catch {
    /* ignore */
  }
}

function onPointerMove(e) {
  if (!drag) return
  const dx = e.clientX - drag.px
  const dy = e.clientY - drag.py
  if (!moved && Math.abs(dx) < CLICK_SLOP && Math.abs(dy) < CLICK_SLOP) return
  moved = true
  pos.value = clamp({ x: drag.x + dx, y: drag.y + dy })
}

function onPointerUp() {
  if (!drag) return
  drag = null
  if (!moved) return
  // 松手吸附最近边。pos 已被 clamp 到 EDGE，比较时得先减掉 EDGE，
  // 否则"离边 < SNAP"永远不成立 —— 旧实现就是拿钳制后的值直接比，吸附从没生效过。
  const snapX = window.innerWidth - BALL_SIZE - EDGE
  const snapY = window.innerHeight - BALL_SIZE - EDGE
  const next = { ...pos.value }
  if (pos.value.x - EDGE < SNAP) next.x = EDGE
  else if (snapX - pos.value.x < SNAP) next.x = snapX
  if (pos.value.y - EDGE < SNAP) next.y = EDGE
  else if (snapY - pos.value.y < SNAP) next.y = snapY
  hasManualPos.value = true   // 摆过就以本机为准，不再跟随设置页的角位预设
  pos.value = next
  savePos()
  // 球动过 → 小窗回到"贴着球展开"（否则球走了、小窗还孤零零挂在原地）
  winPos.value = null
  saveWinPos()
}

// ─── 小窗拖拽：抓标题栏移动，位置固定下来（存 localStorage）───
let winDrag = null

function onWinPointerDown(e) {
  if (e.button !== 0) return
  // 标题栏里的按钮（收起）不该触发拖拽
  if (e.target?.closest?.('button')) return
  const p = currentWinPos()
  winDrag = { px: e.clientX, py: e.clientY, x: p.x, y: p.y }
  try {
    e.currentTarget.setPointerCapture(e.pointerId)
  } catch {
    /* ignore */
  }
}

function onWinPointerMove(e) {
  if (!winDrag) return
  winPos.value = clampWin({
    x: winDrag.x + e.clientX - winDrag.px,
    y: winDrag.y + e.clientY - winDrag.py,
  })
}

function onWinPointerUp() {
  if (!winDrag) return
  winDrag = null
  saveWinPos()
}

// ─── 小窗缩放：8 个方向把手（4 边 + 4 角）───
// 拖左/上边时，对边保持不动 → 尺寸变的同时位置也要跟着变，所以这里同时写 winSize 与 winPos。
let grip = null

function onGripPointerDown(e, dir) {
  if (e.button !== 0) return
  e.stopPropagation() // 别让标题栏跟着一起拖动窗口
  const { w, h } = size.value
  const p = currentWinPos()
  grip = { dir, px: e.clientX, py: e.clientY, w, h, x: p.x, y: p.y }
  try {
    e.currentTarget.setPointerCapture(e.pointerId)
  } catch {
    /* ignore */
  }
}

function onGripPointerMove(e) {
  if (!grip) return
  const dx = e.clientX - grip.px
  const dy = e.clientY - grip.py
  const b = sizeBounds()
  const rightEdge = grip.x + grip.w    // 拖左边时要保持不动的右边界
  const bottomEdge = grip.y + grip.h   // 拖上边时要保持不动的下边界

  let w = grip.w
  let h = grip.h
  let x = grip.x
  let y = grip.y

  if (grip.dir.includes('e')) {
    const hi = Math.max(b.minW, Math.min(b.maxW, window.innerWidth - EDGE - grip.x))
    w = Math.min(Math.max(b.minW, grip.w + dx), hi)
  }
  if (grip.dir.includes('w')) {
    const hi = Math.max(b.minW, Math.min(b.maxW, rightEdge - EDGE))
    w = Math.min(Math.max(b.minW, grip.w - dx), hi)
    x = rightEdge - w
  }
  if (grip.dir.includes('s')) {
    const hi = Math.max(b.minH, Math.min(b.maxH, window.innerHeight - EDGE - grip.y))
    h = Math.min(Math.max(b.minH, grip.h + dy), hi)
  }
  if (grip.dir.includes('n')) {
    const hi = Math.max(b.minH, Math.min(b.maxH, bottomEdge - EDGE))
    h = Math.min(Math.max(b.minH, grip.h - dy), hi)
    y = bottomEdge - h
  }

  winSize.value = { w: Math.round(w), h: Math.round(h) }
  winPos.value = { x: Math.round(x), y: Math.round(y) }
}

function onGripPointerUp() {
  if (!grip) return
  grip = null
  saveWinSize()
  saveWinPos()
}

function onBallClick() {
  if (moved) return // 拖拽结束时浏览器也会补一个 click，别误触
  open.value = !open.value
}

// ─── 开关联动 ───
// ① 父级把 visible 置 false（切到对话页 / 用户关掉总开关）→ 收起小窗
// ② 进入非对话页且偏好里"自动展开" → 直接展开
watch(
  () => props.visible,
  (v, prev) => {
    if (!v) {
      open.value = false
      return
    }
    if (!prev && ballPrefs.default_open) open.value = true
  },
  { immediate: true }
)

/**
 * 设置页改了「小窗尺寸 / 小球位置」= 用户明确要换一套预设 →
 * 清掉本机对应的手动微调，否则预设看起来像"点了没反应"。
 * 用 revision（只有显式保存才 +1）区分，避免首次加载把本地微调冲掉。
 */
watch(
  () => ballPrefsMeta.revision,
  () => {
    if (ballPrefsMeta.changed.includes('size')) {
      winSize.value = null
      saveWinSize()
    }
    if (ballPrefsMeta.changed.includes('corner')) {
      // 回到"跟随预设"。这里的 pos 重算不能省：上面的 corner watch 建得更早、
      // 会先于本 watch 执行，那时 hasManualPos 还是 true，它不会落位。
      hasManualPos.value = false
      pos.value = defaultPos()
      savePos()                    // 顺手清掉本机存的老位置
      winPos.value = null
      saveWinPos()
    }
  }
)

function onResize() {
  // 跟随预设的球按新视口重算；手动摆过的只做可见性钳制
  pos.value = hasManualPos.value ? clamp(pos.value) : defaultPos()
  // 视口变了：手动摆过的小窗重新钳进可见范围；手动缩放的尺寸也要跟着收
  if (winPos.value) winPos.value = clampWin(winPos.value)
  if (winSize.value) winSize.value = clampSize(winSize.value)
}

function onKeydown(e) {
  if (e.key === 'Escape' && open.value) open.value = false
}

onMounted(() => {
  window.addEventListener('resize', onResize)
  document.addEventListener('keydown', onKeydown)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  document.removeEventListener('keydown', onKeydown)
  // 拖拽中途组件被卸载（切到对话页）时别把状态挂住
  drag = null
  winDrag = null
  grip = null
})
</script>

<template>
  <!-- 挂到 body：不受对话页/其它页的布局与 overflow 影响 -->
  <Teleport to="body">
    <div v-if="visible" class="ball-layer">
      <!-- 小窗：只放对话 -->
      <section
        v-if="open"
        class="ball-win"
        :style="winStyle"
        role="dialog"
        aria-label="AI 对话小窗"
      >
        <header
          class="bw-head"
          title="按住可移动小窗"
          @pointerdown="onWinPointerDown"
          @pointermove="onWinPointerMove"
          @pointerup="onWinPointerUp"
          @pointercancel="onWinPointerUp"
        >
          <span class="bw-title">AI 学习助手</span>
          <button class="bw-min" type="button" aria-label="收起小窗" @click="open = false">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
              <line x1="6" y1="12" x2="18" y2="12" />
            </svg>
          </button>
        </header>
        <div class="bw-body">
          <ChatArea />
        </div>

      </section>

      <!-- 8 个缩放把手（4 边 + 4 角）。单独一层放在窗口外框上：
           ① 不被 .ball-win 的 overflow:hidden 裁掉
           ② 不压住对话区右边缘的滚动条 -->
      <div v-if="open" class="bw-frame" :style="winStyle" aria-hidden="true">
        <div
          v-for="dir in RESIZE_DIRS"
          :key="dir"
          class="bw-grip"
          :class="`g-${dir}`"
          title="拖动调整小窗大小"
          @pointerdown="onGripPointerDown($event, dir)"
          @pointermove="onGripPointerMove"
          @pointerup="onGripPointerUp"
          @pointercancel="onGripPointerUp"
        >
          <svg v-if="dir === 'se'" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round">
            <path d="M14 7 L7 14" />
            <path d="M14 11 L11 14" />
          </svg>
        </div>
      </div>

      <!-- 悬浮球 -->
      <button
        class="ball"
        type="button"
        :style="ballStyle"
        :aria-expanded="open"
        aria-label="打开 AI 对话小窗"
        @pointerdown="onPointerDown"
        @pointermove="onPointerMove"
        @pointerup="onPointerUp"
        @pointercancel="onPointerUp"
        @click="onBallClick"
        @contextmenu.prevent
      >
        <!-- 品牌图标：与 favicon / 活动栏 logo / 对话 AI 头像共用同一份 public/brand-star.svg，
             这里不再内联写死图形（原来是个机器人头，跟其它三处对不上） -->
        <img class="ball-icon" :src="BRAND_ICON" alt="" aria-hidden="true" />
      </button>
    </div>
  </Teleport>
</template>

<style scoped>
/* 覆盖层本身不吃事件，只有球与小窗吃 */
.ball-layer {
  position: fixed;
  inset: 0;
  pointer-events: none;
  /* 低于 Element Plus 弹窗（2000+），高于应用内容 */
  z-index: 1500;
}

.ball {
  position: absolute;
  pointer-events: auto;
  /* 宽高由 ballStyle 下发（BALL_SIZE 是唯一来源，别在这里再写死一份） */
  padding: 0;
  border: none;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--color-accent);
  color: var(--color-text-inverse);
  cursor: pointer;
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.28);
  touch-action: none;
  user-select: none;
  transition: transform 0.16s ease-out;
}
.ball:hover {
  transform: scale(1.06);
}
.ball:active {
  transform: scale(0.98);
}
.ball:focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: 3px;
}
/* 品牌像素星：viewBox 14x13，等比 26 * 13/14 ≈ 24（与对话 AI 头像同口径） */
.ball-icon {
  display: block;
  width: 26px;
  height: 24px;
}
@media (prefers-reduced-motion: reduce) {
  .ball {
    transition: none;
  }
}

.ball-win {
  position: absolute;
  pointer-events: auto;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border: 1px solid var(--color-border);
  border-radius: 14px;
  background: var(--color-bg-primary);
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.24);
}

.bw-head {
  flex-shrink: 0;
  height: 42px;
  padding: 0 8px 0 14px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid var(--color-border);
  background: var(--color-bg-secondary);
  /* 标题栏是小窗的拖拽把手 */
  cursor: move;
  touch-action: none;
  user-select: none;
}
.bw-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--color-text-primary);
}
.bw-min {
  width: 28px;
  height: 28px;
  border: none;
  border-radius: 7px;
  background: transparent;
  color: var(--color-text-secondary);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
}
.bw-min:hover {
  background: var(--color-bg-hover);
  color: var(--color-text-primary);
}

/* 缩放把手层：与小窗同尺寸同位置，但自身不吃事件（只有把手吃） */
.bw-frame {
  position: absolute;
  pointer-events: none;
}
.bw-grip {
  position: absolute;
  pointer-events: auto;
  touch-action: none;
}

/* 4 条边：贴在窗口外侧，避免压住内容（右边缘有对话区的滚动条） */
.g-n { top: -6px; left: 12px; right: 12px; height: 6px; cursor: ns-resize; }
.g-s { bottom: -6px; left: 12px; right: 12px; height: 6px; cursor: ns-resize; }
.g-w { left: -6px; top: 12px; bottom: 12px; width: 6px; cursor: ew-resize; }
.g-e { right: -6px; top: 12px; bottom: 12px; width: 6px; cursor: ew-resize; }

/* 4 个角：跨在角上（向外探 8px 便于抓取，向内探 8px 那里没有可点元素） */
.g-nw { top: -8px; left: -8px; width: 16px; height: 16px; cursor: nwse-resize; }
.g-ne { top: -8px; right: -8px; width: 16px; height: 16px; cursor: nesw-resize; }
.g-sw { bottom: -8px; left: -8px; width: 16px; height: 16px; cursor: nesw-resize; }
.g-se {
  bottom: -8px;
  right: -8px;
  width: 16px;
  height: 16px;
  cursor: nwse-resize;
  color: var(--color-text-tertiary);
}
.g-se:hover {
  color: var(--color-accent);
}
/* 只有右下角画一个可见的斜线标识（其余靠 hover 时鼠标形状提示） */
.g-se svg {
  position: absolute;
  right: 10px;
  bottom: 10px;
  width: 12px;
  height: 12px;
  display: block;
}

/* 只放对话：小窗主体就是 ChatArea */
.bw-body {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
/* 小窗里收紧间距（ChatArea 的 padding 是给整页设计的） */
.bw-body :deep(.message-list) {
  padding: 12px 12px 4px;
}
.bw-body :deep(.input-area) {
  padding: 10px 12px 12px;
}
.bw-body :deep(.welcome-icon) {
  font-size: 38px;
  margin-bottom: 8px;
}
.bw-body :deep(.welcome h3) {
  font-size: 17px;
}
.bw-body :deep(.welcome p) {
  font-size: 13px;
}
</style>
