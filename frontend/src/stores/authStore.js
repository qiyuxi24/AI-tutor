import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { apiClient } from '../api/index.js'
import { formatError } from '../utils/errorCodes.js'

const STORAGE_KEY_TOKEN = 'ai_tutor_token'
const STORAGE_KEY_USER = 'ai_tutor_user'

export const useAuthStore = defineStore('auth', () => {
  // ─── 状态 ───
  const token = ref(localStorage.getItem(STORAGE_KEY_TOKEN) || null)
  const user = ref(JSON.parse(localStorage.getItem(STORAGE_KEY_USER) || 'null'))
  const loginLoading = ref(false)
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

  // ─── 注册 ───
  async function register(username, password) {
    loginLoading.value = true
    loginError.value = ''
    try {
      const { data } = await apiClient.post('/api/v1/auth/register', {
        username,
        password,
      })
      setToken(data.token)
      setUser(data.user)
      return true
    } catch (err) {
      loginError.value = formatError(err, { action: '注册失败' })
      return false
    } finally {
      loginLoading.value = false
    }
  }

  // ─── 登录 ───
  async function login(username, password) {
    loginLoading.value = true
    loginError.value = ''
    try {
      const { data } = await apiClient.post('/api/v1/auth/login', {
        username,
        password,
      })
      setToken(data.token)
      setUser(data.user)
      return true
    } catch (err) {
      loginError.value = formatError(err, { action: '登录失败' })
      return false
    } finally {
      loginLoading.value = false
    }
  }

  // ─── 退出登录 ───
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

  return {
    token,
    user,
    loginLoading,
    loginError,
    isLoggedIn,
    username,
    register,
    login,
    logout,
    checkAuth,
    setToken,
    setUser,
  }
})
