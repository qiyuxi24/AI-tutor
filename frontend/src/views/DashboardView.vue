<script setup>
/**
 * DashboardView.vue — 学习进度仪表盘（活动栏"仪表盘"入口）
 *
 * 设计理念：图谱 = 地图 = 科技树。仪表盘是"概览层"，所有数字从图谱 mastery
 * 聚合而来（单一数据源，GET /knowledge/stats），图谱改了仪表盘就变。
 * 点击任意学科 / 薄弱点 / 推荐节点 → emit 事件切到图谱视图并聚焦。
 */
import { ref, computed, onMounted, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useChatStore } from '../stores/chatStore'

const store = useChatStore()
const { stats, statsLoading, subjects } = storeToRefs(store)

// 学科筛选：'' = 全部
const subjectFilter = ref('')

const emit = defineEmits(['go-graph', 'go-node'])

const overall = computed(() => stats.value?.overall || null)
const bySubject = computed(() => stats.value?.by_subject || [])
const weakPoints = computed(() => stats.value?.weak_points || [])
const nextToLearn = computed(() => stats.value?.next_to_learn || null)

/** 时长格式化：分钟 → "X 小时 Y 分" / "X 分钟" */
function formatMinutes(min) {
  if (!min || min <= 0) return '0 分钟'
  if (min < 60) return `${min} 分钟`
  const h = Math.floor(min / 60)
  const m = min % 60
  return m ? `${h} 小时 ${m} 分` : `${h} 小时`
}

/** 占比百分比（取整，用于进度条宽度） */
function pct(num, denom) {
  if (!denom) return 0
  return Math.round((num / denom) * 100)
}

/** 掌握度分布条：mastered / learning / unstarted 三色堆叠宽度 */
const dist = computed(() => {
  const o = overall.value
  if (!o) return { mastered: 0, learning: 0, unstarted: 0 }
  return {
    mastered: pct(o.mastered_count, o.node_count),
    learning: pct(o.learning_count, o.node_count),
    unstarted: pct(o.unstarted_count, o.node_count),
  }
})

async function refresh() {
  await store.fetchStats(subjectFilter.value || null)
}

// 首次进入自动拉取统计 + 学科列表
onMounted(async () => {
  await Promise.all([store.fetchSubjects(), refresh()])
})

// 切换学科筛选 → 重新拉取对应统计
watch(subjectFilter, () => refresh())

// 图谱变更（SSE）后重新拉统计，保持"图谱改了仪表盘就变"
watch(
  () => [store.graphLoaded, store.knowledgeNodes.length],
  () => {
    if (store.graphLoaded) refresh()
  },
)
</script>

