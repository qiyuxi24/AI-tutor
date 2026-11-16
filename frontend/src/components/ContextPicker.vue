<script setup>
/**
 * ContextPicker.vue — 引用候选面板（资料 / 知识点）
 *
 * 业界范式（2026-11 调研）：从输入区的一个 `+` 展开一个**统一候选面板**，
 * 里面可切类别、可搜、可多选；选中项回到输入区变成 chip。
 *
 * 多选就一条规则（别加复选框、别加"确认添加"）：**点一行 = 勾上，再点 = 取消**，
 * 面板不关 —— 一次进来勾几份是常态。底部实时显示「已选 N」，点「完成」或 Esc 收起。
 *
 * 两类数据走**两条通路**（这是它们的本质区别，别合并）：
 *   资料   → 目录树 `GET /kb/tree` + 本地过滤。树不大，本地过滤零延迟；
 *            文件夹也可选（后端 `collect_files` 会递归展开）。
 *   知识点 → `GET /knowledge/nodes?q=`。前端只持有**当前学科**的切片，
 *            本地过滤会把"在 A 学科的对话里引用 B 学科"这条路堵死。
 */
import { ref, computed, watch, nextTick, onMounted, onBeforeUnmount } from 'vue'
import { getKbTree } from '../api/kb.js'
import { useChatStore } from '../stores/chatStore'
import AppIcon from './AppIcon.vue'

const emit = defineEmits(['close'])
const store = useChatStore()

const query = ref('')
const activeTab = ref('docs')
const flatDocs = ref([])          // [{id,name,type,path}]
const nodes = ref([])             // [{id,name,subject,...}]
const selectedIndex = ref(0)
const inputRef = ref(null)
const listRef = ref(null)
const loading = ref(false)
const treeLoaded = ref(false)
const treeFailed = ref(false)

/** 目录树 → 扁平列表（带完整路径，同名文件靠路径才能区分） */
function flatten(tree, path = '', out = []) {
  for (const n of tree || []) {
    const p = path ? `${path}/${n.name}` : n.name
    if (n.type === 'file') {
      out.push({ id: n.id, name: n.name, type: 'file', path: p })
    } else {
      out.push({ id: n.id, name: n.name, type: 'folder', path: p })
      flatten(n.children, p, out)
    }
  }
  return out
}

async function loadTree() {
  if (treeLoaded.value) return
  treeLoaded.value = true
  try {
    const res = await getKbTree()
    flatDocs.value = flatten(res.data?.tree || [])
  } catch {
    flatDocs.value = []
    treeFailed.value = true       // 没上传过资料 / 离线 → 空态文案要说清是哪种
  }
}

let searchTimer = null
watch(query, () => {
  // 知识点走后端搜索 → 防抖，避免每敲一个字打一次接口
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(async () => {
    loading.value = true
    try {
      nodes.value = await store.searchKnowledgeNodes(query.value.trim(), 12)
    } finally {
      loading.value = false
      selectedIndex.value = 0
    }
  }, 220)
}, { immediate: true })

onBeforeUnmount(() => { if (searchTimer) clearTimeout(searchTimer) })

const docResults = computed(() => {
  const q = query.value.trim().toLowerCase()
  const list = q ? flatDocs.value.filter(d => d.path.toLowerCase().includes(q)) : flatDocs.value
  // 文件优先（多半是想引一份具体资料），同类型浅层优先（根目录的更可能是目标）
  return [...list]
    .sort((a, b) => (a.type === b.type ? a.path.length - b.path.length : a.type === 'file' ? -1 : 1))
    .slice(0, 30)
})

const showDocs = computed(() => activeTab.value !== 'nodes')
const showNodes = computed(() => activeTab.value !== 'docs')

/** 键盘导航用的统一列表 —— 顺序必须与模板渲染顺序严格一致，否则选中会错位 */
const options = computed(() => {
  const out = []
  if (showDocs.value) out.push(...docResults.value.map(d => ({ kind: 'docs', item: d })))
  if (showNodes.value) out.push(...nodes.value.map(n => ({ kind: 'nodes', item: n })))
  return out
})

