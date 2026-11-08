<script setup>
import { computed, ref, watch, nextTick, onBeforeUnmount } from 'vue'
import { renderMarkdown } from '../utils/markdown.js'
import { avatarState } from '../utils/avatar.js'
import QuizCard from './QuizCard.vue'
import { BRAND_ICON } from '../utils/brandAssets.js'
import { formatMsgTime } from '../utils/time.js'

const props = defineProps({
  message: { type: Object, required: true },
  knowledgeNodes: { type: Array, default: () => [] },
  fallbackTs: { type: [Number, String], default: 0 },
  /** 外部注入的扩展动作项 [{ key, title, icon, onClick }]，保留后续功能无侵入接入能力 */
  extraActions: { type: Array, default: () => [] },
})

const emit = defineEmits(['navigate-to-node', 'retry', 'submit-quiz', 'action'])

const isUser = computed(() => props.message.role === 'user')
const isStreaming = computed(() => !isUser.value && !props.message.content
  && !(props.message.tools?.length) && !(props.message.thinking?.length))
const bubbleRef = ref(null)

// 复制反馈状态
const copied = ref(false)
let copyTimer = null

async function handleCopy() {
  const text = props.message.content || ''
  if (!text) return
  try {
    await navigator.clipboard.writeText(text)
  } catch {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    document.execCommand('copy')
    document.body.removeChild(ta)
  }
  copied.value = true
  if (copyTimer) clearTimeout(copyTimer)
  copyTimer = setTimeout(() => { copied.value = false }, 1500)
}

/** 格式化消息时间（如 "10月23日 21:08"）—— 单位换算与阈值只有 utils/time.js 一份 */
const timeLabel = computed(() =>
  formatMsgTime(props.message.ts || props.message.createdAt || props.message.created_at || props.fallbackTs)
)

/**
 * 底部工具栏动作项（声明式注册表）：
 *   后续新增功能（如朗读、引用追问、导出笔记、分叉/回退）只需在此数组追加或通过 extraActions 传入，
 *   模板与样式无需重改。
 */
const toolbarActions = computed(() => [
  {
    key: 'copy',
    title: copied.value ? '已复制' : '复制内容',
    active: copied.value,
    run: handleCopy,
  },
  ...props.extraActions.map((a) => ({
    ...a,
    run: () => (a.onClick ? a.onClick(props.message) : emit('action', { key: a.key, message: props.message })),
  })),
])

onBeforeUnmount(() => {
  if (copyTimer) clearTimeout(copyTimer)
})

// 工具/思考状态
const hasTools = computed(() => !isUser.value && props.message.tools?.length > 0)
const hasThinking = computed(() => !isUser.value && props.message.thinking?.length > 0)
const showThinking = ref(false)
// 工具面板展开态：有工具正在执行时默认展开，全部完成后可折叠查看步骤与入参/结果摘要
const showTools = ref(false)
const expandedToolIdx = ref(-1)

const TOOL_NAMES = {
  add_knowledge_node: '添加节点',
  plan_knowledge_nodes: '规划知识节点',
  update_node_content: '更新节点内容',
  update_node_sections: '更新节点小节',
  update_mastery: '更新掌握度',
  add_edge: '添加知识关联',
  delete_node: '删除节点',
  kg_snapshot: '查看图谱快照',
  update_user_profile: '更新学情画像',
  search_resources: '搜索教学资源',
  collect_resources: '采集资源入库',
  fetch_webpage: '抓取网页',
  download_resource: '下载资源',
  rag_search: '知识库检索',
  quiz_generate: '生成测验题',
  grade_answer: '判分',
  mark_section_understood: '标记小节已懂',
  mcp__websearch__web_search: '联网搜索',
}

function getToolDisplayName(name) {
  if (!name) return '调用工具'
  if (TOOL_NAMES[name]) return TOOL_NAMES[name]
  if (name.startsWith('mcp__')) {
    const short = name.split('__').pop() || name
    return `MCP · ${short}`
  }
  return name
}

