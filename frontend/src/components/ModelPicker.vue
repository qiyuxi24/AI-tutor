<script setup>
/**
 * ModelPicker.vue — 对话框内的模型切换器（2026-11-16）
 *
 * 为什么放在对话框而不是只放设置页：换模型是**对话中途**的动作（"这题它答得不好，
 * 换一个"），跑去设置页 = 离开对话上下文。设置页仍然是"增删改 / 填 Key"的地方，
 * 两处看到的是同一份后端状态（`/llm/models`）。
 *
 * 后端是唯一真值：这里只调 `activate` / `deactivate`，不缓存"我选了谁"——
 * 用户没配任何模型时只有"系统默认"一项，选中它 = `deactivate`（回落 .env 档）。
 *
 * 交互与 ContextPicker 一致：点外面即收（全局 v-click-outside），行内显示
 * 状态点（可用 / 未验证 / 不可用 / 已停用）—— 选中一个测不通的模型不会报错，
 * 而是静默降级到下一个可用档（后端候选链的语义，界面上要能提前看出来）。
 */
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import {
  listLlmModels, activateLlmModel, deactivateLlmModel,
} from '../api/index.js'
import AppIcon from './AppIcon.vue'

/** `close` 由父层收起面板；`changed` 让父层重读一次标签（切换已生效） */
const emit = defineEmits(['close', 'changed'])

const loading = ref(false)
const busy = ref('')                 // 正在切换的 id（'' = 系统默认）
const state = ref({
  models: [], active_id: null, effective_id: null, system_default: {},
})

/** 只列能用的：disabled 不进候选链，选中它等于什么都没发生 */
const usable = computed(() => state.value.models.filter((m) => m.status !== 'disabled'))

/** 当前选中项：有 active_id 是它，否则系统默认 */
const currentId = computed(() => state.value.active_id || '')

const effectiveText = computed(() => {
  const m = state.value.models.find((x) => x.id === state.value.effective_id)
  return m ? m.name : (state.value.system_default?.model || '系统默认')
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

async function choose(id) {
  if (busy.value || id === currentId.value) {
    if (id === currentId.value) emit('close')
    return
  }
  busy.value = id
  try {
    if (id) await activateLlmModel(id)
    else await deactivateLlmModel()
    await load()
    emit('changed')
    emit('close')
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || e.message || '切换失败')
  } finally {
    busy.value = ''
  }
}

/** 状态点颜色：可用的绿、未验证的灰、不可用的红（选中也能用，只是多半会降级） */
function dotClass(m) {
  if (m.status === 'available') return 'ok'
  if (m.status === 'unavailable') return 'bad'
  return 'idle'
}
</script>

<template>
  <div class="mp" role="dialog" aria-label="切换模型">
    <div class="mp-head">
      <span class="mp-title">选择模型</span>
      <span class="mp-effective" :title="`当前实际调用的仍是：${effectiveText}`">
        生效：{{ effectiveText }}
      </span>
    </div>

    <div class="mp-body">
      <!-- 系统默认档：没配模型 / 主动回落时选它 -->
      <button
        class="mp-item"
        :class="{ 'is-current': !currentId }"
        :disabled="!!busy"
        @click="choose('')"
      >
        <span class="mp-dot ok" />
        <span class="mp-text">
          <span class="mp-name">系统默认</span>
          <span class="mp-sub">{{ state.system_default?.model || '—' }}</span>
        </span>
        <span v-if="!currentId" class="mp-check"><AppIcon name="check" :size="13" /></span>
      </button>

      <button
        v-for="m in usable" :key="m.id"
        class="mp-item"
        :class="{ 'is-current': m.id === currentId }"
        :disabled="!!busy"
        @click="choose(m.id)"
      >
        <span class="mp-dot" :class="dotClass(m)" />
        <span class="mp-text">
          <span class="mp-name">{{ m.name }}</span>
          <span class="mp-sub">
            {{ m.model }}
            <template v-if="m.status === 'unverified'"> · 未验证</template>
            <template v-else-if="m.status === 'unavailable'"> · 测试失败</template>
          </span>
        </span>
        <span v-if="busy === m.id" class="mp-spinner" />
        <span v-else-if="m.id === currentId" class="mp-check">
          <AppIcon name="check" :size="13" />
        </span>
      </button>

      <div v-if="!usable.length && !loading" class="mp-empty">
        还没有自定义模型。到「设置 · 模型」里添加后就能在这里切换。
      </div>
      <div v-else-if="!state.models.length && loading" class="mp-empty">读取中…</div>
    </div>

    <div class="mp-foot">
      切换只影响之后的对话；主模型失败时后端会自动降级到下一个可用档。
    </div>
  </div>
</template>

<style scoped>
/* 锚点仍是整块输入容器（与 ContextPicker 同一口径）：浮在输入区上方。
   但这颗按钮在右侧 → 面板右对齐，否则浮层会飘到输入区最左边、和按钮脱节。 */
.mp {
  position: absolute;
  bottom: calc(100% + 10px);
  right: 0;
  width: 320px;
  max-width: min(94vw, 320px);
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-popup);
  z-index: 60;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.mp-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 10px 12px;
  border-bottom: 1px solid var(--color-border-soft);
}
.mp-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--color-text-secondary);
}
.mp-effective {
  font-size: 11px;
  color: var(--color-text-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 60%;
}

.mp-body {
  display: flex;
  flex-direction: column;
  padding: 4px;
  max-height: 300px;
  overflow-y: auto;
}

.mp-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 8px 8px;
  border: none;
  background: transparent;
  border-radius: var(--radius-md);
  cursor: pointer;
  text-align: left;
  font-family: inherit;
  transition: background 0.12s ease;
}
.mp-item:hover:not(:disabled) { background: var(--color-bg-hover); }
.mp-item.is-current { background: var(--color-accent-light); }
.mp-item:disabled { cursor: not-allowed; opacity: 0.6; }
.mp-item:focus-visible { outline: 2px solid var(--color-accent); outline-offset: -2px; }

/* 状态点：可用绿 / 不可用红 / 未验证灰（13px 以下描边会糊，所以用实心小圆） */
.mp-dot {
  flex-shrink: 0;
  width: 6px;
  height: 6px;
  border-radius: var(--radius-full);
  margin-left: 3px;
}
.mp-dot.ok { background: var(--color-success, #4caf50); }
.mp-dot.bad { background: var(--color-danger, #e5534b); }
.mp-dot.idle { background: var(--color-border-light); }

.mp-text {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 0;
  flex: 1;
}
.mp-name {
  font-size: 13px;
  color: var(--color-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.mp-sub {
  font-size: 11px;
  color: var(--color-text-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mp-check {
  flex-shrink: 0;
  color: var(--color-accent);
  display: flex;
}

.mp-spinner {
  flex-shrink: 0;
  width: 12px;
  height: 12px;
  border: 1.5px solid var(--color-border);
  border-top-color: var(--color-accent);
  border-radius: 50%;
  animation: mp-spin 0.7s linear infinite;
}
@keyframes mp-spin { to { transform: rotate(360deg); } }

.mp-empty {
  padding: 14px 10px;
  font-size: 12px;
  color: var(--color-text-muted);
  text-align: center;
  line-height: 1.6;
}

.mp-foot {
  padding: 8px 12px;
  border-top: 1px solid var(--color-border-soft);
  font-size: 11px;
  color: var(--color-text-muted);
  line-height: 1.5;
}

@media (prefers-reduced-motion: reduce) {
  .mp-item { transition: none; }
  .mp-spinner { animation: none; }
}
</style>
