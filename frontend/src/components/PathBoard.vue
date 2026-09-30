<script setup>
/**
 * PathBoard.vue — 右侧「学习任务栏」（分层学习看板）
 *
 * 定位：图谱画布只回答"知识点之间是什么关系"；本面板回答"我该按什么顺序学、卡在哪"。
 * 因此路径不再画在图上（图上一淡出就丢上下文），而是抽成一条独立的表。
 *
 * 结构（对齐 ds-path-board.html 原型）：
 *   1. 顶部：学科 + 统计 + 「推荐学习顺序」（已解锁且未掌握，按层号排）
 *   2. 分层列表：**没有前置的知识点并排放在第 1 层**，下层等前置掌握后解锁
 *   3. 点卡片就地展开：直接前置 / 学完它解锁 / 向前追溯结果 + 两个动作按钮
 *
 * 「向前追溯」由后端 `path_board` 算好（`trace.chain` 是 id 列表，名字/状态从本组件
 * 的 items 里取，避免同一份数据在接口里重复两遍）。
 *
 * 纯展示组件：不调 apiClient、不碰 store —— 数据与动作都通过 props / emit 走。
 */

import { computed, ref, watch } from 'vue'

const props = defineProps({
  items: { type: Array, default: () => [] },
  recommended: { type: Array, default: () => [] },
  stats: { type: Object, default: () => ({}) },
  loading: { type: Boolean, default: false },
  subject: { type: String, default: '' },
  board: { type: String, default: '' },
})

const emit = defineEmits(['close', 'focus-node', 'edit-node', 'learn-node'])

/**
 * 四档状态显示口径 —— 与后端 `graph_middleware.mastery_bucket` 的取值一一对应
 * （unstarted / weak / learning / mastered），颜色沿用图谱节点四色，不另起一套。
 */
const STATUS = {
  mastered: { text: '已掌握', cls: 'st-mastered' },
  learning: { text: '学习中', cls: 'st-learning' },
  weak: { text: '没学好', cls: 'st-weak' },
  unstarted: { text: '未接触', cls: 'st-unstarted' },
}

const byId = computed(() => Object.fromEntries(props.items.map((it) => [it.id, it])))

const levels = computed(() => {
  const map = new Map()
  for (const it of props.items) {
    if (!map.has(it.level)) map.set(it.level, [])
    map.get(it.level).push(it)
  }
  return [...map.entries()]
    .map(([level, items]) => ({ level, items }))
    .sort((a, b) => a.level - b.level)
})

const statText = computed(() => [
  ['已掌握', props.stats.mastered, 'dot-mastered'],
  ['学习中', props.stats.learning, 'dot-learning'],
  ['没学好', props.stats.weak, 'dot-weak'],
  ['未接触', props.stats.unstarted, 'dot-unstarted'],
  ['待解锁', props.stats.locked, 'dot-locked'],
].filter(([, n]) => typeof n === 'number'))

/** 有"没学好"且能追到根源的节点 → 顶部给一条提醒（原型的红条） */
const weakTraces = computed(() => props.items.filter((it) => it.status === 'weak' && it.trace))

const selectedId = ref('')
const selected = computed(() => (selectedId.value ? byId.value[selectedId.value] : null))

function toggle(id) {
  selectedId.value = selectedId.value === id ? '' : id
}

function nameOf(id) {
  return byId.value[id]?.name || id
}

function statusOf(id) {
  return STATUS[byId.value[id]?.status] || STATUS.unstarted
}

function chainText(trace) {
  return (trace?.chain || []).map(nameOf).join(' → ')
}

/**
 * 第 1 层的提示文案。
 *
 * 层号 0 里现在可能混着两种节点：**真起点**（什么前置都没有），以及
 * **只有相关概念的节点**（后端把它们并入相关节点的同层，所以也可能落在这里）。
 * 一律写"可以从这里起步"会把后一种说错 —— 它们不是起点，只是和别的知识点相关。
 */
function layer0Tip(lv) {
  const withRelated = lv.items.filter((it) => (it.related || []).length).length
  if (!withRelated) return '没有前置，可以从这里起步'
  return `含 ${withRelated} 个「与其它知识点相关」的节点，不是起点`
}

