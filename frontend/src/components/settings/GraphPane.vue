<script setup>
/**
 * GraphPane.vue — 设置 · 知识图谱（力导向参数）
 * 拖动即生效并保存到本机浏览器（localStorage）。
 */
import { useGraphForces, setGraphForce, resetGraphForces, FORCE_FIELDS } from '../../utils/graphForces'
import GraphForcePreview from '../GraphForcePreview.vue'

const { forces } = useGraphForces()
</script>

<template>
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
</template>
