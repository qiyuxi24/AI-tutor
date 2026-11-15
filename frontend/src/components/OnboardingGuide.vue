<script setup>
/**
 * OnboardingGuide.vue — 新手引导组件
 *
 * 职责：纯展示 + 状态管理，不依赖其他组件、不碰 store / api。
 *
 * 数据流（去耦合）：
 *   localStorage (ai_tutor_onboarded) → OnboardingGuide → (emit: close) → 父组件
 *
 * 特性：
 *   - 首次访问自动弹出（props.forceShow 可强制），关闭后写 localStorage 不再显示
 *   - 6 步：欢迎 / 对话学习 / 知识图谱 / 知识库 / 出题与掌握度 / 进度与个性化
 *   - 左侧步骤导航可点击跳转，键盘 ← → 翻页、Esc 关闭
 *
 * 内容口径（改动前先对照现状，别再写已下线的东西）：
 *   - 已无「三种教学模式」——2026-09-26 起合并为单一提示词，`mode` 字段已删
 *   - 知识库不是独立页面，是图谱页右侧可收起的侧栏（KbPanel）
 *   - 掌握度的唯一主信号是出题判分（答对 +20），没有手动改掌握的入口
 */

import { ref, computed, onMounted, onUnmounted } from 'vue'
import AppIcon from './AppIcon.vue'

/* ================================================================
   组件 Props / Events
   ================================================================ */
const props = defineProps({
  /** 是否强制显示（忽略 localStorage），用于设置中重新查看 */
  forceShow: { type: Boolean, default: false },
})

const emit = defineEmits(['close'])

/* ================================================================
   状态
   ================================================================ */
const STORAGE_KEY = 'ai_tutor_onboarded'

const visible = ref(false)
const currentStep = ref(0)

/* ================================================================
   引导步骤内容
   icon = utils/icons.js 的键名，由 <AppIcon> 渲染（与活动栏同一份图形）
   ================================================================ */
const steps = [
  {
    icon: 'sparkles',
    title: '欢迎使用 AI Tutor',
    description: '一个会记着你在学什么的导学助手。它把你学到的东西存成图谱，再据此决定下一步讲什么、出什么题。',
    features: [
      '对话式学习：像聊天一样探索知识，AI 边教边更新你的图谱',
      '知识图谱：把零散知识点按前置关系连成一张学习地图',
      '学习画像与掌握度随每一次对话和做题慢慢累积，不用自己记',
      '数据都在你自己的账号下，模型与主题都可以自行配置',
    ],
    tip: '接下来按「对话 → 图谱 → 知识库 → 出题 → 进度」的顺序带你看一遍，随时可在 设置 · 引导 重新查看。',
  },
  {
    icon: 'chat',
    title: '对话学习',
    description: '把想学的内容直接说给 AI：提问、追问、让它讲、让它出题都可以，说一句话就能开一轮学习。',
    features: [
      'AI 先读你的画像与已掌握内容，再决定讲多深、怎么讲',
      '回答会先查你的图谱与知识库资料，而不是泛泛而谈',
      '对话会自动保存，左侧栏可随时切换历史会话',
      '会话里提到的知识点可以直接点开，跳到图谱对应节点',
    ],
    tip: '不需要特意「喂」背景，也不用手动切换教学模式——AI 会自己挑合适的讲法。',
  },
  {
    icon: 'graph',
    title: '知识图谱',
    description: '每个知识点是一个节点，前置与相关关系是边。图谱就是你的学习地图，也回答「接下来该学什么」。',
    features: [
      '双击节点看正文（小节化内容）、来源资料与关联试题',
      '右键新建节点 / 连线，拖拽排布，搜索框快速定位',
      '左侧按学科切换，一次只渲染一个学科，图谱不会无限膨胀',
      '三种画法：力导向 / 思维导图 / 大括号（设置 · 知识图谱）',
      '创建或改文即写入索引，之后 AI 提问会优先从这里找依据',
    ],
    tip: '节点颜色对应掌握度四档：未接触 / 没学好 / 学习中 / 已掌握。',
  },
  {
    icon: 'fileUpload',
    title: '知识库：把资料变成图谱',
    description: '上传教材、讲义、PDF / Word / Excel / 图片，AI 读完后可以一键生成整门学科的图谱。',
    features: [
      '图谱页右侧「知识库」上传文件，支持新建文件夹整理',
      '勾选文件 → 填学科名 → 一键生成图谱（阅读 → 拆概念 → 写小节）',
      '上传的资料对 AI 直接可见，回答会引用你的资料',
      '右键资料「在图谱中显示」，高亮它影响到的知识点',
      '原件可预览：PDF / Word / Excel / 图片看原文，其余看解析正文',
    ],
    tip: '上传与建图是两步：先上传索引好，再决定用哪些建图，不必一次全用上。',
  },
  {
    icon: 'quiz',
    title: '出题与掌握度',
    description: '出题页按主题、难度、题型、题量生成练习，答完立刻判分并给出解析。',
    features: [
      '五种题型：单选 / 多选 / 判断 / 填空 / 简答（简答题由 AI 判分）',
      '题目基于教材知识库生成（RAG 依据），不是空手编题',
      '答对确定性 +20 掌握度，答错会回落——这是掌握度的唯一主信号',
      '题目可导出、按批次回看历史练习与正确率',
      '从节点详情「去出题」可直接聚焦某个知识点',
    ],
    tip: '想让它更贴合你，多做题比多聊天更有效——掌握度只认真实作答。',
  },
  {
    icon: 'route',
    title: '学习进度与个性化',
    description: '进度不单独占一页：学科侧栏看掌握度，学习路径看「先学什么」，出题把掌握度往上推。',
    features: [
      '学科侧栏：顶部一条全局汇总，每个学科都有节点数与掌握度进度条',
      '学习路径：图谱左侧「学习路径」tab 按先修关系排好顺序，前置只是建议、不挡跳级',
      '掌握度四色：未开始 / 薄弱 / 学习中 / 已掌握，图谱与侧栏图例同口径',
      '悬浮球：不在对话页也能随手问一句（设置 · 悬浮球）',
      '模型与主题自配：每人独立 Key、主备自动降级（设置 · 模型 / 外观）',
    ],
    tip: '随时可以在 设置 · 引导 重新打开这份说明。',
  },
]

