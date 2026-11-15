<script setup>
/**
 * InputArea.vue — 对话输入区（composer）
 *
 * 结构参考业界主流（DeepSeek / Codex 的输入框，2026-11）：
 *   **一个整块容器**装下"引用区 + 输入框 + 工具栏"，而不是"输入框旁边散着几个按钮"。
 *   外壳统一圆角 + 焦点环，工具栏沉到容器底部，左侧是能力入口（资料/知识点），
 *   右侧是发送 —— 视觉上只有一块，焦点态也只亮一块。
 *
 * 引用的两套机制（调研结论，别混）：
 *   **选区（chip）** vs  **@ 内联 mention** —— 这里是选区：选中即"这段对话都看这些"，
 *   跨轮次常驻，撤销点 chip 上的 ×。不做只对下一条生效的一次性附件（每问一句重选太烦）。
 */
import { ref, computed, nextTick } from 'vue'
import { useChatStore } from '../stores/chatStore'
import ContextPicker from './ContextPicker.vue'

const store = useChatStore()

const text = ref('')
const textareaRef = ref(null)
const focused = ref(false)

// 候选面板：同一个面板承载两类数据，用 tab 区分入口（哪颗胶囊点的就开哪个 tab）
const pickerOpen = ref(false)
const pickerTab = ref('docs')

// 当前对话的选区（每对话绑定，见 chatStore.selection）：资料 + 知识点两类 chip
const selDocs = computed(() => store.selection?.docs || [])
const selNodes = computed(() => store.selection?.nodes || [])
const hasSelection = computed(() => selDocs.value.length > 0 || selNodes.value.length > 0)

function removeDoc(d) { store.toggleSelectionItem('docs', d) }
function removeNode(n) { store.toggleSelectionItem('nodes', n) }
function clearAll() { store.clearSelection() }
function closePicker() { pickerOpen.value = false }

/** 点同一颗胶囊 = 收起；点另一颗 = 切到它的 tab（面板不闪一下再开）。 */
function togglePicker(tab) {
  if (pickerOpen.value && pickerTab.value === tab) {
    pickerOpen.value = false
    return
  }
  pickerTab.value = tab
  pickerOpen.value = true
}

function docLabel(d) {
  const suffix = d.type === 'folder' && d.fileCount ? `（${d.fileCount} 个文件）` : ''
  return `${d.name}${suffix}`
}

/**
 * textarea 自动增高。
 * 副作用：直接改 DOM 的 style.height（不走响应式，避免每个字符触发重渲染）。
 * 先置 auto 才能拿到真实内容高度，否则 scrollHeight 只会等于当前已撑开的高度。
 * 上限由 CSS max-height 兜底，超出后内部滚动。
 */
function autoGrow() {
  const el = textareaRef.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = Math.min(el.scrollHeight, 168) + 'px'
}

/** 清空输入后把高度收回单行（rows="1" 决定 auto 时的基准高度） */
function resetHeight() {
  const el = textareaRef.value
  if (el) el.style.height = 'auto'
}

async function handleSend() {
  if (!text.value.trim() || store.loading) return
  const msg = text.value
  text.value = ''
  await nextTick()
  resetHeight()
  await store.send(msg)
  await nextTick()
  textareaRef.value?.focus()
}

function handleKeydown(e) {
  // 选择器自己处理上下键/Enter；这里只挡 Enter 发送，避免"在面板里敲回车把消息发出去"
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    handleSend()
  }
}
</script>

