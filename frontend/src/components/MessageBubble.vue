<script setup>
import { computed, ref, watch, nextTick } from 'vue'
import { renderMarkdown } from '../utils/markdown.js'

const props = defineProps({
  message: { type: Object, required: true },
  knowledgeNodes: { type: Array, default: () => [] },
})

const emit = defineEmits(['navigate-to-node'])

const isUser = computed(() => props.message.role === 'user')
const isStreaming = computed(() => !isUser.value && !props.message.content
  && !(props.message.tools?.length) && !(props.message.thinking?.length))
const bubbleRef = ref(null)

// 工具/思考状态
const hasTools = computed(() => !isUser.value && props.message.tools?.length > 0)
const hasThinking = computed(() => !isUser.value && props.message.thinking?.length > 0)
const showThinking = ref(false)

// 工具图标映射
const toolIcons = {
  add_knowledge_node: '🔗',
  update_node_content: '✏️',
  update_mastery: '📊',
  add_edge: '🔗',
  delete_node: '🗑️',
  update_user_profile: '👤',
  fetch_webpage: '🌐',
  rag_search: '🔍',
  quiz_generate: '📝',
  grade_answer: '✅',
  mcp__websearch__web_search: '🔎',
}

function getToolIcon(name) {
  return toolIcons[name] || '🔧'
}

function getToolDisplayName(name) {
  const names = {
    add_knowledge_node: '添加节点',
    update_node_content: '更新内容',
    update_mastery: '更新掌握度',
    add_edge: '添加关联',
    delete_node: '删除节点',
    update_user_profile: '更新画像',
    fetch_webpage: '抓取网页',
    rag_search: '知识检索',
    quiz_generate: '出题检验',
    grade_answer: '判分',
    mcp__websearch__web_search: '联网搜索',
  }
  return names[name] || name
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
    <div class="avatar" :class="{ 'user-avatar': isUser }">
      {{ isUser ? '👤' : '🤖' }}
    </div>
    <div class="bubble" :class="{ 'user-bubble': isUser, 'ai-bubble': !isUser }" ref="bubbleRef">
      <div v-if="isUser" class="text">{{ message.content }}</div>
      <template v-else>
        <!-- 工具活动区 -->
        <div v-if="hasTools" class="tool-area">
          <div
            v-for="(tool, idx) in message.tools"
            :key="idx"
            class="tool-chip"
            :class="{
              'tool-running': tool.status === 'running',
              'tool-done': tool.status === 'done',
              'tool-error': tool.status === 'error',
            }"
          >
            <span class="tool-icon">{{ getToolIcon(tool.tool) }}</span>
            <span class="tool-name">{{ getToolDisplayName(tool.tool) }}</span>
            <span v-if="tool.status === 'running'" class="tool-spinner"></span>
            <span v-else-if="tool.status === 'done'" class="tool-status">✓ {{ tool.result?.duration_ms }}ms</span>
            <span v-else-if="tool.status === 'error'" class="tool-status tool-status-error">✗</span>
          </div>
        </div>

        <!-- 思考折叠面板 -->
        <div v-if="hasThinking" class="thinking-panel">
          <button class="thinking-toggle" @click="showThinking = !showThinking">
            <span class="thinking-icon">💭</span>
            <span class="thinking-label">AI 思考过程</span>
            <span class="thinking-count">{{ message.thinking.length }} 段</span>
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

.bubble {
  padding: 12px 16px;
  border-radius: 18px;
  line-height: 1.6;
  font-size: 14px;
  word-break: break-word;
}

.user-bubble {
  background: var(--color-chat-bubble-user, var(--color-accent));
  color: var(--color-text-inverse);
  border-bottom-right-radius: 4px;
}

.ai-bubble {
  background: var(--color-bg-surface);
  color: var(--color-text-primary);
  border-bottom-left-radius: 4px;
}

.text {
  white-space: pre-wrap;
}

/* ─── 工具活动区 ─── */
.tool-area {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 8px;
}

.tool-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 10px;
  border-radius: 12px;
  font-size: 12px;
  font-weight: 500;
  transition: all 0.2s ease;
  animation: chipFadeIn 0.25s ease;
}

@keyframes chipFadeIn {
  from { opacity: 0; transform: scale(0.85); }
  to   { opacity: 1; transform: scale(1); }
}

.tool-running {
  background: var(--color-accent-light);
  color: var(--color-accent);
  border: 1px solid var(--color-accent);
}

.tool-done {
  background: rgba(16, 185, 129, 0.1);
  color: rgb(5, 150, 105);
  border: 1px solid rgba(16, 185, 129, 0.3);
}

.tool-error {
  background: rgba(239, 68, 68, 0.1);
  color: rgb(220, 38, 38);
  border: 1px solid rgba(239, 68, 68, 0.3);
}

.tool-icon {
  font-size: 13px;
}

.tool-name {
  white-space: nowrap;
}

.tool-status {
  font-size: 10px;
  opacity: 0.8;
}

.tool-status-error {
  font-weight: 700;
}

.tool-spinner {
  width: 10px;
  height: 10px;
  border: 1.5px solid currentColor;
  border-top-color: transparent;
  border-radius: 50%;
  animation: spin 0.6s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

/* ─── 思考折叠面板 ─── */
.thinking-panel {
  margin-bottom: 8px;
  border-radius: 8px;
  background: var(--color-bg-tertiary);
  overflow: hidden;
}

.thinking-toggle {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 6px 10px;
  background: none;
  border: none;
  cursor: pointer;
  font-size: 12px;
  color: var(--color-text-secondary);
  transition: background 0.15s;
}

.thinking-toggle:hover {
  background: var(--color-bg-hover);
}

.thinking-icon {
  font-size: 13px;
}

.thinking-label {
  font-weight: 500;
}

.thinking-count {
  font-size: 10px;
  opacity: 0.6;
}

.thinking-arrow {
  margin-left: auto;
  transition: transform 0.2s;
  font-size: 10px;
}

.thinking-arrow.expanded {
  transform: rotate(180deg);
}

.thinking-content {
  padding: 8px 12px;
  border-top: 1px solid var(--color-border);
}

.thinking-block {
  font-size: 12px;
  color: var(--color-text-secondary);
  line-height: 1.5;
  padding: 4px 0;
  border-left: 2px solid var(--color-accent-light);
  padding-left: 8px;
  margin-bottom: 4px;
  font-style: italic;
}

.thinking-block:last-child {
  margin-bottom: 0;
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
