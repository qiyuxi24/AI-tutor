<script setup>
/**
 * AppIcon.vue — 全站唯一的图标渲染组件
 *
 * 图形来源只有 `utils/icons.js`，本组件只负责「尺寸 + 描边 + 无障碍属性」三件套。
 * 不要在这里写具体 path，也不要在调用方复制 <svg>（会重新长出 100 份不一致的内联图标）。
 *
 * 用法：
 *   <AppIcon name="trash" :size="14" />
 *   <AppIcon name="check" :size="14" class="is-done" />        <!-- class 落到 <svg> 上 -->
 *   <AppIcon name="sun" :size="20" label="切换浅色模式" />      <!-- 独立语义时给 label -->
 */
import { computed, watch } from 'vue'
import { ICONS, strokeWidthFor } from '../utils/icons'

const props = defineProps({
  /** icons.js 里的键 */
  name: { type: String, required: true },
  /** 边长 px（正方）。纯装饰用默认 16 */
  size: { type: [Number, String], default: 16 },
  /** 覆盖描边粗细（默认按尺寸推导），只在极少数视觉需要时传 */
  stroke: { type: [Number, String], default: null },
  /** 给了 label 就当作有语义的图片（role=img），否则对读屏隐藏 */
  label: { type: String, default: '' },
})

const box = computed(() => Number(props.size) || 16)
const width = computed(() => props.stroke ?? strokeWidthFor(box.value))
/** 图形来自 icons.js 常量，不含任何用户输入 */
const html = computed(() => ICONS[props.name] || ICONS.file)

// 名字写错时静默退化成 file，肉眼几乎发现不了 —— 开发态喊一声。
if (import.meta.env.DEV) {
  watch(
    () => props.name,
    (n) => {
      if (n && !ICONS[n]) console.warn(`[AppIcon] 未注册的图标名 "${n}"，已回退到 file`)
    },
    { immediate: true },
  )
}
</script>

<template>
  <svg
    :width="box"
    :height="box"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    :stroke-width="width"
    stroke-linecap="round"
    stroke-linejoin="round"
    :role="label ? 'img' : undefined"
    :aria-label="label || undefined"
    :aria-hidden="label ? undefined : 'true'"
    v-html="html"
  />
</template>
