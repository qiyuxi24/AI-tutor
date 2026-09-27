<script setup>
/**
 * SidePanel.vue — 统一的侧边栏容器（可拖宽 + 可折叠 + 可拖高）
 *
 * 三处侧栏共用本组件，保证一套外观与交互，不再各写一份折叠/拖拽：
 *   - 对话历史（HomeView.chat-layout，左侧）
 *   - 图谱学科导航（HomeView.graph-nav，左侧）
 *   - 图谱知识库（HomeView.graph-kb，右侧）
 *
 * 结构：[卡片本体(内容 + 内缘宽度热区 + 底边高度热区)] + [贴卡片外缘的抽屉把手]
 *   - 把手：点击收起 / 展开，图标随 side 与折叠态镜像
 *   - 宽度热区：hover 显强调色竖线，按住左右拖（left 侧向右变宽，right 侧反向）
 *   - 高度热区：hover 显强调色横线，按住上下拖；上限就是父级给的 max-height（拖到底即触底），
 *     双击复位为「随内容自适应」。两个热区在右下角交叠处由宽度优先（z-index 更高）。
 *
 * 定位交给父级：组件只管「卡片 + 把手」这一横排结构，
 * 父级用 class 给 position/top/left/right/bottom/max-height（图谱页浮动）或 flex 项 + margin（对话页）。
 *
 * 宽度为什么不让内容跟着重排：卡片宽度做过渡动画，内部 .sp-content 保持固定宽度，
 * 折叠与拖拽时内容只是被裁切掉，不会一路挤成竖排文字。
 *
 * 持久化：给了 storageKey 就记在 localStorage（宽度 sidePanelWidth:<key>、高度 sidePanelHeight:<key>）——
 * 初始化时若存过值会 emit 修正，之后每次变更自动写入。父级只给默认值即可。
 */
import { ref, computed, watch, onMounted } from 'vue'

const props = defineProps({
  /** 把手/展开方向：left = 面板在左、把手贴其右缘；right 反之 */
  side: { type: String, default: 'left' },
  /** 宽度（v-model:width）；父级需要联动布局（如缩放控件让位）时才订阅 */
  width: { type: Number, default: 240 },
  min: { type: Number, default: 160 },
  max: { type: Number, default: 520 },
  /** 折叠态（v-model:collapsed） */
  collapsed: { type: Boolean, default: false },
  /** 给了就按此 key 持久化宽度与高度 */
  storageKey: { type: String, default: '' },
  /** 把手 tooltip 里的名字，如「知识库」→ 展开知识库 / 收起知识库 */
  label: { type: String, default: '侧栏' },
  /** 是否允许拖下边界调高度（双击复位为随内容自适应） */
  resizableHeight: { type: Boolean, default: false },
  minHeight: { type: Number, default: 140 },
})

const emit = defineEmits(['update:width', 'update:collapsed'])

const rootRef = ref(null)
const resizing = ref(false)     // 拖宽中
const resizingH = ref(false)    // 拖高中
/** 高度：null = 随内容自适应（父级 max-height 兜底） */
const panelHeight = ref(null)

const storageId = (what) => `sidePanel${what}:${props.storageKey}`

onMounted(() => {
  if (!props.storageKey) return
  const savedW = Number(localStorage.getItem(storageId('Width')))
  if (Number.isFinite(savedW) && savedW > 0 && savedW !== props.width) emit('update:width', savedW)

  if (!props.resizableHeight) return
  const savedH = Number(localStorage.getItem(storageId('Height')))
  if (Number.isFinite(savedH) && savedH > 0) panelHeight.value = savedH
})

watch(() => props.width, (v) => {
  if (props.storageKey) localStorage.setItem(storageId('Width'), String(v))
})

watch(panelHeight, (v) => {
  if (!props.storageKey) return
  if (v) localStorage.setItem(storageId('Height'), String(v))
  else localStorage.removeItem(storageId('Height'))   // 复位成自适应就别留着旧值
})

const rootStyle = computed(() => (panelHeight.value ? { height: `${panelHeight.value}px` } : {}))

/** 把手箭头：始终指向「点击后面板会去哪儿」 */
const chevron = computed(() => {
  const pointLeft = props.side === 'left' ? !props.collapsed : props.collapsed
  return pointLeft ? '15 18 9 12 15 6' : '9 18 15 12 9 6'
})

function startResize(e) {
  const startX = e.clientX
  const startW = props.width
  const dir = props.side === 'left' ? 1 : -1
  resizing.value = true
  const onMove = (ev) => {
    emit('update:width', Math.min(props.max, Math.max(props.min, startW + dir * (ev.clientX - startX))))
  }
  const onUp = () => {
    resizing.value = false
    document.removeEventListener('mousemove', onMove)
    document.removeEventListener('mouseup', onUp)
  }
  document.addEventListener('mousemove', onMove)
  document.addEventListener('mouseup', onUp)
}

function startHeightResize(e) {
  const el = rootRef.value
  const startY = e.clientY
  // 自适应态起拖：以当前真实高度为基准，拖多少算多少
  const startH = panelHeight.value ?? el?.offsetHeight ?? props.minHeight
  // 上限 = 这张卡片自己的 max-height（父级给的 calc，浏览器已解析成像素）= 触底
  const maxH = parseFloat(getComputedStyle(el).maxHeight) || Infinity
  resizingH.value = true
  const onMove = (ev) => {
    panelHeight.value = Math.round(
      Math.min(maxH, Math.max(props.minHeight, startH + ev.clientY - startY))
    )
  }
  const onUp = () => {
    resizingH.value = false
    document.removeEventListener('mousemove', onMove)
    document.removeEventListener('mouseup', onUp)
  }
  document.addEventListener('mousemove', onMove)
  document.addEventListener('mouseup', onUp)
}
</script>

