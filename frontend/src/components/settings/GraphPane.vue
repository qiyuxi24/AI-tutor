<script setup>
/**
 * GraphPane.vue — 设置 · 知识图谱（布局 + 力导向参数 + 节点详情面板）
 * 改动即生效并保存到本机浏览器（localStorage）。
 *
 * 「节点详情」（弹窗尺寸 / 正文字号）与图谱是同一条看图链路上的偏好，
 * 2026-11-01 起并入本面板，不再单列一个设置项。
 */
import { computed } from 'vue'
import { useGraphForces, setGraphForce, resetGraphForces, FORCE_FIELDS } from '../../utils/graphForces'
import { useGraphLayout, GRAPH_LAYOUTS } from '../../utils/graphLayout'
import { useDetailPrefs, setDetailPref, resetDetailPrefs, DETAIL_FIELDS } from '../../utils/detailPrefs'
import GraphForcePreview from '../GraphForcePreview.vue'

const { forces } = useGraphForces()
const { layout, setGraphLayout } = useGraphLayout()
const { prefs: detailPrefs } = useDetailPrefs()

/** 当前布局的说明（向用户交代每种布局在表达什么关系） */
const layoutHint = computed(() => GRAPH_LAYOUTS.find((l) => l.key === layout.value)?.hint || '')
</script>

<template>
  <section class="sc-section">
    <h3>知识图谱</h3>

    <div class="sc-row">
      <div class="sc-row-info">
        <div class="sc-row-title">布局</div>
        <div class="sc-row-desc">{{ layoutHint }}</div>
      </div>
      <div class="theme-toggle-group">
        <button
          v-for="l in GRAPH_LAYOUTS"
          :key="l.key"
          class="theme-option"
          :class="{ active: layout === l.key }"
          :title="l.hint"
          @click="setGraphLayout(l.key)"
        >{{ l.label }}</button>
      </div>
    </div>
    <p class="usage-hint">三种布局共用同一份图谱数据、只换画法；改这里即全局生效（图谱页不再单独放切换）。</p>

    <p class="usage-hint">力导向手感（仅「力导向」布局使用），拖动即生效。</p>

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