<template>
  <div class="dashboard-view" data-view="dashboard">
    <!-- ═══ 顶部：标题 + 学科筛选 + 刷新 ═══ -->
    <header class="dash-header">
      <div class="dash-title-wrap">
        <h2 class="dash-title">学习进度</h2>
        <span class="dash-subtitle">数据实时来自知识图谱 · 点击卡片直达图谱</span>
      </div>
      <div class="dash-tools">
        <select v-model="subjectFilter" class="dash-subject-select" title="筛选学科">
          <option value="">全部学科</option>
          <option v-for="s in subjects" :key="s" :value="s">{{ s }}</option>
        </select>
        <button class="dash-refresh" :disabled="statsLoading" title="刷新统计" @click="refresh">
          <svg v-if="!statsLoading" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="23 4 23 10 17 10" />
            <polyline points="1 20 1 14 7 14" />
            <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />
          </svg>
          <span v-else class="dash-spin">⟳</span>
        </button>
      </div>
    </header>

    <!-- ═══ 加载 / 空状态 ═══ -->
    <div v-if="statsLoading && !stats" class="dash-loading">正在汇总图谱进度…</div>
    <div v-else-if="!overall" class="dash-empty">
      <p>暂无图谱数据。先去对话中让 AI 生成知识图谱，或进入「知识图谱」手动创建知识点。</p>
      <button class="dash-go-btn" @click="emit('go-graph', '')">前往知识图谱</button>
    </div>

    <template v-else>
      <!-- ═══ 统计卡网格 ═══ -->
      <div class="dash-stats-grid">
        <div class="dash-stat-card">
          <span class="dash-stat-value">{{ overall.node_count }}</span>
          <span class="dash-stat-label">知识点总数</span>
        </div>
        <div class="dash-stat-card dash-stat-green">
          <span class="dash-stat-value">{{ overall.mastered_count }}</span>
          <span class="dash-stat-label">已掌握 ≥70</span>
        </div>
        <div class="dash-stat-card dash-stat-yellow">
          <span class="dash-stat-value">{{ overall.learning_count }}</span>
          <span class="dash-stat-label">学习中</span>
        </div>
        <div class="dash-stat-card dash-stat-red">
          <span class="dash-stat-value">{{ overall.unstarted_count }}</span>
          <span class="dash-stat-label">未开始</span>
        </div>
      </div>

      <div class="dash-grid">
        <!-- ═══ 左列：掌握度分布 + 学科进度 ═══ -->
        <div class="dash-col dash-col-main">
          <!-- 整体掌握度分布 -->
          <section class="dash-panel">
            <h3 class="dash-panel-title">整体掌握度</h3>
            <div class="dash-dist-bar">
              <span class="dash-dist-seg seg-mastered" :style="{ width: dist.mastered + '%' }" :title="`已掌握 ${dist.mastered}%`"></span>
              <span class="dash-dist-seg seg-learning" :style="{ width: dist.learning + '%' }" :title="`学习中 ${dist.learning}%`"></span>
              <span class="dash-dist-seg seg-unstarted" :style="{ width: dist.unstarted + '%' }" :title="`未开始 ${dist.unstarted}%`"></span>
            </div>
            <div class="dash-dist-legend">
              <span><i class="legend-dot" style="background: var(--color-green)"></i>已掌握 {{ dist.mastered }}%</span>
              <span><i class="legend-dot" style="background: var(--color-yellow)"></i>学习中 {{ dist.learning }}%</span>
              <span><i class="legend-dot" style="background: var(--color-graph-node)"></i>未开始 {{ dist.unstarted }}%</span>
            </div>
            <div class="dash-meta-row">
              <span>平均掌握度 <b>{{ overall.mastery_avg }}</b> / 100</span>
              <span>完成率 <b>{{ pct(overall.mastered_count, overall.node_count) }}%</b></span>
              <span>剩余学时 ≈ <b>{{ formatMinutes(overall.estimated_minutes_remaining) }}</b></span>
            </div>
          </section>

          <!-- 学科进度列表 -->
          <section class="dash-panel">
            <h3 class="dash-panel-title">学科进度</h3>
            <div v-if="bySubject.length === 0" class="dash-panel-empty">暂无学科数据</div>
            <div
              v-for="s in bySubject"
              :key="s.subject"
              class="dash-subject-row"
              :title="`点击进入「${s.subject || '未分类'}」图谱`"
              @click="emit('go-graph', s.subject || '')"
            >
              <div class="dash-subject-top">
                <span class="dash-subject-name">{{ s.subject || '未分类' }}</span>
                <span class="dash-subject-pct">{{ pct(s.mastered_count, s.node_count) }}% 完成</span>
              </div>
              <div class="dash-subject-bar">
                <span class="dash-subject-fill" :style="{ width: pct(s.mastered_count, s.node_count) + '%' }"></span>
              </div>
              <div class="dash-subject-sub">
                <span>共 {{ s.node_count }} 个知识点</span>
                <span>已掌握 {{ s.mastered_count }} · 学习中 {{ s.learning_count }} · 未开始 {{ s.unstarted_count }}</span>
              </div>
            </div>
          </section>
        </div>

        <!-- ═══ 右列：下一步推荐 + 薄弱点 ═══ -->
        <div class="dash-col dash-col-side">
          <!-- 下一步学什么 -->
          <section class="dash-panel dash-panel-accent">
            <h3 class="dash-panel-title">下一步学什么</h3>
            <template v-if="nextToLearn">
              <div class="dash-next-card">
                <div class="dash-next-name">{{ nextToLearn.name }}</div>
                <div class="dash-next-reason">{{ nextToLearn.reason }}</div>
                <button class="dash-go-btn" @click="emit('go-node', nextToLearn.node_id)">去图谱学习</button>
              </div>
            </template>
            <div v-else class="dash-panel-empty">所有知识点已掌握，太棒了！🎉</div>
          </section>

          <!-- 薄弱点 Top3 -->
          <section class="dash-panel">
            <h3 class="dash-panel-title">薄弱点 Top{{ weakPoints.length || 3 }}</h3>
            <div v-if="weakPoints.length === 0" class="dash-panel-empty">没有薄弱点，继续保持！</div>
            <div
              v-for="(w, i) in weakPoints"
              :key="w.id"
              class="dash-weak-row"
              :title="`点击进入「${w.name}」`"
              @click="emit('go-node', w.id)"
            >
              <span class="dash-weak-rank">{{ i + 1 }}</span>
              <div class="dash-weak-info">
                <span class="dash-weak-name">{{ w.name }}</span>
                <span class="dash-weak-sub">{{ w.subject }} · 难度 {{ '★'.repeat(w.difficulty) }}{{ '☆'.repeat(Math.max(0, 5 - w.difficulty)) }}</span>
              </div>
              <span class="dash-weak-arrow">→</span>
            </div>
          </section>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.dashboard-view {
  height: 100%;
  overflow-y: auto;
  padding: 24px 28px 40px;
  background: var(--color-bg-primary);
}

