<script setup>
/**
 * KbView.vue — 知识库（活动栏「知识库」入口）
 *
 * 与图谱页右侧的 KbPanel 复用同一个组件，功能重复是有意的：
 * 图谱页那侧是「边看图边挑资料」的窄栏（最宽 560），这里是「集中管理资料」的宽页。
 * 「在图谱中显示」的副作用（切视图 + 高亮）属 HomeView，本组件只转发意图。
 */
import KbPanel from '../components/KbPanel.vue'

const emit = defineEmits(['show-in-graph'])
</script>

<template>
  <div class="page-scroll">
    <div class="page-inner page-inner--wide kbv-inner">
      <header class="kbv-header">
        <h2 class="page-title">知识库</h2>
        <p class="page-sub">上传的资料是知识图谱与对话检索的来源</p>
      </header>

      <div class="kbv-body">
        <KbPanel @show-in-graph="emit('show-in-graph', $event)" />
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 内容区吃满剩余高度：卡片内部（目录树）自己滚动 */
.kbv-inner {
  display: flex;
  flex-direction: column;
}
.kbv-header {
  flex-shrink: 0;
  margin-bottom: 16px;
}

/* 宽卡片：图谱页那侧栏最宽 560，这里给到 900 并居中，管理资料才不局促 */
.kbv-body {
  flex: 1 1 auto;
  min-height: 320px;
  display: flex;
  flex-direction: column;
  width: 100%;
  max-width: 900px;
  margin: 0 auto;
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  overflow: hidden;
}

/* KbPanel 根节点自带 flex:1 1 auto，这里补 min-height 让目录树区吃满剩余空间并滚动 */
.kbv-body > .kb-panel {
  flex: 1 1 auto;
  min-height: 0;
}
</style>
