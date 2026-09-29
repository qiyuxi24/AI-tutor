<script setup>
/**
 * NodeDetailPane.vue — 设置 · 节点详情（弹窗尺寸 / 正文字号）
 * 拖动即生效并保存到本机浏览器（localStorage）。
 */
import { useDetailPrefs, setDetailPref, resetDetailPrefs, DETAIL_FIELDS } from '../../utils/detailPrefs'

const { prefs: detailPrefs } = useDetailPrefs()
</script>

<template>
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
</template>
