<script setup>
/**
 * SettingsView.vue — 设置页（活动栏第四个入口）
 *
 * 职责：集中管理应用设置
 *   - 外观：主题切换（深色 / 浅色）
 *   - 引导：重新查看新手引导
 *   - 账号：用户信息、退出登录
 */

import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { useTheme } from '../utils/theme'
import { useAuthStore } from '../stores/authStore'
import { getProfile, saveProfileData } from '../api/index.js'

const { mode, setTheme } = useTheme()
const authStore = useAuthStore()

const emit = defineEmits(['replay-onboarding', 'logout'])

function handleThemeChange(value) {
  setTheme(value)
}

// ─── 资源与版权（usage_mode，读写用户画像 preferences）───
const usageMode = ref('personal')   // personal | commercial
const usageSaving = ref(false)
const usageLoaded = ref(false)

const MODE_OPTIONS = [
  { value: 'personal', title: '个人使用', desc: '采集 L0 / L1 / L2 资料，仅用于个人学习（默认）' },
  { value: 'commercial', title: '发布 / 商用', desc: '仅采 L0 开放授权资料，规避商用版权风险' },
]

onMounted(async () => {
  try {
    const { data } = await getProfile()
    const prefs = data?.data?.preferences || {}
    usageMode.value = prefs.usage_mode === 'commercial' ? 'commercial' : 'personal'
  } catch {
    // 读取失败保持默认 personal
  } finally {
    usageLoaded.value = true
  }
})

async function handleUsageModeChange(value) {
  if (!usageLoaded.value || usageSaving.value || usageMode.value === value) return
  usageSaving.value = true
  try {
    // 复用 PATCH /profile：读取当前画像 → 合并 usage_mode → 全量保存（避免覆盖其他偏好）
    const { data } = await getProfile()
    const current = data?.data || {}
    const merged = {
      ...current,
      preferences: { ...(current.preferences || {}), usage_mode: value },
    }
    await saveProfileData(merged)
    usageMode.value = value
    ElMessage.success(value === 'commercial' ? '已切换到发布 / 商用模式' : '已切换到个人使用模式')
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || e.message || '保存失败')
    usageMode.value = value === 'commercial' ? 'personal' : 'commercial'
  } finally {
    usageSaving.value = false
  }
}
</script>

<template>
  <div class="settings-layout">
    <!-- 左侧：设置分类导航 -->
    <aside class="settings-nav">
      <div class="sn-title">设置</div>
      <nav class="sn-list">
        <a class="sn-item active">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M12 20h9" />
            <path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z" />
          </svg>
          外观
        </a>
        <a class="sn-item">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="12" cy="12" r="10" />
            <path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3" />
            <line x1="12" y1="17" x2="12.01" y2="17" />
          </svg>
          引导
        </a>
        <a class="sn-item">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
            <polyline points="7 10 12 15 17 10" />
            <line x1="12" y1="15" x2="12" y2="3" />
          </svg>
          资源与版权
        </a>
        <a class="sn-item">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
            <circle cx="12" cy="7" r="4" />
          </svg>
          账号
        </a>
      </nav>
    </aside>

    <!-- 右侧：设置内容 -->
    <main class="settings-content">
      <!-- 外观 -->
      <section class="sc-section">
        <h3>外观</h3>
        <div class="sc-row">
          <div class="sc-row-info">
            <div class="sc-row-title">主题</div>
            <div class="sc-row-desc">切换深色 / 浅色 / 跟随系统，适配不同环境</div>
          </div>
          <div class="theme-toggle-group">
            <button
              class="theme-option"
              :class="{ active: mode === 'dark' }"
              @click="handleThemeChange('dark')"
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
              </svg>
              深色
            </button>
            <button
              class="theme-option"
              :class="{ active: mode === 'light' }"
              @click="handleThemeChange('light')"
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
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
              浅色
            </button>
            <button
              class="theme-option"
              :class="{ active: mode === 'system' }"
              @click="handleThemeChange('system')"
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <rect x="2" y="3" width="20" height="14" rx="2" />
                <line x1="8" y1="21" x2="16" y2="21" />
                <line x1="12" y1="17" x2="12" y2="21" />
              </svg>
              跟随系统
            </button>
          </div>
        </div>
      </section>

      <!-- 引导 -->
      <section class="sc-section">
        <h3>引导</h3>
        <div class="sc-row">
          <div class="sc-row-info">
            <div class="sc-row-title">新手引导</div>
            <div class="sc-row-desc">重新查看产品功能介绍</div>
          </div>
          <button class="sc-btn" @click="emit('replay-onboarding')">重新查看</button>
        </div>
      </section>

      <!-- 资源与版权 -->
      <section class="sc-section">
        <h3>资源与版权</h3>
        <p class="usage-hint">用于「资源采集」页的授权范围与检索过滤；切换会即时保存到用户画像。</p>
        <div v-for="opt in MODE_OPTIONS" :key="opt.value" class="sc-row">
          <div class="sc-row-info">
            <div class="sc-row-title">{{ opt.title }}</div>
            <div class="sc-row-desc">{{ opt.desc }}</div>
          </div>
          <button
            class="usage-radio"
            :class="{ active: usageMode === opt.value }"
            :disabled="usageSaving || !usageLoaded"
            @click="handleUsageModeChange(opt.value)"
          >
            <span class="usage-dot" :class="{ on: usageMode === opt.value }"></span>
            <span v-if="usageMode === opt.value">使用中</span>
            <span v-else>选择</span>
          </button>
        </div>
      </section>

      <!-- 账号 -->
      <section class="sc-section">
        <h3>账号</h3>
        <div class="sc-row">
          <div class="sc-row-info">
            <div class="sc-row-title">当前用户</div>
            <div class="sc-row-desc">{{ authStore.username || '未登录' }}</div>
          </div>
        </div>
        <div class="sc-row">
          <div class="sc-row-info">
            <div class="sc-row-title">退出登录</div>
            <div class="sc-row-desc">退出当前账号</div>
          </div>
          <button class="sc-btn danger" @click="emit('logout')">退出登录</button>
        </div>
      </section>
    </main>
  </div>