watch(options, (list) => {
  if (selectedIndex.value >= list.length) selectedIndex.value = Math.max(0, list.length - 1)
})

function indexOfDoc(i) {
  return showDocs.value ? i : -1
}
function indexOfNode(i) {
  return (showDocs.value ? docResults.value.length : 0) + i
}

function pick(opt) {
  if (!opt) return
  if (opt.kind === 'docs') {
    store.toggleSelectionItem('docs', {
      id: opt.item.id, name: opt.item.name, type: opt.item.type, path: opt.item.path,
    })
  } else {
    store.toggleSelectionItem('nodes', {
      id: opt.item.id, name: opt.item.name, subject: opt.item.subject || '',
    })
  }
  // 选完留在面板里：勾多份是常态，逐条重开很烦
  nextTick(() => inputRef.value?.focus())
}

function scrollToSelected() {
  nextTick(() => {
    listRef.value?.querySelector('.pk-item.is-active')?.scrollIntoView({ block: 'nearest' })
  })
}

function handleKeydown(e) {
  if (e.key === 'ArrowDown') {
    e.preventDefault()
    selectedIndex.value = Math.min(selectedIndex.value + 1, options.value.length - 1)
    scrollToSelected()
  } else if (e.key === 'ArrowUp') {
    e.preventDefault()
    selectedIndex.value = Math.max(selectedIndex.value - 1, 0)
    scrollToSelected()
  } else if (e.key === 'Enter') {
    e.preventDefault()
    pick(options.value[selectedIndex.value])
  } else if (e.key === 'Escape') {
    e.preventDefault()
    emit('close')
  }
}

function isPicked(kind, id) {
  return (store.selection?.[kind] || []).some(x => x.id === id)
}

/** 已选总数（底部「完成（N）」与关闭按钮共用；空选区时不给括号数字） */
const pickedCount = computed(
  () => (store.selection?.docs?.length || 0) + (store.selection?.nodes?.length || 0)
)

onMounted(() => {
  loadTree()
  nextTick(() => inputRef.value?.focus())
})
</script>

