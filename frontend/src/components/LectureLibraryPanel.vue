<script setup>
/**
 * LectureLibraryPanel.vue — 视频导入页左栏「课程库」
 *
 * 为什么要它：导入页原先是一条窄长页面（粘贴 → 队列 → 历史），导完的讲义只在那条
 * 队列里露一下，刷新后要翻历史任务才找得回来看。成熟课程平台（Coursera / Udemy /
 * egghead）都是**左侧课程目录常驻 + 右侧内容区**：目录回答"我有哪些课/哪一集"，
 * 右侧回答"这一集讲了什么"。本面板就是那个目录。
 *
 * 数据来源刻意只有一处：父级传进来的 `tasks`（`GET /kb/import/tasks`，全站队列唯一
 * 真相）。本组件**不拉接口、不存副本** —— 另存一份就会与队列漂移。正在导入的任务
 * 与已产出的讲义从中派生，不引入第二条数据链。
 *
 * 交互：
 *   点「导入中」的任务 → 回到右侧导入台的任务列表（它自己才展示暂停/重试/顺序）；
 *   点某一集讲义 → 父级 emit('open-lecture') 路由到课时页（时间轴 + 截图 + 看原视频）。
 */
import { computed } from 'vue'
import AppIcon from './AppIcon.vue'
import { buildLectureDirectory } from '../utils/lectureLibrary.js'

const props = defineProps({
  /** 队列快照（导入进度的唯一真相），每条 task 带 items[] */
  tasks: { type: Array, default: () => [] },
  /** KB 树里所有已结构化的讲义 { docId, title }（目录的唯一真相，含老讲义） */
  kbLectures: { type: Array, default: () => [] },
  /** 当前高亮的讲义 doc_id（0 = 没选中；从课时页返回时可保留记忆） */
  activeDocId: { type: Number, default: 0 },
})

const emit = defineEmits(['new-import', 'focus-task', 'select', 'go-kb'])

const OPEN = ['pending', 'running', 'paused']
const OPEN_LABEL = { pending: '排队中', running: '导入中', paused: '已暂停' }

const openTasks = computed(() => props.tasks.filter((t) => OPEN.includes(t.status)))

/** 讲义目录 = KB 树全量打底 + 队列补元信息（合并逻辑见纯函数 lectureLibrary.js） */
const lectureItems = computed(() => buildLectureDirectory(props.tasks, props.kbLectures))

function percent(task) {
  if (!task.total) return 0
  return Math.min(100, Math.round(((task.done + task.failed) / task.total) * 100))
}
</script>

<template>
  <div class="llp">
    <!-- 首行与折叠按钮同行：左内边距由 SidePanel 的 --sp-head-pad-l 让出 -->
    <header class="llp-head">
      <span class="llp-title">课程库</span>
      <button type="button" class="llp-new" title="导入新课程" @click="emit('new-import')">
        <AppIcon name="plus" :size="15" />
      </button>
    </header>

    <div class="llp-body">
      <!-- 导入中：有才出现，空的时候不该占一段标题 -->
      <template v-if="openTasks.length">
        <p class="llp-group">导入中<span class="llp-count">{{ openTasks.length }}</span></p>
        <button
          v-for="t in openTasks"
          :key="t.id"
          type="button"
          class="llp-task"
          :class="t.status"
          title="在右侧查看任务详情"
          @click="emit('focus-task', t.id)"
        >
          <span class="llp-task-top">
            <span class="llp-status" :class="t.status">
              <i aria-hidden="true" />{{ OPEN_LABEL[t.status] || t.status }}
            </span>
            <span class="llp-pct">{{ percent(t) }}%</span>
          </span>
          <span class="llp-task-title">{{ t.title || '未命名导入' }}</span>
          <span class="llp-bar" aria-hidden="true"><i :style="{ width: percent(t) + '%' }" /></span>
          <span class="llp-task-sub">{{ t.done + t.failed }} / {{ t.total }} 集</span>
        </button>
      </template>

      <p class="llp-group">讲义<span class="llp-count">{{ lectureItems.length }}</span></p>

      <button
        v-for="it in lectureItems"
        :key="it.docId"
        type="button"
        class="llp-item"
        :class="{ active: activeDocId === it.docId }"
        :aria-current="activeDocId === it.docId ? 'page' : undefined"
        :title="it.title"
        @click="emit('select', it)"
      >
        <AppIcon name="bookOpen" :size="14" class="llp-item-icon" />
        <span class="llp-item-main">
          <span class="llp-item-title">{{ it.title }}</span>
          <span v-if="it.meta" class="llp-item-sub">{{ it.meta }}</span>
        </span>
      </button>

      <div v-if="!lectureItems.length" class="llp-empty">
        <AppIcon name="film" :size="18" />
        <p>导入一门课程后，<br />每一集的讲义会出现在这里</p>
        <button type="button" class="llp-empty-btn" @click="emit('new-import')">
          粘贴链接导入
        </button>
      </div>
    </div>

    <footer class="llp-foot">
      <button type="button" class="llp-link" @click="emit('go-kb')">去知识库看全部文件</button>
    </footer>
  </div>
