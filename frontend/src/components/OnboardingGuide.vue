<script setup>
/**
 * OnboardingGuide.vue — 新手引导组件
 *
 * 职责：纯展示 + 状态管理，不依赖其他组件。
 *
 * 数据流（去耦合）：
 *   localStorage (ai_tutor_onboarded) → OnboardingGuide → (emit: close) → 父组件
 *
 * 特性：
 *   - 首次访问自动弹出，引导完成后不再显示
 *   - 3 张卡片，介绍产品核心功能
 *   - 支持"跳过"和"下一步/知道了"两种导航
 *   - localStorage 记住状态
 */

import { ref, onMounted } from 'vue'

/* ================================================================
   组件 Props
   ================================================================ */
const props = defineProps({
  /** 是否强制显示（忽略 localStorage），用于设置中重新查看 */
  forceShow: { type: Boolean, default: false },
})

/* ================================================================
   组件 Events
   ================================================================ */
const emit = defineEmits(['close'])

/* ================================================================
   状态
   ================================================================ */
const STORAGE_KEY = 'ai_tutor_onboarded'

const visible = ref(false)
const currentStep = ref(0)

/* ================================================================
   引导卡片内容
   ================================================================ */
const steps = [
  {
    icon: '✦',
    title: '欢迎使用 AI Tutor',
    description: '你的专属 AI 学习伙伴，基于知识图谱的自适应学习系统。',
    features: [
      '对话式学习 — 像聊天一样探索知识',
      '知识图谱 — 可视化你的知识结构',
      '用户画像 — AI 了解你，针对性教学',
    ],
  },
  {
    icon: '◉',
    title: '知识图谱',
    description: '你的知识以节点和边组成可视化图谱，直观展示知识关联。',
    features: [
      '双击节点查看详细内容',
      '右键创建新节点或连线',
      '拖拽节点自由排列图谱',
      '搜索框快速定位知识点',
    ],
  },
  {
    icon: '◎',
    title: '智能对话',
    description: 'AI 会根据你的知识水平和学习偏好，提供个性化教学。',
    features: [
      '三种教学模式：阶梯提问 / 先思后答 / 反向教学',
      'AI 自动更新你的知识图谱',
      '学习进度实时追踪',
    ],
  },
]

/* ================================================================
   操作
   ================================================================ */
function next() {
  if (currentStep.value < steps.length - 1) {
    currentStep.value++
  } else {
    close()
  }
}

function prev() {
  if (currentStep.value > 0) {
    currentStep.value--
  }
}

function skip() {
  close()
}

function close() {
  visible.value = false
  localStorage.setItem(STORAGE_KEY, 'true')
  emit('close')
}

/* ================================================================
   初始化
   ================================================================ */
onMounted(() => {
  if (props.forceShow || !localStorage.getItem(STORAGE_KEY)) {
    visible.value = true
  }
})

/* ================================================================
   暴露方法（父组件可调用重新显示）
   ================================================================ */
defineExpose({ show: () => { visible.value = true; currentStep.value = 0 } })
</script>

<template>
  <Teleport to="body">
    <Transition name="onboard-overlay">
      <div v-if="visible" class="onboarding-overlay" @click.self="close">
        <Transition name="onboard-card" mode="out-in">
          <div class="onboarding-card" :key="currentStep">
            <!-- 步骤指示器 -->
            <div class="card-steps">
              <span
                v-for="(_, i) in steps"
                :key="i"
                class="step-dot"
                :class="{ active: i === currentStep, done: i < currentStep }"
              ></span>
            </div>

            <!-- 图标 -->
            <div class="card-icon">{{ steps[currentStep].icon }}</div>

            <!-- 标题 -->
            <h2 class="card-title">{{ steps[currentStep].title }}</h2>

            <!-- 描述 -->
            <p class="card-desc">{{ steps[currentStep].description }}</p>

            <!-- 功能列表 -->
            <ul class="card-features">
              <li v-for="feat in steps[currentStep].features" :key="feat">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                  <polyline points="20 6 9 17 4 12"/>
                </svg>
                {{ feat }}
              </li>
            </ul>

            <!-- 底部操作 -->
            <div class="card-actions">
              <button class="action-skip" @click="skip">
                {{ currentStep < steps.length - 1 ? '跳过' : '' }}
              </button>

              <div class="action-nav">
                <button
                  v-if="currentStep > 0"
                  class="action-prev"
                  @click="prev"
                >
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
                    <path d="m15 18-6-6 6-6"/>
                  </svg>
                  上一步
                </button>
                <button class="action-next" @click="next">
                  {{ currentStep < steps.length - 1 ? '下一步' : '我知道了' }}
                  <svg v-if="currentStep < steps.length - 1" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
                    <path d="m9 18 6-6-6-6"/>
                  </svg>
                </button>
              </div>
            </div>
          </div>
        </Transition>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