<template>
  <div class="pk" role="dialog" aria-label="引用资料或知识点" @keydown="handleKeydown">
    <div class="pk-head">
      <AppIcon class="pk-search-icon" name="search" :size="14" />
      <input
        ref="inputRef"
        v-model="query"
        class="pk-input"
        type="text"
        :placeholder="activeTab === 'nodes' ? '搜索知识点…' : '搜索资料…'"
        aria-label="搜索"
      />
      <span v-if="loading" class="pk-spinner" aria-hidden="true"></span>
      <button v-else-if="query" class="pk-clear-q" aria-label="清空搜索" @click="query = ''">
        <AppIcon name="x" :size="11" />
      </button>
    </div>

    <div class="pk-tabs" role="tablist">
      <button
        role="tab" :aria-selected="activeTab === 'docs'"
        :class="{ on: activeTab === 'docs' }"
        @click="activeTab = 'docs'; selectedIndex = 0"
      >
        资料
        <span v-if="flatDocs.length" class="pk-tab-num">{{ flatDocs.length }}</span>
      </button>
      <button
        role="tab" :aria-selected="activeTab === 'nodes'"
        :class="{ on: activeTab === 'nodes' }"
        @click="activeTab = 'nodes'; selectedIndex = 0"
      >
        知识点
      </button>
    </div>

    <div ref="listRef" class="pk-body">
      <!-- 资料 -->
      <template v-if="showDocs">
        <div v-if="!docResults.length" class="pk-empty">
          <template v-if="flatDocs.length">没有匹配的资料</template>
          <template v-else-if="treeFailed">知识库加载失败，请稍后重试</template>
          <template v-else>知识库还是空的，先去「知识库」上传资料</template>
        </div>
        <button
          v-for="(d, i) in docResults" :key="'d' + d.id"
          class="pk-item"
          :class="{ 'is-active': selectedIndex === indexOfDoc(i), 'is-picked': isPicked('docs', d.id) }"
          :aria-pressed="isPicked('docs', d.id)"
          @mouseenter="selectedIndex = indexOfDoc(i)"
          @click="pick({ kind: 'docs', item: d })"
        >
          <span class="pk-ico" :class="d.type">
            <AppIcon :name="d.type === 'folder' ? 'folder' : 'file'" :size="14" />
          </span>
          <span class="pk-text">
            <span class="pk-name">{{ d.name }}</span>
            <span class="pk-sub">{{ d.path }}</span>
          </span>
          <!-- 常驻方框 = 一眼看出"这里能多选"，勾上才填色（不是只在选中后才冒出来的对勾） -->
          <span class="pk-box" :class="{ on: isPicked('docs', d.id) }" aria-hidden="true">
            <AppIcon v-if="isPicked('docs', d.id)" name="check" :size="11" />
          </span>
        </button>
      </template>

      <!-- 知识点 -->
      <template v-if="showNodes">
        <div v-if="!nodes.length" class="pk-empty">
          {{ query.trim() ? (loading ? '搜索中…' : '没有匹配的知识点') : '输入关键词搜索知识点' }}
        </div>
        <button
          v-for="(n, i) in nodes" :key="'n' + n.id"
          class="pk-item"
          :class="{ 'is-active': selectedIndex === indexOfNode(i), 'is-picked': isPicked('nodes', n.id) }"
          :aria-pressed="isPicked('nodes', n.id)"
          @mouseenter="selectedIndex = indexOfNode(i)"
          @click="pick({ kind: 'nodes', item: n })"
        >
          <span class="pk-ico node">
            <AppIcon name="target" :size="13" />
          </span>
          <span class="pk-text">
            <span class="pk-name">{{ n.name }}</span>
            <span class="pk-sub">{{ n.subject || '未分类' }}{{ n.board ? ` · ${n.board}` : '' }}</span>
          </span>
          <span v-if="n.mastery" class="pk-mastery">{{ n.mastery }}%</span>
          <span class="pk-box" :class="{ on: isPicked('nodes', n.id) }" aria-hidden="true">
            <AppIcon v-if="isPicked('nodes', n.id)" name="check" :size="11" />
          </span>
        </button>
      </template>
    </div>

    <div class="pk-foot">
      <span class="pk-foot-tip">点一行加入，再点取消 · ↑↓ + Enter 同效</span>
      <span v-if="pickedCount" class="pk-foot-count">已选 {{ pickedCount }} 项</span>
      <button class="pk-done" @click="emit('close')">
        {{ pickedCount ? '完成' : '关闭' }}
      </button>
    </div>
  </div>
</template>

<style scoped>
/* 从输入区上方展开：锚点是**整块输入容器**（`.composer{position:relative}`），
   所以浮层贴的是输入区的上沿，宽度也就是正文宽度 —— 比挂在 30px 高的胶囊上更稳。 */
.pk {
  position: absolute;
  bottom: calc(100% + 10px);
  left: 0;
  width: 400px;
  max-width: min(94vw, 400px);
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-popup);
  z-index: 60;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

/* ─── 搜索行 ─── */
.pk-head {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 10px 12px;
  border-bottom: 1px solid var(--color-border-soft);
}
.pk-search-icon { color: var(--color-text-muted); flex-shrink: 0; }

.pk-input {
  flex: 1;
  border: none;
  outline: none;
  background: transparent;
  font-size: 13px;
  font-family: inherit;
  color: var(--color-text-primary);
  min-width: 0;
}
.pk-input::placeholder { color: var(--color-text-placeholder); }

.pk-clear-q {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 18px; height: 18px;
  border: none;
  background: var(--color-bg-hover);
  color: var(--color-text-muted);
  border-radius: var(--radius-full);
  cursor: pointer;
  padding: 0;
  flex-shrink: 0;
}
.pk-clear-q:hover { color: var(--color-text-primary); }

/* 搜索中的小转圈（比"搜索中…"文字更省地方，也不会让列表跳高度） */
.pk-spinner {
  width: 12px; height: 12px;
  border: 1.5px solid var(--color-border);
  border-top-color: var(--color-accent);
  border-radius: 50%;
  animation: pk-spin 0.7s linear infinite;
  flex-shrink: 0;
}
@keyframes pk-spin { to { transform: rotate(360deg); } }