/** 从 args_head JSON 中提取人类可读的核心参数摘要（如查询词、URL、节点名） */
function extractToolBrief(tool) {
  const raw = (tool?.args_head || '').trim()
  if (!raw || raw === '{}') return ''
  try {
    const obj = JSON.parse(raw)
    if (!obj || typeof obj !== 'object') return ''
    if (Array.isArray(obj.nodes) && obj.nodes.length) {
      const names = obj.nodes.map((n) => n?.name || n?.id).filter(Boolean)
      const head = names.slice(0, 3).join('、')
      return names.length > 3 ? `${head} 等 ${names.length} 项` : head
    }
    const keys = ['query', 'q', 'name', 'title', 'subject', 'node_id', 'url', 'section_id', 'topic']
    for (const k of keys) {
      const val = obj[k]
      if (typeof val === 'string' && val.trim()) {
        const s = val.trim()
        return s.length > 42 ? s.slice(0, 42) + '…' : s
      }
    }
  } catch {
    // args_head 可能被后端截断为非完整 JSON，尝试正则提取常见字段
    const m = /"(?:query|name|subject|node_id|url)"\s*:\s*"([^"]{1,60})"/i.exec(raw)
    if (m?.[1]) return m[1].length > 42 ? m[1].slice(0, 42) + '…' : m[1]
  }
  return ''
}

function formatDuration(ms) {
  const n = Number(ms)
  if (!n || !Number.isFinite(n) || n <= 0) return ''
  return n >= 1000 ? `${(n / 1000).toFixed(1)}s` : `${Math.round(n)}ms`
}

const toolStats = computed(() => {
  const list = props.message.tools || []
  const running = list.find((t) => t.status === 'running') || null
  const errorCount = list.filter((t) => t.status === 'error').length
  const totalMs = list.reduce((sum, t) => sum + (Number(t.result?.duration_ms) || 0), 0)
  // 去重后的工具名标签（折叠态概览用，最多展示 3 个）
  const uniqueNames = [...new Set(list.map((t) => getToolDisplayName(t.tool)))]
  return {
    total: list.length,
    running,
    errorCount,
    totalDuration: formatDuration(totalMs),
    uniqueNames,
  }
})

function toggleToolDetail(idx) {
  expandedToolIdx.value = expandedToolIdx.value === idx ? -1 : idx
}

const renderedContent = computed(() => {
  if (isUser.value) return props.message.content
  return renderMarkdown(props.message.content)
})

/**
 * 在 AI 回复的 HTML 中，将匹配知识节点名称的文本替换为可点击链接。
 * 只在非用户消息、非流式状态下执行。
 */
