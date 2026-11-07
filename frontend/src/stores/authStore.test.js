/**
 * authStore 回归（P1 前端健壮性）：本地 user 被写坏时不能「启动即白屏」。
 * localStorage 是信任边界（旧版本残留 / 浏览器插件 / 手工改都可能写坏），
 * 损坏值必须回退 null 而非让 JSON.parse 在 store setup 阶段抛错。
 *
 * 跑法（本机 npm test 因 Vite net use / spawn EPERM 跑不通）：
 *   cd frontend && node node_modules/vitest/vitest.mjs run \
 *     --config ../.dsh_local/vitest_p1_a11y.mjs --configLoader runner --pool=threads
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

const USER_KEY = 'ai_tutor_user'

describe('authStore 启动读取本地用户信息', () => {
  let warnSpy

  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
    warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
  })

  afterEach(() => {
    warnSpy.mockRestore()
    vi.resetModules()
  })

  it('损坏的 JSON 回退 user=null 并 console.warn，不抛异常', async () => {
    localStorage.setItem(USER_KEY, '{bad json')
    const { useAuthStore } = await import('./authStore.js')
    const store = useAuthStore()
    expect(store.user).toBe(null)
    expect(warnSpy).toHaveBeenCalled()
  })

  it('合法 JSON 正常解析，不被误伤成 null', async () => {
    localStorage.setItem(USER_KEY, JSON.stringify({ id: 1, username: 'alice' }))
    const { useAuthStore } = await import('./authStore.js')
    const store = useAuthStore()
    expect(store.user).toEqual({ id: 1, username: 'alice' })
  })
})
