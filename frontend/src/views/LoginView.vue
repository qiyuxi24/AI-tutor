<script setup>
import { ref, watch, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useAuthStore } from '../stores/authStore'
import { clientError } from '../utils/errorCodes.js'

const router = useRouter()
const route = useRoute()
const authStore = useAuthStore()

const isRegister = ref(false)
const username = ref('')
const password = ref('')
const confirmPassword = ref('')
const localError = ref('')
const skipLoading = ref(false)
let errorTimer = null

// 默认管理员账户
const DEFAULT_USER = 'admin'
const DEFAULT_PASS = 'admin123'

// 页面加载时，如果已有 token 且有效，直接跳转
onMounted(async () => {
  if (authStore.isLoggedIn) {
    const valid = await authStore.checkAuth()
    if (valid) {
      const redirect = route.query.redirect || '/'
      router.replace(redirect)
    }
  }
})

// 显示错误，5秒后自动消失
function showError(msg) {
  // 清除旧的定时器
  if (errorTimer) clearTimeout(errorTimer)

  // 优先显示 store 中的错误（来自后端，含错误码），否则显示本地校验错误
  if (authStore.loginError) {
    localError.value = ''
  } else {
    localError.value = msg
  }

  // 5 秒后自动清除
  errorTimer = setTimeout(() => {
    localError.value = ''
    authStore.loginError = ''
    errorTimer = null
  }, 5000)
}

// 切换模式时清空错误
watch(isRegister, () => {
  clearErrors()
})

function clearErrors() {
  localError.value = ''
  authStore.loginError = ''
  if (errorTimer) {
    clearTimeout(errorTimer)
    errorTimer = null
  }
}

async function handleSubmit() {
  clearErrors()

  if (!username.value.trim() || !password.value.trim()) {
    showError(clientError('VALIDATION', '请填写用户名和密码'))
    return
  }

  if (username.value.trim().length < 3) {
    showError(clientError('VALIDATION', '用户名至少需要 3 个字符'))
    return
  }

  if (password.value.length < 6) {
    showError(clientError('VALIDATION', '密码至少需要 6 个字符'))
    return
  }

  if (isRegister.value && password.value !== confirmPassword.value) {
    showError(clientError('VALIDATION', '两次输入的密码不一致'))
    return
  }

  let success
  if (isRegister.value) {
    success = await authStore.register(username.value.trim(), password.value)
  } else {
    success = await authStore.login(username.value.trim(), password.value)
  }

  if (success) {
    const redirect = route.query.redirect || '/'
    router.replace(redirect)
  } else {
    // 后端返回的错误已在 authStore.loginError 中，触发显示
    showError('')
  }
}

// 跳过登录：使用默认管理员账户自动登录
async function handleSkipLogin() {
  clearErrors()
  skipLoading.value = true

  // 先用默认账户尝试登录
  let success = await authStore.login(DEFAULT_USER, DEFAULT_PASS)

  if (!success) {
    // 如果默认账户不存在（比如首次启动），先注册再登录
    success = await authStore.register(DEFAULT_USER, DEFAULT_PASS)
  }

  skipLoading.value = false

  if (success) {
    const redirect = route.query.redirect || '/'
    router.replace(redirect)
  }
  // 失败时错误已在 authStore.loginError 中
}
</script>

