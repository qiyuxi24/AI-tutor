<script setup>
/**
 * ActivityBar.vue — 最左侧活动栏（VSCode / ima 风格，沉浸式无顶部栏）
 *
 * 职责：
 *   1. 页面入口图标导航（对话 / 图谱 / 出题）
 *      ⚠️ 资源采集已改为后端 AI 工具（`search_resources` / `collect_resources`），
 *         前端入口整体下线：本导航项已移除，CollectorView.vue 保留但不再挂载。
 *      知识库有两个入口、同一个组件（KbPanel）：独立页（KbView）+ 图谱页右侧可收起的侧栏
 *      （HomeView 的 .graph-kb）。功能重复是有意的，取舍见 KbView.vue 顶部说明。
 *   2. 品牌 logo
 *   3. 底部：主题切换 / 设置 / 用户菜单（含用户画像、切换账号）
 *
 * 组件自包含主题与账号逻辑，HomeView 无需参与。
 * 图标一律走 <AppIcon>（图形定义在 utils/icons.js）。
 */

import { ref } from 'vue'
import { useTheme } from '../utils/theme'
import { useAuthStore } from '../stores/authStore'
import { avatarState } from '../utils/avatar.js'
import { BRAND_ICON } from '../utils/brandAssets.js'
import AppIcon from './AppIcon.vue'

defineProps({
  activeView: { type: String, default: 'chat' },
})

const emit = defineEmits(['select', 'open-profile', 'switch-account'])

const { isDark, toggleTheme } = useTheme()
const authStore = useAuthStore()
const showUserMenu = ref(false)

// 导航项：icon 直接是 icons.js 的键
const items = [
  { id: 'chat', title: '对话', icon: 'chat' },
  { id: 'graph', title: '知识图谱', icon: 'graph' },
  { id: 'kb', title: '知识库', icon: 'database' },
  { id: 'lecture', title: '视频导入', icon: 'film' },
  { id: 'quiz', title: '出题', icon: 'quiz' },
]

function handleSelect(id) {
  emit('select', id)
}

function handleUserMenu(action) {
  showUserMenu.value = false
  if (action === 'profile') emit('open-profile')
  if (action === 'switch-account') emit('switch-account')
}
</script>

<template>
  <aside class="activity-bar">
    <!-- 顶部品牌：像素小星（public/brand-star.svg，自带配色） -->
    <div class="ab-logo" title="AI Tutor">
      <img class="ab-logo-img" :src="BRAND_ICON" alt="AI Tutor" />
    </div>

    <!-- 中部导航区 -->
    <nav class="ab-nav">
      <button
        v-for="item in items"
        :key="item.id"
        class="ab-item"
        :class="{ active: activeView === item.id }"
        :title="item.title"
        @click="handleSelect(item.id)"
      >
        <AppIcon :name="item.icon" :size="22" />
      </button>
    </nav>

    <!-- 底部功能区 -->
    <div class="ab-bottom">
      <!-- 主题切换 -->
      <button class="ab-item" :title="isDark ? '切换浅色模式' : '切换深色模式'" @click="toggleTheme">
        <AppIcon :name="isDark ? 'moon' : 'sun'" :size="20" />
      </button>

      <!-- 设置入口 -->
      <button
        class="ab-item"
        :class="{ active: activeView === 'settings' }"
        title="设置"
        @click="handleSelect('settings')"
      >
        <AppIcon name="settings" :size="22" />
      </button>

      <!-- 用户菜单 -->
      <div class="ab-user-wrap" v-click-outside="() => showUserMenu = false">
        <button class="ab-item ab-avatar" :title="authStore.username || '用户'" @click="showUserMenu = !showUserMenu">
          <!-- 设置了自定义头像就显示照片，否则回落到人形图标 -->
          <img v-if="avatarState.url" class="ab-avatar-img" :src="avatarState.url" alt="" />
          <AppIcon v-else name="user" :size="20" />
        </button>

        <!-- 用户下拉菜单 -->
        <Transition name="ab-menu-fade">
          <div v-if="showUserMenu" class="ab-user-menu">
            <div class="ab-user-info">
              <span class="ab-user-label">当前用户</span>
              <span class="ab-user-name">{{ authStore.username || '未登录' }}</span>
            </div>
            <div class="ab-user-divider"></div>
            <button class="ab-user-item" @click="handleUserMenu('profile')">
              <AppIcon name="user" :size="14" />
              用户画像
            </button>
            <button class="ab-user-item" @click="handleUserMenu('switch-account')">
              <AppIcon name="switch" :size="14" />
              切换账号
            </button>
          </div>
        </Transition>
      </div>
    </div>
  </aside>