function linkifyKnowledgeNodes() {
  if (isUser.value || isStreaming.value || !bubbleRef.value) return
  const nodes = props.knowledgeNodes || []
  if (nodes.length === 0) return

  const nodeNames = nodes
    .map(n => n.name)
    .filter(Boolean)
    .sort((a, b) => b.length - a.length)

  if (nodeNames.length === 0) return

  const escaped = nodeNames.map(s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
  const pattern = new RegExp(`(${escaped.join('|')})`, 'g')

  const body = bubbleRef.value.querySelector('.markdown-body')
  if (!body) return

  const walker = document.createTreeWalker(body, NodeFilter.SHOW_TEXT, null)
  const textNodes = []
  while (walker.nextNode()) textNodes.push(walker.currentNode)

  for (const textNode of textNodes) {
    const text = textNode.textContent
    if (!pattern.test(text)) {
      pattern.lastIndex = 0
      continue
    }
    pattern.lastIndex = 0

    const fragment = document.createDocumentFragment()
    let lastIndex = 0
    let match

    while ((match = pattern.exec(text)) !== null) {
      if (match.index > lastIndex) {
        fragment.appendChild(document.createTextNode(text.slice(lastIndex, match.index)))
      }
      const span = document.createElement('span')
      span.className = 'kg-link'
      span.textContent = match[0]
      span.title = '点击跳转到图谱中的「' + match[0] + '」'
      span.addEventListener('click', () => {
        const node = nodes.find(n => n.name === match[0])
        if (node) emit('navigate-to-node', node.id)
      })
      fragment.appendChild(span)
      lastIndex = pattern.lastIndex
    }

    if (lastIndex < text.length) {
      fragment.appendChild(document.createTextNode(text.slice(lastIndex)))
    }

    textNode.parentNode?.replaceChild(fragment, textNode)
  }
}

watch(renderedContent, () => {
  nextTick(() => linkifyKnowledgeNodes())
})
</script>

<template>
  <div class="bubble-wrapper" :class="{ 'is-user': isUser }">
    <!-- 头像：AI 侧用站点品牌像素星（public/brand-star.svg）；用户侧优先用上传的照片，
         没有则回落到人形 SVG（原先这里是 👤 emoji，与"项目禁 emoji"的约定冲突） -->
    <div class="avatar" :class="{ 'user-avatar': isUser, 'ai-avatar': !isUser }">
      <img v-if="!isUser" class="avatar-img" :src="BRAND_ICON" alt="AI" />
      <img v-else-if="avatarState.url" class="avatar-photo" :src="avatarState.url" alt="我的头像" />
      <svg v-else class="avatar-person" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
        <circle cx="12" cy="7" r="4" />
      </svg>
    </div>
    <div class="bubble" :class="{ 'user-bubble': isUser, 'ai-bubble': !isUser }" ref="bubbleRef">
      <div v-if="isUser" class="text">{{ message.content }}</div>
      <template v-else>
        <!-- 工具活动区（折叠总览 + 可展开步骤与参数/结果详情） -->
        <div v-if="hasTools" class="tool-panel">
          <button
            type="button"
            class="tool-header"
            @click="showTools = !showTools"
          >
            <!-- 状态指示：执行中转圈 / 有失败标红 / 全部成功勾号 -->
            <span v-if="toolStats.running" class="tool-spinner" />
            <svg
              v-else-if="toolStats.errorCount > 0"
              class="tool-head-icon is-error"
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2.2"
              stroke-linecap="round"
              stroke-linejoin="round"
              aria-hidden="true"
            >
              <circle cx="12" cy="12" r="10" />
              <line x1="12" y1="8" x2="12" y2="12" />
              <line x1="12" y1="16" x2="12.01" y2="16" />
            </svg>
            <svg
              v-else
              class="tool-head-icon is-done"
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2.2"
              stroke-linecap="round"
              stroke-linejoin="round"
              aria-hidden="true"
            >
              <polyline points="20 6 9 17 4 12" />
            </svg>

            <span class="tool-head-title">
              <template v-if="toolStats.running">
                正在{{ getToolDisplayName(toolStats.running.tool) }}…
              </template>
              <template v-else>
                已调用 {{ toolStats.total }} 个工具
              </template>
            </span>

            <!-- 折叠态工具胶囊概览 -->
            <span v-if="!showTools && !toolStats.running" class="tool-chips">
              <span
                v-for="name in toolStats.uniqueNames.slice(0, 3)"
                :key="name"
                class="tool-chip"
              >{{ name }}</span>
              <span v-if="toolStats.uniqueNames.length > 3" class="tool-chip">
                +{{ toolStats.uniqueNames.length - 3 }}
              </span>
            </span>

            <span v-if="toolStats.totalDuration" class="tool-head-duration">
              {{ toolStats.totalDuration }}
            </span>

            <span class="thinking-arrow" :class="{ expanded: showTools || !!toolStats.running }">▾</span>
          </button>

          <!-- 展开的步骤时间线（执行中自动展开，完成后可点开查看每一步） -->
          <div v-show="showTools || !!toolStats.running" class="tool-steps">
            <div
              v-for="(tool, idx) in message.tools"
              :key="idx"
              class="tool-step"
              :class="{
                'tool-running': tool.status === 'running',
                'tool-done': tool.status === 'done',
                'tool-error': tool.status === 'error',
              }"
            >
              <button
                type="button"
                class="tool-step-main"
                :class="{ clickable: !!(tool.args_head || tool.result?.summary) }"
                :disabled="!(tool.args_head || tool.result?.summary)"
                :aria-expanded="!!(tool.args_head || tool.result?.summary) ? expandedToolIdx === idx : undefined"
                @click="(tool.args_head || tool.result?.summary) && toggleToolDetail(idx)"
              >
                <span v-if="tool.status === 'running'" class="tool-spinner" />
                <span v-else-if="tool.status === 'error'" class="tool-dot is-error">✕</span>
                <span v-else class="tool-dot is-done">✓</span>

                <span class="tool-name">{{ getToolDisplayName(tool.tool) }}</span>

                <span v-if="extractToolBrief(tool)" class="tool-brief">
                  {{ extractToolBrief(tool) }}
                </span>

                <span v-if="tool.status === 'error'" class="tool-status tool-status-error">失败</span>
                <span v-else-if="formatDuration(tool.result?.duration_ms)" class="tool-step-duration">
                  {{ formatDuration(tool.result?.duration_ms) }}
                </span>
              </button>

              <!-- 点击步骤展开：入参 & 返回结果摘要 -->
              <div
                v-if="expandedToolIdx === idx && (tool.args_head || tool.result?.summary)"
                class="tool-detail"
              >
                <div v-if="tool.args_head && tool.args_head !== '{}'" class="tool-detail-row">
                  <span class="tool-detail-label">参数</span>
                  <code class="tool-detail-code">{{ tool.args_head }}</code>
                </div>
                <div v-if="tool.result?.summary" class="tool-detail-row">
                  <span class="tool-detail-label">结果</span>
                  <span class="tool-detail-text">{{ tool.result.summary }}</span>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- 思考折叠面板 -->
        <div v-if="hasThinking" class="thinking-panel">
          <button class="thinking-toggle" @click="showThinking = !showThinking">
            <span class="thinking-label">AI 思考过程</span>
            <span class="thinking-arrow" :class="{ expanded: showThinking }">▾</span>
          </button>
          <div v-show="showThinking" class="thinking-content">
            <div v-for="(think, idx) in message.thinking" :key="idx" class="thinking-block">
              {{ think }}
            </div>
          </div>
        </div>

        <!-- 流式填充中：显示打字动画 -->
        <div v-if="isStreaming" class="typing-indicator">
          <span></span><span></span><span></span>
        </div>
        <!-- 流式内容渲染 -->
        <div v-else-if="message.content" class="markdown-body" v-html="renderedContent"></div>

        <!-- 题目小卡片（结构化作答：点选项 → 提交 → 后端确定性判分）
             题目数据由 SSE 的 quiz_ready 事件挂在消息上（chatStore.handleQuizReady）。 -->
        <QuizCard
          v-if="message.quiz?.length"
          :questions="message.quiz"
          @submit="(p) => emit('submit-quiz', p)"
        />

        <!-- 失败兜底（后端文本未送达等）：给明确提示 + 一键重试，绝不静默消失 -->
        <div v-if="message.failed" class="failed-actions">
          <button class="retry-btn" @click="emit('retry')">
            <!-- 旋转箭头（lucide rotate-ccw），替代 ↻ 字符 -->
            <svg
              class="retry-icon"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
              stroke-linecap="round"
              stroke-linejoin="round"
              aria-hidden="true"
            >
              <path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" />
              <path d="M3 3v5h5" />
            </svg>
            <span>重试</span>
          </button>
        </div>

        <!-- 底部操作栏（光标移到 LLM 回复上时浮现：复制 + 扩展动作槽 + 时间） -->
        <div
          v-if="!isStreaming && message.content && !message.failed"
          class="msg-toolbar"
          :class="{ 'is-active': copied }"
        >
          <button
            v-for="act in toolbarActions"
            :key="act.key"
            type="button"
            class="msg-action-btn"
            :class="{ 'is-done': act.active }"
            :title="act.title"
            :aria-label="act.title"
            @click="act.run"
          >
            <!-- 复制按钮：常态为双层方框，复制成功后短暂切换为对勾 -->
            <template v-if="act.key === 'copy'">
              <svg
                v-if="!copied"
                width="15"
                height="15"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="1.9"
                stroke-linecap="round"
                stroke-linejoin="round"
                aria-hidden="true"
              >
                <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
                <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
              </svg>
              <svg
                v-else
                width="15"
                height="15"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="2.2"
                stroke-linecap="round"
                stroke-linejoin="round"
                aria-hidden="true"
              >
                <polyline points="20 6 9 17 4 12" />
              </svg>
            </template>
            <!-- 外部扩展动作：支持传入内联 SVG 字符串（仅限受信常量） -->
            <svg
              v-else-if="act.icon"
              width="15"
              height="15"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="1.9"
              stroke-linecap="round"
              stroke-linejoin="round"
              aria-hidden="true"
              v-html="act.icon"
            />
          </button>

          <!-- 插槽：保留自定义组件级操作接入点 -->
          <slot name="actions" :message="message" />

          <span v-if="timeLabel" class="msg-time">{{ timeLabel }}</span>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.bubble-wrapper {
  display: flex;
  gap: 10px;
  margin-bottom: 16px;
  max-width: 85%;
  animation: fadeIn 0.2s ease;
}

