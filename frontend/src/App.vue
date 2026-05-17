<script setup>
import { ref, onMounted, computed } from 'vue'
import { useChatStore } from './stores/chatStore'
import Sidebar from './components/Sidebar.vue'
import ChatArea from './components/ChatArea.vue'
import ForceGraph from './components/ForceGraph.vue'
import Settings from './components/Settings.vue'

const store = useChatStore()
const viewMode = ref('chat')
const sidebarCollapsed = ref(false)
const showSettings = ref(false)
const isTransitioning = ref(false)

const SIDEBAR_WIDTH = 260

const MODE_LABELS = {
  scaffolding: { label: '阶梯提问', color: '#10b981' },
  think_first: { label: '先思后答', color: '#f59e0b' },
  reverse_teaching: { label: '反向教学', color: '#8b5cf6' },
}
const modeInfo = computed(() => MODE_LABELS[store.mode] || { label: store.mode, color: '#6b7280' })

onMounted(() => {
  store.init()
})

function toggleSidebar() {
  sidebarCollapsed.value = !sidebarCollapsed.value
}

function openSettings() {
  showSettings.value = true
}

function closeSettings() {
  showSettings.value = false
}

function switchViewMode(newMode) {
  if (isTransitioning.value || viewMode.value === newMode) return
  // 收起侧栏后再切换视图，防止图谱视图下多出一块空白
  sidebarCollapsed.value = true
  isTransitioning.value = true
  viewMode.value = newMode
  setTimeout(() => {
    isTransitioning.value = false
  }, 300)
}

// ─── 视图切换动画 ───
const slideTransition = {
  onEnter(el, done) {
    const entering = el.dataset.view
    const isChat = entering === 'chat'
    const from = isChat ? '-100%' : '100%'
    el.style.transform = `translateX(${from}) scale(0.96)`
    el.style.opacity = '0.8'
    el.offsetHeight
    el.style.transition = 'transform 400ms cubic-bezier(0.4, 0.0, 0.2, 1), opacity 400ms cubic-bezier(0.4, 0.0, 0.2, 1)'
    el.style.transform = 'translateX(0) scale(1)'
    el.style.opacity = '1'
    setTimeout(done, 400)
  },
  onLeave(el, done) {
    const leaving = el.dataset.view
    const isChat = leaving === 'chat'
    const to = isChat ? '100%' : '-100%'
    el.style.transform = 'translateX(0)'
    el.style.opacity = '1'
    el.offsetHeight
    el.style.transition = 'transform 350ms cubic-bezier(0.4, 0.0, 0.2, 1), opacity 350ms cubic-bezier(0.4, 0.0, 0.2, 1)'
    el.style.transform = `translateX(${to})`
    el.style.opacity = '0.3'
    setTimeout(done, 350)
  }
}
</script>

<template>
  <div class="app-container">
    <!-- ═══ 顶部栏（始终可见）═══ -->
    <header class="app-top-bar">
      <!-- 侧边栏切换按钮：永远在固定位置，不跟随滑动 -->
      <button class="sidebar-toggle-btn" @click="toggleSidebar" title="切换侧边栏">
        <svg v-if="sidebarCollapsed" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round">
          <path d="M9 18l6-6-6-6"/>
        </svg>
        <svg v-else width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round">
          <path d="M15 18l-6-6 6-6"/>
        </svg>
      </button>

      <!-- 品牌区：与侧边栏同步滑动 -->
      <div class="tb-brand" :class="{ collapsed: sidebarCollapsed }" :style="{ width: SIDEBAR_WIDTH + 'px' }">
        <span class="brand">AI Tutor</span>
      </div>

      <!-- 右侧：自适应填充 -->
      <div class="tb-right">
        <div class="view-switch" :class="{ disabled: isTransitioning }">
          <span :class="{ active: viewMode === 'chat' }" @click="switchViewMode('chat')">对话</span>
          <div class="switch-slider" :class="{ switched: viewMode === 'graph' }" @click="switchViewMode(viewMode === 'chat' ? 'graph' : 'chat')">
            <div class="slider-dot"></div>
          </div>
          <span :class="{ active: viewMode === 'graph' }" @click="switchViewMode('graph')">图谱</span>
        </div>

        <div v-if="viewMode === 'chat'" class="mode-tag" :style="{
          background: modeInfo.color + '14',
          color: modeInfo.color,
          borderColor: modeInfo.color + '30'
        }">
          <span class="mode-dot" :style="{ background: modeInfo.color }"></span>
          {{ modeInfo.label }}
        </div>

        <button class="tb-btn" @click="openSettings" title="设置">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <circle cx="12" cy="12" r="3"/>
            <path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/>
          </svg>
        </button>
      </div>
    </header>

    <!-- ═══ 主内容区域 ═══ -->
    <div class="main-content">
      <!-- 对话视图 -->
      <Transition name="view-fade" v-bind="slideTransition">
        <div v-if="viewMode === 'chat'" class="app-layout" data-view="chat">
          <!-- 侧边栏：与 tb-left 使用完全相同的滑动动画 -->
          <div class="sidebar-panel" :class="{ collapsed: sidebarCollapsed }" :style="{ width: SIDEBAR_WIDTH + 'px' }">
            <Sidebar />
          </div>
          <ChatArea :sidebarCollapsed="sidebarCollapsed" />
        </div>
      </Transition>

      <!-- 知识图谱视图 -->
      <Transition name="view-fade" v-bind="slideTransition">
        <div v-if="viewMode === 'graph'" class="graph-layout" data-view="graph">
          <ForceGraph />
        </div>
      </Transition>
    </div>

    <!-- 设置面板 -->
    <Transition name="fade">
      <Settings v-if="showSettings" @close="closeSettings" />
    </Transition>
  </div>