// ══════════ 一键生成学习任务（纯规则、不落库）══════════
// 候选判据与后端 `graph_middleware.path_board` 的 recommended **完全一致**：
//   ① 未掌握（status !== mastered）  ② 没有未掌握的直接前置（blocked_by 为空 = 已解锁）
// 唯一差别：后端 recommended 截断到 5 个（那是给「推荐学习顺序」当快捷入口用的），
// 这里用户要的是「当前切片里能做、还没做完的全部」，所以不截断。
// 零 LLM、零请求、秒出 —— 点一下只是把 props.items 过滤 + 排序成 id 列表，所以没有 loading 态。
// 不落库：组件内 ref 存 id，关面板/刷新即失（也没有"勾选完成"的本地状态）。
// 「完成」的唯一可信来源是知识点的掌握度本身 —— 所以任务项的状态徽标实时取自图谱，
// 不另存一份待办状态（两份状态必然不一致）。
const generated = ref(false)
const taskIds = ref([])

const candidates = computed(() => props.items
  .filter((it) => it.status !== 'mastered' && !(it.blocked_by || []).length)
  .sort((a, b) => (a.level - b.level)
    || (a.mastery - b.mastery)
    || String(a.id).localeCompare(String(b.id))))

const taskItems = computed(() => taskIds.value.map((id) => byId.value[id]).filter(Boolean))
const taskDoneCount = computed(() => taskItems.value.filter((it) => it.status === 'mastered').length)

function generateTasks() {
  taskIds.value = candidates.value.map((it) => it.id)
  generated.value = true
}

function clearTasks() {
  taskIds.value = []
  generated.value = false
}

// 切片（学科 / 板块）一变，已生成的任务就是上一个切片的 → 自动按新切片重排一次
// （"重新生成"的语义保留：用户点了清空后就不再自动生成）
watch(() => [props.subject, props.board], () => {
  if (generated.value) generateTasks()
})

// 图谱切换学科/板块后，选中项可能已不在表里 → 收起详情，避免指向不存在的节点
watch(() => props.items, () => {
  if (selectedId.value && !byId.value[selectedId.value]) selectedId.value = ''
})
</script>