<template>
  <div
    ref="rootRef"
    class="side-panel"
    :class="[`sp-${side}`, { 'sp-collapsed': collapsed, 'sp-resizing': resizing || resizingH }]"
    :style="rootStyle"
  >
    <div class="sp-body" :style="{ width: collapsed ? '0px' : width + 'px' }">
      <div class="sp-content" :style="{ width: width + 'px' }">
        <slot />
      </div>
      <span v-if="!collapsed" class="sp-resizer" @mousedown.prevent="startResize"></span>
      <span
        v-if="resizableHeight && !collapsed"
        class="sp-resize-h"
        title="拖动调整高度，双击复位"
        @mousedown.prevent="startHeightResize"
        @dblclick="panelHeight = null"
      ></span>
    </div>

    <button
      class="sp-toggle"
      @click="emit('update:collapsed', !collapsed)"
      :title="`${collapsed ? '展开' : '收起'}${label}`"
    >
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
        <polyline :points="chevron" />
      </svg>
    </button>
  </div>
</template>

<style scoped>
.side-panel {
  display: flex;
  align-items: stretch;
  flex-shrink: 0;
  min-width: 0;
}

/* ── 卡片本体 ── */
.sp-body {
  position: relative;
  order: 1;
  display: flex;
  flex-direction: column;
  /* 父容器若是「高度自适应 + max-height 封顶」，这里必须允许收缩，内部列表才会滚动 */
  min-height: 0;
  overflow: hidden;
  background: var(--color-bg-secondary);
  border-radius: 12px;
  /* 用内描边当边框：不占布局宽度，内容可按 width 精确对齐（border 会白吃 2px） */
  box-shadow: inset 0 0 0 1px var(--color-border);
  transition: width 0.28s cubic-bezier(0.4, 0, 0.2, 1), opacity 0.2s ease;
}

/* basis 用 auto（而非 flex:1 的 0）：容器高度自适应时，内容才撑得出卡片真实高度；
   容器封顶后 grow 再吃掉剩余空间。 */
.sp-content {
  display: flex;
  flex-direction: column;
  flex: 1 1 auto;
  min-height: 0;
  overflow: hidden;
}

.sp-collapsed .sp-body {
  opacity: 0;
  box-shadow: none;
  pointer-events: none;
}

/* 拖拽中禁用过渡，否则面板跟不上鼠标 */
.sp-resizing .sp-body {
  transition: none;
}

/* ── 内缘宽度热区：平时隐形，悬停浮出一条强调色竖线 ── */
.sp-resizer {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 7px;
  z-index: 3;   /* 高于底边热区：右下角交叠处按「调宽」处理 */
  cursor: col-resize;
}
.sp-resizer::after {
  content: '';
  position: absolute;
  top: 0;
  bottom: 0;
  width: 2px;
  background: transparent;
  transition: background 0.18s;
}
.sp-resizer:hover::after {
  background: var(--color-accent, #5b8ff9);
}

/* ── 底边高度热区：悬停浮出强调色横线；拖动上限即卡片 max-height（触底），双击复位 ── */
.sp-resize-h {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  height: 7px;
  z-index: 2;
  cursor: row-resize;
}
.sp-resize-h::after {
  content: '';
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  height: 2px;
  background: transparent;
  transition: background 0.18s;
}
.sp-resize-h:hover::after {
  background: var(--color-accent, #5b8ff9);
}

/* ── 抽屉把手：像拉手一样贴在卡片外缘，默认半隐、悬停浮现 ── */
.sp-toggle {
  order: 2;
  align-self: center;
  flex-shrink: 0;
  width: 16px;
  height: 46px;
  padding: 0;
  border: 1px solid var(--color-border);
  border-radius: 0 8px 8px 0;
  background: var(--color-bg-secondary);
  color: var(--color-text-tertiary);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  opacity: 0.5;
  transition: opacity 0.18s, background 0.18s, color 0.18s;
}
.sp-toggle:hover {
  opacity: 1;
  background: var(--color-bg-surface);
  color: var(--color-text-primary);
}

/* ── side=left：把手贴卡片右缘 ── */
.sp-left .sp-toggle {
  margin-left: -1px;
  border-left: none;
}
.sp-left .sp-resizer { right: 0; }
.sp-left .sp-resizer::after { right: 0; }

/* ── side=right：把手贴卡片左缘 ── */
.sp-right .sp-toggle {
  order: 0;
  margin-right: -1px;
  border-right: none;
  border-radius: 8px 0 0 8px;
}
.sp-right .sp-resizer { left: 0; }
.sp-right .sp-resizer::after { left: 0; }

/* 收起后卡片消失，把手脱开浮在原位，补全四边与圆角提示可展开 */
.sp-collapsed .sp-toggle {
  opacity: 0.85;
  border-radius: 8px;
}
.sp-collapsed.sp-left .sp-toggle { border-left: 1px solid var(--color-border); }
.sp-collapsed.sp-right .sp-toggle { border-right: 1px solid var(--color-border); }
</style>