</template>

<style scoped>
/* ═════════════════════════
   布局容器
   ═════════════════════════ */
.app-container {
  width: 100vw;
  height: 100vh;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

/* ═════════════════════════
   顶部栏
   ═════════════════════════ */
.app-top-bar {
  display: flex;
  align-items: center;
  height: 48px;
  background: #1e1e2e;
  border-bottom: 1px solid #313244;
  flex-shrink: 0;
  padding: 0 12px;
  gap: 4px;
  z-index: 10;
}

/* 侧边栏切换按钮（固定在顶部栏，永不溢出） */
.sidebar-toggle-btn {
  width: 34px;
  height: 34px;
  flex-shrink: 0;
  border: none;
  background: transparent;
  color: #cdd6f4;
  border-radius: 8px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background 0.2s ease;
}
.sidebar-toggle-btn:hover { background: #313244; }

/* 品牌区：与侧边栏同步滑动 */
.tb-brand {
  display: flex;
  align-items: center;
  padding-left: 6px;
  transition: width 0.3s cubic-bezier(0.4, 0.0, 0.2, 1),
              opacity 0.25s cubic-bezier(0.4, 0.0, 0.2, 1);
  overflow: hidden;
  flex-shrink: 0;
  white-space: nowrap;
}
.tb-brand.collapsed {
  width: 0 !important;
  opacity: 0;
}

.brand {
  font-size: 16px;
  font-weight: 700;
  color: #ffffff;
  white-space: nowrap;
}

/* 右侧：居中自适应 */
.tb-right {
  display: flex;
  align-items: center;
  gap: 12px;
  flex: 1;
  justify-content: center;
}

.tb-btn {
  width: 34px;
  height: 34px;
  border: none;
  background: #313244;
  color: #cdd6f4;
  border-radius: 8px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background 0.2s ease;
  flex-shrink: 0;
}
.tb-btn:hover { background: #45475a; }

/* 视图切换 */
.view-switch {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 13px;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
}
.view-switch span {
  color: #6c7086;
  cursor: pointer;
  transition: color 0.2s ease;
  user-select: none;
}
.view-switch span.active { color: #cdd6f4; font-weight: 500; }
.view-switch.disabled span { cursor: not-allowed; opacity: 0.5; }

.switch-slider {
  width: 44px;
  height: 22px;
  background: #313244;
  border-radius: 11px;
  cursor: pointer;
  position: relative;
  transition: background 0.2s ease;
}
.switch-slider.switched { background: #89b4fa; }
.slider-dot {
  position: absolute; top: 2px; left: 2px;
  width: 18px; height: 18px;
  background: #cdd6f4;
  border-radius: 50%;
  transition: transform 0.2s ease;
}
.switch-slider.switched .slider-dot { transform: translateX(22px); }

.mode-tag {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 12px;
  border-radius: 20px;
  font-size: 12px;
  font-weight: 500;
  border: 1px solid;
  white-space: nowrap;
  flex-shrink: 0;
}
.mode-dot { width: 6px; height: 6px; border-radius: 50%; }

/* ═════════════════════════
   主内容
   ═════════════════════════ */
.main-content {
  flex: 1;
  overflow: hidden;
  position: relative;
}

.app-layout {
  display: flex;
  width: 100%;
  height: 100%;
  position: absolute;
  top: 0;
  left: 0;
}

/* 侧边栏面板 — 使用 margin-left 负值彻底收起，不占用空间 */
.sidebar-panel {
  flex-shrink: 0;
  transition: margin-left 0.3s cubic-bezier(0.4, 0.0, 0.2, 1);
  overflow: hidden;
}

.sidebar-panel.collapsed {
  margin-left: -260px;
}

.graph-layout {
  width: 100%;
  height: 100%;
  position: absolute;
  top: 0;
  left: 0;
}

/* 设置面板淡入淡出 */
.fade-enter-active, .fade-leave-active { transition: opacity 0.2s ease; }
.fade-enter-from, .fade-leave-to { opacity: 0; }
.fade-enter-to, .fade-leave-from { opacity: 1; }
</style>