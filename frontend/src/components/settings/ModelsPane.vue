<script setup>
/**
 * ModelsPane.vue — 设置 · 模型管理
 *
 * 每个用户可以接入自己的对话模型（任何 OpenAI 兼容服务：base_url + Key + 模型名），
 * 互不影响；没配置的用户走 .env 的「系统默认档」。
 *
 * 状态机（两级，界面各显示一处）：
 *   · 配置态 status —— unverified / available / unavailable / disabled，由「测试」驱动
 *   · 运行时态 effective_id —— 最近一次真实调用成功的模型（"当前生效档位"）
 *   候选链 = 当前使用模型 → 其余可用模型，主模型故障由后端静默降级到下一个。
 */
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  listLlmModels, createLlmModel, updateLlmModel,
  deleteLlmModel, testLlmModel, activateLlmModel,
} from '../../api/index.js'

const STATUS_TEXT = {
  unverified: '未验证',
  available: '可用',
  unavailable: '不可用',
  disabled: '已停用',
}

const state = ref({ models: [], active_id: null, effective_id: null, system_default: {} })
const loading = ref(false)
const busyId = ref('')          // 正在测试 / 切换的模型 id

const dialogVisible = ref(false)
const editingId = ref('')       // 空 = 新建
const saving = ref(false)
const form = ref({ name: '', base_url: '', api_key: '', model: '' })

/** 当前生效档位文案：降级到备用模型时这里会跟着变，用户能看出"用哪个在跑"。 */
const effectiveText = computed(() => {
  const m = state.value.models.find((x) => x.id === state.value.effective_id)
  if (m) return m.name
  const def = state.value.system_default?.model
  return def ? `系统默认（${def}）` : '系统默认'
})

async function load() {
  loading.value = true
  try {
    const { data } = await listLlmModels()
    state.value = data
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || e.message || '读取模型失败')
  } finally {
    loading.value = false
  }
}

onMounted(load)

function openCreate() {
  editingId.value = ''
  form.value = { name: '', base_url: '', api_key: '', model: '' }
  dialogVisible.value = true
}

function openEdit(m) {
  editingId.value = m.id
  // Key 不回显：留空 = 不修改
  form.value = { name: m.name, base_url: m.base_url, api_key: '', model: m.model }
  dialogVisible.value = true
}

async function submit() {
  const f = form.value
  if (!f.name.trim() || !f.base_url.trim() || !f.model.trim()) {
    ElMessage.warning('名称、接入地址、模型名都不能为空')
    return
  }
  const apiKey = f.api_key.trim()
  if (!editingId.value && !apiKey) {
    ElMessage.warning('新建时必须填写 API Key')
    return
  }
  saving.value = true
  try {
    const payload = {
      name: f.name.trim(), base_url: f.base_url.trim(), model: f.model.trim(),
      ...(apiKey ? { api_key: apiKey } : {}),
    }
    if (editingId.value) {
      await updateLlmModel(editingId.value, payload)
    } else {
      await createLlmModel(payload)
    }
    dialogVisible.value = false
    ElMessage.success(editingId.value ? '已保存（改了接入参数，请重新测试）' : '模型已添加')
    await load()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || e.message || '保存失败')
  } finally {
    saving.value = false
  }
}

async function runTest(m) {
  busyId.value = m.id
  try {
    const { data } = await testLlmModel(m.id)
    if (data.status === 'available') {
      ElMessage.success(`「${m.name}」连通正常`)
    } else {
      ElMessage.error(`「${m.name}」不可用：${data.last_error}`)
    }
    await load()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || e.message || '测试失败')
  } finally {
    busyId.value = ''
  }
}

async function activate(m) {
  busyId.value = m.id
  try {
    await activateLlmModel(m.id)
    ElMessage.success(`已切换到「${m.name}」`)
    await load()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || e.message || '切换失败')
  } finally {
    busyId.value = ''
  }
}

async function toggleDisable(m) {
  const toDisabled = m.status !== 'disabled'
  try {
    await updateLlmModel(m.id, { status: toDisabled ? 'disabled' : 'unverified' })
    ElMessage.success(toDisabled ? '已停用（不进候选链）' : '已启用，请重新测试连通性')
    await load()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || e.message || '操作失败')
  }
}

async function remove(m) {
  try {
    await ElMessageBox.confirm(`确定删除模型「${m.name}」？`, '删除确认', { type: 'warning' })
  } catch {
    return  // 用户取消
  }
  try {
    await deleteLlmModel(m.id)
    ElMessage.success('已删除')
    await load()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || e.message || '删除失败')
  }
}
</script>