/* ─── Tabs ─── */
.pk-tabs {
  display: flex;
  gap: 2px;
  padding: 6px 8px;
  border-bottom: 1px solid var(--color-border-soft);
}
.pk-tabs button {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  border: none;
  background: transparent;
  color: var(--color-text-secondary);
  font-size: 12px;
  font-family: inherit;
  padding: 4px 10px;
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease;
}
.pk-tabs button:hover { background: var(--color-bg-hover); }
.pk-tabs button.on {
  background: var(--color-accent-light);
  color: var(--color-accent-hover);
  font-weight: 500;
}
.pk-tab-num {
  font-size: 10px;
  color: var(--color-text-muted);
}
.pk-tabs button.on .pk-tab-num { color: var(--color-accent); }

/* ─── 列表 ─── */
.pk-body { max-height: 288px; overflow-y: auto; padding: 4px; }
.pk-body::-webkit-scrollbar { width: 4px; }
.pk-body::-webkit-scrollbar-thumb {
  background: var(--color-border);
  border-radius: var(--radius-sm);
}

.pk-empty {
  padding: 22px 14px;
  text-align: center;
  font-size: 12px;
  line-height: 1.6;
  color: var(--color-text-muted);
}

.pk-item {
  display: flex;
  align-items: center;
  gap: 9px;
  width: 100%;
  padding: 7px 9px;
  border: none;
  border-radius: var(--radius-md);
  background: transparent;
  cursor: pointer;
  text-align: left;
  font-family: inherit;
  transition: background 0.12s ease;
}
.pk-item.is-active { background: var(--color-bg-hover); }
.pk-item.is-picked .pk-name { color: var(--color-accent-hover); }

.pk-ico {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 22px; height: 22px;
  border-radius: var(--radius-sm);
  background: var(--color-bg-hover);
  color: var(--color-text-secondary);
  flex-shrink: 0;
}
.pk-ico.folder { color: var(--color-warning); background: var(--color-warning-soft); }
.pk-ico.node { color: var(--color-accent); background: var(--color-accent-light); }

.pk-text { display: flex; flex-direction: column; min-width: 0; flex: 1; gap: 1px; }
.pk-name {
  font-size: 13px;
  color: var(--color-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pk-sub {
  font-size: 11px;
  color: var(--color-text-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pk-mastery {
  font-size: 10px;
  color: var(--color-text-muted);
  flex-shrink: 0;
  font-variant-numeric: tabular-nums;
}

/* 多选标记：常驻空心方框（一眼看出这行能勾），勾上填成强调色 + 对勾 */
.pk-box {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  border: 1.5px solid var(--color-border);
  border-radius: var(--radius-sm);
  color: var(--color-text-inverse);
  flex-shrink: 0;
  transition: background 0.12s ease, border-color 0.12s ease;
}
.pk-box.on {
  background: var(--color-accent);
  border-color: var(--color-accent);
}

/* ─── 底部：左提示 / 中已选数 / 右完成 ─── */
.pk-foot {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 10px 6px 12px;
  border-top: 1px solid var(--color-border-soft);
  background: var(--color-bg-hover);
  font-size: 10.5px;
  color: var(--color-text-muted);
}
.pk-foot-tip { flex: 1; min-width: 0; }
.pk-foot-count {
  color: var(--color-accent-hover);
  font-variant-numeric: tabular-nums;
  flex-shrink: 0;
}
.pk-done {
  flex-shrink: 0;
  height: 24px;
  padding: 0 12px;
  border: 1px solid var(--color-accent);
  border-radius: var(--radius-full);
  background: var(--color-accent-light);
  color: var(--color-accent-hover);
  font-family: inherit;
  font-size: 11.5px;
  cursor: pointer;
  transition: background 0.15s ease;
}
.pk-done:hover { background: var(--color-accent); color: var(--color-text-inverse); }
.pk-done:focus-visible { outline: 2px solid var(--color-accent); outline-offset: 2px; }

@media (prefers-reduced-motion: reduce) {
  .pk-spinner { animation: none; }
  .pk-tabs button, .pk-item { transition: none; }
}
</style>