@keyframes fadeIn {
  from { opacity: 0; transform: translateY(6px); }
  to   { opacity: 1; transform: translateY(0); }
}

.bubble-wrapper.is-user {
  flex-direction: row-reverse;
  margin-left: auto;
}

.avatar {
  flex-shrink: 0;
  width: 32px;
  height: 32px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  background: var(--color-bg-surface);
}

.user-avatar {
  background: var(--color-accent-light);
}

/* AI 头像：直接用品牌像素星，不垫圆形底色（星形本身自带留白与配色） */
.ai-avatar {
  background: transparent;
}

.avatar-img {
  display: block;
  width: 26px;   /* brand-star.svg viewBox 14x13，等比：24 * 14/13 */
  height: 24px;
}

/* 用户上传的照片：铺满整个圆形头像位（正方形输出，cover 保证不拉伸） */
.avatar-photo {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
  border-radius: 50%;
}

/* 没有照片时的默认人形图标（替代原 👤 emoji） */
.avatar-person {
  width: 18px;
  height: 18px;
  color: var(--color-primary);
}

.bubble {
  padding: 12px 16px;
  border-radius: 18px;
  line-height: 1.6;
  font-size: 14px;
  word-break: break-word;
}

.user-bubble {
  background: var(--color-chat-bubble-user, var(--color-accent));
  color: var(--color-chat-bubble-user-text, #111827);
  border-bottom-right-radius: 4px;
}

.ai-bubble {
  /* 无气泡：AI 回复直接平铺在页面上 */
  background: none;
  padding: 0;
  border-radius: 0;
  color: var(--color-text-primary);
}

.text {
  white-space: pre-wrap;
}

/* ─── 工具调用面板（折叠总览 + 可展开步骤与参数/结果详情） ─── */
.tool-panel {
  margin-bottom: 10px;
}

.tool-header {
  display: inline-flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 7px;
  padding: 5px 10px;
  border: 1px solid var(--color-border-subtle, var(--color-border));
  border-radius: 8px;
  background: var(--color-bg-secondary);
  color: var(--color-text-secondary);
  font-size: 12px;
  cursor: pointer;
  transition: background 0.15s ease, border-color 0.15s ease, color 0.15s ease;
}

.tool-header:hover {
  border-color: var(--color-border-strong, var(--color-border));
  color: var(--color-text-primary);
}

.tool-head-icon {
  flex-shrink: 0;
}

.tool-head-icon.is-done {
  color: var(--color-success);
}

.tool-head-icon.is-error {
  color: var(--color-danger);
}

.tool-head-title {
  font-weight: 500;
  color: var(--color-text-primary);
}

.tool-chips {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.tool-chip {
  padding: 1px 6px;
  border-radius: 999px;
  background: var(--color-bg-tertiary);
  color: var(--color-text-secondary);
  font-size: 11px;
  line-height: 1.4;
}

.tool-head-duration {
  font-size: 11px;
  color: var(--color-text-tertiary);
  font-variant-numeric: tabular-nums;
}

.tool-steps {
  display: flex;
  flex-direction: column;
  gap: 5px;
  margin-top: 6px;
  padding: 6px 0 2px 12px;
  border-left: 2px solid var(--color-border);
}

.tool-step {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  animation: toolFadeIn 0.2s ease;
}

@keyframes toolFadeIn {
  from { opacity: 0; transform: translateY(-2px); }
  to   { opacity: 1; transform: translateY(0); }
}

.tool-step-main {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  min-width: 0;
  padding: 2px 6px;
  border-radius: 6px;
  color: var(--color-text-secondary);
  transition: background 0.15s ease;
  /* 真 <button>：清掉浏览器按钮默认外观（原本是 <div @click>，键盘不可达） */
  appearance: none;
  border: 0;
  background: none;
  font: inherit;
  text-align: left;
}

/* 没有可展开内容时是纯展示行（disabled 只为退出 tab 序，不是"不可用"语义） */
.tool-step-main:disabled {
  cursor: default;
  opacity: 1;
}

.tool-step-main:focus-visible {
  outline: 2px solid var(--color-accent, var(--color-text-secondary));
  outline-offset: 1px;
}

.tool-step-main.clickable {
  cursor: pointer;
}

.tool-step-main.clickable:hover {
  background: var(--color-bg-hover);
}

.tool-dot {
  font-size: 11px;
  font-weight: 700;
  line-height: 1;
  flex-shrink: 0;
}

.tool-dot.is-done {
  color: var(--color-success);
}

.tool-dot.is-error {
  color: var(--color-danger);
}

.tool-running .tool-name {
  color: var(--color-accent);
}

.tool-name {
  font-weight: 500;
  color: var(--color-text-primary);
  white-space: nowrap;
}

.tool-brief {
  max-width: 320px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--color-text-tertiary);
  font-family: var(--font-mono);
  font-size: 11.5px;
}

.tool-step-duration {
  font-size: 11px;
  color: var(--color-text-tertiary);
  font-variant-numeric: tabular-nums;
}

.tool-status {
  font-size: 11px;
  opacity: 0.8;
}

.tool-status-error {
  color: var(--color-danger);
  font-weight: 700;
}

.tool-detail {
  margin-left: 18px;
  padding: 6px 10px;
  border-radius: 6px;
  background: var(--color-bg-secondary);
  border: 1px solid var(--color-border-subtle, var(--color-border));
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 11.5px;
}

.tool-detail-row {
  display: flex;
  align-items: baseline;
  gap: 8px;
  min-width: 0;
}

.tool-detail-label {
  flex-shrink: 0;
  color: var(--color-text-tertiary);
  font-weight: 500;
}

.tool-detail-code {
  font-family: var(--font-mono);
  color: var(--color-text-secondary);
  word-break: break-all;
}

.tool-detail-text {
  color: var(--color-text-secondary);
  word-break: break-word;
}

.tool-spinner {
  flex-shrink: 0;
  width: 11px;
  height: 11px;
  border: 1.5px solid var(--color-accent);
  border-top-color: transparent;
  border-radius: 50%;
  animation: spin 0.6s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

/* ─── 失败兜底：重试按钮 ─── */
.failed-actions {
  margin-top: 8px;
}

.retry-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 12px;
  border: 1px solid var(--color-accent-light);
  border-radius: 6px;
  background: transparent;
  color: var(--color-accent);
  font-size: 13px;
  cursor: pointer;
  transition: all 0.15s;
}

.retry-icon {
  width: 14px;
  height: 14px;
  flex-shrink: 0;
}

.retry-btn:hover { background: var(--color-accent-light); }

/* ─── AI 回复底部操作栏（悬停显现，常驻占位防止布局跳动） ─── */
.msg-toolbar {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 8px;
  min-height: 24px;
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.16s ease;
  user-select: none;
}

.bubble-wrapper:hover .msg-toolbar,
.msg-toolbar:focus-within,
.msg-toolbar.is-active {
  opacity: 1;
  pointer-events: auto;
}

.msg-action-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  padding: 0;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--color-text-tertiary);
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease;
}