<template>
  <aside class="path-board">
    <!-- ── 头部 ── -->
    <header class="pb-header">
      <div class="pb-title">
        <h2>学习任务栏</h2>
        <p class="pb-sub">
          <template v-if="subject">{{ subject }}<span v-if="board"> · {{ board }}</span> · </template>
          共 {{ items.length }} 个知识点 · {{ levels.length }} 层
        </p>
      </div>
      <button class="pb-close" title="关闭" @click="emit('close')">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round">
          <line x1="18" y1="6" x2="6" y2="18" />
          <line x1="6" y1="6" x2="18" y2="18" />
        </svg>
      </button>
    </header>

    <div class="pb-body">
      <!-- ── 加载 / 空态 ── -->
      <div v-if="loading" class="pb-empty">加载中…</div>
      <div v-else-if="!subject" class="pb-empty">先在左侧选一个学科，任务栏会按它的先修关系排层。</div>
      <div v-else-if="!items.length" class="pb-empty">该范围还没有知识点。</div>

      <template v-else>
        <!-- ── 统计 ── -->
        <div class="pb-stats">
          <span v-for="[label, n, dot] in statText" :key="label" class="pb-stat">
            <i class="dot" :class="dot"></i>{{ label }} {{ n }}
          </span>
        </div>

        <!-- ── 没学好的提醒（点一下滚到那个知识点） ── -->
        <div v-if="weakTraces.length" class="pb-warn">
          <div class="pb-warn-title">有 {{ weakTraces.length }} 个知识点没学好</div>
          <button
            v-for="it in weakTraces.slice(0, 3)"
            :key="it.id"
            class="pb-warn-item"
            @click="selectedId = it.id"
          >
            「{{ it.name }}」→ 建议先补 <b>{{ nameOf(it.trace.focus_id) }}</b>
          </button>
        </div>

        <!-- ── 一键生成学习任务（按钮区）── -->
        <div class="pb-tools">
          <template v-if="!generated">
            <button class="pb-gen" :disabled="!candidates.length" @click="generateTasks">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                <path d="M13 2 4.5 13.5H11l-1 8.5 8.5-11.5H12l1-8.5z" />
              </svg>
              一键生成学习任务
              <span v-if="candidates.length" class="pb-gen-n">{{ candidates.length }}</span>
            </button>
            <span v-if="!candidates.length" class="pb-tools-hint">没有「已解锁且未掌握」的知识点</span>
          </template>
          <template v-else>
            <span class="pb-tools-info">
              共 {{ taskItems.length }} 项<template v-if="taskDoneCount"> · 已完成 {{ taskDoneCount }}</template>
            </span>
            <button class="pb-tool" @click="generateTasks">重新生成</button>
            <button class="pb-tool" @click="clearTasks">清空</button>
          </template>
        </div>

        <!-- ── 学习任务清单（一键生成后出现）── -->
        <section v-if="generated" class="pb-block">
          <h3 class="pb-h3">学习任务清单<span class="pb-count">{{ taskItems.length }}</span></h3>
          <p class="pb-note">
            按「层号 → 掌握度」排序，从上往下学即可；点一条可展开它的前置与解锁。
            完成情况看知识点掌握度（≥ 70 自动标为已完成）。
          </p>
          <p v-if="!taskItems.length" class="pb-note">当前切片里没有「已解锁且未掌握」的知识点。</p>
          <div v-else class="pb-tasks">
            <button
              v-for="(it, i) in taskItems"
              :key="it.id"
              class="task"
              :class="[statusOf(it.id).cls, { first: i === 0, done: it.status === 'mastered', on: selectedId === it.id }]"
              @click="toggle(it.id)"
            >
              <span class="task-seq">{{ i + 1 }}</span>
              <span class="task-main">
                <span class="task-name">{{ it.name }}</span>
                <span class="task-meta">
                  第 {{ it.level + 1 }} 层 · {{ statusOf(it.id).text }}<template v-if="it.hint"> · {{ it.hint }}</template>
                </span>
              </span>
              <span v-if="it.status === 'mastered'" class="task-done">已完成</span>
            </button>
          </div>
        </section>

        <!-- ── 推荐学习顺序 ──
             与任务清单**互斥展示**（!generated 时才出现）：两者是同一批知识点
             （「已解锁且未掌握」），差别只是后端版截断 5 个、任务清单不截断。
             同一个面板里把同批名字列两遍只会徒增噪音，所以生成后由清单接管。 -->
        <section v-if="recommended.length && !generated" class="pb-block">
          <h3 class="pb-h3">推荐学习顺序</h3>
          <p class="pb-note">已解锁（前置都掌握了）且还没学会的知识点，按层号排。</p>
          <div class="pb-recs">
            <span v-for="(r, i) in recommended" :key="r.id" class="pb-rec" :class="{ first: i === 0 }">
              {{ i + 1 }}. {{ r.name }}
            </span>
          </div>
        </section>

        <!-- ── 分层列表 ── -->
        <section v-for="lv in levels" :key="lv.level" class="pb-block">
          <h3 class="pb-h3">
            第 {{ lv.level + 1 }} 层
            <span v-if="lv.level === 0" class="pb-h3-tip">{{ layer0Tip(lv) }}</span>
            <span class="pb-count">{{ lv.items.length }}</span>
          </h3>

          <div class="pb-cards">
            <template v-for="it in lv.items" :key="it.id">
              <!-- ── 知识点卡片 ── -->
              <button
                class="kp"
                :class="[statusOf(it.id).cls, { locked: it.blocked_by.length > 0, on: selectedId === it.id }]"
                @click="toggle(it.id)"
              >
                <span class="kp-top">
                  <span class="kp-name">{{ it.name }}</span>
                  <span class="kp-badge">{{ statusOf(it.id).text }}</span>
                </span>
                <span v-if="it.hint" class="kp-hint">{{ it.hint }}</span>
                <span class="kp-pre">
                  <template v-if="it.prerequisites.length">
                    前置
                    <i
                      v-for="p in it.prerequisites"
                      :key="p.id"
                      class="pip"
                      :class="statusOf(p.id).cls"
                      :title="`${p.name}（${statusOf(p.id).text}）`"
                    ></i>
                  </template>
                  <!-- 只有相关概念（无前置）：拆开写清楚"和谁相关、哪种关系"，
                       不能写成"起点"—— 那正是本次要修的错误读法 -->
                  <template v-else-if="(it.related || []).length">
                    相关
                    <span
                      v-for="r in (it.related || []).slice(0, 2)"
                      :key="r.id"
                      class="rel-chip"
                      :title="`${r.name}（${r.relation_label}）`"
                    >{{ r.name }}</span>
                    <span v-if="it.related.length > 2" class="rel-more">+{{ it.related.length - 2 }}</span>
                  </template>
                  <template v-else>无前置 · 起点</template>
                </span>
              </button>

              <!-- ── 展开详情（就地展开，不跳走） ── -->
              <div v-if="selectedId === it.id" class="kp-detail">
                <p class="kd-name">{{ selected.name }}</p>
                <p class="kd-sub">
                  第 {{ selected.level + 1 }} 层
                  <template v-if="selected.summary"> · {{ selected.summary }}</template>
                </p>

                <!-- 相关概念（非前置关系）：单独列出来，与"开它解锁"分开 ——
                     前者只说明"摆在一起看"，不决定学习顺序 -->
                <template v-if="(selected.related || []).length">
                  <h4 class="kd-h4">相关概念（{{ selected.related.length }}）</h4>
                  <ul class="kd-list">
                    <li v-for="r in selected.related" :key="r.id" :class="statusOf(r.id).cls">
                      <span>{{ r.name }}<span class="kd-rel-label"> · {{ r.relation_label }}</span></span>
                      <span class="kd-s">{{ statusOf(r.id).text }}</span>
                    </li>
                  </ul>
                </template>

                <h4 class="kd-h4">直接前置（{{ selected.prerequisites.length }}）</h4>
                <p v-if="!selected.prerequisites.length" class="kd-empty">
                  <template v-if="(selected.related || []).length">
                    没有前置 —— 它与上面列出的概念相关，但<b>不是</b>学习起点。
                  </template>
                  <template v-else>没有前置 —— 它就是起点。</template>
                </p>
                <ul v-else class="kd-list">
                  <li v-for="p in selected.prerequisites" :key="p.id" :class="statusOf(p.id).cls">
                    <span>{{ p.name }}</span>
                    <span class="kd-s">{{ statusOf(p.id).text }}</span>
                  </li>
                </ul>

                <h4 class="kd-h4">学完它解锁（{{ selected.unlocks.length }}）</h4>
                <p v-if="!selected.unlocks.length" class="kd-empty">暂时没有下游知识点。</p>
                <ul v-else class="kd-list">
                  <li v-for="u in selected.unlocks" :key="u.id" :class="statusOf(u.id).cls">
                    <span>{{ u.name }}</span>
                    <span class="kd-s">{{ statusOf(u.id).text }}</span>
                  </li>
                </ul>

                <!-- 向前追溯：只盯直接前置会漏掉"直接前置已掌握、它自己没掌握"的根源缺口 -->
                <div v-if="selected.trace" class="kd-trace">
                  <b>向前追溯结果</b>：找到 {{ selected.trace.unmastered_count }} 个没掌握的前置。
                  <div class="kd-chain">{{ chainText(selected.trace) }}</div>
                  <div class="kd-advice">建议先补：<b>{{ nameOf(selected.trace.focus_id) }}</b></div>
                </div>

                <div class="kd-acts">
                  <button class="kd-act pri" @click="emit('learn-node', selected.id)">去学习</button>
                  <button class="kd-act" @click="emit('focus-node', selected.id)">在图谱中定位</button>
                  <button class="kd-act" @click="emit('edit-node', selected.id)">修改掌握度</button>
                </div>
              </div>
            </template>
          </div>
        </section>

        <p class="pb-foot">
          规则：前置全部掌握（掌握度 ≥ 70）后该知识点才算解锁；标成「没学好」时会沿依赖向前追溯，
          定位到最根源的未掌握知识点。
        </p>
      </template>
    </div>
  </aside>
