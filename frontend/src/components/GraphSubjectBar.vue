<script setup>
/**
 * GraphSubjectBar.vue — 学科收藏栏（图谱页一级导航）
 *
 * 职责：
 *   按学科分类管理知识图谱，一次只渲染一个学科——这是防止图谱随学习
 *   无限生长（节点越堆越多、画布与提示词双双膨胀）的入口。
 *
 * 数据流：
 *   store.subjectSummaries（学科 + 节点数/掌握度统计） → 渲染
 *   store.currentSubject（当前选中） → 高亮
 *   store.setSubject()（点击） → 按需拉取该学科子图
 *
 * 与板块侧栏（GraphBoardSidebar）构成两级导航：学科 → 知识板块。
 * 「未分类」是后端合成的分组（无学科归属的节点），排在列表末尾、样式弱化。
 *
 * 设计风格：与 GraphBoardSidebar / ConversationSidebar 一致，Obsidian 极简，无边框卡片。
 */

import { storeToRefs } from 'pinia'
import { useChatStore } from '../stores/chatStore'

const store = useChatStore()
const { subjectSummaries, currentSubject } = storeToRefs(store)

function handleSelect(s) {
  store.setSubject(s.subject)
}

/** 平均掌握度 → 整数百分比 */
function masteryPct(s) {
  const v = Number(s.mastery_avg)
  return Number.isFinite(v) ? Math.round(v) : 0
}
</script>

<template>
  <aside class="graph-subject-bar">
    <div class="subject-bar-header">
      <span class="subject-bar-title">学科</span>
      <span class="subject-bar-count">{{ subjectSummaries.length }}</span>
    </div>

    <div class="subject-list">
      <button
        v-for="s in subjectSummaries"
        :key="s.subject"
        class="subject-item"
        :class="{ active: currentSubject === s.subject, muted: s.unclassified }"
        :title="`${s.subject}：${s.node_count} 节点，已掌握 ${s.mastered_count}`"
        @click="handleSelect(s)"
      >
        <span class="subject-row">
          <span class="subject-name">{{ s.subject }}</span>
          <span class="subject-total">{{ s.node_count }}</span>
        </span>
        <!-- 掌握度进度条：一眼看出该学科推进到哪 -->
        <span class="subject-bar">
          <span class="subject-bar-fill" :style="{ width: masteryPct(s) + '%' }"></span>
        </span>
      </button>

      <p v-if="!subjectSummaries.length" class="subject-empty">
        暂无学科<br />
        <span class="subject-empty-hint">去「知识库」页从教材生成，或直接和 AI 对话建知识点</span>
      </p>
    </div>
  </aside>
</template>

<style scoped>
.graph-subject-bar {
  display: flex;
  flex-direction: column;
  width: 168px;
  flex-shrink: 0;
  background: var(--color-bg-secondary);
  border: 1px solid var(--color-border);
  border-radius: 8px;
  padding: 8px 6px;
  overflow: hidden;
}

.subject-bar-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 10px 10px;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.02em;
  color: var(--color-text-secondary);
}

.subject-bar-count {
  font-size: 11px;
  font-weight: 500;
  color: var(--color-text-tertiary);
}

.subject-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
  overflow-y: auto;
  min-height: 0;
}

.subject-list::-webkit-scrollbar { width: 4px; }
.subject-list::-webkit-scrollbar-thumb {
  background: var(--color-border-light);
  border-radius: 2px;
}

.subject-item {
  display: flex;
  flex-direction: column;
  gap: 5px;
  width: 100%;
  padding: 7px 10px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--color-text-primary);
  font-size: 13px;
  text-align: left;
  cursor: pointer;
  transition: background 0.15s;
}

.subject-item:hover {
  background: var(--color-bg-hover, rgba(127, 127, 127, 0.08));
}

.subject-item.active {
  background: var(--color-accent, #5b8ff9);
  color: var(--color-on-accent, #fff);
}

.subject-item.muted .subject-name {
  color: var(--color-text-tertiary);
}

.subject-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.subject-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.subject-total {
  font-size: 11px;
  color: var(--color-text-tertiary);
  flex-shrink: 0;
}

.subject-item.active .subject-total {
  color: inherit;
  opacity: 0.85;
}

.subject-bar {
  display: block;
  height: 3px;
  border-radius: 2px;
  background: var(--color-border-light);
  overflow: hidden;
}

.subject-bar-fill {
  display: block;
  height: 100%;
  border-radius: 2px;
  background: var(--color-accent, #5b8ff9);
  transition: width 0.25s ease;
}

.subject-item.active .subject-bar {
  background: rgba(255, 255, 255, 0.3);
}

.subject-item.active .subject-bar-fill {
  background: var(--color-on-accent, #fff);
}

.subject-empty {
  margin: 4px 6px;
  font-size: 12px;
  line-height: 1.7;
  color: var(--color-text-tertiary);
}

.subject-empty-hint {
  font-size: 11px;
  opacity: 0.85;
}
</style>