const isLast = computed(() => currentStep.value === steps.length - 1)
const progress = computed(() => Math.round(((currentStep.value + 1) / steps.length) * 100))

/* ================================================================
   操作
   ================================================================ */
function go(step) {
  currentStep.value = Math.min(Math.max(step, 0), steps.length - 1)
}

function next() {
  isLast.value ? close() : go(currentStep.value + 1)
}

function prev() {
  go(currentStep.value - 1)
}

function close() {
  visible.value = false
  localStorage.setItem(STORAGE_KEY, 'true')
  emit('close')
}

/* ================================================================
   键盘导航（仅在可见时生效）
   ================================================================ */
function onKeydown(e) {
  if (!visible.value) return
  if (e.key === 'Escape') close()
  else if (e.key === 'ArrowRight') next()
  else if (e.key === 'ArrowLeft') prev()
}

/* ================================================================
   初始化
   ================================================================ */
onMounted(() => {
  window.addEventListener('keydown', onKeydown)
  if (props.forceShow || !localStorage.getItem(STORAGE_KEY)) visible.value = true
})

onUnmounted(() => {
  window.removeEventListener('keydown', onKeydown)
})

/* ================================================================
   暴露方法（父组件可调用重新显示）
   ================================================================ */
defineExpose({
  show: () => {
    visible.value = true
    currentStep.value = 0
  },
})
</script>

