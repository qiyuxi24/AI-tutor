<script setup>
/**
 * PathBoard.vue — 「学习路径」（图谱页左侧导航的一个 tab）
 *
 * 定位：图谱画布回答"知识点之间是什么关系"；本面板回答"我该按什么顺序学"。
 * 表现形式 = GitLens / 鱼骨式**依赖脊**：一条紫色主干把全部未掌握节点串成 commit 序列，
 * 左侧灰弧是前置依赖，行内挂状态徽标，第一项高亮为「建议起点」。
 *
 * 前置**不是硬门槛**（2026-11-15）：`recommended` 是后端算好的**完整建议全序**（全部未掌握
 * 节点，不剔除被挡的、不截断）。`blocked_by` / `hint` / `trace` 只是"建议先补谁"的软提示，
 * 任意节点都能点、都能去学习。状态完全派生自掌握度（后端 `mastery_bucket`），不另存待办。
 *
 * 挂载位置：`HomeView` 左侧 `SidePanel`（.graph-nav）里，与「学科分类」**共用同一块位置**，
 * 顶部两个 tab 二选一 —— 不自带卡片外观（由 SidePanel 提供），也没有「关闭」按钮。
 *
 * 纯展示组件：不调 apiClient、不碰 store —— 数据与动作都通过 props / emit 走。
 */

import { computed, nextTick, ref, watch } from 'vue'

const props = defineProps({
  items: { type: Array, default: () => [] },
  recommended: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  subject: { type: String, default: '' },
  board: { type: String, default: '' },
})

const emit = defineEmits(['focus-node', 'edit-node', 'learn-node', 'switch-tab'])

/** 四档状态口径 —— 与后端 `graph_middleware.mastery_bucket` 一一对应，颜色沿用图谱四色。 */
const STATUS = {
  mastered: { text: '已掌握', cls: 'st-mastered' },
  learning: { text: '学习中', cls: 'st-learning' },
  weak: { text: '没学好', cls: 'st-weak' },
  unstarted: { text: '未接触', cls: 'st-unstarted' },
}

const bodyRef = ref(null)
const byId = computed(() => Object.fromEntries(props.items.map((it) => [it.id, it])))

// ── 数据：一条建议路径 + 一段已完成的尾巴 ──────────────────
// recommended 已按 (层号 → 前置未达标排后 → 掌握度升序) 排好，映射回完整 item 拿徽标/提示。
const path = computed(() => props.recommended.map((r) => byId.value[r.id]).filter(Boolean))
const done = computed(() => props.items.filter((it) => it.status === 'mastered'))
const doneDots = computed(() => Math.min(done.value.length, 5))

const total = computed(() => props.items.length)
const masteredCount = computed(() => done.value.length)
const percent = computed(() => (total.value ? Math.round((masteredCount.value / total.value) * 100) : 0))

// ── 依赖脊几何：行高固定，SVG 才能和行一一对齐 ─────────────
const ROW = 48
const GUT = 50
const CX = 19
const railHeight = computed(() => path.value.length * ROW)

// 依赖弧：path 节点 j 的「未掌握直接前置」若也在 path 里（索引 i），画一条灰弧。
// ponytail: 弧线只是结构装饰，密集时截断避免糊成一团 —— 真正的"补谁"由行内 hint 承载。
const arcs = computed(() => {
  const idx = Object.fromEntries(path.value.map((n, i) => [n.id, i]))
  const seen = new Set()
  const out = []
  path.value.forEach((n, j) => {
    for (const p of n.prerequisites || []) {
      const i = idx[p.id]
      if (i === undefined || i === j) continue
      const key = `${Math.min(i, j)}-${Math.max(i, j)}`
      if (seen.has(key)) continue
      seen.add(key)
      out.push([Math.min(i, j), Math.max(i, j)])
    }
  })
  return out.slice(0, 20)
})

function arcPath(a, b, k) {
  const y1 = ROW / 2 + a * ROW
  const y2 = ROW / 2 + b * ROW
  const bx = CX - 6 - (k % 3) * 4
  return `M ${CX} ${y1} C ${bx} ${y1} ${bx} ${y2} ${CX} ${y2}`
}