/* ─── 顶部 ─── */
.dash-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 20px;
}
.dash-title-wrap { display: flex; flex-direction: column; gap: 2px; }
.dash-title { font-size: 20px; font-weight: 600; color: var(--color-text-primary); margin: 0; }
.dash-subtitle { font-size: 12px; color: var(--color-text-tertiary); }
.dash-tools { display: flex; align-items: center; gap: 8px; }
.dash-subject-select {
  background: var(--color-bg-secondary);
  color: var(--color-text-primary);
  border: 1px solid var(--color-border);
  border-radius: 8px;
  padding: 7px 10px;
  font-size: 13px;
  outline: none;
  cursor: pointer;
}
.dash-refresh {
  display: flex; align-items: center; justify-content: center;
  width: 34px; height: 34px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: var(--color-bg-secondary);
  color: var(--color-text-secondary);
  cursor: pointer;
  transition: all 0.15s;
}
.dash-refresh:hover { border-color: var(--color-accent); color: var(--color-accent); }
.dash-spin { display: inline-block; animation: dashRotate 0.8s linear infinite; }
@keyframes dashRotate { to { transform: rotate(360deg); } }

/* ─── 统计卡 ─── */
.dash-stats-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 14px;
  margin-bottom: 20px;
}
.dash-stat-card {
  background: var(--color-bg-secondary);
  border: 1px solid var(--color-border);
  border-radius: 12px;
  padding: 16px 18px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  transition: border-color 0.2s, transform 0.15s;
}
.dash-stat-card:hover { border-color: var(--color-accent); transform: translateY(-1px); }
.dash-stat-value { font-size: 30px; font-weight: 700; color: var(--color-text-primary); line-height: 1.1; }
.dash-stat-label { font-size: 12px; color: var(--color-text-tertiary); }
.dash-stat-green .dash-stat-value { color: var(--color-green); }
.dash-stat-yellow .dash-stat-value { color: var(--color-yellow); }
.dash-stat-red .dash-stat-value { color: var(--color-red); }

/* ─── 双列布局 ─── */
.dash-grid {
  display: grid;
  grid-template-columns: 1.6fr 1fr;
  gap: 20px;
  align-items: start;
}
@media (max-width: 900px) { .dash-grid { grid-template-columns: 1fr; } }
.dash-col { display: flex; flex-direction: column; gap: 20px; }

/* ─── 面板 ─── */
.dash-panel {
  background: var(--color-bg-secondary);
  border: 1px solid var(--color-border);
  border-radius: 12px;
  padding: 18px 20px;
}
.dash-panel-accent { border-color: var(--color-accent); }
.dash-panel-title { font-size: 14px; font-weight: 600; color: var(--color-text-primary); margin: 0 0 14px; }
.dash-panel-empty { font-size: 13px; color: var(--color-text-tertiary); padding: 8px 0; }

