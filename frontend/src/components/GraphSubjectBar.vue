<script setup>
/**
 * GraphSubjectBar.vue — 学科侧栏（图谱页一级导航）
 *
 * 职责：
 *   按学科分类管理知识图谱，一次只渲染一个学科——这是防止图谱随学习无限生长
 *   （节点越堆越多、画布与提示词双双膨胀）的入口。
 *
 * 数据流：
 *   store.subjectSummaries（学科 + 节点数/掌握度统计） → 渲染
 *   store.graphOverall（跨学科聚合，原「学习进度」整页撤掉后并入这里） → 顶部汇总行
 *   store.currentSubject（当前选中） → 高亮
 *   store.setSubject()（左键点击） → 按需拉取该学科子图
 *   store.deleteSubjectGraph()（条目菜单） → 删除该学科整张图
 *
 * 交互：左键切换学科，条目右侧常驻三点按钮弹操作菜单（开关与图谱画布右键共用一套机制，
 * 见 utils/contextMenu.js）。
 *
 * 视觉：条目样式与 ConversationSidebar 统一 —— 浅色强调底 + 左侧色条表示选中，
 * 无边框条目，4px 细滚动条。
 *
 * 「未分类」是后端合成的分组（无学科归属的节点），排在列表末尾、样式弱化。
 */

import { storeToRefs } from 'pinia'
import { ElMessage, ElMessageBox } from 'element-plus'
import { confirmAction } from '../utils/feedback'
import { useChatStore } from '../stores/chatStore'
import { useContextMenu } from '../utils/contextMenu'
import ContextMenu from './ContextMenu.vue'
import ItemMoreButton from './ItemMoreButton.vue'
import AppIcon from './AppIcon.vue'

const store = useChatStore()
const { subjectSummaries, currentSubject, graphOverall } = storeToRefs(store)

// 条目菜单：与画布共用同一套开关，由常驻三点按钮触发；targetData = 该学科摘要对象
const {
  visible: menuVisible,
  x: menuX,
  y: menuY,
  targetData: menuSubject,
  openAt: openMenuAt,
  close: closeMenu,
} = useContextMenu()

function handleSelect(s) {
  store.setSubject(s.subject)
}

/** 平均掌握度 → 整数百分比 */
function masteryPct(s) {
  const v = Number(s.mastery_avg)
  return Number.isFinite(v) ? Math.round(v) : 0
}

/**
 * 重命名学科：只改课名（该学科的知识点与主题树一起改名），知识点内容不动。
 */
async function handleRename() {
  const s = menuSubject.value
  if (!s) return
  let value
  try {
    ({ value } = await ElMessageBox.prompt(
      '请输入新的学科名（该学科下的知识点会一起改名，内容不受影响）',
      '重命名学科',
      { inputValue: s.subject, confirmButtonText: '保存', cancelButtonText: '取消' }
    ))
  } catch {
    return   // 取消
  }
  const name = (value || '').trim()
  if (!name || name === s.subject) return
  // 本地先挡一层重名：与后端同规则，省一次往返（后端仍会再校验）
  if (store.subjects.includes(name)) {
    ElMessage.error(`学科「${name}」已存在，请换一个名字`)
    return
  }
  try {
    const r = await store.renameSubject(s.subject, name)
    ElMessage.success(`已重命名为「${name}」：${r.renamed_nodes} 个知识点`)
  } catch (e) {
    ElMessage.error('重命名失败：' + (e.response?.data?.detail || e.message))
  }
}

/**
 * 删除整个学科图谱（不可撤销）。
 * 只删图谱（节点/关系/节点正文），知识库里的教材原文与向量索引不动 —— 可重新建图。
 */
async function handleDelete() {
  const s = menuSubject.value
  if (!s) return
  const ok = await confirmAction(
    `确定删除学科「${s.subject}」的整个知识图谱吗？该学科 ${s.node_count} 个知识点及其关系、`
      + '掌握度记录将被永久删除。知识库中的教材原文不受影响。'
  )
  if (!ok) return   // 取消
  try {
    const r = await store.deleteSubjectGraph(s.subject)
    ElMessage.success(`已删除「${s.subject}」：${r.deleted_nodes} 个知识点`)
  } catch (e) {
    ElMessage.error('删除失败：' + (e.response?.data?.detail || e.message))
  }
}
</script>

