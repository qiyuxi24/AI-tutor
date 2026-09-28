/**
 * 统一前端提示工具
 *
 * 分工：轻提示（成功/失败/警告）走 Element Plus 的 ElMessage；确认对话框走浏览器原生 confirm。
 *
 * 使用方式:
 *   import { notifyError, notifySuccess, notifyInfo, notifyWarning, confirmAction } from '@/utils/feedback'
 *   notifyError(formatError(e, { action: '删除节点' }))
 *   notifySuccess('保存成功')
 *   if (!confirmAction('确定删除该文件吗？此操作不可撤销。')) return
 *
 * ElMessage 已由 main.js 的 app.use(ElementPlus) 全局注册，
 * 此处直接导入即可，样式自动适配浅色/深色主题。
 */
import { ElMessage } from 'element-plus'

/**
 * 错误提示
 * @param {string} message - 提示文本（通常是 formatError 的结果）
 */
export function notifyError(message) {
  ElMessage({ type: 'error', message, duration: 4000, showClose: true })
}

/**
 * 成功提示
 * @param {string} message - 提示文本
 */
export function notifySuccess(message) {
  ElMessage({ type: 'success', message, duration: 2000 })
}

/**
 * 信息提示
 * @param {string} message - 提示文本
 */
export function notifyInfo(message) {
  ElMessage({ type: 'info', message, duration: 2000 })
}

/**
 * 警告提示（常用于表单校验等温和提醒）
 * @param {string} message - 提示文本
 */
export function notifyWarning(message) {
  ElMessage({ type: 'warning', message, duration: 3000, showClose: true })
}

/**
 * 危险操作确认 —— 全项目唯一的确认出口，内部用浏览器原生 window.confirm
 *
 * 为什么用原生：样式虽朴素，但零维护、零依赖、不可能与主题/暗色/无障碍配置脱节。
 * 曾用 ElMessageBox 统一过一版（可定制标题与红色危险按钮），观感不满意后回退；
 * 那版实现留档在 frontend/parked/confirm-dialog/（该目录已 gitignore，仅本地保留）。
 * 自研一套好看的确认 UI 成本高（样式、暗色主题、焦点管理、调用队列），暂不做。
 *
 * 为什么仍然保留这一层薄封装（而不是各调用点直接写 window.confirm）：
 *   全项目只有一个确认出口 —— 将来若真要换自研 UI，只改这里一处，调用点零改动。
 *
 * 调用约定（两种实现共用，勿依赖原生 confirm 的返回值之外的特性）：
 *   以 `await confirmAction(正文)` 调用亦可（await 一个 boolean 合法），故签名可平滑替换。
 *
 * @param {string} message - 正文，建议 `确定删除<对象>「<名>」吗？` + 后果句（不可撤销等）
 * @returns {boolean} 确认 true；取消 false
 */
export function confirmAction(message) {
  return window.confirm(message)
}