// ── 展示辅助 ────────────────────────────────────────────
function statusOf(node) {
  return STATUS[node?.status] || STATUS.unstarted
}
function softHint(node) {
  const h = node?.hint || ''
  // 「可以直接开始」不值得占一行；只显示有信息量的软提示
  if (h.startsWith('建议先补') || h.startsWith('最根源') || h.startsWith('相关')) return h
  return ''
}
function chainText(trace) {
  return (trace?.chain || []).map((id) => byId.value[id]?.name || id).join(' → ')
}
function focusTarget(node) {
  return node?.trace?.focus_id || (node?.blocked_by || [])[0] || ''
}

// ── 选中 → 顶部 inspector（放在 rail 之外，行高才不会被展开内容顶歪）──
const selectedId = ref('')
const selected = computed(() => (selectedId.value ? byId.value[selectedId.value] : null))
const showDone = ref(false)

function rowById(id) {
  const nodes = bodyRef.value?.querySelectorAll?.('[data-node]') || []
  return Array.from(nodes).find((el) => el.dataset.node === String(id))
}

async function selectRow(node) {
  selectedId.value = selectedId.value === node.id ? '' : node.id
  if (!selectedId.value) return
  emit('focus-node', node.id) // 路径 ↔ 图谱联动：选中即在画布里定位
  await nextTick()
  rowById(node.id)?.scrollIntoView?.({ behavior: 'smooth', block: 'nearest' })
}

/** 点「建议先补 X」→ 跳到那个节点并展开（修掉旧版只 selectedId、不滚动的坏交互）。 */
async function jumpTo(id) {
  if (!id || !byId.value[id]) return
  selectedId.value = id
  emit('focus-node', id)
  await nextTick()
  rowById(id)?.scrollIntoView?.({ behavior: 'smooth', block: 'center' })
}

// 切片（学科 / 板块）一变，选中项可能已不在表里 → 收起 inspector
watch(() => props.items, () => {
  if (selectedId.value && !byId.value[selectedId.value]) selectedId.value = ''
})
</script>