</template>

<style scoped>
.llp {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

/* ── 首行：与折叠按钮同一行 ── */
.llp-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-sm);
  flex-shrink: 0;
  padding: 12px max(8px, var(--sp-head-pad-r, 0px)) 8px max(12px, var(--sp-head-pad-l, 0px));
}
.llp-title {
  font-size: 13px;
  font-weight: 600;
  letter-spacing: 0.3px;
  color: var(--color-text-primary);
}
.llp-new {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  padding: 0;
  border: none;
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--color-text-secondary);
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
}
.llp-new:hover {
  background: var(--color-bg-hover);
  color: var(--color-text-primary);
}

.llp-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 0 8px 12px;
}

/* ── 分组标题 ── */
.llp-group {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0;
  padding: 12px 10px 6px;
  font-size: var(--font-size-xs);
  font-weight: 600;
  color: var(--color-text-placeholder);
}
.llp-count {
  font-weight: 500;
  font-variant-numeric: tabular-nums;
  color: var(--color-text-placeholder);
  opacity: 0.8;
}

/* ── 导入中的任务卡：比讲义条目多一条进度，但仍是一眼能看完的块 ── */
.llp-task {
  display: flex;
  flex-direction: column;
  gap: 4px;
  width: 100%;
  margin-bottom: 4px;
  padding: 8px 10px;
  border: 1px solid var(--color-border-soft);
  border-radius: var(--radius-md);
  background: var(--color-bg-card);
  text-align: left;
  font-family: inherit;
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s;
}
.llp-task:hover { border-color: var(--color-border-strong); }

.llp-task-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.llp-status {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: var(--font-size-xs);
  color: var(--color-text-secondary);
}
.llp-status i {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: currentColor;
}
.llp-status.running { color: var(--color-primary); }
.llp-status.paused { color: var(--color-warning); }
.llp-pct {
  font-size: var(--font-size-xs);
  font-variant-numeric: tabular-nums;
  color: var(--color-text-placeholder);
}

.llp-task-title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  color: var(--color-text-primary);
}

.llp-bar {
  display: block;
  height: 2px;
  border-radius: var(--radius-full);
  background: var(--color-bg-disabled);
  overflow: hidden;
}
.llp-bar i {
  display: block;
  height: 100%;
  background: var(--color-primary);
  transition: width 0.3s ease;
}
.llp-task.paused .llp-bar i { background: var(--color-warning); }

.llp-task-sub {
  font-size: var(--font-size-xs);
  font-variant-numeric: tabular-nums;
  color: var(--color-text-placeholder);
}

/* ── 讲义条目（与对话列表同一套读法：左 3px 主色条 + 浅底 = 当前） ── */
.llp-item {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  width: 100%;
  padding: 8px 10px;
  border: none;
  border-left: 3px solid transparent;
  border-radius: var(--radius-md);
  background: transparent;
  text-align: left;
  font-family: inherit;
  cursor: pointer;
  transition: background 0.15s;
}
.llp-item:hover { background: var(--color-bg-hover); }
.llp-item.active {
  background: var(--color-primary-soft);
  border-left-color: var(--color-primary);
}
.llp-item-icon {
  flex-shrink: 0;
  margin-top: 1px;
  color: var(--color-text-placeholder);
}
.llp-item.active .llp-item-icon { color: var(--color-primary); }

.llp-item-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.llp-item-title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  color: var(--color-text-primary);
}
.llp-item.active .llp-item-title { font-weight: 600; color: var(--color-primary); }
.llp-item-sub {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--font-size-xs);
  color: var(--color-text-placeholder);
}

/* ── 空态：说清这里会长出什么 ── */
.llp-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 24px 12px;
  color: var(--color-text-placeholder);
  text-align: center;
}
.llp-empty p {
  margin: 0;
  font-size: var(--font-size-xs);
  line-height: var(--line-height-base);
}
.llp-empty-btn {
  padding: 5px 12px;
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-md);
  background: var(--color-bg-card);
  color: var(--color-text-secondary);
  font-family: inherit;
  font-size: var(--font-size-xs);
  cursor: pointer;
  transition: border-color 0.15s, color 0.15s;
}
.llp-empty-btn:hover {
  border-color: var(--color-primary);
  color: var(--color-primary);
}

.llp-foot {
  flex-shrink: 0;
  padding: 8px 12px 10px;
  border-top: 1px solid var(--color-border-soft);
}
.llp-link {
  padding: 0;
  border: none;
  background: transparent;
  color: var(--color-text-secondary);
  font-family: inherit;
  font-size: var(--font-size-xs);
  cursor: pointer;
}
.llp-link:hover { color: var(--color-primary); text-decoration: underline; }
</style>
