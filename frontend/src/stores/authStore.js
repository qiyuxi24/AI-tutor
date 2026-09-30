import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { apiClient } from '../api/index.js'
import { encryptPassword } from '../utils/crypto.js'
import { formatError } from '../utils/errorCodes.js'

const STORAGE_KEY_TOKEN = 'ai_tutor_token'
const STORAGE_KEY_USER = 'ai_tutor_user'

// 免登录体验账户：无 token 时静默登录它（不存在则先注册）。
// 站点已取消 #/login 路由页，"登录"退化为切换账号。
//
// DEFAULT_PASS 必须与后端建号用的 DEFAULT_ADMIN_PASSWORD 一致，否则静默登录会
// 失败（login 401 + register 409）→ 首屏弹登录框让用户手工登录。构建期可用
// VITE_DEMO_PASSWORD 覆盖，避免把口令烘进生产产物（见 README 配置表）。
const DEFAULT_USER = 'admin'
const DEFAULT_PASS = import.meta.env.VITE_DEMO_PASSWORD || 'admin123'

// in-flight 去重：首屏并发请求可能同时 401，只允许触发一次静默登录
let sessionPromise = null

export const useAuthStore = defineStore('auth', () => {
  // ─── 状态 ───
  const token = ref(localStorage.getItem(STORAGE_KEY_TOKEN) || null)
  const user = ref(JSON.parse(localStorage.getItem(STORAGE_KEY_USER) || 'null'))
  const loginError = ref('')

  // ─── 计算属性 ───
  const isLoggedIn = computed(() => !!token.value)
  const username = computed(() => user.value?.username || '')

  // ─── 设置 token ───
  function setToken(newToken) {
    token.value = newToken
    if (newToken) {
      localStorage.setItem(STORAGE_KEY_TOKEN, newToken)
    } else {
      localStorage.removeItem(STORAGE_KEY_TOKEN)
    }
  }

  // ─── 设置用户信息 ───
  function setUser(newUser) {
    user.value = newUser
    if (newUser) {
      localStorage.setItem(STORAGE_KEY_USER, JSON.stringify(newUser))
    } else {
      localStorage.removeItem(STORAGE_KEY_USER)
    }
  }

  // ─── 带传输加密的请求（密码 RSA 加密后提交） ───
  /**
   * @param {string} url
   * @param {(refresh: boolean) => Promise<object>} buildBody 每次重试都重新加密（密文不可复用）
   */
  async function _postEncrypted(url, buildBody) {
    try {
      return await apiClient.post(url, await buildBody(false))
    } catch (err) {
      // 服务端密钥可能已轮换（如容器重建）：刷新公钥重试一次
      const detail = String(err.response?.data?.detail || '')
      if (err.response?.status === 400 && detail.includes('E-AUTH-007')) {
        return await apiClient.post(url, await buildBody(true))
      }
      throw err
    }
  }

  /** 构造 {username, password} 请求体（password 为 RSA 密文） */
  const _credentialBody = (username, password) => async (refresh) => ({
    username,
    password: await encryptPassword(password, { refresh }),
  })

  // ─── 注册 ───
  async function register(username, password) {
    loginError.value = ''
    try {
      const { data } = await _postEncrypted(
        '/api/v1/auth/register', _credentialBody(username, password))
      setToken(data.token)
      setUser(data.user)
      return true
    } catch (err) {
      loginError.value = formatError(err, { action: '注册失败' })
      return false
    }
  }

  // ─── 登录 ───
  async function login(username, password) {
    loginError.value = ''
    try {
      const { data } = await _postEncrypted(
        '/api/v1/auth/login', _credentialBody(username, password))
      setToken(data.token)
      setUser(data.user)
      return true
    } catch (err) {
      loginError.value = formatError(err, { action: '登录失败' })
      return false
    }
  }

  // ─── 修改密码（原密码 + 新密码，均 RSA 加密）───
  // 不用 loginError：那个是给登录弹窗显示的；这里由调用方拿 message 自己提示。
  async function changePassword(oldPassword, newPassword) {
    try {
      await _postEncrypted('/api/v1/auth/change-password', async (refresh) => ({
        old_password: await encryptPassword(oldPassword, { refresh }),
        new_password: await encryptPassword(newPassword, { refresh }),
      }))
      return { ok: true, message: '' }
    } catch (err) {
      return { ok: false, message: formatError(err, { action: '修改密码失败' }) }
    }
  }

  // ─── 清除会话（内部用：401 处理与 token 校验失败；UI 已无"退出登录"入口）───
  function logout() {
    setToken(null)
    setUser(null)
  }

  // ─── 初始化时验证 token 有效性 ───
  async function checkAuth() {
    if (!token.value) return false
    try {
      const { data } = await apiClient.get('/api/v1/auth/me')
      // /auth/me 返回 { user_id, username }，转换为 user 对象
      setUser({ id: data.user_id, username: data.username })
      return true
    } catch {
      // token 无效，清除
      logout()
      return false
    }
  }

  // ─── 确保有可用会话（无登录墙：无 token 就静默登体验账户）───
  // 唯一调用点：HomeView.onMounted（首屏）与 api 层 401 处理（token 过期）。
  async function ensureSession() {
    if (isLoggedIn.value && (await checkAuth())) return true
    if (!sessionPromise) {
      sessionPromise = (async () => {
        // 先用默认账户登录；首次启动库里没这个账户时注册兜底
        return (await login(DEFAULT_USER, DEFAULT_PASS)) || (await register(DEFAULT_USER, DEFAULT_PASS))
      })().finally(() => {
        sessionPromise = null
      })
    }
    return sessionPromise
  }

  return {
    token,
    user,
    loginError,
    isLoggedIn,
    username,
    register,
    login,
    changePassword,
    logout,
    checkAuth,
    ensureSession,
    setToken,
    setUser,
  }
})