.msg-action-btn:hover {
  background: var(--color-bg-hover);
  color: var(--color-text-primary);
}

.msg-action-btn.is-done {
  color: var(--color-success);
}

.msg-time {
  margin-left: 4px;
  font-size: 12px;
  line-height: 1;
  color: var(--color-text-tertiary);
  white-space: nowrap;
}

/* ─── 思考折叠面板（无边框：只留收起 / 展开） ─── */
.thinking-panel {
  margin-bottom: 8px;
}

.thinking-toggle {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 2px 0;
  background: none;
  border: none;
  cursor: pointer;
  font-size: 12px;
  color: var(--color-text-secondary);
  transition: color 0.15s;
}

.thinking-toggle:hover {
  color: var(--color-text-primary);
}

.thinking-label {
  font-weight: 500;
}

.thinking-arrow {
  transition: transform 0.2s;
  font-size: 11px;
}

.thinking-arrow.expanded {
  transform: rotate(180deg);
}

.thinking-content {
  padding: 4px 0 4px 2px;
}

.thinking-block {
  font-size: 12px;
  color: var(--color-text-tertiary);
  line-height: 1.5;
  padding: 3px 0;
  font-style: italic;
}

/* Markdown 渲染样式 */
.markdown-body :deep(p) {
  margin: 0 0 8px;
}

