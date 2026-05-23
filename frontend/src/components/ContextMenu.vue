<script setup>
/**
 * ContextMenu.vue — 知识图谱右键菜单组件
 *
 * 根据右键点击的目标类型（节点/边/空白）显示不同的操作选项。
 * 自动检测视口边界，确保菜单不超出屏幕。
 *
 * 用法（由 ForceGraph.vue 集成）：
 *   <ContextMenu
 *     :visible="menuVisible"
 *     :x="menuX" :y="menuY"
 *     :targetType="menuTargetType"
 *     :targetData="menuTargetData"
 *     @close="closeMenu"
 *     @create-node="handleCreateNode"
 *     @edit-node="handleEditNode"
 *     ...
 *   />
 */

import { ref, watch, nextTick } from 'vue'

/* ================================================================
   Props
   ================================================================ */
const props = defineProps({
  visible: { type: Boolean, default: false },
  x: { type: Number, default: 0 },
  y: { type: Number, default: 0 },
  /** 'node' | 'edge' | 'canvas' */
  targetType: { type: String, default: 'canvas' },
  /** 节点对象 / 边对象 / null */
  targetData: { type: Object, default: null },
})

/* ================================================================
   Emits
   ================================================================ */
const emit = defineEmits([
  'close',
  'create-node',        // 空白处创建节点 → { x, y }
  'edit-node',          // 编辑节点 → node对象
  'delete-node',        // 删除节点 → nodeId
  'add-edge-from-node', // 从节点出发添加边 → nodeId
  'edit-edge',          // 编辑边 → { edge, index }
  'delete-edge',        // 删除边 → { edge, index }
])

/* ================================================================
   边界修正：菜单定位不超出视口
   ================================================================ */
const adjustedX = ref(0)
const adjustedY = ref(0)
const menuRef = ref(null)

/**
 * 计算菜单实际位置，防止溢出视口
 */
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

/* ================================================================
   事件处理（emit 后自动关闭菜单）
   ================================================================ */
function handleClick(action, payload = null) {
  emit(action, payload)
  emit('close')
}
</script>

<template>
  <Teleport to="body">
    <!-- 点击空白处关闭菜单的遮罩层 -->
    <div
      v-if="visible"
      class="context-menu-backdrop"
      @click.self="emit('close')"
      @contextmenu.prevent="emit('close')"
    ></div>

    <!-- 菜单主体 -->
    <div
      v-if="visible"
      ref="menuRef"
      class="context-menu"
      :style="{ left: adjustedX + 'px', top: adjustedY + 'px' }"
    >
      <!-- ── 空白区域 ── -->
      <template v-if="targetType === 'canvas'">
        <div class="menu-item" @click="handleClick('create-node', { x, y })">
          <span class="menu-icon">➕</span> 创建新节点
        </div>
      </template>

      <!-- ── 节点 ── -->
      <template v-else-if="targetType === 'node'">
        <div class="menu-item" @click="handleClick('edit-node', targetData)">
          <span class="menu-icon">✏️</span> 编辑节点
        </div>
        <div class="menu-item" @click="handleClick('add-edge-from-node', targetData?.id)">
          <span class="menu-icon">🔗</span> 添加关联边
        </div>
        <div class="menu-divider"></div>
        <div class="menu-item menu-item-danger" @click="handleClick('delete-node', targetData?.id)">
          <span class="menu-icon">❌</span> 删除节点
        </div>
      </template>

      <!-- ── 边 ── -->
      <template v-else-if="targetType === 'edge'">
        <div class="menu-item" @click="handleClick('edit-edge', targetData)">
          <span class="menu-icon">✏️</span> 编辑边标签
        </div>
        <div class="menu-divider"></div>
        <div class="menu-item menu-item-danger" @click="handleClick('delete-edge', targetData)">
          <span class="menu-icon">❌</span> 删除边
        </div>
      </template>
    </div>
  </Teleport>
</template>

<style scoped>
/* ================================================================
   遮罩层：点击即关闭菜单
   ================================================================ */
.context-menu-backdrop {
  position: fixed;
  inset: 0;
  z-index: 998;
  background: transparent;
}

/* ================================================================
   菜单容器
   ================================================================ */
.context-menu {
  position: fixed;
  z-index: 999;
  min-width: 170px;
  background: #1e1e2e;
  border: 1px solid #313244;
  border-radius: 8px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.45);
  padding: 6px 0;
  /* 字体 */
  font-size: 13px;
  color: #e5e7eb;
  user-select: none;
}

/* ================================================================
   菜单项
   ================================================================ */
.menu-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px;
  cursor: pointer;
  transition: background 0.15s ease;
  white-space: nowrap;
}

.menu-item:hover {
  background: #313244;
}

.menu-item:active {
  background: #45475a;
}

/* 危险操作（删除） */
.menu-item-danger {
  color: #f87171;
}

.menu-item-danger:hover {
  background: #3b2025;
}

/* 图标区域 */
.menu-icon {
  width: 18px;
  text-align: center;
  font-size: 13px;
  flex-shrink: 0;
}

/* 分隔线 */
.menu-divider {
  height: 1px;
  background: #313244;
  margin: 4px 8px;
}
</style>