<template>
  <div class="login-page">
    <div class="login-card">
      <div class="login-header">
        <h1 class="brand-title">AI Tutor</h1>
        <p class="brand-subtitle">智能学习助手</p>
      </div>

      <h2 class="form-title">{{ isRegister ? '创建账号' : '登录账号' }}</h2>

      <form class="login-form" @submit.prevent="handleSubmit">
        <div class="form-group">
          <label for="username">用户名</label>
          <input
            id="username"
            v-model="username"
            type="text"
            placeholder="请输入用户名"
            autocomplete="username"
            @input="clearErrors"
          />
        </div>

        <div class="form-group">
          <label for="password">密码</label>
          <input
            id="password"
            v-model="password"
            type="password"
            placeholder="请输入密码"
            autocomplete="current-password"
            @input="clearErrors"
          />
        </div>

        <div v-if="isRegister" class="form-group">
          <label for="confirmPassword">确认密码</label>
          <input
            id="confirmPassword"
            v-model="confirmPassword"
            type="password"
            placeholder="请再次输入密码"
            autocomplete="new-password"
            @input="clearErrors"
          />
        </div>

        <!-- 错误提示 -->
        <div v-if="localError || authStore.loginError" class="error-msg">
          {{ localError || authStore.loginError }}
        </div>

        <button
          type="submit"
          class="submit-btn"
          :disabled="authStore.loginLoading"
        >
          <span v-if="authStore.loginLoading" class="spinner"></span>
          {{ authStore.loginLoading ? '处理中...' : (isRegister ? '注册' : '登录') }}
        </button>
      </form>

      <p class="switch-mode">
        {{ isRegister ? '已有账号？' : '没有账号？' }}
        <a href="#" @click.prevent="isRegister = !isRegister">
          {{ isRegister ? '去登录' : '去注册' }}
        </a>
      </p>

      <div class="skip-section">
        <div class="divider">
          <span class="divider-text">快速体验</span>
        </div>
        <button
          class="skip-btn"
          :disabled="authStore.loginLoading || skipLoading"
          @click="handleSkipLogin"
        >
          <span v-if="skipLoading" class="spinner"></span>
          {{ skipLoading ? '登录中...' : '跳过登录，体验默认账户' }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.login-page {
  width: 100vw;
  height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--color-bg-secondary);
}

.login-card {
  width: 380px;
  padding: 40px 36px;
  background: var(--color-bg-primary);
  border: 1px solid var(--color-border);
  border-radius: 16px;
  box-shadow: var(--shadow-card);
}

.login-header {
  text-align: center;
  margin-bottom: 28px;
}

.brand-title {
  font-size: 28px;
  font-weight: 700;
  color: var(--color-text-primary);
  margin: 0 0 4px 0;
}

.brand-subtitle {
  font-size: 13px;
  color: var(--color-text-tertiary);
  margin: 0;
}

.form-title {
  font-size: 18px;
  font-weight: 600;
  color: var(--color-text-primary);
  margin: 0 0 24px 0;
  text-align: center;
}

.login-form {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.form-group {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.form-group label {
  font-size: 13px;
  color: var(--color-text-secondary);
  font-weight: 500;
}

.form-group input {
  height: 40px;
  padding: 0 12px;
  background: var(--color-bg-secondary);
  border: 1px solid var(--color-border);
  border-radius: 8px;
  color: var(--color-text-primary);
  font-size: 14px;
  outline: none;
  transition: border-color 0.2s ease;
}

.form-group input:focus {
  border-color: var(--color-blue-light);
}

.form-group input::placeholder {
  color: var(--color-text-tertiary);
}

.error-msg {
  padding: 10px 12px;
  background: var(--color-red-light);
  border: 1px solid var(--color-red);
  border-radius: 8px;
  color: var(--color-red);
  font-size: 13px;
}

.submit-btn {
  height: 42px;
  background: var(--color-blue-light);
  color: var(--color-bg-secondary);
  border: none;
  border-radius: 8px;
  font-size: 15px;
  font-weight: 600;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  transition: background 0.2s ease, opacity 0.2s ease;
}

.submit-btn:hover:not(:disabled) {
  background: var(--color-blue);
}

.submit-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.spinner {
  width: 16px;
  height: 16px;
  border: 2px solid var(--color-bg-secondary);
  border-top-color: transparent;
  border-radius: 50%;
  animation: spin 0.6s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.switch-mode {
  text-align: center;
  margin-top: 20px;
  font-size: 13px;
  color: var(--color-text-tertiary);
}

.switch-mode a {
  color: var(--color-blue-light);
  text-decoration: none;
  font-weight: 500;
}

.switch-mode a:hover {
  text-decoration: underline;
}

.skip-section {
  margin-top: 20px;
}

.divider {
  display: flex;
  align-items: center;
  margin-bottom: 12px;
}

.divider::before,
.divider::after {
  content: '';
  flex: 1;
  height: 1px;
  background: var(--color-border);
}

.divider-text {
  padding: 0 12px;
  font-size: 12px;
  color: var(--color-text-tertiary);
}

.skip-btn {
  width: 100%;
  height: 38px;
  background: transparent;
  color: var(--color-text-secondary);
  border: 1px dashed var(--color-border);
  border-radius: 8px;
  font-size: 13px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  transition: all 0.2s ease;
}

.skip-btn:hover:not(:disabled) {
  border-color: var(--color-blue-light);
  color: var(--color-blue-light);
  background: rgba(59, 130, 246, 0.05);
}

.skip-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