<template>
  <aside class="graph-subject-bar">
    <div class="subject-bar-header">
      <span class="subject-bar-title">学科</span>
      <span class="subject-bar-count">{{ subjectSummaries.length }}</span>
    </div>

    <!-- 跨学科汇总行：原「学习进度」整页撤掉后只保留这一层全局视角（2026-11-15）。
         口径与学科条目一致 = mastery_avg，不引「完成率」，避免两处百分比打架。 -->
    <div v-if="graphOverall && graphOverall.node_count" class="subject-overview">
      <div class="so-row">
        <span class="so-label">全部</span>
        <span class="so-count">{{ graphOverall.node_count }} 点 · 已掌握 {{ graphOverall.mastered_count }}</span>
      </div>
      <div class="subject-bar">
        <span class="subject-bar-fill" :style="{ width: masteryPct(graphOverall) + '%' }"></span>
      </div>
      <div class="so-hint">平均掌握度 {{ masteryPct(graphOverall) }}%</div>
    </div>

    <div class="subject-list">
      <div
        v-for="s in subjectSummaries"
        :key="s.subject"
        class="subject-item"
        :class="{ active: currentSubject === s.subject, muted: s.unclassified }"
        :title="`${s.subject}：${s.node_count} 节点，已掌握 ${s.mastered_count}`"
        @click="handleSelect(s)"
      >
        <AppIcon class="subject-icon" name="layers" :size="16" />

        <span class="subject-body">
          <span class="subject-row">
            <span class="subject-name">{{ s.subject }}</span>
            <span class="subject-total">{{ s.node_count }}</span>
          </span>
          <!-- 掌握度进度条：一眼看出该学科推进到哪 -->
          <span class="subject-bar">
            <span class="subject-bar-fill" :style="{ width: masteryPct(s) + '%' }"></span>
          </span>
        </span>

        <ItemMoreButton
          title="重命名 / 删除"
          @click="openMenuAt($event, 'subject', s)"
        />
      </div>

      <p v-if="!subjectSummaries.length" class="subject-empty">
        还没有学科<br />
        <span class="subject-empty-hint">在右侧知识库勾选教材生成，或直接对话建知识点</span>
      </p>
    </div>

    <!-- 条目菜单：容器与画布共用；「未分类」是后端合成的散落节点分组，不是真实学科 -->
    <ContextMenu :visible="menuVisible" :x="menuX" :y="menuY" @close="closeMenu">
      <template #default="{ close }">
        <template v-if="menuSubject && !menuSubject.unclassified">
          <div class="menu-item" @click="close(); handleRename()">
            <span class="menu-icon">
              <AppIcon name="pencil" :size="14" />
            </span>
            重命名学科
          </div>
          <div class="menu-divider"></div>
          <div class="menu-item menu-item-danger" @click="close(); handleDelete()">
            <span class="menu-icon">
              <AppIcon name="trash" :size="14" />
            </span>
            删除「{{ menuSubject.subject }}」图谱
          </div>
        </template>
        <div v-else class="menu-item menu-item-muted" @click="close()">
          未分类的知识点请逐个删除
        </div>
      </template>
    </ContextMenu>
  </aside>
</template>

<style scoped>
.graph-subject-bar {
  display: flex;
  flex-direction: column;
  width: 100%;
  min-height: 0;
  flex-shrink: 1;
  background: var(--color-bg-secondary);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  overflow: hidden;
}

/* 标题区：与 ConversationSidebar 的 .cs-header 同款 */
.subject-bar-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 12px 8px;
  flex-shrink: 0;
}

.subject-bar-title {
  font-size: 13px;
  font-weight: 600;
  letter-spacing: 0.3px;
  color: var(--color-text-primary);
}

.subject-bar-count {
  font-size: var(--font-size-xs);
  color: var(--color-text-muted);
}

/* 跨学科汇总行（原学习进度整页撤掉后并入侧栏，一处定义、不压画布） */
.subject-overview {
  flex-shrink: 0;
  margin: 0 12px 8px;
  padding: 8px 10px;
  border-radius: var(--radius-md);
  background: var(--color-bg-tertiary);
}
.so-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 6px;
}
.so-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--color-text-primary);
}
.so-count {
  font-size: var(--font-size-xs);
  color: var(--color-text-muted);
}
.so-hint {
  margin-top: 5px;
  font-size: var(--font-size-xs);
  color: var(--color-text-muted);
}

.subject-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 0 6px 6px;
  overflow-y: auto;
  min-height: 0;
}

.subject-list::-webkit-scrollbar { width: 4px; }
.subject-list::-webkit-scrollbar-thumb {
  background: var(--color-border-light);
  border-radius: var(--radius-sm);
}

/* 条目：与 ConversationSidebar 的 .history-item 同款（无边框 + 左侧色条选中） */
.subject-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border-radius: var(--radius-md);
  border-left: 3px solid transparent;
  cursor: pointer;
  transition: background 0.15s;
}

.subject-item:hover {
  background: var(--color-bg-hover);
}

.subject-item.active {
  background: var(--color-accent-light);
  border-left-color: var(--color-accent);
}

.subject-icon {
  flex-shrink: 0;
  opacity: 0.5;
}

.subject-item.active .subject-icon {
  opacity: 1;
  color: var(--color-accent);
}

/* 三点按钮样式已收进 ItemMoreButton.vue（与知识库树 / 对话列表共用一份） */

.subject-body {
  display: flex;
  flex-direction: column;
  gap: 4px;
  flex: 1;
  min-width: 0;
}

.subject-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.subject-name {
  /* 长学科名收缩省略，别把右侧计数挤出条目（同 .kb-node 那类 min-width:auto 的坑） */
  flex: 1 1 auto;
  min-width: 0;
  font-size: 13px;
  color: var(--color-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.subject-total {
  font-size: var(--font-size-xs);
  color: var(--color-text-muted);
  flex-shrink: 0;
}

.subject-item.active .subject-total {
  color: var(--color-accent);
}

.subject-bar {
  display: block;
  height: 3px;
  border-radius: var(--radius-sm);
  background: var(--color-border-light);
  overflow: hidden;
}

.subject-bar-fill {
  display: block;
  height: 100%;
  border-radius: var(--radius-sm);
  background: var(--color-accent);
  transition: width 0.25s ease;
}

/* 「未分类」是后端合成的分组，弱化处理 */
.subject-item.muted .subject-name,
.subject-item.muted .subject-icon {
  color: var(--color-text-tertiary);
}

.subject-empty {
  margin: 4px 6px;
  font-size: 12px;
  line-height: 1.7;
  color: var(--color-text-muted);
}

.subject-empty-hint {
  font-size: var(--font-size-xs);
  opacity: 0.85;
}
</style>
