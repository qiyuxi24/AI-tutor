<template>
  <el-container class="layout-container">
    <el-aside width="220px">
      <div class="logo">
        <el-icon><Monitor /></el-icon>
        运维后台
      </div>
      
      <el-menu
        :default-active="$route.path"
        router
        background-color="#304156"
        text-color="#bfcbd9"
        active-text-color="#409eff"
      >
        <el-menu-item index="/users">
          <el-icon><User /></el-icon>
          <span>用户管理</span>
        </el-menu-item>
        <el-menu-item index="/admins">
          <el-icon><Setting /></el-icon>
          <span>管理员账户</span>
        </el-menu-item>
        <el-menu-item index="/audit-logs">
          <el-icon><Document /></el-icon>
          <span>审计日志</span>
        </el-menu-item>
      </el-menu>
      
      <div class="sidebar-footer">
        <div class="admin-info">
          <el-icon><UserFilled /></el-icon>
          <span class="admin-name">{{ adminInfo?.username || 'Admin' }}</span>
          <el-tag size="small" :type="adminInfo?.role === 'super_admin' ? 'danger' : 'info'">
            {{ adminInfo?.role === 'super_admin' ? '超管' : '管理员' }}
          </el-tag>
        </div>
        <div class="footer-actions">
          <el-button size="small" @click="pwdVisible = true">改密码</el-button>
          <el-button type="danger" size="small" @click="handleLogout">退出</el-button>
        </div>
      </div>
    </el-aside>

    <!-- 修改自己的密码 -->
    <el-dialog v-model="pwdVisible" title="修改密码" width="420px">
      <el-form label-width="80px">
        <el-form-item label="原密码">
          <el-input v-model="pwdForm.old_password" type="password" show-password />
        </el-form-item>
        <el-form-item label="新密码">
          <el-input
            v-model="pwdForm.new_password"
            type="password"
            placeholder="6-50 位"
            show-password
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="pwdVisible = false">取消</el-button>
        <el-button type="primary" :loading="pwdLoading" @click="handleChangePassword">
          确认修改
        </el-button>
      </template>
    </el-dialog>
    
    <el-container>
      <el-main>
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Monitor, User, UserFilled, Document, Setting } from '@element-plus/icons-vue'
import api, { errorMessage } from '../api'

const router = useRouter()
const adminInfo = ref(null)

const pwdVisible = ref(false)
const pwdLoading = ref(false)
const pwdForm = ref({ old_password: '', new_password: '' })

onMounted(() => {
  const info = localStorage.getItem('admin_info')
  if (info) {
    adminInfo.value = JSON.parse(info)
  }
})

const handleChangePassword = async () => {
  if (pwdForm.value.new_password.length < 6) {
    ElMessage.warning('新密码长度至少 6 位')
    return
  }

  pwdLoading.value = true
  try {
    await api.post('/me/password', pwdForm.value)
    pwdVisible.value = false
    pwdForm.value = { old_password: '', new_password: '' }
    // 旧 token 仍有效，但按提示重新登录更稳妥
    await ElMessageBox.alert('密码已修改，请重新登录', '完成', { type: 'success' })
    handleLogout(true)
  } catch (err) {
    ElMessage.error(errorMessage(err, '修改失败'))
  } finally {
    pwdLoading.value = false
  }
}

const handleLogout = async (skipConfirm = false) => {
  if (!skipConfirm) {
    await ElMessageBox.confirm('确定要退出登录吗？', '提示', {
      type: 'warning'
    })
  }

  localStorage.removeItem('admin_token')
  localStorage.removeItem('admin_info')
  router.push('/login')
}
</script>

<style scoped>
.layout-container {
  min-height: 100vh;
}

.el-aside {
  background-color: #304156;
  display: flex;
  flex-direction: column;
}

.logo {
  height: 60px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: #fff;
  font-size: 18px;
  font-weight: 600;
  border-bottom: 1px solid #263445;
}

.el-menu {
  border-right: none;
  flex: 1;
}

.sidebar-footer {
  padding: 16px;
  border-top: 1px solid #263445;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.admin-info {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #bfcbd9;
  font-size: 14px;
}

.admin-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.footer-actions {
  display: flex;
  gap: 8px;
}

.footer-actions .el-button {
  flex: 1;
}

.el-main {
  background: #f5f7fa;
  padding: 20px;
}
</style>
