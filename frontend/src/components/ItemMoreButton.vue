<script setup>
/**
 * ItemMoreButton.vue — 条目右侧的常驻「⋯」按钮，点一下在当前条目下方弹菜单。
 *
 * 只抽按钮，不抽菜单：菜单项是各处的业务动作，统一由 ContextMenu + 插槽提供
 * （理由见 ContextMenu.vue 顶部「为什么不做成按 targetType 内置菜单项」）。
 * 此前三个使用方（知识库树 / 学科栏 / 对话列表）各抄一份 22×22 样式与同一段三点 SVG。
 *
 * 用法：
 *   <ItemMoreButton title="更多操作" @click="openMenuAt($event, 'kb', data)" />
 *   —— 事件参数是按钮 DOM 元素，直接喂给 useContextMenu().openAt()。
 *
 * 为什么不用 position+z-index 去「压在文字之上」：那是行内元素没有正确收缩时的补丁，
 * 补不出被挤出容器的按钮。父级须给文字节点 min-width:0（见调用方注释）。
 */
import { ref } from 'vue'

defineProps({
  title: { type: String, default: '更多操作' },
})

const emit = defineEmits(['click'])

const btn = ref(null)
</script>

<template>
  <button
    ref="btn"
    type="button"
    class="item-more"
    :title="title"
    @click.stop="emit('click', btn)"
  >
    <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <circle cx="12" cy="5" r="1.7" /><circle cx="12" cy="12" r="1.7" /><circle cx="12" cy="19" r="1.7" />
    </svg>
  </button>
</template>

<style scoped>
.item-more {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  padding: 0;
  border: none;
  border-radius: 5px;
  background: transparent;
  color: var(--color-text-muted);
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
}
.item-more:hover {
  background: var(--color-bg-hover);
  color: var(--color-text-primary);
}
</style>
