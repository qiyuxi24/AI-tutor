<script setup>
/**
 * SettingsView.vue — 设置页（活动栏第四个入口）
 *
 * 只做两件事：左侧 tab 导航 + 右侧渲染当前面板。
 * 每个设置面板是独立子组件（components/settings/*.vue），各自管自己的数据与交互，
 * 切 tab 才挂载、离开即卸载 —— 不会再像旧版那样把九个板块一次性铺在同一页。
 * 跨页事件（重新看引导 / 切换账号）由本组件向上转发给 HomeView。
 */
import { ref, computed } from 'vue'
import AccountPane from '../components/settings/AccountPane.vue'
import AppearancePane from '../components/settings/AppearancePane.vue'
import ModelsPane from '../components/settings/ModelsPane.vue'
import GraphPane from '../components/settings/GraphPane.vue'
import NodeDetailPane from '../components/settings/NodeDetailPane.vue'
import GuidePane from '../components/settings/GuidePane.vue'
import CopyrightPane from '../components/settings/CopyrightPane.vue'
import FloatingBallPane from '../components/settings/FloatingBallPane.vue'

const emit = defineEmits(['replay-onboarding', 'switch-account'])

// icon 是硬编码的 SVG 内容串（非用户输入），用 v-html 注入避免九个手写 <a> 重复
const TABS = [
  { key: 'account', label: '账号', comp: AccountPane, icon:
    '<circle cx="12" cy="8" r="4"/><path d="M5 20c1.5-3.5 4-5 7-5s5.5 1.5 7 5"/>' },
  { key: 'appearance', label: '外观', comp: AppearancePane, icon:
    '<path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"/>' },
  { key: 'models', label: '模型', comp: ModelsPane, icon:
    '<rect x="2" y="3" width="20" height="7" rx="2"/><rect x="2" y="14" width="20" height="7" rx="2"/>' +
    '<line x1="6" y1="6.5" x2="6.01" y2="6.5"/><line x1="6" y1="17.5" x2="6.01" y2="17.5"/>' },
  { key: 'graph', label: '知识图谱', comp: GraphPane, icon:
    '<circle cx="6" cy="6" r="3"/><circle cx="18" cy="6" r="3"/><circle cx="12" cy="18" r="3"/>' +
    '<line x1="8.6" y1="7.6" x2="10.6" y2="15.5"/><line x1="15.4" y1="7.6" x2="13.4" y2="15.5"/>' },
  { key: 'node', label: '节点详情', comp: NodeDetailPane, icon:
    '<path d="M4 7V4h16v3"/><line x1="9" y1="20" x2="15" y2="20"/><line x1="12" y1="4" x2="12" y2="20"/>' },
  { key: 'guide', label: '引导', comp: GuidePane, icon:
    '<circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/>' +
    '<line x1="12" y1="17" x2="12.01" y2="17"/>' },
  { key: 'copyright', label: '资源与版权', comp: CopyrightPane, icon:
    '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/>' +
    '<line x1="12" y1="15" x2="12" y2="3"/>' },
  { key: 'ball', label: '悬浮球', comp: FloatingBallPane, icon:
    '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="3"/>' },
]

const activeKey = ref('account')
const current = computed(() => TABS.find((t) => t.key === activeKey.value))
</script>

<template>
  <div class="settings-layout">
    <!-- 左侧：设置分类导航（tab） -->
    <aside class="settings-nav">
      <div class="sn-title">设置</div>
      <nav class="sn-list">
        <a
          v-for="t in TABS"
          :key="t.key"
          class="sn-item"
          :class="{ active: activeKey === t.key }"
          @click="activeKey = t.key"
        >
          <svg
            width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
            stroke-width="2" stroke-linecap="round" stroke-linejoin="round"
            v-html="t.icon"
          ></svg>
          {{ t.label }}
        </a>
      </nav>
    </aside>

    <!-- 右侧：当前设置面板 -->
    <main class="settings-content">
      <component
        :is="current.comp"
        @replay-onboarding="emit('replay-onboarding')"
        @switch-account="emit('switch-account')"
      />
    </main>
  </div>
</template>