<template>
  <Teleport to="body">
    <Transition name="ob-overlay">
      <div v-if="visible" class="ob-overlay" @click.self="close">
        <div
          class="ob-card"
          role="dialog"
          aria-modal="true"
          aria-labelledby="ob-title"
        >
          <!-- ═══ 左：步骤导航 ═══ -->
          <aside class="ob-nav">
            <div class="ob-nav-list">
              <button
                v-for="(s, i) in steps"
                :key="s.title"
                class="ob-nav-item"
                :class="{ active: i === currentStep, done: i < currentStep }"
                @click="go(i)"
              >
                <span class="ob-nav-index">{{ i + 1 }}</span>
                <span class="ob-nav-text">{{ s.title }}</span>
              </button>
            </div>

            <div class="ob-nav-foot">
              <div class="ob-progress">
                <span class="ob-progress-bar" :style="{ width: progress + '%' }"></span>
              </div>
              <div class="ob-nav-count">{{ currentStep + 1 }} / {{ steps.length }}</div>
            </div>
          </aside>

          <!-- ═══ 右：当前步骤内容 ═══ -->
          <section class="ob-body">
            <Transition name="ob-fade" mode="out-in">
              <div class="ob-step" :key="currentStep">
                <div class="ob-icon">
                  <AppIcon :name="steps[currentStep].icon" :size="26" :stroke="1.8" />
                </div>

                <h2 id="ob-title" class="ob-title">{{ steps[currentStep].title }}</h2>
                <p class="ob-desc">{{ steps[currentStep].description }}</p>

                <ul class="ob-features">
                  <li v-for="feat in steps[currentStep].features" :key="feat">
                    <AppIcon name="check" :size="14" />
                    <span>{{ feat }}</span>
                  </li>
                </ul>

                <p class="ob-tip">
                  <AppIcon name="info" :size="14" />
                  <span>{{ steps[currentStep].tip }}</span>
                </p>
              </div>
            </Transition>

            <footer class="ob-actions">
              <button v-if="!isLast" class="ob-skip" @click="close">跳过引导</button>
              <span v-else class="ob-skip-placeholder"></span>

              <div class="ob-buttons">
                <button v-if="currentStep > 0" class="ob-btn ob-prev" @click="prev">
                  <AppIcon name="chevronLeft" :size="14" />
                  上一步
                </button>
                <button class="ob-btn ob-next" @click="next">
                  {{ isLast ? '开始使用' : '下一步' }}
                  <AppIcon v-if="!isLast" name="chevronRight" :size="14" />
                </button>
              </div>
            </footer>
          </section>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
/* ════════════════════════════════════════════
   遮罩层
   ════════════════════════════════════════════ */
.ob-overlay {
  position: fixed;
  inset: 0;
  z-index: 1000;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: var(--color-bg-overlay);
  backdrop-filter: blur(4px);
}

/* ════════════════════════════════════════════
   卡片（左导航 + 右内容）
   ════════════════════════════════════════════ */
.ob-card {
  display: flex;
  width: 780px;
  max-width: 100%;
  max-height: 100%;
  background: var(--color-bg-primary);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-popup);
  overflow: hidden;
}

/* ── 左侧步骤导航 ── */
.ob-nav {
  flex: 0 0 226px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 22px 14px 18px;
  background: var(--color-bg-secondary);
  border-right: 1px solid var(--color-border);
}

.ob-nav-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}

.ob-nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 9px 10px;
  border: none;
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--color-text-secondary);
  font-family: inherit;
  font-size: 13px;
  text-align: left;
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
}
.ob-nav-item:hover { background: var(--color-bg-hover); color: var(--color-text-primary); }
.ob-nav-item.active { background: var(--color-accent-light); color: var(--color-accent); font-weight: 600; }

.ob-nav-index {
  flex-shrink: 0;
  width: 22px;
  height: 22px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  background: var(--color-bg-surface);
  color: var(--color-text-tertiary);
  font-size: var(--font-size-xs);
  font-weight: 600;
  transition: background 0.15s, color 0.15s;
}
.ob-nav-item.active .ob-nav-index { background: var(--color-accent); color: #fff; }
.ob-nav-item.done .ob-nav-index { background: var(--color-green-light); color: var(--color-green); }

.ob-nav-text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ob-nav-foot {
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding-top: 10px;
  border-top: 1px solid var(--color-border-subtle);
}
.ob-progress {
  height: 4px;
  border-radius: var(--radius-full);
  background: var(--color-bg-disabled);
  overflow: hidden;
}
.ob-progress-bar {
  display: block;
  height: 100%;
  border-radius: var(--radius-full);
  background: var(--color-accent);
  transition: width 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}
.ob-nav-count {
  font-size: var(--font-size-xs);
  color: var(--color-text-tertiary);
  font-variant-numeric: tabular-nums;
}

/* ── 右侧内容 ── */
.ob-body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  padding: 32px 34px 22px;
}