<template>
  <aside class="path-board">
    <div ref="bodyRef" class="pb-body">
      <!-- ── 骨架 ── -->
      <div v-if="loading" class="pb-skeleton" aria-label="加载中">
        <div class="sk-bar"></div>
        <div v-for="n in 5" :key="n" class="sk-row">
          <span class="sk-dot"></span>
          <span class="sk-line" :style="{ width: (52 + ((n * 17) % 40)) + '%' }"></span>
        </div>
      </div>

      <template v-else>
        <!-- ── 空态（带动作）── -->
        <div v-if="!subject" class="pb-empty">
          <p>先在「学科分类」里选一个学科</p>
          <button class="pb-empty-act" @click="emit('switch-tab')">去选学科</button>
        </div>
        <div v-else-if="!items.length" class="pb-empty">
          <p>「{{ subject }}」还没有知识点</p>
          <button class="pb-empty-act" @click="emit('switch-tab')">换个学科</button>
        </div>

        <template v-else>
          <!-- ── 范围 + 进度条（替掉原来的 5 个统计 dot）── -->
          <p class="pb-scope">
            <b>{{ subject }}</b><span v-if="board"> · {{ board }}</span> · {{ total }} 个知识点
          </p>
          <div class="pb-prog">
            <span class="pb-prog-track"><span class="pb-prog-fill" :style="{ width: percent + '%' }"></span></span>
            <span class="pb-prog-num">{{ masteredCount }} / {{ total }}</span>
          </div>

          <!-- ── 已完成（折叠；由掌握度派生，不另存状态）── -->
          <div v-if="done.length" class="pb-done">
            <button class="pb-done-head" @click="showDone = !showDone">
              <span class="pb-done-dots"><i v-for="n in doneDots" :key="n"></i></span>
              <span>已完成 {{ done.length }} · 已掌握</span>
              <span class="pb-done-exp">{{ showDone ? '收起' : '展开' }}</span>
            </button>
            <Transition name="pb-fade">
              <div v-if="showDone" class="pb-done-list">
                <span v-for="n in done" :key="n.id" class="pb-done-chip">{{ n.name }}</span>
              </div>
            </Transition>
          </div>

          <!-- ── 全掌握 ── -->
          <div v-if="!path.length" class="pb-empty pb-all-done">这个学科的知识点都掌握了</div>

          <!-- ── 建议路径：依赖脊 ── -->
          <template v-else>
            <div class="pb-legend">
              <span><i class="lg-spine"></i>建议路径</span>
              <span><i class="lg-arc"></i>前置依赖</span>
              <span class="pb-legend-seq">按建议顺序</span>
            </div>

            <!-- 选中节点的 inspector：放在 rail 之外，避免撑歪固定行高 -->
            <Transition name="pb-fade">
              <div v-if="selected" class="pb-inspector">
                <div class="pb-insp-head">
                  <span class="pb-insp-name">{{ selected.name }}</span>
                  <span class="pb-badge" :class="statusOf(selected).cls">{{ statusOf(selected).text }}</span>
                  <button class="pb-insp-close" aria-label="收起" @click="selectedId = ''">×</button>
                </div>
                <p v-if="selected.summary" class="pb-insp-summary">{{ selected.summary }}</p>
                <p v-if="selected.trace" class="pb-insp-trace">根源缺口：{{ chainText(selected.trace) }}</p>
                <p v-if="softHint(selected)" class="pb-insp-hint">{{ softHint(selected) }}</p>
                <div class="pb-insp-acts">
                  <button class="pb-act pri" @click="emit('learn-node', selected.id)">去学习</button>
                  <button class="pb-act" @click="emit('focus-node', selected.id)">在图谱中定位</button>
                  <button class="pb-act" @click="emit('edit-node', selected.id)">详情</button>
                </div>
              </div>
            </Transition>

            <div class="pb-rail" :style="{ height: railHeight + 'px' }">
              <svg class="pb-rail-svg" :width="GUT" :height="railHeight" aria-hidden="true">
                <line :x1="CX" :y1="ROW / 2" :x2="CX" :y2="railHeight - ROW / 2" class="rail-spine" />
                <path v-for="([a, b], k) in arcs" :key="`${a}-${b}`" :d="arcPath(a, b, k)" class="rail-arc" />
                <template v-for="(n, i) in path" :key="n.id">
                  <circle v-if="i === 0" :cx="CX" :cy="ROW / 2 + i * ROW" r="9" class="rail-halo" />
                  <circle
                    :cx="CX" :cy="ROW / 2 + i * ROW" :r="i === 0 ? 5.5 : 4.5"
                    :class="i === 0 ? 'rail-dot-start' : 'rail-dot'"
                  />
                  <circle v-if="i === 0" :cx="CX" :cy="ROW / 2 + i * ROW" r="2" class="rail-dot-core" />
                </template>
              </svg>

              <div
                v-for="(n, i) in path"
                :key="n.id"
                :data-node="n.id"
                class="pb-row"
                :class="[statusOf(n).cls, { start: i === 0, on: selectedId === n.id }]"
                :style="{ animationDelay: Math.min(i, 12) * 18 + 'ms' }"
                @click="selectRow(n)"
              >
                <div class="pb-row-line">
                  <span class="pb-row-seq">{{ i + 1 }}</span>
                  <span class="pb-row-name">{{ n.name }}</span>
                  <span class="pb-badge" :class="statusOf(n).cls">{{ statusOf(n).text }}</span>
                  <span v-if="i === 0" class="pb-pill">建议起点</span>
                  <span class="pb-lv">L{{ n.level + 1 }}</span>
                </div>
                <div v-if="i === 0" class="pb-row-hint">
                  <button class="pb-go" @click.stop="emit('learn-node', n.id)">去学习</button>
                </div>
                <div v-else-if="softHint(n)" class="pb-row-hint">
                  <button class="pb-hint-btn" @click.stop="jumpTo(focusTarget(n))">↳ {{ softHint(n) }}</button>
                </div>
              </div>
            </div>
            <p class="pb-foot">点任意节点即可开始，无需按顺序</p>
          </template>
        </template>
      </template>
    </div>
  </aside>
</template>

<style scoped>
/* 卡片外观由外层 SidePanel 提供；这里只负责填满它给的高度并让主体自己滚动。
   用 flex 而非 height:100% —— 侧栏是「随内容自适应 + max-height 封顶」，百分比高度链拿不到确定值。 */
.path-board {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  width: 100%;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}
.pb-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 10px 12px 16px;
}
.pb-body::-webkit-scrollbar { width: 5px; }
.pb-body::-webkit-scrollbar-thumb { background: var(--color-border); border-radius: var(--radius-sm); }

