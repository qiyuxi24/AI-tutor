<template>
  <el-card>
    <template #header>
      <div class="card-header">
        <span>用量与缓存命中</span>
        <span class="sub">数据来自主系统 llm_usage 表（供应商响应里的 usage 真值，非本地估算）</span>
      </div>
    </template>

    <div class="search-bar">
      <el-select v-model="sinceHours" style="width: 140px" @change="load">
        <el-option label="近 24 小时" :value="24" />
        <el-option label="近 7 天" :value="168" />
        <el-option label="近 30 天" :value="720" />
      </el-select>
      <el-select v-model="groupBy" style="width: 140px" @change="load">
        <el-option
          v-for="(label, value) in GROUP_LABELS"
          :key="value"
          :label="label"
          :value="value"
        />
      </el-select>
      <el-button @click="load">
        <el-icon><Refresh /></el-icon>
        刷新
      </el-button>
    </div>

    <el-row :gutter="12" class="stat-row">
      <el-col :span="4">
        <el-statistic title="调用次数" :value="total.calls" />
      </el-col>
      <el-col :span="5">
        <el-statistic title="输入 token" :value="total.prompt_tokens" />
      </el-col>
      <el-col :span="5">
        <el-statistic title="缓存命中 token" :value="total.cached_tokens" />
      </el-col>
      <el-col :span="4">
        <el-statistic title="输出 token" :value="total.completion_tokens" />
      </el-col>
      <el-col :span="6">
        <el-statistic title="实付金额（元）" :value="total.cost_total_yuan" :precision="4">
          <template #suffix>
            <span class="saving">已省 {{ total.cached_saving_yuan }} 元</span>
          </template>
        </el-statistic>
      </el-col>
    </el-row>

    <div class="rate-box">
      <span class="rate-label">整体缓存命中率</span>
      <el-progress
        :percentage="Math.round((total.cache_hit_rate || 0) * 100)"
        :color="rateColor(total.cache_hit_rate)"
        :stroke-width="16"
        text-inside
        style="flex: 1"
      />
      <span class="rate-hint">
        命中价约为输入价的 1/5 —— 命中率越低，同样的 token 越费钱
      </span>
    </div>

    <el-table :data="groups" v-loading="loading" stripe>
      <el-table-column :label="GROUP_LABELS[groupBy]" min-width="160">
        <template #default="{ row }">{{ row.dim === null || row.dim === '' ? '(空)' : row.dim }}</template>
      </el-table-column>
      <el-table-column prop="calls" label="调用次数" width="110" sortable />
      <el-table-column label="输入 token" width="140" sortable :sort-by="'prompt_tokens'">
        <template #default="{ row }">{{ fmtNum(row.prompt_tokens) }}</template>
      </el-table-column>
      <el-table-column label="命中 token" width="150" sortable :sort-by="'cached_tokens'">
        <template #default="{ row }">{{ fmtNum(row.cached_tokens) }}</template>
      </el-table-column>
      <el-table-column label="命中率" width="200">
        <template #default="{ row }">
          <el-progress
            :percentage="Math.round((row.cache_hit_rate || 0) * 100)"
            :color="rateColor(row.cache_hit_rate)"
            :stroke-width="12"
          />
        </template>
      </el-table-column>
      <el-table-column label="输出 token" width="140" sortable :sort-by="'completion_tokens'">
        <template #default="{ row }">{{ fmtNum(row.completion_tokens) }}</template>
      </el-table-column>
    </el-table>
  </el-card>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import api, { errorMessage } from '../api'

const GROUP_LABELS = { kind: '功能', model: '模型', day: '日期', user: '用户' }

const loading = ref(false)
const sinceHours = ref(168)
const groupBy = ref('kind')
const groups = ref([])
const total = ref({
  calls: 0,
  prompt_tokens: 0,
  cached_tokens: 0,
  completion_tokens: 0,
  cache_hit_rate: 0,
  cost_total_yuan: 0,
  cached_saving_yuan: 0,
})

const fmtNum = (n) => (n || 0).toLocaleString()

// 命中率分档配色：<30% 红（前缀被破坏）/ <60% 橙（有优化空间）/ 其余绿
const rateColor = (rate) => {
  const p = (rate || 0) * 100
  if (p < 30) return '#f56c6c'
  if (p < 60) return '#e6a23c'
  return '#67c23a'
}

const load = async () => {
  loading.value = true
  try {
    const { data } = await api.get('/usage', {
      params: { since_hours: sinceHours.value, group_by: groupBy.value },
    })
    groups.value = data.groups
    total.value = data.total
  } catch (err) {
    ElMessage.error(errorMessage(err, '加载失败'))
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.card-header {
  display: flex;
  align-items: baseline;
  gap: 12px;
}

.card-header span:first-child {
  font-size: 16px;
  font-weight: 600;
}

.sub {
  color: #909399;
  font-size: 12px;
}

.search-bar {
  display: flex;
  gap: 12px;
  margin-bottom: 16px;
}

.stat-row {
  margin-bottom: 16px;
}

.saving {
  margin-left: 8px;
  color: #67c23a;
  font-size: 12px;
}

.rate-box {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}

.rate-label {
  font-weight: 600;
  white-space: nowrap;
}

.rate-hint {
  color: #909399;
  font-size: 12px;
  white-space: nowrap;
}
</style>
