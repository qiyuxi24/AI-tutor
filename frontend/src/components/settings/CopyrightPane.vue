<script setup>
/**
 * CopyrightPane.vue — 设置 · 资源与版权
 * usage_mode 真值源 = 服务端用户画像 preferences（跟账号走）。
 */
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { getProfile, saveProfileData } from '../../api/index.js'

const MODE_OPTIONS = [
  { value: 'personal', title: '个人使用', desc: '采集 L0 / L1 / L2 资料，仅用于个人学习（默认）' },
  { value: 'commercial', title: '发布 / 商用', desc: '仅采 L0 开放授权资料，规避商用版权风险' },
]

const usageMode = ref('personal')
const usageSaving = ref(false)
const usageLoaded = ref(false)

onMounted(async () => {
  try {
    const { data } = await getProfile()
    const prefs = data?.data?.preferences || {}
    usageMode.value = prefs.usage_mode === 'commercial' ? 'commercial' : 'personal'
  } catch {
    // 读取失败保持默认 personal
  } finally {
    usageLoaded.value = true
  }
})

async function handleUsageModeChange(value) {
  if (!usageLoaded.value || usageSaving.value || usageMode.value === value) return
  usageSaving.value = true
  try {
    // 复用 PATCH /profile：读取当前画像 → 合并 usage_mode → 全量保存（避免覆盖其他偏好）
    const { data } = await getProfile()
    const current = data?.data || {}
    const merged = {
      ...current,
      preferences: { ...(current.preferences || {}), usage_mode: value },
    }
    await saveProfileData(merged)
    usageMode.value = value
    ElMessage.success(value === 'commercial' ? '已切换到发布 / 商用模式' : '已切换到个人使用模式')
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || e.message || '保存失败')
    usageMode.value = value === 'commercial' ? 'personal' : 'commercial'
  } finally {
    usageSaving.value = false
  }
}
</script>

<template>
  <section class="sc-section">
    <h3>资源与版权</h3>
    <p class="usage-hint">用于「资源采集」页的授权范围与检索过滤；切换会即时保存到用户画像。</p>
    <div v-for="opt in MODE_OPTIONS" :key="opt.value" class="sc-row">
      <div class="sc-row-info">
        <div class="sc-row-title">{{ opt.title }}</div>
        <div class="sc-row-desc">{{ opt.desc }}</div>
      </div>
      <button
        class="usage-radio"
        :class="{ active: usageMode === opt.value }"
        :disabled="usageSaving || !usageLoaded"
        @click="handleUsageModeChange(opt.value)"
      >
        <span class="usage-dot" :class="{ on: usageMode === opt.value }"></span>
        <span v-if="usageMode === opt.value">使用中</span>
        <span v-else>选择</span>
      </button>
    </div>
  </section>
</template>
