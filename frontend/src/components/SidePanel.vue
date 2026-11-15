<script setup>
/**
 * SidePanel.vue — 统一的侧边栏容器（可拖宽 + 可折叠）
 *
 * 三处侧栏共用本组件，保证一套外观与交互，不再各写一份折叠/拖拽：
 *   - 对话历史（HomeView.chat-layout，左侧）
 *   - 图谱学科导航（HomeView.graph-nav，左侧）
 *   - 图谱知识库（HomeView.graph-kb，右侧）
 *
 * 统一标准（三处一致）：**贴容器边缘、满高、无外边距、无圆角无阴影**，
 * 只用一条 1px 内边线跟内容区分；高度不可调 —— 侧栏永远满高（2026-11-15 起删掉拖高）。
 *
 * 结构：[内容 + 内缘宽度热区] + [顶部一角的折叠按钮]
 *   - 折叠按钮：常驻可见，图标是「面板」方块（竖线在面板那一侧）；收起后同一按钮浮在原位，
 *     点它就是「展开」
 *   - 宽度热区：hover 显强调色竖线，按住左右拖（left 侧向右变宽，right 侧反向）
 *
 * 定位交给父级：组件自己吃满父级高度，父级用 class 给 position/top/left/right/bottom（图谱页）
 * 或当 flex 项（对话页）。
 *
 * 宽度为什么不让内容跟着重排：卡片宽度做过渡动画，内部 .sp-content 保持固定宽度，
 * 折叠与拖拽时内容只是被裁切掉，不会一路挤成竖排文字。
 *
 * 持久化：给了 storageKey 就记在 localStorage（宽度 sidePanelWidth:<key>）——
 * 初始化时若存过值会 emit 修正，之后每次变更自动写入。父级只给默认值即可。
 */
import { ref, watch, onMounted } from 'vue'
import AppIcon from './AppIcon.vue'

const props = defineProps({
  /** 面板在哪一侧：left = 贴容器左缘（边线在右），right 反之 */
  side: { type: String, default: 'left' },
  /** 宽度（v-model:width）；父级需要联动布局（如缩放控件让位）时才订阅 */
  width: { type: Number, default: 240 },
  min: { type: Number, default: 160 },
  max: { type: Number, default: 520 },
  /** 折叠态（v-model:collapsed） */
  collapsed: { type: Boolean, default: false },
  /** 给了就按此 key 持久化宽度 */
  storageKey: { type: String, default: '' },
  /** 折叠按钮 tooltip 里的名字，如「知识库」→ 展开知识库 / 收起知识库 */
  label: { type: String, default: '侧栏' },
})

const emit = defineEmits(['update:width', 'update:collapsed'])

const resizing = ref(false)     // 拖宽中

const storageId = (what) => `sidePanel${what}:${props.storageKey}`

onMounted(() => {
  if (!props.storageKey) return
  const savedW = Number(localStorage.getItem(storageId('Width')))
  if (Number.isFinite(savedW) && savedW > 0 && savedW !== props.width) emit('update:width', savedW)
})

watch(() => props.width, (v) => {
  if (props.storageKey) localStorage.setItem(storageId('Width'), String(v))
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
</script>

<template>
  <div
    class="side-panel"
    :class="[`sp-${side}`, { 'sp-collapsed': collapsed, 'sp-resizing': resizing }]"
  >
    <div class="sp-body" :style="{ width: collapsed ? '0px' : width + 'px' }">
      <div class="sp-content" :style="{ width: width + 'px' }">
        <slot />
      </div>
      <span v-if="!collapsed" class="sp-resizer" @mousedown.prevent="startResize"></span>
    </div>

    <!-- 折叠/展开按钮：面板顶部靠外一角，常驻。收起后面板宽度归零，它就浮在原位当「展开」。 -->
    <button
      class="sp-toggle"
      @click="emit('update:collapsed', !collapsed)"
      :title="`${collapsed ? '展开' : '收起'}${label}`"
    >
      <AppIcon :name="side === 'left' ? 'panelLeft' : 'panelRight'" :size="15" />
    </button>
  </div>
</template>

<style scoped>
/* 贴边满高：不吃外边距、不留圆角，跟相邻内容共用一条 1px 边线 */
.side-panel {
  position: relative;
  display: flex;
  align-items: stretch;
  flex-shrink: 0;
  height: 100%;
  min-width: 0;
}

.sp-body {
  position: relative;
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
  background: var(--color-bg-secondary);
  transition: width 0.22s cubic-bezier(0.4, 0, 0.2, 1), opacity 0.18s ease;
}
.sp-left .sp-body { border-right: 1px solid var(--color-border); }
.sp-right .sp-body { border-left: 1px solid var(--color-border); }

/* basis 用 auto（而非 flex:1 的 0）：内容才撑得出真实高度。
   顶部那条 32px 是折叠按钮的固定位置（三处侧栏一致），子面板自己的首行紧接其下。 */
.sp-content {
  display: flex;
  flex-direction: column;
  flex: 1 1 auto;
  min-height: 0;
  padding-top: 32px;
  overflow: hidden;
}

.sp-collapsed .sp-body {
  opacity: 0;
  border-color: transparent;
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
  z-index: 3;
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
.sp-left .sp-resizer { right: 0; }
.sp-left .sp-resizer::after { right: 0; }
.sp-right .sp-resizer { left: 0; }
.sp-right .sp-resizer::after { left: 0; }

/* ── 折叠 / 展开按钮 ── */
.sp-toggle {
  position: absolute;
  top: 3px;
  z-index: 5;
  width: 26px;
  height: 26px;
  padding: 0;
  border: none;
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--color-text-tertiary);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background 0.15s, color 0.15s;
}
.sp-toggle:hover {
  background: var(--color-bg-hover);
  color: var(--color-text-primary);
}
.sp-left .sp-toggle { left: 8px; }
.sp-right .sp-toggle { right: 8px; }

/* 收起后卡片没了，按钮浮在原位：给个底色与描边，否则贴在画布上看不清 */
.sp-collapsed .sp-toggle {
  background: var(--color-bg-secondary);
  border: 1px solid var(--color-border);
  color: var(--color-text-secondary);
}
.sp-collapsed .sp-toggle:hover { color: var(--color-text-primary); }
</style>