<template>
  <div class="composer-wrap">
    <div class="composer" :class="{ 'is-focus': focused }">
      <!-- 已引用：chip 行贴在输入框上方（没选时不占位，保持输入区干净） -->
      <div v-if="hasSelection" class="composer-chips">
        <span class="chips-label">引用</span>
        <span
          v-for="d in selDocs" :key="'d' + d.id"
          class="chip"
          :title="d.path || d.name"
        >
          <svg class="chip-icon" width="12" height="12" viewBox="0 0 24 24" fill="none"
               stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
            <polyline points="14 2 14 8 20 8" />
          </svg>
          <span class="chip-text">{{ docLabel(d) }}</span>
          <button class="chip-x" :aria-label="`移除 ${d.name}`" @click="removeDoc(d)">
            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                 stroke-width="3" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12" /></svg>
          </button>
        </span>
        <span
          v-for="n in selNodes" :key="'n' + n.id"
          class="chip chip-node"
          :title="n.subject ? `${n.name} · ${n.subject}` : n.name"
        >
          <svg class="chip-icon" width="12" height="12" viewBox="0 0 24 24" fill="none"
               stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="12" cy="12" r="3" /><circle cx="12" cy="12" r="9" opacity="0.45" />
          </svg>
          <span class="chip-text">{{ n.name }}</span>
          <button class="chip-x" :aria-label="`移除 ${n.name}`" @click="removeNode(n)">
            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                 stroke-width="3" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12" /></svg>
          </button>
        </span>
        <button class="chips-clear" @click="clearAll">全部清除</button>
      </div>

      <textarea
        ref="textareaRef"
        v-model="text"
        placeholder="有什么想问的？"
        rows="1"
        class="composer-input"
        aria-label="输入消息"
        @input="autoGrow"
        @keydown="handleKeydown"
        @focus="focused = true"
        @blur="focused = false"
      ></textarea>

      <!-- 工具栏沉在容器底部：左侧能力入口，右侧发送。
           面板锚在胶囊组上、点外面即收（全局 v-click-outside，见 main.js）：
           点在输入框里也算"外面"—— 用户开始打字就该让面板让位。 -->
      <div class="composer-toolbar">
        <div class="picker-anchor" v-click-outside="closePicker">
          <button
            class="tool-pill"
            :class="{ active: pickerOpen && pickerTab === 'docs', filled: selDocs.length }"
            :disabled="store.loading"
            aria-label="引用资料"
            @click="togglePicker('docs')"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                 stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
              <polyline points="14 2 14 8 20 8" />
            </svg>
            <span>资料</span>
            <span v-if="selDocs.length" class="pill-count">{{ selDocs.length }}</span>
          </button>

          <button
            class="tool-pill"
            :class="{ active: pickerOpen && pickerTab === 'nodes', filled: selNodes.length }"
            :disabled="store.loading"
            aria-label="引用知识点"
            @click="togglePicker('nodes')"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                 stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <circle cx="12" cy="12" r="9" /><circle cx="12" cy="12" r="3" />
            </svg>
            <span>知识点</span>
            <span v-if="selNodes.length" class="pill-count">{{ selNodes.length }}</span>
          </button>

          <ContextPicker
            v-if="pickerOpen"
            :initial-tab="pickerTab"
            @close="closePicker"
          />
        </div>

        <div class="toolbar-right">
          <!-- 生成中：同一位置换成「停止」（方形 = 停止符，公认语义）。
               忙碌时把发送位变成停止位是这个位置的通行做法，也省一个按钮位。 -->
          <button
            v-if="store.loading"
            class="send-btn stop"
            title="停止生成"
            aria-label="停止生成"
            @click="store.stopGenerating()"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
              <rect x="7" y="7" width="10" height="10" rx="2" />
            </svg>
          </button>
          <button
            v-else
            class="send-btn"
            :class="{ active: text.trim() }"
            :disabled="!text.trim()"
            @click="handleSend"
            title="发送"
            aria-label="发送消息"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none"
                 stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M12 19V5" /><polyline points="5 12 12 5 19 12" />
            </svg>
          </button>
        </div>
      </div>
    </div>

    <p class="composer-note">内容由 AI 生成，请仔细甄别</p>
  </div>
</template>

<style scoped>
/* 与消息列同宽对齐：`ChatArea.message-list` 把正文封顶在 760px 并居中，这里照抄同一
   口径（外层留 24px 兜底边距），否则宽屏下输入框会顶到窗口两边、和上面的对话不在一条轴上。
   `container-type` 是**必须**的：输入区是 `.chat-body` 的**兄弟节点**，借不到外层那个
   容器上下文，不自己声明一条的话下面 `@container` 的窄窗规则永远不会命中。 */
.composer-wrap {
  padding: 10px 24px 6px;
  background: var(--color-bg-primary);
  container-type: inline-size;
}

.composer {
  position: relative;   /* 候选面板以**整块输入区**为锚（不是那颗胶囊）→ 从容器上沿展开 */
  width: 100%;
  max-width: 760px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 10px 10px 8px 12px;
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-sm);
  transition: border-color 0.18s ease, box-shadow 0.18s ease;
}

/* 焦点环亮在**整块**上（不是一个裸 textarea 描边）：用户感知是"一个输入区被激活" */
.composer.is-focus {
  border-color: var(--color-accent);
  box-shadow: 0 0 0 3px var(--color-primary-ring), var(--shadow-md);
}

/* ─── 引用 chip 行 ─── */
.composer-chips {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  padding: 2px 2px 6px;
  border-bottom: 1px solid var(--color-border-soft);
}

.chips-label {
  font-size: 11px;
  color: var(--color-text-muted);
  flex-shrink: 0;
  letter-spacing: 0.2px;
}

.chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  max-width: 240px;
  height: 24px;
  padding: 0 4px 0 7px;
  border-radius: var(--radius-full);
  background: var(--color-bg-hover);
  border: 1px solid var(--color-border-soft);
  font-size: 12px;
  color: var(--color-text-secondary);
}

