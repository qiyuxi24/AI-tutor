<template>
  <div class="admins-page">
    <el-card>
      <template #header>
        <div class="card-header">
          <span>管理员账户</span>
          <el-button type="primary" @click="openCreate">
            <el-icon><Plus /></el-icon>
            新建管理员
          </el-button>
        </div>
      </template>

      <el-alert
        v-if="!isSuperAdmin"
        type="warning"
        :closable="false"
        show-icon
        title="仅超级管理员可以管理管理员账户"
        style="margin-bottom: 16px"
      />

      <div class="search-bar">
        <el-input
          v-model="search"
          placeholder="搜索管理员用户名"
          clearable
          style="width: 240px"
          @keyup.enter="reload"
        >
          <template #prefix>
            <el-icon><Search /></el-icon>
          </template>
        </el-input>
        <el-button @click="reload">
          <el-icon><Refresh /></el-icon>
          刷新
        </el-button>
      </div>

      <el-table :data="admins" v-loading="loading" stripe>
        <el-table-column prop="username" label="用户名">
          <template #default="{ row }">
            {{ row.username }}
            <el-tag v-if="row.id === myId" size="small" type="info">当前登录</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="role" label="角色" width="160">
          <template #default="{ row }">
            <el-select
              v-model="row.role"
              size="small"
              :disabled="!isSuperAdmin || row.id === myId"
              @change="handleUpdateRole(row)"
            >
              <el-option label="普通管理员" value="admin" />
              <el-option label="超级管理员" value="super_admin" />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column prop="is_active" label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="row.is_active ? 'success' : 'danger'" size="small">
              {{ row.is_active ? '启用' : '禁用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="创建时间" width="180" />
        <el-table-column prop="last_login_at" label="最后登录" width="200">
          <template #default="{ row }">
            {{ row.last_login_at || '-' }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="260" fixed="right">
          <template #default="{ row }">
            <el-button size="small" @click="openResetPwd(row)">重置密码</el-button>
            <el-button
              v-if="row.is_active"
              size="small"
              type="danger"
              :disabled="row.id === myId"
              @click="handleToggleActive(row, false)"
            >
              禁用
            </el-button>
            <el-button
              v-else
              size="small"
              type="success"
              :disabled="row.id === myId"
              @click="handleToggleActive(row, true)"
            >
              启用
            </el-button>
            <el-button
              size="small"
              type="danger"
              plain
              :disabled="row.id === myId"
              @click="handleDelete(row)"
            >
              删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="pagination">
        <el-pagination
          v-model:current-page="page"
          v-model:page-size="pageSize"
          :total="total"
          :page-sizes="[10, 20, 50]"
          layout="total, sizes, prev, pager, next"
          @size-change="loadAdmins"
          @current-change="loadAdmins"
        />
      </div>
    </el-card>

    <!-- 新建管理员 -->
    <el-dialog v-model="createVisible" title="新建管理员" width="440px">
      <el-form :model="form" label-width="80px">
        <el-form-item label="用户名">
          <el-input v-model="form.username" placeholder="3-20 位" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input v-model="form.password" type="password" placeholder="6-50 位" show-password />
        </el-form-item>
        <el-form-item label="角色">
          <el-select v-model="form.role" style="width: 100%">
            <el-option label="普通管理员" value="admin" />
            <el-option label="超级管理员" value="super_admin" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitLoading" @click="handleCreate">创建</el-button>
      </template>
    </el-dialog>

    <!-- 重置密码 -->
    <el-dialog v-model="resetPwdVisible" title="重置管理员密码" width="440px">
      <el-form label-width="80px">
        <el-form-item label="管理员">
          <el-input :model-value="current?.username" disabled />
        </el-form-item>
        <el-form-item label="新密码">
          <el-input v-model="newPassword" type="password" placeholder="6-50 位" show-password />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="resetPwdVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitLoading" @click="handleResetPassword">
          确认重置
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh, Search } from '@element-plus/icons-vue'
import api, { errorMessage } from '../api'

const myInfo = JSON.parse(localStorage.getItem('admin_info') || 'null')
const myId = myInfo?.id
const isSuperAdmin = computed(() => myInfo?.role === 'super_admin')

const admins = ref([])
const loading = ref(false)
const search = ref('')
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)

const createVisible = ref(false)
const resetPwdVisible = ref(false)
const submitLoading = ref(false)
const current = ref(null)
const form = ref({ username: '', password: '', role: 'admin' })
const newPassword = ref('')

const loadAdmins = async () => {
  loading.value = true
  try {
    const { data } = await api.get('/admins', {
      params: {
        page: page.value,
        page_size: pageSize.value,
        search: search.value || undefined,
      },
    })
    admins.value = data.admins
    total.value = data.total
  } catch (err) {
    ElMessage.error(errorMessage(err, '加载失败'))
  } finally {
    loading.value = false
  }
}

const reload = () => {
  page.value = 1
  loadAdmins()
}

const openCreate = () => {
  form.value = { username: '', password: '', role: 'admin' }
  createVisible.value = true
}

const handleCreate = async () => {
  const { username, password } = form.value
  if (username.trim().length < 3 || username.trim().length > 20) {
    ElMessage.warning('用户名长度 3-20 位')
    return
  }
  if (password.length < 6) {
    ElMessage.warning('密码长度至少 6 位')
    return
  }

  submitLoading.value = true
  try {
    await api.post('/admins', { ...form.value, username: username.trim() })
    ElMessage.success('管理员已创建')
    createVisible.value = false
    reload()
  } catch (err) {
    ElMessage.error(errorMessage(err, '创建失败'))
  } finally {
    submitLoading.value = false
  }
}

const handleUpdateRole = async (row) => {
  try {
    await api.post(`/admins/${row.id}/role`, { role: row.role })
    ElMessage.success('角色已更新')
  } catch (err) {
    ElMessage.error(errorMessage(err, '更新失败'))
  } finally {
    loadAdmins() // 失败时也要刷新，把下拉回滚成服务端真实值
  }
}

const handleToggleActive = async (row, enable) => {
  const action = enable ? '启用' : '禁用'
  try {
    await ElMessageBox.confirm(`确定要${action}管理员 "${row.username}" 吗？`, '提示', {
      type: 'warning',
    })
  } catch {
    return
  }

  try {
    await api.post(`/admins/${row.id}/${enable ? 'enable' : 'disable'}`)
    ElMessage.success(`已${action}`)
    loadAdmins()
  } catch (err) {
    ElMessage.error(errorMessage(err, `${action}失败`))
  }
}

const handleDelete = async (row) => {
  try {
    await ElMessageBox.confirm(
      `删除后该账户无法登录，且不可恢复。确定删除管理员 "${row.username}" 吗？`,
      '危险操作',
      { type: 'error', confirmButtonText: '确认删除' }
    )
  } catch {
    return
  }

  try {
    await api.delete(`/admins/${row.id}`)
    ElMessage.success('管理员已删除')
    loadAdmins()
  } catch (err) {
    ElMessage.error(errorMessage(err, '删除失败'))
  }
}

const openResetPwd = (row) => {
  current.value = row
  newPassword.value = ''
  resetPwdVisible.value = true
}

const handleResetPassword = async () => {
  if (newPassword.value.length < 6) {
    ElMessage.warning('密码长度至少 6 位')
    return
  }

  submitLoading.value = true
  try {
    await api.post(`/admins/${current.value.id}/reset-password`, {
      new_password: newPassword.value,
    })
    ElMessage.success('密码已重置')
    resetPwdVisible.value = false
  } catch (err) {
    ElMessage.error(errorMessage(err, '重置失败'))
  } finally {
    submitLoading.value = false
  }
}

onMounted(loadAdmins)
</script>

<style scoped>
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 16px;
  font-weight: 600;
}

.search-bar {
  display: flex;
  gap: 12px;
  margin-bottom: 16px;
}

.pagination {
  margin-top: 16px;
  display: flex;
  justify-content: flex-end;
}
</style>