/* ── 范围 / 进度 ── */
.pb-scope {
  margin: 0 0 8px;
  padding: 0 2px;
  font-size: var(--font-size-xs);
  line-height: 1.6;
  color: var(--color-text-tertiary);
}
.pb-scope b { color: var(--color-text-secondary); font-weight: 600; }
.pb-prog { display: flex; align-items: center; gap: 8px; margin: 0 0 12px; }
.pb-prog-track {
  flex: 1;
  height: 6px;
  border-radius: var(--radius-xl);
  background: var(--color-bg-tertiary);
  overflow: hidden;
}
.pb-prog-fill {
  display: block;
  height: 100%;
  border-radius: var(--radius-xl);
  background: var(--color-accent);
  transition: width 0.35s ease;
}
.pb-prog-num { font-size: var(--font-size-xs); font-weight: 700; color: var(--color-accent-hover); white-space: nowrap; }

/* ── 已完成折叠 ── */
.pb-done { margin: 0 0 12px; }
.pb-done-head {
  display: flex;
  align-items: center;
  gap: 7px;
  width: 100%;
  padding: 7px 9px;
  border: 1px dashed var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-green-light);
  color: var(--color-text-secondary);
  font-family: inherit;
  font-size: var(--font-size-xs);
  cursor: pointer;
}
.pb-done-dots { display: flex; gap: 3px; flex-shrink: 0; }
.pb-done-dots i { width: 8px; height: 8px; border-radius: 50%; background: var(--color-green); display: block; }
.pb-done-exp { margin-left: auto; color: var(--color-accent-hover); font-weight: 600; }
.pb-done-list { display: flex; flex-wrap: wrap; gap: 5px; padding: 8px 2px 2px; }
.pb-done-chip {
  font-size: var(--font-size-xs);
  padding: 2px 8px;
  border-radius: var(--radius-xl);
  border: 1px solid var(--color-border-subtle);
  background: var(--color-bg-tertiary);
  color: var(--color-text-tertiary);
}

/* ── 图例 ── */
.pb-legend {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: var(--font-size-xs);
  color: var(--color-text-tertiary);
  margin-bottom: 8px;
}
.pb-legend i { display: inline-block; width: 14px; height: 0; vertical-align: middle; margin-right: 4px; }
.lg-spine { border-top: 2px solid var(--color-accent); }
.lg-arc { border-top: 2px solid var(--color-border); }
.pb-legend-seq { margin-left: auto; }

/* ── 选中 inspector ── */
.pb-inspector {
  border: 1px solid var(--color-accent);
  border-radius: var(--radius-md);
  background: var(--color-accent-light);
  padding: 8px 10px;
  margin-bottom: 10px;
}
.pb-insp-head { display: flex; align-items: center; gap: 6px; }
.pb-insp-name { font-size: 12.5px; font-weight: 600; color: var(--color-text-primary); flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pb-insp-close {
  border: none; background: transparent; cursor: pointer; font-family: inherit;
  font-size: 16px; line-height: 1; color: var(--color-text-tertiary); padding: 0 2px;
}
.pb-insp-summary, .pb-insp-trace, .pb-insp-hint {
  margin: 5px 0 0;
  font-size: var(--font-size-xs);
  line-height: 1.55;
  color: var(--color-text-secondary);
  word-break: break-word;
}
.pb-insp-trace { font-family: ui-monospace, Menlo, Consolas, monospace; }
.pb-insp-hint { color: var(--color-accent-hover); }
.pb-insp-acts { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 9px; }
.pb-act {
  font-family: inherit;
  font-size: var(--font-size-xs);
  padding: 4px 9px;
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border);
  background: var(--color-bg-card);
  color: var(--color-text-secondary);
  cursor: pointer;
}
.pb-act:hover { border-color: var(--color-border-light); color: var(--color-text-primary); }
.pb-act.pri { border-color: var(--color-accent); background: var(--color-bg-card); color: var(--color-accent-hover); font-weight: 600; }

/* ── 依赖脊：SVG 覆盖左侧 gutter，行是固定高度的标准流，圆点才能逐行对齐 ── */
.pb-rail { position: relative; }
.pb-rail-svg { position: absolute; left: 0; top: 0; z-index: 1; pointer-events: none; overflow: visible; }
.rail-spine { stroke: var(--color-accent); stroke-width: 2.5; stroke-linecap: round; }
.rail-arc { fill: none; stroke: var(--color-border); stroke-width: 1.6; }
.rail-halo { fill: none; stroke: var(--color-accent); stroke-width: 1.5; opacity: 0.45; }
.rail-dot { fill: var(--color-bg-card); stroke: var(--color-accent); stroke-width: 2.5; }
.rail-dot-start { fill: var(--color-accent); }
.rail-dot-core { fill: var(--color-bg-card); }

