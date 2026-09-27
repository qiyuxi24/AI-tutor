<script setup>
/**
 * ContextMenu.vue — 通用右键菜单容器
 *
 * 只管三件事：定位（不越出视口）、点击外部关闭、条目样式。
 * **菜单项由使用方通过默认插槽给出**，插槽里能拿到 close()，动作执行完自己关：
 *
 *   <ContextMenu :visible="visible" :x="x" :y="y" @close="close">
 *     <template #default="{ close: hide }">
 *       <div class="menu-item menu-item-danger" @click="doIt(); hide()">删除</div>
 *     </template>
 *   </ContextMenu>
 *
 * 为什么不做成「按 targetType 内置菜单项」：那样每加一个右键区域（学科栏、板块栏…）
 * 都要改本组件，还得让组件知道各处的业务动作（组件不该认识 store）。容器 + 插槽让
 * 改动只落在使用方一处。条目样式见下方非 scoped 样式，插槽内容照常命中。
 */
import { ref, watch, nextTick } from 'vue'

const props = defineProps({
  visible: { type: Boolean, default: false },
  x: { type: Number, default: 0 },
  y: { type: Number, default: 0 },
})

const emit = defineEmits(['close'])

/* ================================================================
   边界修正：菜单定位不超出视口
   ================================================================ */
const adjustedX = ref(0)
const adjustedY = ref(0)
const menuRef = ref(null)

function adjustPosition() {
  if (!menuRef.value) {
    adjustedX.value = props.x
    adjustedY.value = props.y
    return
  }

  const menu = menuRef.value
  const menuWidth = menu.offsetWidth || 180
  const menuHeight = menu.offsetHeight || 120
  const viewW = window.innerWidth
  const viewH = window.innerHeight

  let x = props.x
  let y = props.y

  // 右侧溢出 → 向左翻转
  if (x + menuWidth > viewW) {
    x = viewW - menuWidth - 8
  }
  // 底部溢出 → 向上翻转
  if (y + menuHeight > viewH) {
    y = viewH - menuHeight - 8
  }
  // 左侧/顶部不越界
  if (x < 8) x = 8
  if (y < 8) y = 8

  adjustedX.value = x
  adjustedY.value = y
}

// 每次 visible 变化时重新计算位置
watch(() => props.visible, async (val) => {
  if (val) {
    await nextTick()
    adjustPosition()
  }
})
</script>

<template>
  <Teleport to="body">
    <div class="ctx-menu-root">
      <!-- 点击空白处关闭菜单的遮罩层 -->
      <div
        v-if="visible"
        class="context-menu-backdrop"
        @click.self="emit('close')"
        @contextmenu.prevent="emit('close')"
      ></div>

      <!-- 菜单主体：条目由使用方插槽提供 -->
      <div
        v-if="visible"
        ref="menuRef"
        class="context-menu"
        :style="{ left: adjustedX + 'px', top: adjustedY + 'px' }"
      >
        <slot :close="() => emit('close')" />
      </div>
    </div>
  </Teleport>
</template>

<style>
/* ContextMenu 样式（非 scoped，因为使用 Teleport）
   所有选择器加 .ctx-menu-root 前缀防止全局污染；
   插槽内容由使用方模板渲染，这些类是它唯一的样式来源，故必须是全局的。 */

/* 遮罩层：点击即关闭菜单 */
.ctx-menu-root .context-menu-backdrop {
  position: fixed;
  inset: 0;
  z-index: 998;
  background: transparent;
}

/* 菜单容器 */
.ctx-menu-root .context-menu {
  position: fixed;
  z-index: 999;
  min-width: 170px;
  background: var(--color-bg-primary, #ffffff);
  border: 1px solid var(--color-border, #e5e7eb);
  border-radius: 8px;
  box-shadow: var(--shadow-popup, 0 4px 16px rgba(0,0,0,0.12));
  padding: 6px 0;
  font-size: 13px;
  color: var(--color-text-primary, #1f2937);
  user-select: none;
}

/* 菜单项 */
.ctx-menu-root .menu-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px;
  cursor: pointer;
  transition: background 0.15s ease;
  white-space: nowrap;
}

.ctx-menu-root .menu-item:hover {
  background: var(--color-bg-surface, #f3f4f6);
}

.ctx-menu-root .menu-item:active {
  background: var(--color-bg-hover, #e5e7eb);
}

/* 危险操作（删除） */
.ctx-menu-root .menu-item-danger {
  color: var(--color-red, #ef4444);
}

.ctx-menu-root .menu-item-danger:hover {
  background: var(--color-red-light, #fef2f2);
}

/* 无操作可执行的说明项（如「未分类」不给删除）：灰字 + 不可点，点击仅关闭菜单 */
.ctx-menu-root .menu-item-muted {
  color: var(--color-text-muted, #94a3b8);
  cursor: default;
}

.ctx-menu-root .menu-item-muted:hover {
  background: transparent;
}

/* 图标区域：内联 SVG 跟随条目文字色 */
.ctx-menu-root .menu-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  flex-shrink: 0;
  opacity: 0.85;
}

/* 分隔线 */
.ctx-menu-root .menu-divider {
  height: 1px;
  background: var(--color-border, #e5e7eb);
  margin: 4px 8px;
}
</style>
