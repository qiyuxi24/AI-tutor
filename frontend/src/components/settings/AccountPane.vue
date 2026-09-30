<script setup>
/**
 * AccountPane.vue — 设置 · 账号（第一个 tab）
 *
 * 一个面板收齐所有"跟账号本身有关"的事：头像 / 当前用户 / 修改密码 / 切换账号。
 * 「切换账号」要触达 HomeView（登录弹窗挂在那里），所以只做事件转发。
 *
 * 改密码走 transport_crypto（RSA-OAEP）—— 明文密码不上行，与登录/注册同一套。
 */
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '../../stores/authStore'
import { avatarState, removeAvatar, uploadAvatar } from '../../utils/avatar.js'

defineEmits(['switch-account'])

const authStore = useAuthStore()

// ─── 头像（真值源 = 服务端图片；avatarState 共享，改完活动栏/对话立刻同步）───
const avatarFileInput = ref(null)
const avatarBusy = ref(false)

/** 打开系统文件选择框（真正的上传在 @change 里做） */
function pickAvatarFile() {
  if (avatarBusy.value) return
  avatarFileInput.value?.click()
}

async function handleAvatarFile(event) {
  const file = event.target.files?.[0]
  // 清空 value：否则"选了同一个文件再传一次"不会触发 change
  event.target.value = ''
  if (!file) return
  avatarBusy.value = true
  try {
    await uploadAvatar(file)
    ElMessage.success('头像已更新')
  } catch (e) {
    // 后端按 413/415/422 分了级，detail 是可直接读的中文说明
    ElMessage.error(e.response?.data?.detail || e.message || '上传失败')
  } finally {
    avatarBusy.value = false
  }
}

async function handleAvatarReset() {
  if (avatarBusy.value || !avatarState.url) return
  avatarBusy.value = true
  try {
    await removeAvatar()
    ElMessage.success('已恢复默认头像')
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || e.message || '操作失败')
  } finally {
    avatarBusy.value = false
  }
}

// ─── 修改密码 ───
const showPwdForm = ref(false)
const pwdForm = ref({ oldPwd: '', newPwd: '', confirmPwd: '' })
const pwdSaving = ref(false)

function togglePwdForm() {
  showPwdForm.value = !showPwdForm.value
  pwdForm.value = { oldPwd: '', newPwd: '', confirmPwd: '' }
}

async function submitPassword() {
  const { oldPwd, newPwd, confirmPwd } = pwdForm.value
  if (!oldPwd || !newPwd || !confirmPwd) return ElMessage.warning('请把三项都填写完整')
  if (newPwd.length < 6) return ElMessage.warning('新密码至少需要 6 个字符')
  if (newPwd !== confirmPwd) return ElMessage.warning('两次输入的新密码不一致')

  pwdSaving.value = true
  const res = await authStore.changePassword(oldPwd, newPwd)
  pwdSaving.value = false

  if (res.ok) {
    ElMessage.success('密码已修改')
    showPwdForm.value = false
    pwdForm.value = { oldPwd: '', newPwd: '', confirmPwd: '' }
  } else {
    ElMessage.error(res.message || '修改密码失败')
  }
}
</script>

<template>
  <section class="sc-section">
    <h3>账号</h3>

    <!-- 头像 -->
    <div class="sc-row">
      <div class="avatar-preview">
        <img v-if="avatarState.url" class="avatar-preview-img" :src="avatarState.url" alt="当前头像" />
        <svg v-else class="avatar-preview-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
          <circle cx="12" cy="7" r="4" />
        </svg>
      </div>
      <div class="sc-row-info">
        <div class="sc-row-title">{{ avatarState.url ? '当前头像' : '默认头像' }}</div>
      </div>
      <div class="avatar-actions">
        <button class="usage-radio" :disabled="avatarBusy" @click="pickAvatarFile">
          <span class="usage-dot" :class="{ on: !!avatarState.url }"></span>
          <span>{{ avatarBusy ? '处理中…' : (avatarState.url ? '更换' : '上传') }}</span>
        </button>
        <button class="usage-radio" :disabled="avatarBusy || !avatarState.url" @click="handleAvatarReset">
          <span>恢复默认</span>
        </button>
      </div>
      <input
        ref="avatarFileInput"
        class="avatar-file-input"
        type="file"
        accept="image/png,image/jpeg,image/webp"
        @change="handleAvatarFile"
      />
    </div>

    <!-- 当前用户 -->
    <div class="sc-row">
      <div class="sc-row-info">
        <div class="sc-row-title">当前用户</div>
        <div class="sc-row-desc">{{ authStore.username || '未登录' }}</div>
      </div>
    </div>

    <!-- 修改密码 -->
    <div class="sc-row">
      <div class="sc-row-info">
        <div class="sc-row-title">修改密码</div>
        <div class="sc-row-desc">当前登录状态不变，下次登录请使用新密码</div>
      </div>
      <button class="sc-btn" @click="togglePwdForm">{{ showPwdForm ? '收起' : '修改密码' }}</button>
    </div>

    <div v-if="showPwdForm" class="pwd-form">
      <div class="form-row">
        <label>原密码</label>
        <el-input v-model="pwdForm.oldPwd" type="password" show-password placeholder="请输入当前密码" />
      </div>
      <div class="form-row">
        <label>新密码</label>
        <el-input v-model="pwdForm.newPwd" type="password" show-password placeholder="至少 6 个字符" />
      </div>
      <div class="form-row">
        <label>确认新密码</label>
        <el-input v-model="pwdForm.confirmPwd" type="password" show-password placeholder="再次输入新密码" />
      </div>
      <div class="pwd-actions">
        <button class="sc-btn" @click="togglePwdForm">取消</button>
        <button class="sc-btn primary" :disabled="pwdSaving" @click="submitPassword">
          {{ pwdSaving ? '提交中…' : '保存新密码' }}
        </button>
      </div>
    </div>

    <!-- 切换账号 -->
    <div class="sc-row">
      <div class="sc-row-info">
        <div class="sc-row-title">切换账号</div>
        <div class="sc-row-desc">登录另一个账号</div>
      </div>
      <button class="sc-btn" @click="$emit('switch-account')">切换账号</button>
    </div>
  </section>
</template>

<style scoped>
.avatar-preview {
  flex-shrink: 0;
  width: 56px;
  height: 56px;
  border-radius: 50%;
  background: var(--color-bg-surface);
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
}

.avatar-preview-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.avatar-preview-icon {
  width: 26px;
  height: 26px;
  color: var(--color-text-tertiary);
}

.avatar-actions {
  margin-left: auto;
  flex-shrink: 0;
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

/* 原生文件选择框藏起来，由「上传 / 更换」按钮代为触发 */
.avatar-file-input {
  display: none;
}

.pwd-form {
  padding: 16px 18px;
  margin-bottom: 10px;
  background: var(--color-bg-secondary);
  border: 1px solid var(--color-border);
  border-radius: 10px;
}

.form-row {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 14px;
}
.form-row label {
  font-size: 13px;
  color: var(--color-text-secondary);
}

.pwd-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
</style>