</template>

<style scoped>
.settings-layout {
  display: flex;
  width: 100%;
  height: 100%;
}

/* 左侧设置导航 */
.settings-nav {
  width: 200px;
  min-width: 200px;
  height: 100%;
  background: var(--color-bg-primary);
  border-right: 1px solid var(--color-border);
  padding: 16px 12px;
  overflow-y: auto;
  flex-shrink: 0;
}

.sn-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--color-text-muted);
  padding: 4px 10px 12px;
  letter-spacing: 0.3px;
}

.sn-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.sn-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 10px;
  border-radius: 8px;
  font-size: 13px;
  color: var(--color-text-secondary);
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
  text-decoration: none;
}

.sn-item:hover {
  background: var(--color-bg-hover);
  color: var(--color-text-primary);
}

.sn-item.active {
  background: var(--color-accent-light);
  color: var(--color-accent);
  font-weight: 500;
}

/* 右侧内容 */
.settings-content {
  flex: 1;
  overflow-y: auto;
  padding: 32px 40px;
  background: var(--color-bg-primary);
}

.settings-content::-webkit-scrollbar { width: 5px; }
.settings-content::-webkit-scrollbar-thumb {
  background: var(--color-border-light);
  border-radius: 3px;
}

.sc-section {
  max-width: 640px;
  margin-bottom: 28px;
}

.sc-section h3 {
  font-size: 16px;
  font-weight: 600;
  color: var(--color-text-primary);
  margin: 0 0 14px;
}

.sc-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 16px 18px;
  background: var(--color-bg-secondary);
  border: 1px solid var(--color-border);
  border-radius: 10px;
  margin-bottom: 10px;
}

.sc-row-info {
  min-width: 0;
}

.sc-row-title {
  font-size: 14px;
  font-weight: 500;
  color: var(--color-text-primary);
}

.sc-row-desc {
  font-size: 12px;
  color: var(--color-text-tertiary);
  margin-top: 3px;
}

.theme-toggle-group {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
  flex-wrap: wrap;
}

.theme-option {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 7px 14px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: transparent;
  color: var(--color-text-secondary);
  font-size: 13px;
  cursor: pointer;
  transition: all 0.15s;
}

.theme-option.active {
  background: var(--color-accent-light);
  border-color: var(--color-accent);
  color: var(--color-accent);
  font-weight: 500;
}

.theme-option:hover:not(.active) {
  background: var(--color-bg-hover);
  color: var(--color-text-primary);
}

.sc-btn {
  flex-shrink: 0;
  padding: 7px 16px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: transparent;
  color: var(--color-text-secondary);
  font-size: 13px;
  cursor: pointer;
  transition: all 0.15s;
}

.sc-btn:hover {
  background: var(--color-bg-hover);
  color: var(--color-text-primary);
}

.sc-btn.danger {
  color: var(--color-red);
  border-color: var(--color-red);
}

.sc-btn.danger:hover {
  background: var(--color-red-light);
  color: var(--color-red);
}

/* ── 资源与版权 ── */
.usage-hint {
  font-size: 12px;
  color: var(--color-text-tertiary);
  margin: -6px 0 14px;
}

.usage-radio {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 7px 14px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: transparent;
  color: var(--color-text-secondary);
  font-size: 13px;
  cursor: pointer;
  transition: all 0.15s;
}
.usage-radio:hover:not(.active) {
  background: var(--color-bg-hover);
  color: var(--color-text-primary);
}
.usage-radio.active {
  background: var(--color-accent-light);
  border-color: var(--color-accent);
  color: var(--color-accent);
  font-weight: 500;
}
.usage-radio:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
.usage-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--color-border-light);
}
.usage-dot.on {
  background: var(--color-accent);
}
</style>
