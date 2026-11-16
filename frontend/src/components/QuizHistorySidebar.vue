<script setup>
/**
 * QuizHistorySidebar.vue — 出题页常驻左侧栏内容（出题历史题集列表）
 *
 * 纯展示：数据由 QuizView 传入，点一条 emit select。外观/折叠/拖宽由外层 SidePanel 提供，
 * 与「对话历史」侧栏同一套手感（选中高亮 + 左侧色条）。
 */
const props = defineProps({
  batches: { type: Array, default: () => [] },
  total: { type: Number, default: 0 },
  loading: { type: Boolean, default: false },
  activeId: { type: String, default: '' },
  openingId: { type: String, default: '' },
})

const emit = defineEmits(['select'])

const difficultyLabels = { easy: '简单', medium: '中等', hard: '困难' }

/** 后端 created_at 是 UTC（SQLite datetime('now')）→ 转本地展示 */
function formatTime(s) {
  if (!s) return ''
  const d = new Date(String(s).replace(' ', 'T') + 'Z')
  if (Number.isNaN(d.getTime())) return s
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}
</script>

<template>
  <aside class="quiz-history">
    <div class="qh-header">
      <span class="qh-title">出题历史</span>
      <span v-if="total" class="qh-count">{{ total }}</span>
    </div>

    <div v-if="loading" class="qh-empty">加载中…</div>
    <div v-else-if="!batches.length" class="qh-empty">还没有出题记录</div>

    <nav v-else class="qh-list">
      <div
        v-for="b in batches"
        :key="b.batch_id"
        class="qh-item"
        :class="{ active: activeId === b.batch_id, opening: openingId === b.batch_id }"
        :title="b.subject || '未命名主题'"
        role="button"
        tabindex="0"
        @click="emit('select', b)"
        @keydown.enter.self="emit('select', b)"
        @keydown.space.self.prevent="emit('select', b)"
      >
        <div class="qh-top">
          <span class="qh-subject">{{ b.subject || '未命名主题' }}</span>
          <span class="qh-num">{{ b.question_count }} 题</span>
        </div>
        <div class="qh-meta">
          <span v-if="b.source === 'chat'" class="qh-tag">对话内</span>
          <span v-if="b.section_id" class="qh-tag">小节</span>
          <span class="qh-diff">{{ difficultyLabels[b.difficulty] || b.difficulty }}</span>
          <span class="qh-time">{{ formatTime(b.created_at) }}</span>
        </div>
      </div>
    </nav>
  </aside>
</template>

<style scoped>
.quiz-history {
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  background: transparent;
  color: var(--color-text-primary);
  user-select: none;
}

.qh-header {
  display: flex;
  align-items: center;
  gap: 8px;
  /* 左侧让开折叠按钮（它现在与这一行同名同行）；右栏下该变量为 0，max() 回落 14px */
  padding: 12px max(14px, var(--sp-head-pad-r, 0px)) 8px max(14px, var(--sp-head-pad-l, 0px));
  flex-shrink: 0;
}

.qh-title {
  font-size: 13px;
  font-weight: 600;
  letter-spacing: 0.3px;
}

.qh-count {
  font-size: 11px;
  color: var(--color-text-tertiary);
  background: var(--color-bg-hover);
  border-radius: var(--radius-lg);
  padding: 1px 7px;
}

.qh-empty {
  padding: 32px 14px;
  text-align: center;
  color: var(--color-text-tertiary);
  font-size: 13px;
}

.qh-list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 4px 8px 12px;
}

.qh-item {
  padding: 10px;
  border-radius: var(--radius-md);
  border-left: 3px solid transparent;
  margin-bottom: 4px;
  cursor: pointer;
  transition: background 0.15s, border-color 0.15s;
}

.qh-item:hover { background: var(--color-bg-hover); }
.qh-item:focus-visible { outline: 2px solid var(--color-border-focus, var(--color-accent)); outline-offset: -2px; }
.qh-item.active {
  background: var(--color-accent-light);
  border-left-color: var(--color-accent);
}
.qh-item.opening { opacity: 0.6; pointer-events: none; }

.qh-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.qh-subject {
  font-size: 13px;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.qh-num {
  flex-shrink: 0;
  font-size: 11px;
  color: var(--color-text-tertiary);
}

.qh-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 5px;
  font-size: 11px;
  color: var(--color-text-tertiary);
}

.qh-tag {
  padding: 1px 5px;
  border-radius: var(--radius-sm);
  background: var(--color-bg-hover);
  color: var(--color-text-secondary);
  font-size: 10px;
}

.qh-time { margin-left: auto; }
</style>