.chip-node {
  background: var(--color-accent-light);
  border-color: transparent;
  color: var(--color-accent-hover);
}

.chip-icon { flex-shrink: 0; opacity: 0.85; }

.chip-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chip-x {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  border: none;
  background: transparent;
  color: inherit;
  opacity: 0.6;
  cursor: pointer;
  border-radius: var(--radius-full);
  padding: 0;
  flex-shrink: 0;
  transition: opacity 0.15s ease, background 0.15s ease;
}
.chip-x:hover { opacity: 1; background: rgba(0, 0, 0, 0.08); }

.chips-clear {
  border: none;
  background: transparent;
  color: var(--color-text-muted);
  font-size: 11px;
  cursor: pointer;
  padding: 0 2px;
  font-family: inherit;
  margin-left: auto;
}
.chips-clear:hover { color: var(--color-danger); }

/* ─── 输入框（无边框，融进容器） ─── */
.composer-input {
  width: 100%;
  resize: none;
  border: none;
  outline: none;
  background: transparent;
  padding: 4px 4px 0;
  font-size: 15px;
  line-height: 1.6;
  font-family: inherit;
  color: var(--color-text-primary);
  max-height: 168px;
  overflow-y: auto;
}
.composer-input::placeholder { color: var(--color-text-placeholder); }

/* ─── 工具栏 ─── */
.composer-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding-top: 2px;
}

/* 不设 position：面板要相对**整块 composer** 定位（浮在输入区上方），
   anchor 在这里负责的是点击外部判定，不是定位。 */
.picker-anchor {
  display: flex;
  align-items: center;
  gap: 6px;
}

/* 能力入口胶囊：参照 DeepSeek 的「深度思考 / 智能搜索」，低饱和描边 + 选中才上色 */
.tool-pill {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 30px;
  padding: 0 11px;
  border-radius: var(--radius-full);
  border: 1px solid var(--color-border);
  background: transparent;
  color: var(--color-text-secondary);
  font-size: 12.5px;
  font-family: inherit;
  cursor: pointer;
  transition: color 0.15s ease, border-color 0.15s ease, background 0.15s ease;
}
.tool-pill:hover:not(:disabled) {
  color: var(--color-accent);
  border-color: var(--color-accent);
}
.tool-pill.active {
  color: var(--color-accent);
  border-color: var(--color-accent);
  background: var(--color-accent-light);
}
/* 有引用时保持浅色高亮（不必展开面板也能看出"这段对话引了东西"） */
.tool-pill.filled {
  color: var(--color-accent-hover);
  border-color: var(--color-accent-light);
  background: var(--color-accent-light);
}
.tool-pill:disabled { cursor: not-allowed; opacity: 0.5; }

.pill-count {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 15px;
  height: 15px;
  padding: 0 4px;
  border-radius: var(--radius-full);
  background: var(--color-accent);
  color: var(--color-text-inverse);
  font-size: 10px;
  font-weight: 600;
  line-height: 1;
}

.toolbar-right {
  display: flex;
  align-items: center;
  gap: 10px;
}

/* 发送：圆形，空输入时灰、有内容时实心强调色（视觉重量随可用性变化） */
.send-btn {
  flex-shrink: 0;
  width: 34px;
  height: 34px;
  border-radius: var(--radius-full);
  border: none;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: background 0.16s ease, color 0.16s ease, transform 0.12s ease;
  background: var(--color-bg-disabled);
  color: var(--color-text-muted);
}
.send-btn.active {
  background: var(--color-accent);
  color: var(--color-text-inverse);
}
.send-btn.active:hover {
  background: var(--color-accent-hover);
  transform: translateY(-1px);
}
.send-btn:disabled { cursor: not-allowed; }
.send-btn:focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: 2px;
}

/* 停止态：换成暖灰实底，和"可发送"的强调色明显区分（不靠图标猜当前能不能发） */
.send-btn.stop {
  background: var(--color-text-secondary);
  color: var(--color-text-inverse);
}
.send-btn.stop:hover {
  background: var(--color-text-primary);
}

/* 免责声明：小字居中，与输入区同宽（参照业界底部提示的做法） */
.composer-note {
  margin: 8px 0 0;
  text-align: center;
  font-size: 11px;
  color: var(--color-text-muted);
}

/* 窄容器（悬浮小窗）：工具按钮只留图标省横向空间 */
@container (max-width: 480px) {
  .tool-pill span:not(.pill-count) { display: none; }
  .tool-pill { padding: 0 9px; }
}

@media (prefers-reduced-motion: reduce) {
  .composer, .tool-pill, .send-btn, .chip-x { transition: none; }
}
</style>
