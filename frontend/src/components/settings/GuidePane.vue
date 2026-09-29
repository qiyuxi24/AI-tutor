<script setup>
/**
 * GuidePane.vue — 设置 · 引导
 * 「重新查看新手引导」要触达 HomeView（引导层挂在那里），所以只做事件转发。
 */
defineEmits(['replay-onboarding'])
</script>

<template>
  <section class="sc-section">
    <h3>引导</h3>
    <div class="sc-row">
      <div class="sc-row-info">
        <div class="sc-row-title">新手引导</div>
        <div class="sc-row-desc">重新查看产品功能介绍</div>
      </div>
      <button class="sc-btn" @click="$emit('replay-onboarding')">重新查看</button>
    </div>
  </section>
</template>
