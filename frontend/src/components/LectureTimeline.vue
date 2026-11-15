<script setup>
/**
 * LectureTimeline.vue — 课时页左栏：知识点时间轴
 *
 * 一件事：把「一课 = 一串知识点」按时间顺序摊开，让用户一眼看到课堂推进到哪、
 * 每一段在讲什么，点一下就换到中栏。
 *
 * 与 course2md 对齐的三处细节：
 *   · 时间戳当**链接**用（等宽字体 + 主色）—— 用户的第一反应是"这一秒在哪"，不是"这叫什么名字"；
 *   · 有截图才给缩略图（没抽截图的课不该出现一排占位灰块）；
 *   · 当前项左侧 3px 主色条 + 浅色底，滚动时自动把选中项拉回视野
 *     （中栏可能由键盘/外部跳转驱动选中，左栏不跟手就会"选中的看不到"）。
 *
 * 截图的 objectURL 生命周期由 `useSlideAssets` 统一管（卸载即 revoke）。
 * 本组件是纯展示：不调课时详情接口，也不改数据。
 *
 * Props:
 *   segments  Array   — 课时详情里的 segments（顺序由调用方或本组件内 sortSegments 兜底）
 *   activeSeq Number  — 当前选中项的 seq（null = 没选）
 *   loading   Boolean — 正在加载课时详情（骨架占位，此时列表为空）
 *
 * Emits:
 *   select: (segment) — 用户点了某一项
 */
import { computed, nextTick, ref, watch } from 'vue'
import { sortSegments } from '../utils/lecture.js'
import { useSlideAssets } from '../utils/slideAssets.js'

const props = defineProps({
  segments: { type: Array, default: () => [] },
  activeSeq: { type: Number, default: null },
  loading: { type: Boolean, default: false },
})

const emit = defineEmits(['select'])

const { urls, failed, load } = useSlideAssets()
const listRef = ref(null)

/**
 * 排序放在组件内再做一次：左栏是**时间轴**，顺序错了会直接误导"讲到哪了"，
 * 不值得把它托付给调用方的数组顺序（后端将来若按重要性返回，这里也不会乱）。
 */
const ordered = computed(() => sortSegments(props.segments))

// 换课时 / 换一批截图 → 重新取图（已取过的地址命中缓存，不会重复请求）
watch(
  () => props.segments.map((s) => s.slide_url).join('|'),
  () => load(props.segments.map((s) => s.slide_url)),
  { immediate: true },
)

/** 选中项跟着滚动：`block: 'nearest'` 不打断用户正在看的位置（离得远才滚） */
watch(() => props.activeSeq, async () => {
  await nextTick()
  const host = listRef.value
  if (!host) return
  const el = host.querySelector('.tl-item.is-active')
  el?.scrollIntoView?.({ block: 'nearest' })
})
</script>

<template>
  <nav class="tl" aria-label="知识点时间轴">
    <!-- 加载骨架：形状与真实行一致，切过去不会"跳一下" -->
    <template v-if="loading">
      <div v-for="i in 4" :key="i" class="tl-skel">
        <span class="tl-skel-line w40" />
        <span class="tl-skel-line w90" />
        <span class="tl-skel-line w70" />
      </div>
    </template>

    <p v-else-if="!segments.length" class="tl-empty">
      这集还没有知识点。
    </p>

    <ul v-else ref="listRef" class="tl-list">
      <li v-for="seg in ordered" :key="seg.seq">
        <button
          type="button"
          class="tl-item"
          :class="{ 'is-active': seg.seq === activeSeq }"
          :aria-current="seg.seq === activeSeq ? 'true' : undefined"
          :title="seg.title"
          @click="emit('select', seg)"
        >
          <span class="tl-row">
            <!-- 时间戳是这一栏的主角：等宽 + 主色，视觉上就是一串可点的锚点 -->
            <span class="tl-time">{{ seg.t_label || '--:--' }}</span>
            <span class="tl-title">{{ seg.title || '未命名知识点' }}</span>
          </span>
          <span v-if="seg.brief" class="tl-brief">{{ seg.brief }}</span>
          <!-- 缩略图：只有真的取到图才占位（失败/没截图都不留灰框） -->
          <img
            v-if="seg.slide_url && urls[seg.slide_url]"
            class="tl-thumb"
            :src="urls[seg.slide_url]"
            alt=""
            loading="lazy"
          />
          <span v-else-if="seg.slide_url && failed[seg.slide_url]" class="tl-thumb-fail">
            截图加载失败
          </span>
        </button>
      </li>
    </ul>
  </nav>
