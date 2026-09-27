/**
 * contextMenu.js — 右键菜单状态封装
 *
 * 只负责「在哪弹、对谁弹、开没开」；菜单项与动作由使用方给出（见 ContextMenu.vue 的插槽）。
 * 各处右键入口（图谱画布 / 学科栏 / 板块栏…）共用这一套开关逻辑，新增入口不必再抄一遍
 * 5 个 ref 与 open/close。
 *
 * 用法：
 *   const { visible, x, y, targetType, targetData, open, close } = useContextMenu()
 *   <div @contextmenu.prevent="open($event, 'subject', s)">…</div>
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

  function close() {
    visible.value = false
  }

  return { visible, x, y, targetType, targetData, open, close }
}