.markdown-body :deep(p:last-child) {
  margin-bottom: 0;
}

.markdown-body :deep(strong) {
  font-weight: 600;
}

.markdown-body :deep(code) {
  background: var(--color-bg-hover);
  padding: 2px 6px;
  border-radius: 4px;
  font-family: 'Consolas', 'Courier New', monospace;
  font-size: 13px;
}

.markdown-body :deep(pre) {
  background: var(--color-bg-tertiary);
  color: var(--color-text-primary);
  padding: 14px 16px;
  border-radius: 10px;
  overflow-x: auto;
  margin: 10px 0;
  font-size: 13px;
  line-height: 1.5;
}

.markdown-body :deep(pre code) {
  background: none;
  padding: 0;
  color: inherit;
}

.markdown-body :deep(ul),
.markdown-body :deep(ol) {
  padding-left: 20px;
  margin: 6px 0;
}

.markdown-body :deep(li) {
  margin-bottom: 4px;
}

.markdown-body :deep(blockquote) {
  border-left: 3px solid var(--color-accent);
  margin: 8px 0;
  padding: 4px 12px;
  color: var(--color-text-secondary);
  background: var(--color-accent-light);
  border-radius: 0 6px 6px 0;
}

.markdown-body :deep(h3),
.markdown-body :deep(h4) {
  margin: 12px 0 6px;
}