.ob-step {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}

.ob-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 46px;
  height: 46px;
  border-radius: var(--radius-lg);
  background: var(--color-accent-light);
  color: var(--color-accent);
  margin-bottom: 16px;
}

.ob-title {
  font-size: 20px;
  font-weight: 700;
  color: var(--color-text-primary);
  margin: 0 0 8px;
}

.ob-desc {
  font-size: 14px;
  color: var(--color-text-secondary);
  line-height: 1.65;
  margin: 0 0 18px;
}

.ob-features {
  list-style: none;
  padding: 0;
  margin: 0 0 16px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.ob-features li {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  font-size: 13px;
  line-height: 1.55;
  color: var(--color-text-primary);
  padding: 9px 12px;
  background: var(--color-bg-secondary);
  border: 1px solid var(--color-border-subtle);
  border-radius: var(--radius-md);
}
.ob-features li svg {
  flex-shrink: 0;
  margin-top: 2px;
  color: var(--color-green);
}

.ob-tip {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin: 0;
  padding: 10px 12px;
  border-radius: var(--radius-md);
  background: var(--color-info-soft);
  color: var(--color-text-secondary);
  font-size: 12.5px;
  line-height: 1.6;
}
.ob-tip svg { flex-shrink: 0; margin-top: 1px; color: var(--color-info); }

/* ── 底部操作 ── */
.ob-actions {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding-top: 18px;
  margin-top: 16px;
  border-top: 1px solid var(--color-border-subtle);
}

.ob-skip {
  border: none;
  background: transparent;
  color: var(--color-text-tertiary);
  font-family: inherit;
  font-size: 13px;
  cursor: pointer;
  padding: 6px 8px;
  border-radius: var(--radius-sm);
  transition: color 0.15s, background 0.15s;
}
.ob-skip:hover { color: var(--color-text-secondary); background: var(--color-bg-hover); }
.ob-skip-placeholder { width: 1px; }

.ob-buttons {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-left: auto;
}

.ob-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-family: inherit;
  font-size: 13px;
  font-weight: 600;
  padding: 8px 18px;
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: all 0.15s ease;
}
.ob-prev {
  border: 1px solid var(--color-border);
  background: var(--color-bg-primary);
  color: var(--color-text-secondary);
}
.ob-prev:hover { background: var(--color-bg-hover); color: var(--color-text-primary); }
.ob-next {
  border: 1px solid var(--color-accent);
  background: var(--color-accent);
  color: var(--color-text-inverse);
}
.ob-next:hover { background: var(--color-accent-hover); border-color: var(--color-accent-hover); }

/* ════════════════════════════════════════════
   过渡动画
   ════════════════════════════════════════════ */
.ob-overlay-enter-active { transition: opacity 0.25s ease; }
.ob-overlay-leave-active { transition: opacity 0.2s ease; }
.ob-overlay-enter-from,
.ob-overlay-leave-to { opacity: 0; }

.ob-fade-enter-active { transition: opacity 0.22s ease, transform 0.22s ease; }
.ob-fade-leave-active { transition: opacity 0.14s ease; }
.ob-fade-enter-from { opacity: 0; transform: translateX(10px); }
.ob-fade-leave-to { opacity: 0; }

/* ════════════════════════════════════════════
   窄屏：导航转为顶部横向步骤条
   ════════════════════════════════════════════ */
@media (max-width: 680px) {
  .ob-card { flex-direction: column; }
  .ob-nav {
    flex: 0 0 auto;
    flex-direction: row;
    align-items: center;
    gap: 10px;
    padding: 12px 14px;
    border-right: none;
    border-bottom: 1px solid var(--color-border);
  }
  .ob-nav-list { flex-direction: row; gap: 6px; overflow-x: auto; }
  .ob-nav-text { display: none; }
  .ob-nav-item { width: auto; padding: 4px; }
  .ob-nav-foot { flex: 0 0 auto; padding-top: 0; border-top: none; }
  .ob-progress { width: 60px; }
  .ob-nav-count { display: none; }
  .ob-body { padding: 22px 20px 16px; }
}
</style>