</template>

<style scoped>
.activity-bar {
  width: 52px;
  min-width: 52px;
  height: 100%;
  background: var(--color-bg-secondary);
  border-right: 1px solid var(--color-border);
  display: flex;
  flex-direction: column;
  align-items: center;
  user-select: none;
  flex-shrink: 0;
}

/* 品牌 logo：像素星（图形自带配色，不再用 currentColor 跟随主题色） */
.ab-logo {
  width: 52px;
  height: 48px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.ab-logo-img {
  display: block;
  width: 28px;   /* viewBox 14x13，等比：26 * 14/13 */
  height: 26px;
}

/* 中部导航 */
.ab-nav {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  flex: 1;
  padding-top: 4px;
}

/* 底部功能区 */
.ab-bottom {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  padding-bottom: 8px;
}

.ab-item {
  position: relative;
  width: 42px;
  height: 42px;
  border: none;
  background: transparent;
  color: var(--color-text-tertiary);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-md);
  transition: background 0.15s, color 0.15s;
}

.ab-item:hover {
  background: var(--color-bg-hover);
  color: var(--color-text-primary);
}

.ab-item.active {
  color: var(--color-accent);
}

/* 左侧激活指示条 */
.ab-item.active::before {
  content: '';
  position: absolute;
  left: -5px;
  top: 50%;
  transform: translateY(-50%);
  width: 3px;
  height: 22px;
  background: var(--color-accent);
  border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
}

/* 用户头像 */
.ab-avatar {
  margin-top: 2px;
  border-radius: 50%;
  background: var(--color-bg-surface);
}
.ab-avatar:hover {
  background: var(--color-bg-hover);
}

/* 自定义头像照片：铺满按钮内的圆形区域（按钮本身 42px，留一圈内边距） */
.ab-avatar-img {
  display: block;
  width: 32px;
  height: 32px;
  object-fit: cover;
  border-radius: 50%;
}

/* 用户下拉菜单 */
.ab-user-wrap {
  position: relative;
}

.ab-user-menu {
  position: absolute;
  left: calc(100% + 8px);
  bottom: 0;
  min-width: 170px;
  background: var(--color-bg-primary);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-popup);
  z-index: 100;
  overflow: hidden;
  padding: 6px;
}

.ab-user-info {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 8px 10px;
}

.ab-user-label {
  font-size: var(--font-size-xs);
  color: var(--color-text-tertiary);
}

.ab-user-name {
  font-size: 13px;
  font-weight: 500;
  color: var(--color-text-primary);
}

.ab-user-divider {
  height: 1px;
  background: var(--color-border);
  margin: 4px 0;
}

.ab-user-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 8px 10px;
  border: none;
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--color-text-primary);
  font-size: 13px;
  cursor: pointer;
  transition: background 0.15s;
}

.ab-user-item:hover {
  background: var(--color-bg-hover);
}

.ab-menu-fade-enter-active, .ab-menu-fade-leave-active {
  transition: opacity 0.15s ease, transform 0.15s ease;
}
.ab-menu-fade-enter-from, .ab-menu-fade-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}
</style>