.markdown-body :deep(a) {
  color: var(--color-accent);
  text-decoration: underline;
}

.markdown-body :deep(.kg-link) {
  color: var(--color-accent);
  font-weight: 500;
  cursor: pointer;
  border-bottom: 1.5px dashed var(--color-accent-light);
  transition: color 0.15s, border-color 0.15s, background 0.15s;
  padding: 0 2px;
  border-radius: 2px;
}

.markdown-body :deep(.kg-link:hover) {
  color: var(--color-accent-hover);
  border-bottom-color: var(--color-accent);
  background: var(--color-accent-light);
}

.markdown-body :deep(table) {
  border-collapse: collapse;
  margin: 10px 0;
  width: 100%;
  font-size: 13px;
}

.markdown-body :deep(th),
.markdown-body :deep(td) {
  border: 1px solid var(--color-border);
  padding: 6px 10px;
  text-align: left;
}

.markdown-body :deep(th) {
  background: var(--color-bg-surface);
  font-weight: 600;
}

/* 流式打字动画 */
.typing-indicator {
  display: flex;
  gap: 4px;
  padding: 4px 0;
}

.typing-indicator span {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--color-text-tertiary);
  animation: typingBounce 1.2s ease-in-out infinite;
}

.typing-indicator span:nth-child(2) {
  animation-delay: 0.2s;
}

.typing-indicator span:nth-child(3) {
  animation-delay: 0.4s;
}

@keyframes typingBounce {
  0%, 60%, 100% {
    transform: translateY(0);
    opacity: 0.4;
  }
  30% {
    transform: translateY(-6px);
    opacity: 1;
  }
}
</style>