.pb-row {
  position: relative;
  height: 48px;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 3px;
  padding: 4px 0 4px 50px;
  border-bottom: 1px solid var(--color-border-subtle);
  cursor: pointer;
  border-radius: var(--radius-md);
  opacity: 0;
  animation: pb-in 0.28s ease forwards;
}
.pb-row:last-child { border-bottom: none; }
.pb-row:hover { background: var(--color-bg-hover); }
.pb-row.on { background: var(--color-accent-light); }
.pb-row.start { background: var(--color-accent-light); }
@keyframes pb-in { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: none; } }

.pb-row-line { display: flex; align-items: center; gap: 5px; min-width: 0; }
.pb-row-seq {
  flex-shrink: 0;
  width: 15px; height: 15px;
  border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  font-size: 10px; font-weight: 700;
  background: var(--color-bg-surface); color: var(--color-text-tertiary);
}
.pb-row.start .pb-row-seq { background: var(--color-accent); color: var(--color-text-inverse); }
.pb-row-name {
  flex: 1; min-width: 0;
  font-size: 12.5px; font-weight: 600; line-height: 1.3;
  color: var(--color-text-primary);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.pb-row.start .pb-row-name { color: var(--color-accent-hover); }
.pb-badge { flex-shrink: 0; font-size: 10px; padding: 1px 6px; border-radius: var(--radius-xl); white-space: nowrap; font-weight: 500; }
.st-mastered { background: var(--color-green); color: var(--color-text-inverse); }
.st-learning { background: var(--color-yellow); color: var(--color-text-inverse); }
.st-weak { background: var(--color-red); color: var(--color-text-inverse); }
.st-unstarted { background: var(--color-bg-surface); color: var(--color-text-secondary); }
.pb-pill { flex-shrink: 0; font-size: 9.5px; padding: 1px 6px; border-radius: var(--radius-xl); background: var(--color-accent); color: var(--color-text-inverse); font-weight: 600; white-space: nowrap; }
.pb-lv { flex-shrink: 0; font-size: 10px; color: var(--color-text-muted); white-space: nowrap; }
.pb-row-hint { font-size: 10.5px; line-height: 1.4; color: var(--color-accent-hover); min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pb-go, .pb-hint-btn {
  border: none; background: transparent; padding: 0; font-family: inherit;
  font-size: 10.5px; font-weight: 700; color: var(--color-accent-hover); cursor: pointer;
  text-align: left; max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.pb-hint-btn { font-weight: 500; }
.pb-go:hover, .pb-hint-btn:hover { text-decoration: underline; }

.pb-foot { margin: 10px 0 0; font-size: var(--font-size-xs); color: var(--color-text-muted); text-align: center; }

/* ── 空态 / 骨架 ── */
.pb-empty { padding: 28px 8px; text-align: center; font-size: 12px; line-height: 1.7; color: var(--color-text-tertiary); }
.pb-empty p { margin: 0 0 10px; }
.pb-empty-act {
  font-family: inherit; font-size: var(--font-size-xs); font-weight: 600;
  padding: 5px 12px; border-radius: var(--radius-md);
  border: 1px solid var(--color-accent); background: var(--color-accent-light);
  color: var(--color-accent-hover); cursor: pointer;
}
.pb-all-done { color: var(--color-green); font-weight: 600; }
.pb-skeleton { padding: 4px 2px; }
.sk-bar { height: 6px; border-radius: var(--radius-xl); background: var(--color-bg-tertiary); margin-bottom: 14px; }
.sk-row { display: flex; align-items: center; gap: 10px; height: 48px; border-bottom: 1px solid var(--color-border-subtle); }
.sk-dot { width: 12px; height: 12px; border-radius: 50%; background: var(--color-bg-tertiary); flex-shrink: 0; }
.sk-line { height: 9px; border-radius: var(--radius-sm); background: var(--color-bg-tertiary); animation: sk-pulse 1.2s ease-in-out infinite; }
@keyframes sk-pulse { 0%, 100% { opacity: 0.5; } 50% { opacity: 1; } }

/* ── 过渡 ── */
.pb-fade-enter-active, .pb-fade-leave-active { transition: opacity 0.16s ease, transform 0.16s ease; }
.pb-fade-enter-from, .pb-fade-leave-to { opacity: 0; transform: translateY(-4px); }

@media (prefers-reduced-motion: reduce) {
  .pb-row { animation: none; opacity: 1; }
  .pb-prog-fill, .sk-line { transition: none; animation: none; }
}
</style>
