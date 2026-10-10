/**
 * contextMenu.js — 弹出菜单状态封装
 *
 * 只负责「在哪弹、对谁弹、开没开」；菜单项与动作由使用方给出（见 ContextMenu.vue 的插槽）。
 * 各处入口（侧栏三点按钮 / 图谱画布右键）共用这一套开关逻辑，新增入口不必再抄一遍
 * 5 个 ref 与 open/openAt/close。
 *
 * 用法：
 *   const { visible, x, y, targetType, targetData, open, openAt, close } = useContextMenu()
 *   <div @contextmenu.prevent="open($event, 'subject', s)">…</div>          // 右键
 *   <button @click.stop="openAt($event.currentTarget, 'subject', s)">⋯</button>  // 三点按钮
 *   <ContextMenu :visible="visible" :x="x" :y="y" @close="close">…</ContextMenu>
 */
import { ref } from 'vue'

export function useContextMenu() {
  const visible = ref(false)
  const x = ref(0)
  const y = ref(0)
  const targetType = ref('')
  const targetData = ref(null)

  /**
   * 在鼠标位置打开菜单。
   * @param {MouseEvent} event - 原生 contextmenu 事件（取 clientX/clientY）
   * @param {string} type - 右键目标类型，取值由使用方约定（如 'node' / 'subject'）
   * @param {any} data - 右键目标数据（菜单动作据此决定操作对象）
   */
  function open(event, type = '', data = null) {
    x.value = event.clientX
    y.value = event.clientY
    targetType.value = type
    targetData.value = data
    visible.value = true
  }

  /**
   * 在指定元素的右下方打开菜单（常驻三点按钮的入口；菜单越界由 ContextMenu 自行修正）。
   * @param {HTMLElement} el - 触发元素，取其视口坐标
   * @param {string} type - 目标类型，取值由使用方约定
   * @param {any} data - 目标数据
   */
  function openAt(el, type = '', data = null) {
    const rect = el.getBoundingClientRect()
    x.value = rect.right
    y.value = rect.bottom
    targetType.value = type
    targetData.value = data
    visible.value = true
  }

  function close() {
    visible.value = false
  }

  return { visible, x, y, targetType, targetData, open, openAt, close }
}