</template>

<style scoped>
.path-board {
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100%;
  background: var(--color-bg-secondary);
  border: 1px solid var(--color-border);
  border-radius: 12px;
  overflow: hidden;
}

/* ── 头部 ── */
.pb-header {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 12px 14px 10px;
  border-bottom: 1px solid var(--color-border);
  flex-shrink: 0;
}
.pb-title { min-width: 0; flex: 1; }
.pb-title h2 {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--color-text-primary);
}
.pb-sub {
  margin: 3px 0 0;
  font-size: 11px;
  color: var(--color-text-tertiary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pb-close {
  border: none;
  background: transparent;
  color: var(--color-text-muted);
  cursor: pointer;
  padding: 4px;
  border-radius: 6px;
  display: flex;
  flex-shrink: 0;
}
.pb-close:hover { background: var(--color-bg-hover); color: var(--color-text-primary); }

/* ── 主体滚动区 ── */
.pb-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 10px 12px 16px;
}
.pb-body::-webkit-scrollbar { width: 5px; }
.pb-body::-webkit-scrollbar-thumb { background: var(--color-border); border-radius: 3px; }

.pb-empty {
  padding: 24px 8px;
  text-align: center;
  font-size: 12px;
  line-height: 1.7;
  color: var(--color-text-tertiary);
}

/* ── 统计 ── */
.pb-stats {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 10px;
  font-size: 11px;
  color: var(--color-text-secondary);
  padding: 2px 2px 8px;
}
.pb-stat { display: flex; align-items: center; gap: 4px; }
.dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.dot-mastered { background: var(--color-green); }
.dot-learning { background: var(--color-yellow); }
.dot-weak { background: var(--color-red); }
.dot-unstarted { background: var(--color-graph-node); }
.dot-locked { background: transparent; border: 1px dashed var(--color-text-tertiary); }

/* ── 没学好提醒 ── */
.pb-warn {
  background: var(--color-red-light);
  border: 1px solid var(--color-red);
  border-radius: 8px;
  padding: 8px 10px;
  margin-bottom: 10px;
}
.pb-warn-title { font-size: 11px; font-weight: 600; color: var(--color-red); margin-bottom: 5px; }
.pb-warn-item {
  display: block;
  width: 100%;
  text-align: left;
  border: none;
  background: transparent;
  color: var(--color-text-secondary);
  font-size: 11px;
  line-height: 1.6;
  padding: 2px 0;
  cursor: pointer;
  font-family: inherit;
}
.pb-warn-item:hover { color: var(--color-text-primary); }

/* ── 区块 ── */
.pb-block { margin-bottom: 14px; }
.pb-h3 {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0 0 6px;
  font-size: 11px;
  font-weight: 600;
  color: var(--color-text-secondary);
  text-transform: none;
}
.pb-h3-tip { font-weight: 400; color: var(--color-text-muted); }
.pb-count {
  margin-left: auto;
  font-weight: 400;
  color: var(--color-text-tertiary);
}
.pb-note { margin: 0 0 6px; font-size: 11px; color: var(--color-text-muted); }
.pb-recs { display: flex; flex-wrap: wrap; gap: 5px; }
.pb-rec {
  font-size: 11px;
  padding: 3px 8px;
  border-radius: 6px;
  border: 1px solid var(--color-border);
  color: var(--color-text-secondary);
}
.pb-rec.first {
  border-color: var(--color-accent);
  background: var(--color-accent-light);
  color: var(--color-text-primary);
}

/* ── 一键生成任务（工具条）── */
.pb-tools {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0 0 10px;
}
.pb-gen {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-family: inherit;
  font-size: 11.5px;
  font-weight: 500;
  padding: 6px 11px;
  border-radius: 7px;
  border: 1px solid var(--color-accent);
  background: var(--color-accent-light);
  color: var(--color-text-primary);
  cursor: pointer;
  transition: background 0.12s;
}
.pb-gen:hover:not(:disabled) { background: var(--color-bg-hover); }
.pb-gen:disabled { opacity: 0.5; cursor: not-allowed; }
.pb-gen-n {
  font-size: 10px;
  padding: 0 5px;
  border-radius: 20px;
  background: var(--color-accent);
  color: var(--color-text-inverse);
}
.pb-tools-hint { font-size: 10.5px; color: var(--color-text-muted); }
.pb-tools-info { margin-right: auto; font-size: 11px; color: var(--color-text-secondary); }
.pb-tool {
  font-family: inherit;
  font-size: 11px;
  padding: 4px 9px;
  border-radius: 6px;
  border: 1px solid var(--color-border);
  background: var(--color-bg-tertiary);
  color: var(--color-text-secondary);
  cursor: pointer;
}
.pb-tool:hover { border-color: var(--color-border-light); color: var(--color-text-primary); }

/* ── 学习任务清单 ── */
.pb-tasks { display: flex; flex-direction: column; gap: 5px; }
.task {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  width: 100%;
  text-align: left;
  font-family: inherit;
  padding: 7px 9px;
  border: 1px solid var(--color-border-subtle);
  border-left-width: 3px;
  border-radius: 8px;
  background: var(--color-bg-tertiary);
  color: var(--color-text-primary);
  cursor: pointer;
  transition: border-color 0.12s, background 0.12s;
}
.task:hover { border-color: var(--color-border-light); }
.task.on { border-color: var(--color-accent); background: var(--color-bg-hover); }
.task-seq {
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  font-size: 10.5px;
  font-weight: 600;
  background: var(--color-bg-surface);
  color: var(--color-text-secondary);
}
.task.first .task-seq { background: var(--color-accent); color: var(--color-text-inverse); }
.task-main { display: flex; flex-direction: column; gap: 2px; flex: 1; min-width: 0; }
.task-name { font-size: 12.5px; font-weight: 500; line-height: 1.4; }
.task-meta { font-size: 10.5px; line-height: 1.5; color: var(--color-text-tertiary); }
.task-done {
  flex-shrink: 0;
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 20px;
  background: var(--color-green);
  color: var(--color-text-inverse);
}
/* 四档状态：左侧色条，与分层卡片同一套口径 */
.task.st-mastered { border-left-color: var(--color-green); }
.task.st-mastered .task-name { text-decoration: line-through; color: var(--color-text-tertiary); }
.task.st-learning { border-left-color: var(--color-yellow); }
.task.st-weak { border-left-color: var(--color-red); }
.task.st-unstarted { border-left-color: var(--color-border-light); }

/* ── 卡片 ── */
.pb-cards { display: flex; flex-direction: column; gap: 5px; }
.kp {
  display: flex;
  flex-direction: column;
  gap: 3px;
  width: 100%;
  text-align: left;
  font-family: inherit;
  padding: 7px 9px;
  border: 1px solid var(--color-border-subtle);
  border-left-width: 3px;
  border-radius: 8px;
  background: var(--color-bg-tertiary);
  color: var(--color-text-primary);
  cursor: pointer;
  transition: border-color 0.12s, background 0.12s;
}
.kp:hover { border-color: var(--color-border-light); }
.kp.on { border-color: var(--color-accent); background: var(--color-bg-hover); }
/* 待解锁：虚线 + 灰字（前置没掌握，先学别的） */
.kp.locked { border-style: dashed; border-left-style: solid; color: var(--color-text-secondary); }

.kp-top { display: flex; align-items: baseline; gap: 6px; }
.kp-name { font-size: 12.5px; font-weight: 500; line-height: 1.4; flex: 1; }
.kp-badge {
  font-size: 10px;
  padding: 1px 5px;
  border-radius: 20px;
  white-space: nowrap;
  background: var(--color-bg-surface);
  color: var(--color-text-secondary);
  flex-shrink: 0;
}
.kp-hint { font-size: 10.5px; color: var(--color-text-tertiary); line-height: 1.5; }
.kp-pre {
  display: flex;
  align-items: center;
  gap: 3px;
  font-size: 10.5px;
  color: var(--color-text-muted);
}
.pip { width: 7px; height: 7px; border-radius: 2px; display: inline-block; background: var(--color-graph-node); }

/* 相关概念 chip：与「前置」的 pip 明确区分开（前置决定顺序，相关只决定摆在哪） */
.rel-chip {
  max-width: 92px;
  padding: 0 5px;
  border-radius: 20px;
  border: 1px solid var(--color-border-subtle);
  background: var(--color-bg-surface);
  color: var(--color-text-secondary);
  font-size: 10px;
  line-height: 1.6;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.rel-more { font-size: 10px; color: var(--color-text-muted); }
.kd-rel-label { color: var(--color-text-muted); font-size: 10.5px; }

/* 四档状态：左侧色条 + 徽标底色（与图谱节点四色同源） */
.kp.st-mastered { border-left-color: var(--color-green); background: var(--color-green-light); }
.kp.st-mastered .kp-badge { background: var(--color-green); color: var(--color-text-inverse); }
.kp.st-learning { border-left-color: var(--color-yellow); background: var(--color-bg-tertiary); }
.kp.st-learning .kp-badge { background: var(--color-yellow); color: var(--color-text-inverse); }
.kp.st-weak { border-left-color: var(--color-red); background: var(--color-red-light); }
.kp.st-weak .kp-badge { background: var(--color-red); color: var(--color-text-inverse); }
.kp.st-unstarted { border-left-color: var(--color-border-light); }

.pip.st-mastered { background: var(--color-green); }
.pip.st-learning { background: var(--color-yellow); }
.pip.st-weak { background: var(--color-red); }
.pip.st-unstarted { background: var(--color-graph-node); }

/* ── 详情 ── */
.kp-detail {
  border: 1px solid var(--color-border);
  border-radius: 8px;
  padding: 9px 10px;
  margin: -2px 0 4px;
  background: var(--color-bg-secondary);
}
.kd-name { margin: 0; font-size: 12.5px; font-weight: 600; color: var(--color-text-primary); }
.kd-sub { margin: 3px 0 8px; font-size: 11px; line-height: 1.6; color: var(--color-text-tertiary); }
.kd-h4 {
  margin: 10px 0 5px;
  font-size: 11px;
  font-weight: 600;
  color: var(--color-text-secondary);
}
.kd-empty { margin: 0; font-size: 11px; color: var(--color-text-muted); }
.kd-list { list-style: none; margin: 0; padding: 0; }
.kd-list li {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  font-size: 11px;
  padding: 3px 7px;
  border-radius: 6px;
  border-left: 3px solid var(--color-border-light);
  background: var(--color-bg-tertiary);
  margin-bottom: 3px;
}
.kd-list li.st-mastered { border-left-color: var(--color-green); }
.kd-list li.st-learning { border-left-color: var(--color-yellow); }
.kd-list li.st-weak { border-left-color: var(--color-red); }
.kd-s { color: var(--color-text-tertiary); white-space: nowrap; }

.kd-trace {
  margin-top: 10px;
  padding: 8px 10px;
  border-radius: 8px;
  background: var(--color-accent-light);
  border: 1px solid var(--color-accent);
  font-size: 11px;
  line-height: 1.7;
  color: var(--color-text-secondary);
}
.kd-chain {
  margin-top: 4px;
  font-family: ui-monospace, Menlo, Consolas, monospace;
  font-size: 10.5px;
  color: var(--color-text-primary);
  word-break: break-all;
}
.kd-advice { margin-top: 5px; }

.kd-acts { display: flex; gap: 6px; margin-top: 10px; }
.kd-act {
  font-family: inherit;
  font-size: 11px;
  padding: 5px 9px;
  border-radius: 6px;
  border: 1px solid var(--color-border);
  background: var(--color-bg-tertiary);
  color: var(--color-text-secondary);
  cursor: pointer;
}
.kd-act:hover { border-color: var(--color-border-light); color: var(--color-text-primary); }
.kd-act.pri {
  border-color: var(--color-accent);
  background: var(--color-accent-light);
  color: var(--color-text-primary);
}

.pb-foot {
  margin: 4px 0 0;
  font-size: 10.5px;
  line-height: 1.7;
  color: var(--color-text-muted);
}
</style>