</template>

<style scoped>
.tl {
  height: 100%;
  overflow-y: auto;
  padding: var(--space-md);
}

.tl-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

/* 整项可点：命中区域覆盖整行（时间戳单独做成链接会让右侧一大片点不动） */
.tl-item {
  display: flex;
  flex-direction: column;
  gap: var(--space-xs);
  width: 100%;
  padding: var(--space-sm) var(--space-md);
  /* 左侧 3px 是选中条的槽位，**恒占位**：否则选中时文字会横向抖一下 */
  border: none;
  border-left: 3px solid transparent;
  border-radius: var(--radius-sm);
  background: transparent;
  color: inherit;
  font-family: inherit;
  text-align: left;
  cursor: pointer;
  transition: background 0.15s, border-color 0.15s;
}
.tl-item:hover { background: var(--color-bg-hover); }
.tl-item:focus-visible {
  outline: 2px solid var(--color-border-focus);
  outline-offset: -2px;
}
.tl-item.is-active {
  border-left-color: var(--color-primary);
  background: var(--color-primary-soft);
}

.tl-row {
  display: flex;
  align-items: baseline;
  gap: var(--space-sm);
  min-width: 0;
}

.tl-time {
  flex-shrink: 0;
  font-family: var(--font-mono);
  font-size: var(--font-size-xs);
  font-variant-numeric: tabular-nums;
  color: var(--color-primary);
}
.tl-item.is-active .tl-time { font-weight: 600; }

.tl-title {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--font-size-base);
  color: var(--color-text-primary);
}
.tl-item.is-active .tl-title { font-weight: 600; }

/* brief 一行截断：左栏是"目录"，不是阅读区 */
.tl-brief {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  padding-left: calc(var(--font-size-xs) * 3.6);   /* 与标题左缘对齐（让过时间戳宽度） */
  font-size: var(--font-size-xs);
  line-height: var(--line-height-base);
  color: var(--color-text-placeholder);
}

.tl-thumb {
  display: block;
  width: 100%;
  max-height: 92px;
  margin-top: var(--space-xs);
  object-fit: cover;
  object-position: top center;
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-sm);
  background: var(--color-bg-hover);
}
.tl-thumb-fail {
  margin-top: var(--space-xs);
  font-size: var(--font-size-xs);
  color: var(--color-text-placeholder);
}

.tl-empty {
  margin: 0;
  padding: var(--space-lg) var(--space-sm);
  font-size: var(--font-size-xs);
  color: var(--color-text-placeholder);
  text-align: center;
}

/* 骨架 */
.tl-skel {
  display: flex;
  flex-direction: column;
  gap: var(--space-xs);
  padding: var(--space-sm) var(--space-md);
}
.tl-skel-line {
  height: 8px;
  border-radius: var(--radius-full);
  background: var(--color-bg-disabled);
  animation: tl-pulse 1.4s ease-in-out infinite;
}
.tl-skel-line.w40 { width: 40%; }
.tl-skel-line.w90 { width: 90%; }
.tl-skel-line.w70 { width: 70%; }
@keyframes tl-pulse {
  0%, 100% { opacity: 0.55; }
  50% { opacity: 1; }
}
</style>
