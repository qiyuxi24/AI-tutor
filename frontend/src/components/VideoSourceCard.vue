<script setup>
/**
 * VideoSourceCard.vue — 节点详情里的「视频出处」
 *
 * 回答一个具体问题：**这个知识点在课堂视频的哪一分钟讲过？**
 * 反向溯源的价值 —— 讲不透时能立刻回到老师原话，而不是只看 AI 的总结。
 *
 * 三态：loading（不占高，避免弹窗跳动）/ error（一行弱提示）/ 空（**整卡不渲染**）。
 * 空时用 `v-if` 整个拿掉而不是留一块"暂无视频出处"的灰框：节点大多是文字资料来的，
 * 绝大多数节点没有视频出处，留一个常驻空卡会把真正有用的区块（前置知识/相关节点）往下挤。
 *
 * `t_url` 可能是空串（该资料没有视频链接，比如普通 PDF）：那种条目只显示文字，
 * 不给一个点了没反应的链接。
 *
 * Props:
 *   nodeId String — 图谱节点 id（用它反查视频出处）
 *
 * Emits:
 *   open-lecture: ({ docId, at }) — 跳到课时页；`at` 是 locator 解析出的秒数
 *                 （节点反查接口只给 locator，没有 seq，所以按"落到哪一秒"传）
 */
import { onMounted, ref, watch } from 'vue'
import { getNodeVideoSources } from '../api/kb.js'
import { tStartFromLocator } from '../utils/lecture.js'

const props = defineProps({
  nodeId: { type: String, required: true },
})

const emit = defineEmits(['open-lecture'])

const sources = ref([])
const loading = ref(false)
const error = ref('')

async function load() {
  if (!props.nodeId) return
  loading.value = true
  error.value = ''
  try {
    const { data } = await getNodeVideoSources(props.nodeId)
    sources.value = data?.sources || []
  } catch (e) {
    // 反查是加分项：失败只记一行，不打断节点详情的其它内容
    error.value = e.response?.data?.detail || e.message || '读取失败'
    sources.value = []
  } finally {
    loading.value = false
  }
}

onMounted(load)
// 同一个弹窗里换节点（双击下一个知识点）→ 跟着重查，不然显示的是上一个节点的出处
watch(() => props.nodeId, load)

/** 整行点击 = 去课时页那一秒；外链图标单独 stop 掉，不让它也跟着走 emit */
function openLecture(src) {
  emit('open-lecture', {
    docId: src.doc_id,
    at: tStartFromLocator(src.locator),
  })
}

function openExternal(url) {
  if (!url) return
  window.open(url, '_blank', 'noopener')
}
</script>

<template>
  <section v-if="loading || error || sources.length" class="vs-card">
    <h3 class="vs-head">视频出处</h3>

    <p v-if="loading" class="vs-hint">正在查找…</p>
    <p v-else-if="error" class="vs-hint">读取失败：{{ error }}</p>

    <ul v-else class="vs-list">
      <li v-for="(src, i) in sources" :key="`${src.doc_id}-${src.locator}-${i}`" class="vs-row">
        <button
          type="button"
          class="vs-main"
          :title="`打开「${src.doc_name || ''}」的 ${src.t_label || ''}`"
          @click="openLecture(src)"
        >
          <span class="vs-time">{{ src.t_label || '--:--' }}</span>
          <span class="vs-title">{{ src.title || src.doc_name || '未命名片段' }}</span>
          <span v-if="src.doc_name" class="vs-doc" :title="src.doc_name">{{ src.doc_name }}</span>
        </button>
        <!-- 有 t_url 才给外链：直接看平台原视频，省一次跳到课时页 -->
        <button
          v-if="src.t_url"
          type="button"
          class="vs-ext"
          title="在新标签页打开原视频"
          @click.stop="openExternal(src.t_url)"
        >
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor"
               stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
            <polyline points="15 3 21 3 21 9" />
            <line x1="10" y1="14" x2="21" y2="3" />
          </svg>
        </button>
      </li>
    </ul>
  </section>
</template>

<style scoped>
/* 视觉上与 NodeDetail 里既有的 .relations-section 同级（同样的内边距），
   但分隔线用最浅一档：它可能紧跟在小节溯源（自带 border-bottom）之后，
   两条 1px 贴在一起会变成一道明显更粗的线。 */
.vs-card {
  padding: 10px 24px 12px;
  border-top: 1px solid var(--color-border-soft);
  flex-shrink: 0;
}

.vs-head {
  margin: 0 0 var(--space-sm);
  font-size: var(--font-size-sm);
  font-weight: 600;
  color: var(--color-text-secondary);
  letter-spacing: 0.5px;
}

.vs-hint {
  margin: 0;
  font-size: var(--font-size-xs);
  color: var(--color-text-placeholder);
}

.vs-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.vs-row {
  display: flex;
  align-items: center;
  gap: var(--space-xs);
  min-width: 0;
}

/* 整行可点：命中区域大，不用瞄准时间戳这几个字符 */
.vs-main {
  display: flex;
  align-items: baseline;
  gap: var(--space-sm);
  flex: 1;
  min-width: 0;
  padding: var(--space-xs) var(--space-sm);
  border: none;
  border-radius: var(--radius-sm);
  background: transparent;
  color: inherit;
  font-family: inherit;
  text-align: left;
  cursor: pointer;
  transition: background 0.15s;
}
.vs-main:hover { background: var(--color-bg-hover); }
.vs-main:focus-visible {
  outline: 2px solid var(--color-border-focus);
  outline-offset: -2px;
}

.vs-time {
  flex-shrink: 0;
  font-family: var(--font-mono);
  font-size: var(--font-size-xs);
  font-variant-numeric: tabular-nums;
  color: var(--color-primary);
}

.vs-title {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--font-size-base);
  color: var(--color-text-primary);
}

/* 资料名始终是次要信息：挤的时候先让位给标题 */
.vs-doc {
  flex-shrink: 1;
  min-width: 0;
  max-width: 42%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--font-size-xs);
  color: var(--color-text-placeholder);
}

.vs-ext {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 22px;
  height: 22px;
  padding: 0;
  border: none;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--color-text-placeholder);
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
}
.vs-ext:hover {
  background: var(--color-bg-hover);
  color: var(--color-primary);
}
</style>
