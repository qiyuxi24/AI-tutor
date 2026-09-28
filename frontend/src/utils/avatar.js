/**
 * avatar.js — 用户头像的加载 / 上传 / 恢复默认
 *
 * 真值源 = 服务端 `<画像目录>/avatars/{user_id}.png`
 * （见 backend/app/api/v1/profile.py 的 `/profile/avatar` 三个端点）。
 *
 * 为什么必须走 axios 取 blob 再 `createObjectURL`：
 *   token 存在 localStorage、由 axios 拦截器挂 `Authorization` 头；原生
 *   `<img src="/api/v1/profile/avatar">` **不受拦截器管辖** → 直接写 URL 必然 401。
 *   所以图片要取回来转成 objectURL 再交给 `<img>`。
 *
 * `avatarState` 是**全应用共享的响应式状态**：设置页一改，活动栏与对话里的头像立刻响应，
 * 不需要刷新页面也不需要事件总线（结构对齐 utils/floatingBall.js）。
 *
 * 文档：docs/用户头像_设计与实施方案.md
 */
import { reactive } from 'vue'
import { deleteAvatar, getAvatarBlob, uploadAvatarFile } from '../api/index.js'

/** 全应用共享的头像状态。`url === null` 表示"没有头像"，组件应回落到默认人形图标 */
export const avatarState = reactive({
  url: null,
  loading: false,
  /** 每次成功变更自增（设置页据此提示"已更新"） */
  version: 0,
})

/** 释放旧的 objectURL —— 不释放就是内存泄漏（浏览器不会自动回收） */
function revoke() {
  if (avatarState.url) {
    URL.revokeObjectURL(avatarState.url)
    avatarState.url = null
  }
}

/**
 * 从服务端加载头像。
 * 404（没设置过）与网络失败一律视为"没有头像" —— 不抛错、不打断首屏。
 * @returns {Promise<string|null>} objectURL
 */
export async function loadAvatar() {
  avatarState.loading = true
  try {
    const { data } = await getAvatarBlob()
    revoke()                                   // 先释放旧的，再换新的
    avatarState.url = URL.createObjectURL(data)
  } catch {
    revoke()
  } finally {
    avatarState.loading = false
  }
  return avatarState.url
}

/**
 * 上传并立即切换成新头像（后端会裁成 256×256 PNG）。
 * @param {File} file
 * @returns {Promise<{bytes:number,size:number}>} 后端返回的处理结果
 * @throws 校验失败时抛出 axios error（413 超限 / 415 格式 / 422 解码失败），由调用方提示
 */
export async function uploadAvatar(file) {
  const { data } = await uploadAvatarFile(file)
  await loadAvatar()
  avatarState.version += 1
  return data
}

/** 恢复默认头像（幂等：本来就没有也成功） */
export async function removeAvatar() {
  await deleteAvatar()
  revoke()
  avatarState.version += 1
}