/* ─── 整体掌握度分布 ─── */
.dash-dist-bar {
  display: flex;
  height: 14px;
  border-radius: 7px;
  overflow: hidden;
  background: var(--color-bg-tertiary);
  margin-bottom: 10px;
}
.dash-dist-seg { height: 100%; transition: width 0.5s ease; }
.seg-mastered { background: var(--color-green); }
.seg-learning { background: var(--color-yellow); }
.seg-unstarted { background: var(--color-graph-node); }
.dash-dist-legend { display: flex; gap: 16px; font-size: 12px; color: var(--color-text-secondary); margin-bottom: 12px; }
.dash-dist-legend span { display: flex; align-items: center; gap: 5px; }
.legend-dot { width: 9px; height: 9px; border-radius: 50%; display: inline-block; }
.dash-meta-row { display: flex; gap: 20px; flex-wrap: wrap; font-size: 12px; color: var(--color-text-tertiary); }
.dash-meta-row b { color: var(--color-text-primary); font-weight: 600; }

/* ─── 学科进度 ─── */
.dash-subject-row {
  padding: 12px 4px;
  border-bottom: 1px solid var(--color-border-subtle);
  cursor: pointer;
  transition: background 0.15s;
  border-radius: 6px;
}
.dash-subject-row:last-child { border-bottom: none; }
.dash-subject-row:hover { background: var(--color-bg-hover); }
.dash-subject-top { display: flex; justify-content: space-between; align-items: center; margin-bottom: 7px; }
.dash-subject-name { font-size: 13px; font-weight: 600; color: var(--color-text-primary); }
.dash-subject-pct { font-size: 12px; color: var(--color-accent); font-weight: 600; }
.dash-subject-bar {
  height: 8px; border-radius: 4px;
  background: var(--color-bg-tertiary);
  overflow: hidden;
  margin-bottom: 7px;
}
.dash-subject-fill {
  display: block; height: 100%;
  background: var(--color-accent);
  border-radius: 4px;
  transition: width 0.5s ease;
}
.dash-subject-sub { display: flex; justify-content: space-between; font-size: 11px; color: var(--color-text-tertiary); }

/* ─── 下一步推荐 ─── */
.dash-next-card { display: flex; flex-direction: column; gap: 10px; }
.dash-next-name { font-size: 16px; font-weight: 600; color: var(--color-text-primary); }
.dash-next-reason { font-size: 12px; color: var(--color-text-secondary); line-height: 1.6; }
.dash-go-btn {
  align-self: flex-start;
  border: none;
  border-radius: 8px;
  padding: 8px 16px;
  background: var(--color-accent);
  color: var(--color-text-inverse);
  font-size: 13px;
  cursor: pointer;
  transition: background 0.15s;
}
.dash-go-btn:hover { background: var(--color-accent-hover); }

/* ─── 薄弱点 ─── */
.dash-weak-row {
  display: flex; align-items: center; gap: 12px;
  padding: 10px 4px;
  border-bottom: 1px solid var(--color-border-subtle);
  cursor: pointer;
  border-radius: 6px;
  transition: background 0.15s;
}
.dash-weak-row:last-child { border-bottom: none; }
.dash-weak-row:hover { background: var(--color-bg-hover); }
.dash-weak-rank {
  width: 22px; height: 22px;
  border-radius: 6px;
  background: var(--color-red-light);
  color: var(--color-red);
  font-size: 12px; font-weight: 700;
  display: flex; align-items: center; justify-content: center;
  flex-shrink: 0;
}
.dash-weak-info { display: flex; flex-direction: column; gap: 2px; flex: 1; min-width: 0; }
.dash-weak-name { font-size: 13px; color: var(--color-text-primary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.dash-weak-sub { font-size: 11px; color: var(--color-text-tertiary); }
.dash-weak-arrow { color: var(--color-text-tertiary); font-size: 13px; }

/* ─── 加载/空状态 ─── */
.dash-loading, .dash-empty {
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 14px; min-height: 300px;
  color: var(--color-text-tertiary); font-size: 13px;
  text-align: center;
}
</style>
