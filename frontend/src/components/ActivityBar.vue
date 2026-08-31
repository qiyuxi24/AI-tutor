<script setup>
/**
 * ActivityBar.vue — 最左侧活动栏（VSCode / ima 风格，沉浸式无顶部栏）
 *
 * 职责：
 *   1. 页面入口图标导航（对话 / 图谱 / 知识库）
 *   2. 品牌 logo
 *   3. 底部：主题切换 / 设置 / 用户菜单（含用户画像、退出登录）
 *
 * 组件自包含主题与账号逻辑，HomeView 无需参与。
 */

import { ref } from 'vue'
import { useTheme } from '../utils/theme'
import { useAuthStore } from '../stores/authStore'

defineProps({
  activeView: { type: String, default: 'chat' },
})

const emit = defineEmits(['select', 'open-profile'])

const { isDark, toggleTheme } = useTheme()
const authStore = useAuthStore()
const showUserMenu = ref(false)

const items = [
  { id: 'chat', title: '对话', icon: 'chat' },
  { id: 'graph', title: '知识图谱', icon: 'graph' },
  { id: 'knowledge', title: '知识库', icon: 'folder' },
  { id: 'quiz', title: '出题', icon: 'quiz' },
]

function handleSelect(id) {
  emit('select', id)
}

function handleUserMenu(action) {
  showUserMenu.value = false
  if (action === 'profile') emit('open-profile')
  if (action === 'logout') emit('logout')
}
</script>

<template>
  <aside class="activity-bar">
    <!-- 顶部品牌 -->
    <div class="ab-logo" title="AI Tutor">
      <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M12 2a2 2 0 0 1 2 2c0 .74-.4 1.39-1 1.73V7h1a7 7 0 0 1 7 7h1a1 1 0 0 1 0 2h-1.08A7 7 0 0 1 14 21v1h-4v-1a7 7 0 0 1-5.92-5H3a1 1 0 0 1 0-2h1a7 7 0 0 1 7-7h1V5.73c-.6-.34-1-.99-1-1.73a2 2 0 0 1 2-2z" />
      </svg>
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
        <!-- 对话图标 -->
        <svg v-if="item.icon === 'chat'" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
        </svg>
        <!-- 图谱图标 -->
        <svg v-else-if="item.icon === 'graph'" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="6" cy="5" r="2.5" />
          <circle cx="18" cy="7" r="2.5" />
          <circle cx="12" cy="19" r="2.5" />
          <line x1="7.5" y1="6.5" x2="15.8" y2="8.4" />
          <line x1="16.5" y1="8.8" x2="13.1" y2="17" />
          <line x1="7.3" y1="7.3" x2="10.7" y2="16.8" />
        </svg>
        <!-- 知识库图标 -->
        <svg v-else-if="item.icon === 'folder'" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
        </svg>
        <!-- 出题图标（测验/试题） -->
        <svg v-else-if="item.icon === 'quiz'" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M9 11l3 3L22 4" />
          <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" />
        </svg>
      </button>
    </nav>

    <!-- 底部功能区 -->
    <div class="ab-bottom">
      <!-- 主题切换 -->
      <button class="ab-item" :title="isDark ? '切换浅色模式' : '切换深色模式'" @click="toggleTheme">
        <svg v-if="!isDark" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="12" r="4" />
          <line x1="12" y1="2" x2="12" y2="4" />
          <line x1="12" y1="20" x2="12" y2="22" />
          <line x1="4.93" y1="4.93" x2="6.34" y2="6.34" />
          <line x1="17.66" y1="17.66" x2="19.07" y2="19.07" />
          <line x1="2" y1="12" x2="4" y2="12" />
          <line x1="20" y1="12" x2="22" y2="12" />
          <line x1="4.93" y1="19.07" x2="6.34" y2="17.66" />
          <line x1="17.66" y1="6.34" x2="19.07" y2="4.93" />
        </svg>
        <svg v-else width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
        </svg>
      </button>

      <!-- 设置入口 -->
      <button
        class="ab-item"
        :class="{ active: activeView === 'settings' }"
        title="设置"
        @click="handleSelect('settings')"
      >
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="12" r="3" />
          <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z" />
        </svg>
      </button>

      <!-- 用户菜单 -->
      <div class="ab-user-wrap" v-click-outside="() => showUserMenu = false">
        <button class="ab-item ab-avatar" :title="authStore.username || '用户'" @click="showUserMenu = !showUserMenu">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
            <circle cx="12" cy="7" r="4" />
          </svg>
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
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
                <circle cx="12" cy="7" r="4" />
              </svg>
              用户画像
            </button>
            <button class="ab-user-item danger" @click="handleUserMenu('logout')">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
                <polyline points="16 17 21 12 16 7" />
                <line x1="21" y1="12" x2="9" y2="12" />
              </svg>
              退出登录
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

/* 品牌 logo */
.ab-logo {
  width: 52px;
  height: 48px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--color-accent);
  flex-shrink: 0;
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
  border-radius: 8px;
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
  border-radius: 0 3px 3px 0;
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
  border-radius: 10px;
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
  font-size: 11px;
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
  border-radius: 6px;
  background: transparent;
  color: var(--color-text-primary);
  font-size: 13px;
  cursor: pointer;
  transition: background 0.15s;
}

.ab-user-item:hover {
  background: var(--color-bg-hover);
}

.ab-user-item.danger {
  color: var(--color-red);
}
.ab-user-item.danger:hover {
  background: var(--color-red-light);
}

.ab-menu-fade-enter-active, .ab-menu-fade-leave-active {
  transition: opacity 0.15s ease, transform 0.15s ease;
}
.ab-menu-fade-enter-from, .ab-menu-fade-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}
</style>