<template>
  <section class="sc-section models-pane">
    <h3>模型</h3>
    <p class="usage-hint">
      接入你自己的对话模型（任何 OpenAI 兼容服务）。列表里「当前使用」的模型优先，
      它失败时会自动静默降级到下一个可用模型；不配置则使用系统默认模型。
      API Key 只保存在服务端，界面只显示掩码。
    </p>

    <div class="effective-row">
      <span class="effective-label">当前生效档位</span>
      <span class="effective-value">{{ effectiveText }}</span>
    </div>

    <div
      v-for="m in state.models"
      :key="m.id"
      class="model-card"
      :class="{ 'is-disabled': m.status === 'disabled' }"
    >
      <div class="model-main">
        <div class="model-title">
          <span class="model-name">{{ m.name }}</span>
          <span v-if="m.id === state.active_id" class="badge badge-active">当前使用</span>
          <span class="badge" :class="`badge-${m.status}`">{{ STATUS_TEXT[m.status] }}</span>
          <span v-if="m.id === state.effective_id" class="badge badge-effective">正在生效</span>
        </div>
        <div class="model-meta">
          <span class="mono">{{ m.base_url }}</span>
          <span class="sep">·</span>
          <span class="mono">{{ m.model }}</span>
          <span class="sep">·</span>
          <span class="mono">Key {{ m.api_key_masked }}</span>
        </div>
        <div v-if="m.last_error" class="model-error">{{ m.last_error }}</div>
      </div>

      <div class="model-actions">
        <button
          v-if="m.id !== state.active_id && m.status !== 'disabled'"
          class="sc-btn"
          :disabled="busyId === m.id"
          @click="activate(m)"
        >设为当前</button>
        <button class="sc-btn" :disabled="busyId === m.id" @click="runTest(m)">
          {{ busyId === m.id ? '测试中…' : '测试' }}
        </button>
        <button class="sc-btn" @click="openEdit(m)">编辑</button>
        <button class="sc-btn" @click="toggleDisable(m)">
          {{ m.status === 'disabled' ? '启用' : '停用' }}
        </button>
        <button class="sc-btn danger" @click="remove(m)">删除</button>
      </div>
    </div>

    <div v-if="!state.models.length && !loading" class="models-empty">
      还没有自定义模型，当前使用系统默认（{{ state.system_default?.model || '—' }}）。
    </div>

    <button class="sc-btn add-btn" @click="openCreate">+ 新建模型</button>

    <el-dialog
      v-model="dialogVisible"
      :title="editingId ? '编辑模型' : '新建模型'"
      width="480px"
      append-to-body
    >
      <div class="form-row">
        <label>名称</label>
        <el-input v-model="form.name" placeholder="例如：校内网关" maxlength="64" />
      </div>
      <div class="form-row">
        <label>接入地址</label>
        <el-input v-model="form.base_url" placeholder="https://api.example.com/v1" />
      </div>
      <div class="form-row">
        <label>API Key</label>
        <el-input
          v-model="form.api_key"
          type="password"
          show-password
          :placeholder="editingId ? '留空表示不修改' : 'sk-...'"
        />
      </div>
      <div class="form-row">
        <label>模型名</label>
        <el-input v-model="form.model" placeholder="例如：MiniMax-M3" />
      </div>
      <template #footer>
        <button class="sc-btn" @click="dialogVisible = false">取消</button>
        <button class="sc-btn primary" :disabled="saving" @click="submit">
          {{ saving ? '保存中…' : '保存' }}
        </button>
      </template>
    </el-dialog>
  </section>
</template>

<style scoped>
.effective-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  margin-bottom: 12px;
  background: var(--color-accent-light);
  border: 1px solid var(--color-accent);
  border-radius: 8px;
  font-size: 13px;
}
.effective-label {
  color: var(--color-text-secondary);
}
.effective-value {
  color: var(--color-accent);
  font-weight: 600;
}

.model-card {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
  padding: 14px 16px;
  background: var(--color-bg-secondary);
  border: 1px solid var(--color-border);
  border-radius: 10px;
  margin-bottom: 10px;
}
.model-card.is-disabled {
  opacity: 0.55;
}

.model-main {
  min-width: 0;
  flex: 1;
}

.model-title {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.model-name {
  font-size: 14px;
  font-weight: 500;
  color: var(--color-text-primary);
}

.badge {
  font-size: 11px;
  padding: 1px 7px;
  border-radius: 999px;
  border: 1px solid var(--color-border);
  color: var(--color-text-tertiary);
}
.badge-active {
  background: var(--color-accent-light);
  border-color: var(--color-accent);
  color: var(--color-accent);
}
.badge-effective {
  border-color: var(--color-success, #4caf50);
  color: var(--color-success, #4caf50);
}
.badge-available {
  border-color: var(--color-success, #4caf50);
  color: var(--color-success, #4caf50);
}
.badge-unavailable {
  border-color: var(--color-danger, #e5534b);
  color: var(--color-danger, #e5534b);
}

.model-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  margin-top: 6px;
  font-size: 12px;
  color: var(--color-text-tertiary);
}
.mono {
  font-family: 'Consolas', 'Courier New', monospace;
}
.sep {
  color: var(--color-border-light);
}

.model-error {
  margin-top: 6px;
  font-size: 11px;
  color: var(--color-danger, #e5534b);
  word-break: break-all;
}

.model-actions {
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.models-empty {
  padding: 18px;
  margin-bottom: 10px;
  text-align: center;
  font-size: 13px;
  color: var(--color-text-tertiary);
  border: 1px dashed var(--color-border);
  border-radius: 10px;
}

.add-btn {
  margin-top: 4px;
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
</style>
