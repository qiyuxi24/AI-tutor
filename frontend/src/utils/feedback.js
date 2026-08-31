/**
 * 统一前端提示工具（替代原生 alert/confirm）
 *
 * 背景：项目已全局引入 Element Plus，原生 alert() 是浏览器模态对话框，
 * 样式丑陋、无主题适配、与 EP 设计语言不一致。统一走 ElMessage 轻提示。
 *
 * 使用方式:
 *   import { notifyError, notifySuccess, notifyInfo, notifyWarning } from '@/utils/feedback'
 *   notifyError(formatError(e, { action: '删除节点' }))
 *   notifySuccess('保存成功')
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
