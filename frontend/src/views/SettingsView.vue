<script setup>
/**
 * SettingsView.vue — 设置页（活动栏第四个入口）
 *
 * 只做两件事：左侧「按模块分组」的 tab 导航 + 右侧渲染当前面板。
 * 每个设置面板是独立子组件（components/settings/*.vue），各自管自己的数据与交互，
 * 切 tab 才挂载、离开即卸载 —— 不会再像旧版那样把九个板块一次性铺在同一页。
 * 跨页事件（重新看引导 / 切换账号）由本组件向上转发给 HomeView。
 */
import { ref, computed } from 'vue'
import AccountPane from '../components/settings/AccountPane.vue'
import AppearancePane from '../components/settings/AppearancePane.vue'
import ModelsPane from '../components/settings/ModelsPane.vue'
import LecturePane from '../components/settings/LecturePane.vue'
import GraphPane from '../components/settings/GraphPane.vue'
import GuidePane from '../components/settings/GuidePane.vue'
import CopyrightPane from '../components/settings/CopyrightPane.vue'
import FloatingBallPane from '../components/settings/FloatingBallPane.vue'
import AppIcon from '../components/AppIcon.vue'

const emit = defineEmits(['replay-onboarding', 'switch-account'])

// icon 是 utils/icons.js 的键名，由 <AppIcon> 渲染（与活动栏同一份图形）
// 导航按「模块」分组：「节点详情」与「知识图谱」同属看图链路，已合并进 GraphPane。
const GROUPS = [
  {
    title: '通用',
    items: [
      { key: 'account', label: '账号', comp: AccountPane, icon: 'user' },
      { key: 'appearance', label: '外观', comp: AppearancePane, icon: 'pencil' },
      { key: 'ball', label: '悬浮球', comp: FloatingBallPane, icon: 'target' },
    ],
  },
  {
    title: '学习',
    items: [
      { key: 'models', label: '模型', comp: ModelsPane, icon: 'server' },
      { key: 'lecture', label: '视频导入', comp: LecturePane, icon: 'play' },
    ],
  },
  {
    title: '知识图谱',
    items: [
      { key: 'graph', label: '图谱与节点详情', comp: GraphPane, icon: 'graph' },
    ],
  },
  {
    title: '帮助与关于',
    items: [
      { key: 'guide', label: '引导', comp: GuidePane, icon: 'help' },
      { key: 'copyright', label: '资源与版权', comp: CopyrightPane, icon: 'download' },
    ],
  },
]

const activeKey = ref('account')
const current = computed(() =>
  GROUPS.flatMap((g) => g.items).find((t) => t.key === activeKey.value),
)
</script>

<template>
  <div class="settings-layout">
    <!-- 左侧：设置分类导航（tab） -->
    <aside class="settings-nav">
      <div class="sn-title">设置</div>
      <nav class="sn-list">
        <div v-for="g in GROUPS" :key="g.title" class="sn-group">
          <div class="sn-group-title">{{ g.title }}</div>
          <button
            v-for="t in g.items"
            :key="t.key"
            type="button"
            class="sn-item"
            :class="{ active: activeKey === t.key }"
            :aria-current="activeKey === t.key ? 'page' : undefined"
            @click="activeKey = t.key"
          >
            <AppIcon :name="t.icon" :size="18" />
            {{ t.label }}
          </button>
        </div>
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

<style scoped>
/*
 * .sn-item 的外观在全局 styles/settings.css；这里只补齐 <button> 的浏览器默认样式，
 * 让按钮与原来的 <a> 视觉一致。
 * 用 :where() 把优先级压到最低（只高于 UA 默认值），避免与 .sn-item.active /
 * .sn-item:hover 打平后靠样式注入顺序决胜负（那会让选中态背景被 transparent 覆盖）。
 */
:where(.sn-item) {
  width: 100%;
  border: none;
  background: transparent;
  font-family: inherit;
  text-align: left;
}
:where(.sn-item):focus-visible {
  outline: 2px solid var(--color-primary);
  outline-offset: 2px;
}
</style>
