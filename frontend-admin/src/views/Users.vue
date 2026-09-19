<template>
  <div class="users-page">
    <el-card>
      <template #header>
        <div class="card-header">
          <span>用户管理</span>
          <el-button type="primary" @click="openCreate">
            <el-icon><Plus /></el-icon>
            新建用户
          </el-button>
        </div>
      </template>

      <!-- 搜索栏 -->
      <div class="search-bar">
        <el-input
          v-model="search"
          placeholder="搜索用户名"
          clearable
          style="width: 240px"
          @keyup.enter="reload"
        >
          <template #prefix>
            <el-icon><Search /></el-icon>
          </template>
        </el-input>

        <el-select
          v-model="statusFilter"
          placeholder="状态筛选"
          clearable
          style="width: 140px"
          @change="reload"
        >
          <el-option label="正常" value="active" />
          <el-option label="禁用" value="disabled" />
        </el-select>

        <el-button type="primary" @click="reload">
          <el-icon><Search /></el-icon>
          搜索
        </el-button>
      </div>

      <!-- 用户列表 -->
      <el-table :data="users" v-loading="loading" stripe>
        <el-table-column prop="id" label="ID" width="80" />
        <el-table-column prop="username" label="用户名" />
        <el-table-column prop="role" label="角色" width="100">
          <template #default="{ row }">
            <el-tag :type="row.role === 'admin' ? 'danger' : 'success'" size="small">
              {{ row.role }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="row.status === 'active' ? 'success' : 'danger'" size="small">
              {{ row.status === 'active' ? '正常' : '禁用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="注册时间" width="180" />
        <el-table-column prop="last_login_at" label="最后登录" width="180">
          <template #default="{ row }">
            {{ row.last_login_at || '-' }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="320" fixed="right">
          <template #default="{ row }">
            <el-button size="small" @click="openDetail(row)">详情</el-button>
            <el-button
              v-if="row.status === 'active'"
              size="small"
              type="danger"
              @click="handleDisable(row)"
            >
              禁用
            </el-button>
            <el-button
              v-else
              size="small"
              type="success"
              @click="handleEnable(row)"
            >
              启用
            </el-button>
            <el-button size="small" type="danger" plain @click="handleDelete(row)">
              删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <!-- 分页 -->
      <div class="pagination">
        <el-pagination
          v-model:current-page="page"
          v-model:page-size="pageSize"
          :total="total"
          :page-sizes="[10, 20, 50]"
          layout="total, sizes, prev, pager, next"
          @size-change="loadUsers"
          @current-change="loadUsers"
        />
      </div>
    </el-card>

    <!-- 用户详情对话框 -->
    <el-dialog v-model="detailVisible" title="用户详情" width="560px">
      <el-descriptions :column="1" border v-if="currentUser">
        <el-descriptions-item label="ID">{{ currentUser.id }}</el-descriptions-item>
        <el-descriptions-item label="用户名">{{ currentUser.username }}</el-descriptions-item>
        <el-descriptions-item label="角色">
          <el-select v-model="currentUser.role" @change="handleUpdateRole" style="width: 140px">
            <el-option label="普通用户" value="user" />
            <el-option label="管理员" value="admin" />
          </el-select>
        </el-descriptions-item>
        <el-descriptions-item label="状态">
          <el-tag :type="currentUser.status === 'active' ? 'success' : 'danger'">
            {{ currentUser.status === 'active' ? '正常' : '禁用' }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="注册时间">{{ currentUser.created_at }}</el-descriptions-item>
        <el-descriptions-item label="最后登录">
          {{ currentUser.last_login_at || '-' }}
        </el-descriptions-item>
      </el-descriptions>

      <el-divider>数据概览</el-divider>

      <div v-loading="statsLoading" class="stat-grid">
        <div v-for="item in statCards" :key="item.label" class="stat-cell">
          <div class="stat-value">{{ item.value }}</div>
          <div class="stat-label">{{ item.label }}</div>
        </div>
      </div>

      <template #footer>
        <el-button @click="openResetPwd">重置密码</el-button>
        <el-button type="danger" plain @click="handleDelete(currentUser)">删除用户</el-button>
        <el-button @click="detailVisible = false">关闭</el-button>
      </template>
    </el-dialog>

    <!-- 新建用户对话框 -->
    <el-dialog v-model="createVisible" title="新建用户" width="440px">
      <el-form :model="createForm" label-width="80px">
        <el-form-item label="用户名">
          <el-input v-model="createForm.username" placeholder="3-20 位" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input
            v-model="createForm.password"
            type="password"
            placeholder="6-50 位"
            show-password
          />
        </el-form-item>
        <el-form-item label="角色">
          <el-select v-model="createForm.role" style="width: 100%">
            <el-option label="普通用户" value="user" />
            <el-option label="管理员" value="admin" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="createLoading" @click="handleCreate">创建</el-button>
      </template>
    </el-dialog>

    <!-- 重置密码对话框 -->
    <el-dialog v-model="resetPwdVisible" title="重置密码" width="400px">
      <el-form label-width="80px">
        <el-form-item label="新密码">
          <el-input
            v-model="newPassword"
            type="password"
            placeholder="6-50 位"
            show-password
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="resetPwdVisible = false">取消</el-button>
        <el-button type="primary" :loading="resetLoading" @click="handleResetPassword">
          确认重置
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Search } from '@element-plus/icons-vue'
import api, { errorMessage } from '../api'

const users = ref([])
const loading = ref(false)
const search = ref('')
const statusFilter = ref('')
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)

const detailVisible = ref(false)
const currentUser = ref(null)
const resetPwdVisible = ref(false)
const newPassword = ref('')
const resetLoading = ref(false)

const stats = ref(null)
const statsLoading = ref(false)

const createVisible = ref(false)
const createLoading = ref(false)
const createForm = ref({ username: '', password: '', role: 'user' })

const statCards = computed(() => {
  const d = stats.value || {}
  return [
    { label: '图谱节点', value: d.nodes ?? '-' },
    { label: '已掌握', value: d.mastered_nodes ?? '-' },
    { label: '节点关系', value: d.edges ?? '-' },
    { label: '对话数', value: d.conversations ?? '-' },
    { label: '知识库文档', value: d.kb_documents ?? '-' },
    { label: '知识库分块', value: d.kb_chunks ?? '-' },
    { label: 'Agent 运行', value: d.agent_runs ?? '-' },
  ]
})

const loadUsers = async () => {
  loading.value = true
  try {
    const { data } = await api.get('/users', {
      params: {
        page: page.value,
        page_size: pageSize.value,
        search: search.value || undefined,
        status_filter: statusFilter.value || undefined,
      },
    })
    users.value = data.users
    total.value = data.total
  } catch (err) {
    ElMessage.error(errorMessage(err, '加载失败'))
  } finally {
    loading.value = false
  }
}

const reload = () => {
  page.value = 1
  loadUsers()
}

const openDetail = async (user) => {
  currentUser.value = { ...user }
  detailVisible.value = true
  stats.value = null
  statsLoading.value = true
  try {
    const { data } = await api.get(`/users/${user.id}/stats`)
    stats.value = data.data
  } catch (err) {
    ElMessage.error(errorMessage(err, '数据概览加载失败'))
  } finally {
    statsLoading.value = false
  }
}

const openCreate = () => {
  createForm.value = { username: '', password: '', role: 'user' }
  createVisible.value = true
}

const handleCreate = async () => {
  const username = createForm.value.username.trim()
  if (username.length < 3 || username.length > 20) {
    ElMessage.warning('用户名长度 3-20 位')
    return
  }
  if (createForm.value.password.length < 6) {
    ElMessage.warning('密码长度至少 6 位')
    return
  }

  createLoading.value = true
  try {
    await api.post('/users', { ...createForm.value, username })
    ElMessage.success('用户已创建')
    createVisible.value = false
    reload()
  } catch (err) {
    ElMessage.error(errorMessage(err, '创建失败'))
  } finally {
    createLoading.value = false
  }
}

const handleDelete = async (user) => {
  try {
    const { value } = await ElMessageBox.prompt(
      `此操作会永久删除用户 "${user.username}" 及其图谱、对话、知识库数据，不可恢复。\n请输入用户名以确认：`,
      '危险操作',
      { type: 'error', confirmButtonText: '确认删除', inputPlaceholder: user.username }
    )
    const r = await api.delete(`/users/${user.id}`, { params: { confirm: value } })
    ElMessage.success(r.data.message)
    detailVisible.value = false
    loadUsers()
  } catch (err) {
    if (err === 'cancel' || err === 'close') return
    ElMessage.error(errorMessage(err, '删除失败'))
  }
}

const handleDisable = async (user) => {
  try {
    await ElMessageBox.confirm(`确定要禁用用户 "${user.username}" 吗？`, '提示', {
      type: 'warning',
    })
  } catch {
    return
  }

  try {
    await api.post(`/users/${user.id}/disable`)
    ElMessage.success('已禁用')
    loadUsers()
  } catch (err) {
    ElMessage.error(errorMessage(err))
  }
}

const handleEnable = async (user) => {
  try {
    await api.post(`/users/${user.id}/enable`)
    ElMessage.success('已启用')
    loadUsers()
  } catch (err) {
    ElMessage.error(errorMessage(err))
  }
}

const openResetPwd = () => {
  newPassword.value = ''
  resetPwdVisible.value = true
}

const handleResetPassword = async () => {
  if (newPassword.value.length < 6) {
    ElMessage.warning('密码长度至少 6 位')
    return
  }

  resetLoading.value = true
  try {
    await api.post(`/users/${currentUser.value.id}/reset-password`, {
      new_password: newPassword.value,
    })
    ElMessage.success('密码已重置')
    resetPwdVisible.value = false
  } catch (err) {
    ElMessage.error(errorMessage(err, '重置失败'))
  } finally {
    resetLoading.value = false
  }
}

const handleUpdateRole = async () => {
  try {
    await api.post(`/users/${currentUser.value.id}/role`, {
      role: currentUser.value.role,
    })
    ElMessage.success('角色已更新')
    loadUsers()
  } catch (err) {
    ElMessage.error(errorMessage(err, '更新失败'))
    loadUsers() // 失败时刷新，把下拉回滚成服务端的真实值
  }
}

onMounted(loadUsers)
</script>

<style scoped>
.users-page {
  height: 100%;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 16px;
  font-weight: 600;
}

.stat-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
}

.stat-cell {
  padding: 12px;
  border-radius: 6px;
  background: #f5f7fa;
  text-align: center;
}

.stat-value {
  font-size: 20px;
  font-weight: 600;
  color: #303133;
}

.stat-label {
  margin-top: 4px;
  font-size: 12px;
  color: #909399;
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
