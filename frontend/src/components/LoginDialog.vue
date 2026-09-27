<script setup>
/**
 * LoginDialog.vue — 登录 / 注册弹窗
 *
 * 站点默认静默登录体验账户（authStore.ensureSession），本弹窗只承担"切换账号"：
 * 左侧活动栏头像或设置页按钮打开它，成功后由父组件重载页面。
 * 原路由页 views/LoginView.vue 已删除（路由表也不再引用）。
 */
import { ref, computed, watch } from 'vue'
import { useAuthStore } from '../stores/authStore'
import { clientError } from '../utils/errorCodes.js'

const props = defineProps({
  visible: { type: Boolean, default: false },
})
const emit = defineEmits(['update:visible', 'success'])

const authStore = useAuthStore()

const show = computed({
  get: () => props.visible,
  set: (v) => emit('update:visible', v),
})

const isRegister = ref(false)
const username = ref('')
const password = ref('')
const confirmPassword = ref('')
const localError = ref('')
const submitting = ref(false)

const errorText = computed(() => localError.value || authStore.loginError)

// 每次打开都从干净的表单开始
watch(show, (v) => {
  if (!v) return
  isRegister.value = false
  username.value = ''
  password.value = ''
  confirmPassword.value = ''
  clearErrors()
})

function clearErrors() {
  localError.value = ''
  authStore.loginError = ''
}

function fail(msg) {
  localError.value = clientError('VALIDATION', msg)
}

async function handleSubmit() {
  clearErrors()
  const name = username.value.trim()

  if (!name || !password.value) return fail('请填写用户名和密码')
  if (name.length < 3) return fail('用户名至少需要 3 个字符')
  if (password.value.length < 6) return fail('密码至少需要 6 个字符')
  if (isRegister.value && password.value !== confirmPassword.value) return fail('两次输入的密码不一致')

  submitting.value = true
  const ok = isRegister.value
    ? await authStore.register(name, password.value)
    : await authStore.login(name, password.value)
  submitting.value = false

  // 失败时 authStore.loginError 已就位，由 errorText 渲染
  if (ok) emit('success')
}
</script>

<template>
  <el-dialog
    v-model="show"
    :title="isRegister ? '注册账号' : '切换账号'"
    width="380px"
    align-center
    append-to-body
  >
    <form class="login-form" @submit.prevent="handleSubmit">
      <div class="form-group">
        <label for="dlg-username">用户名</label>
        <input
          id="dlg-username"
          v-model="username"
          type="text"
          placeholder="请输入用户名"
          autocomplete="username"
          @input="clearErrors"
        />
      </div>

      <div class="form-group">
        <label for="dlg-password">密码</label>
        <input
          id="dlg-password"
          v-model="password"
          type="password"
          placeholder="请输入密码"
          autocomplete="current-password"
          @input="clearErrors"
        />
      </div>

      <div v-if="isRegister" class="form-group">
        <label for="dlg-confirm">确认密码</label>
        <input
          id="dlg-confirm"
          v-model="confirmPassword"
          type="password"
          placeholder="请再次输入密码"
          autocomplete="new-password"
          @input="clearErrors"
        />
      </div>

      <div v-if="errorText" class="error-msg">{{ errorText }}</div>

      <button type="submit" class="submit-btn" :disabled="submitting">
        {{ submitting ? '处理中...' : (isRegister ? '注册' : '登录') }}
      </button>

      <p class="switch-mode">
        {{ isRegister ? '已有账号？' : '没有账号？' }}
        <a href="#" @click.prevent="isRegister = !isRegister">
          {{ isRegister ? '去登录' : '去注册' }}
        </a>
      </p>
    </form>
  </el-dialog>
</template>

<style scoped>
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
  transition: background 0.2s ease, opacity 0.2s ease;
}

.submit-btn:hover:not(:disabled) {
  background: var(--color-blue);
}

.submit-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.switch-mode {
  text-align: center;
  margin: 0;
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
</style>
