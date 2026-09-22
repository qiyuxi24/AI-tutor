<template>
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

    <!-- 工具栏：搜索即筛选；勾选后可批量删除，单行删除在操作列 -->
    <div class="toolbar">
      <el-input
        v-model="search"
        placeholder="搜索用户名"
        clearable
        style="width: 220px"
        @keyup.enter="reload"
        @clear="reload"
      >
        <template #prefix>
          <el-icon><Search /></el-icon>
        </template>
      </el-input>

      <el-select
        v-model="statusFilter"
        placeholder="全部状态"
        clearable
        style="width: 140px"
        @change="reload"
      >
        <el-option label="正常" value="active" />
        <el-option label="禁用" value="disabled" />
      </el-select>

      <div class="toolbar-right">
        <span v-if="selected.length" class="selected-hint">已选 {{ selected.length }} 人</span>
        <el-button
          type="danger"
          plain
          :disabled="!selected.length"
          @click="handleBatchDelete"
        >
          <el-icon><Delete /></el-icon>
          批量删除
        </el-button>
      </div>
    </div>

    <el-table
      :data="users"
      v-loading="loading"
      stripe
      @selection-change="selected = $event"
    >
      <el-table-column type="selection" width="46" />
      <el-table-column prop="id" label="ID" width="70" />
      <el-table-column prop="username" label="用户名" min-width="140" />
      <el-table-column prop="role" label="角色" width="90">
        <template #default="{ row }">
          <el-tag :type="row.role === 'admin' ? 'danger' : 'success'" size="small">
            {{ row.role }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="status" label="状态" width="90">
        <template #default="{ row }">
          <el-tag :type="row.status === 'active' ? 'success' : 'danger'" size="small">
            {{ row.status === 'active' ? '正常' : '禁用' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="created_at" label="注册时间" width="170" />
      <el-table-column label="最后登录" width="170">
        <template #default="{ row }">{{ row.last_login_at || '-' }}</template>
      </el-table-column>
      <el-table-column label="操作" width="200" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="openDetail(row)">详情</el-button>
          <el-button
            v-if="row.status === 'active'"
            link
            type="warning"
            @click="handleToggleStatus(row, false)"
          >
            禁用
          </el-button>
          <el-button v-else link type="success" @click="handleToggleStatus(row, true)">
            启用
          </el-button>
          <el-button link type="danger" @click="handleDelete(row)">删除</el-button>
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
        @size-change="loadUsers"
        @current-change="loadUsers"
      />
    </div>
  </el-card>

  <!-- 用户详情 -->
  <el-dialog v-model="detailVisible" title="用户详情" width="520px">
    <el-descriptions v-if="currentUser" :column="2" border size="small">
      <el-descriptions-item label="ID">{{ currentUser.id }}</el-descriptions-item>
      <el-descriptions-item label="用户名">{{ currentUser.username }}</el-descriptions-item>
      <el-descriptions-item label="角色">
        <el-select v-model="currentUser.role" size="small" @change="handleUpdateRole">
          <el-option label="普通用户" value="user" />
          <el-option label="管理员" value="admin" />
        </el-select>
      </el-descriptions-item>
      <el-descriptions-item label="状态">
        <el-tag :type="currentUser.status === 'active' ? 'success' : 'danger'" size="small">
          {{ currentUser.status === 'active' ? '正常' : '禁用' }}
        </el-tag>
      </el-descriptions-item>
      <el-descriptions-item label="注册时间">{{ currentUser.created_at }}</el-descriptions-item>
      <el-descriptions-item label="最后登录">
        {{ currentUser.last_login_at || '-' }}
      </el-descriptions-item>
    </el-descriptions>

    <p class="stat-line" v-loading="statsLoading">
      <template v-for="(item, i) in statItems" :key="item.label">
        <span v-if="i">·</span>
        <span>{{ item.label }} <b>{{ item.value }}</b></span>
      </template>
    </p>

    <template #footer>
      <el-button @click="openResetPwd">重置密码</el-button>
      <el-button @click="detailVisible = false">关闭</el-button>
    </template>
  </el-dialog>

  <!-- 新建用户 -->
  <el-dialog v-model="createVisible" title="新建用户" width="420px">
    <el-form :model="createForm" label-width="70px">
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
      <el-button type="primary" :loading="submitLoading" @click="handleCreate">创建</el-button>
    </template>
  </el-dialog>

  <!-- 重置密码 -->
  <el-dialog v-model="resetPwdVisible" title="重置密码" width="400px">
    <el-form label-width="70px">
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
      <el-button type="primary" :loading="submitLoading" @click="handleResetPassword">
        确认重置
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete, Plus, Search } from '@element-plus/icons-vue'
import api, { errorMessage } from '../api'

const users = ref([])
const loading = ref(false)
const search = ref('')
const statusFilter = ref('')
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const selected = ref([])

const detailVisible = ref(false)
const currentUser = ref(null)
const stats = ref(null)
const statsLoading = ref(false)

const resetPwdVisible = ref(false)
const newPassword = ref('')

const createVisible = ref(false)
const createForm = ref({ username: '', password: '', role: 'user' })
const submitLoading = ref(false)

const statItems = computed(() => {
  const d = stats.value || {}
  return [
    { label: '图谱节点', value: d.nodes ?? '-' },
    { label: '已掌握', value: d.mastered_nodes ?? '-' },
    { label: '节点关系', value: d.edges ?? '-' },
    { label: '对话', value: d.conversations ?? '-' },
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

const handleToggleStatus = async (user, enable) => {
  const action = enable ? '启用' : '禁用'
  if (!enable) {
    try {
      await ElMessageBox.confirm(`确定要禁用用户 "${user.username}" 吗？`, '提示', {
        type: 'warning',
      })
    } catch {
      return
    }
  }

  try {
    await api.post(`/users/${user.id}/${enable ? 'enable' : 'disable'}`)
    ElMessage.success(`已${action}`)
    loadUsers()
  } catch (err) {
    ElMessage.error(errorMessage(err, `${action}失败`))
  }
}

const handleDelete = async (user) => {
  try {
    await ElMessageBox.confirm(
      `用户 "${user.username}" 的图谱、对话、知识库数据将一并删除，不可恢复。确定删除吗？`,
      '危险操作',
      { type: 'error', confirmButtonText: '确认删除', confirmButtonClass: 'el-button--danger' }
    )
  } catch {
    return
  }

  loading.value = true
  try {
    const { data } = await api.delete(`/users/${user.id}`)
    ElMessage.success(data.message)
    detailVisible.value = false
    loadUsers()
  } catch (err) {
    ElMessage.error(errorMessage(err, '删除失败'))
  } finally {
    loading.value = false
  }
}

const handleBatchDelete = async () => {
  const targets = selected.value.map((u) => u.username)
  try {
    await ElMessageBox.confirm(
      `将永久删除以下 ${targets.length} 个用户及其图谱、对话、知识库数据，不可恢复：\n` +
        `${targets.join('、')}`,
      '危险操作',
      { type: 'error', confirmButtonText: '确认删除', confirmButtonClass: 'el-button--danger' }
    )
  } catch {
    return
  }

  loading.value = true
  try {
    const { data } = await api.post('/users/batch-delete', {
      user_ids: selected.value.map((u) => u.id),
    })
    ElMessage.success(data.message)
    if (data.not_found?.length) {
      ElMessage.warning(`${data.not_found.length} 个用户已不存在，未处理`)
    }
    // 删掉的可能是当前页最后几行，回到第一页重查更稳
    reload()
  } catch (err) {
    ElMessage.error(errorMessage(err, '批量删除失败'))
  } finally {
    loading.value = false
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

  submitLoading.value = true
  try {
    await api.post('/users', { ...createForm.value, username })
    ElMessage.success('用户已创建')
    createVisible.value = false
    reload()
  } catch (err) {
    ElMessage.error(errorMessage(err, '创建失败'))
  } finally {
    submitLoading.value = false
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

  submitLoading.value = true
  try {
    await api.post(`/users/${currentUser.value.id}/reset-password`, {
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
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 16px;
  font-weight: 600;
}

.toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}

.toolbar-right {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-left: auto;
}

.selected-hint {
  font-size: 13px;
  color: #909399;
}

.stat-line {
  margin: 16px 0 0;
  font-size: 13px;
  color: #606266;
}

.stat-line span + span {
  margin-left: 4px;
}

.pagination {
  margin-top: 16px;
  display: flex;
  justify-content: flex-end;
}
</style>
