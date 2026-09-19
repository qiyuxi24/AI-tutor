<template>
  <el-card>
    <template #header>
      <div class="card-header">
        <span>审计日志</span>
      </div>
    </template>

    <div class="search-bar">
      <el-select
        v-model="actionFilter"
        placeholder="操作类型"
        clearable
        style="width: 160px"
        @change="reload"
      >
        <el-option
          v-for="(label, value) in ACTION_LABELS"
          :key="value"
          :label="label"
          :value="value"
        />
      </el-select>

      <el-button @click="reload">
        <el-icon><Refresh /></el-icon>
        刷新
      </el-button>
    </div>

    <el-table :data="logs" v-loading="loading" stripe>
      <el-table-column prop="created_at" label="时间" width="220" />
      <el-table-column prop="admin_username" label="操作人" width="120" />
      <el-table-column label="操作" width="120">
        <template #default="{ row }">
          <el-tag :type="ACTION_TAGS[row.action] || 'info'" size="small">
            {{ ACTION_LABELS[row.action] || row.action }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="target_username" label="目标账户" width="140">
        <template #default="{ row }">
          {{ row.target_username || '-' }}
        </template>
      </el-table-column>
      <el-table-column prop="ip_address" label="来源 IP" width="160" />
      <el-table-column prop="details" label="详情">
        <template #default="{ row }">
          {{ row.details || '-' }}
        </template>
      </el-table-column>
    </el-table>

    <div class="pagination">
      <el-pagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :total="total"
        :page-sizes="[20, 50, 100]"
        layout="total, sizes, prev, pager, next"
        @size-change="loadLogs"
        @current-change="loadLogs"
      />
    </div>
  </el-card>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import api, { errorMessage } from '../api'

const ACTION_LABELS = {
  login: '登录',
  reset_password: '重置密码',
  disable_user: '禁用用户',
  enable_user: '启用用户',
  update_role: '修改角色',
  create_user: '新建用户',
  delete_user: '删除用户',
  create_admin: '新建管理员',
  delete_admin: '删除管理员',
  disable_admin: '禁用管理员',
  enable_admin: '启用管理员',
  change_password: '修改自己密码',
}

const ACTION_TAGS = {
  login: 'info',
  reset_password: 'warning',
  disable_user: 'danger',
  enable_user: 'success',
  update_role: 'primary',
  create_user: 'success',
  delete_user: 'danger',
  create_admin: 'success',
  delete_admin: 'danger',
  disable_admin: 'danger',
  enable_admin: 'success',
  change_password: 'warning',
}

const logs = ref([])
const loading = ref(false)
const actionFilter = ref('')
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)

const loadLogs = async () => {
  loading.value = true
  try {
    const { data } = await api.get('/audit-logs', {
      params: {
        page: page.value,
        page_size: pageSize.value,
        action: actionFilter.value || undefined,
      },
    })
    logs.value = data.logs
    total.value = data.total
  } catch (err) {
    ElMessage.error(errorMessage(err, '加载失败'))
  } finally {
    loading.value = false
  }
}

const reload = () => {
  page.value = 1
  loadLogs()
}

onMounted(loadLogs)
</script>

<style scoped>
.card-header {
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