/* ════════════════════════════════════════════
   遮罩层
   ════════════════════════════════════════════ */
.onboarding-overlay {
  position: fixed;
  inset: 0;
  z-index: 1000;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.45);
  backdrop-filter: blur(4px);
}

/* ════════════════════════════════════════════
   卡片
   ════════════════════════════════════════════ */
.onboarding-card {
  width: 420px;
  max-width: 90vw;
  background: var(--color-bg-primary);
  border-radius: 16px;
  padding: 32px 36px 24px;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
}

/* ── 步骤指示器 ── */
.card-steps {
  display: flex;
  gap: 8px;
  margin-bottom: 20px;
}

.step-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--color-border);
  transition: all 0.3s ease;
}
.step-dot.active {
  background: var(--color-accent);
  box-shadow: 0 0 0 3px var(--color-accent-light, rgba(59, 130, 246, 0.2));
}
.step-dot.done {
  background: var(--color-accent);
  opacity: 0.4;
}

/* ── 图标 ── */
.card-icon {
  font-size: 40px;
  line-height: 1;
  color: var(--color-accent);
  margin-bottom: 12px;
  user-select: none;
}

/* ── 标题 ── */
.card-title {
  font-size: 20px;
  font-weight: 700;
  color: var(--color-text-primary);
  margin: 0 0 8px;
}

/* ── 描述 ── */
.card-desc {
  font-size: 14px;
  color: var(--color-text-secondary);
  line-height: 1.6;
  margin: 0 0 20px;
  max-width: 340px;
}

/* ── 功能列表 ── */
.card-features {
  list-style: none;
  padding: 0;
  margin: 0 0 28px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  width: 100%;
}

.card-features li {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 13px;
  color: var(--color-text-primary);
  text-align: left;
  padding: 8px 12px;
  background: var(--color-bg-surface);
  border-radius: 8px;
  border: 1px solid var(--color-border-subtle);
}

.card-features li svg {
  color: var(--color-green);
  flex-shrink: 0;
}

/* ── 底部操作 ── */
.card-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
}

.action-skip {
  border: none;
  background: transparent;
  color: var(--color-text-tertiary);
  font-size: 13px;
  cursor: pointer;
  padding: 6px 10px;
  border-radius: 6px;
  transition: color 0.15s ease, background 0.15s ease;
}
.action-skip:hover { color: var(--color-text-secondary); background: var(--color-bg-hover); }

.action-nav {
  display: flex;
  align-items: center;
  gap: 8px;
}

.action-prev {
  display: flex;
  align-items: center;
  gap: 4px;
  border: 1px solid var(--color-border);
  background: var(--color-bg-primary);
  color: var(--color-text-secondary);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  padding: 8px 14px;
  border-radius: 8px;
  transition: all 0.15s ease;
}
.action-prev:hover { background: var(--color-bg-surface); color: var(--color-text-primary); }

.action-next {
  display: flex;
  align-items: center;
  gap: 4px;
  border: none;
  background: var(--color-accent);
  color: #fff;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  padding: 8px 18px;
  border-radius: 8px;
  transition: all 0.15s ease;
}
.action-next:hover { opacity: 0.9; transform: translateY(-1px); box-shadow: 0 2px 8px rgba(0,0,0,0.15); }
.action-next:active { transform: translateY(0); }

/* ════════════════════════════════════════════
   过渡动画
   ════════════════════════════════════════════ */
.onboard-overlay-enter-active {
  transition: opacity 0.25s ease;
}
.onboard-overlay-leave-active {
  transition: opacity 0.2s ease;
}
.onboard-overlay-enter-from,
.onboard-overlay-leave-to {
  opacity: 0;
}

.onboard-card-enter-active {
  transition: all 0.3s cubic-bezier(0.4, 0.0, 0.2, 1);
}
.onboard-card-leave-active {
  transition: all 0.2s cubic-bezier(0.4, 0.0, 0.2, 1);
}
.onboard-card-enter-from {
  opacity: 0;
  transform: translateY(16px) scale(0.96);
}
.onboard-card-leave-to {
  opacity: 0;
  transform: translateY(-8px) scale(0.98);
}
</style>
