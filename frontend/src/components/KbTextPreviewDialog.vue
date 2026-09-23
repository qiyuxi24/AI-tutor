<script setup>
/**
 * KbTextPreviewDialog.vue — 知识库文件正文预览弹窗（只读）
 *
 * 用途：知识库里选中一个文件 → 查看它入库时解析出的正文（采集到的网页原文 /
 *      上传文档的解析文本都走这里），可核对采到的到底是什么，再决定怎么用。
 *
 * 去耦：纯展示组件，不调 API、不改数据；正文由父组件（KbPanel）拉取后传入。
 *
 * Props:
 *   visible     Boolean — 弹窗显示态（v-model:visible）
 *   name        String  — 文件名（标题）
 *   markdown    String  — 正文原文（Markdown / 纯文本）
 *   chars       Number  — 本次返回的字符数
 *   totalChars  Number  — 正文总字符数（大于 chars 说明被截断）
 *   truncated   Boolean — 是否被截断
 *   loading     Boolean — 正在读取
 *
 * Emits:
 *   update:visible — 关闭弹窗
 *
 * 安全：正文是不可信输入（网页抓取 / 用户上传），**必须**走 renderMarkdown
 *      （内含 DOMPurify 消毒）后 v-html，禁止绕过。
 * 链接：正文里的 `<a href>` 由本组件拦截，统一**新标签页**打开（见 openInNewTab），
 *      避免把整个应用页面顶掉；不依赖 target 属性（DOMPurify 默认不放行它）。
 */
import { computed } from 'vue'
import { renderMarkdown } from '../utils/markdown.js'

const props = defineProps({
  visible: { type: Boolean, default: false },
  name: { type: String, default: '' },
  markdown: { type: String, default: '' },
  chars: { type: Number, default: 0 },
  totalChars: { type: Number, default: 0 },
  truncated: { type: Boolean, default: false },
  loading: { type: Boolean, default: false },
})

const emit = defineEmits(['update:visible'])

const html = computed(() => renderMarkdown(props.markdown))

/**
 * 正文里的链接一律**新标签页**打开：
 * 用 `window.open` 而不是靠 `target="_blank"`——DOMPurify 默认允许 `href`/`rel` 但**不放行
 * `target`**（实测当前版本），写了也会被消毒掉；且不走 `utils/markdown.js`（那是对话/图谱/
 * 画像三处共用管线，改它影响面太大）。这里只拦本组件容器内的 `<a>`，零共享改动。
 */
function openInNewTab(event) {
  const link = event.target?.closest?.('a[href]')
  if (!link) return
  event.preventDefault()
  window.open(link.getAttribute('href'), '_blank', 'noopener,noreferrer')
}
</script>

<template>
  <Transition name="modal">
    <div v-if="visible" class="modal-overlay" @click.self="emit('update:visible', false)">
      <div class="modal-panel">
        <div class="modal-header">
          <h2 class="modal-title">{{ name || '正文预览' }}</h2>
          <div class="modal-actions">
            <span v-if="!loading && totalChars" class="chars-info">
              {{ chars.toLocaleString() }} / {{ totalChars.toLocaleString() }} 字符
            </span>
            <button class="action-btn close-btn" title="关闭" @click="emit('update:visible', false)">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                   stroke-width="2.5" stroke-linecap="round">
                <line x1="18" y1="6" x2="6" y2="18" />
                <line x1="6" y1="6" x2="18" y2="18" />
              </svg>
            </button>
          </div>
        </div>

        <div class="modal-body">
          <p v-if="loading" class="state-text">正在读取正文...</p>
          <template v-else>
            <p v-if="truncated" class="truncate-tip">
              正文过长，仅显示前 {{ chars.toLocaleString() }} 字（共 {{ totalChars.toLocaleString() }} 字）。
            </p>
            <div class="markdown-body" v-html="html" @click="openInNewTab"></div>
          </template>
        </div>
      </div>
    </div>
  </Transition>
</template>

<style scoped>
.modal-overlay {
  position: fixed;
  inset: 0;
  z-index: 1000;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
}

.modal-panel {
  width: min(760px, 92vw);
  max-height: 85vh;
  background: var(--color-bg-primary);
  border-radius: 14px;
  display: flex;
  flex-direction: column;
  box-shadow: var(--shadow-popup);
  overflow: hidden;
}

.modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 16px 20px;
  border-bottom: 1px solid var(--color-border);
  flex-shrink: 0;
}

.modal-title {
  font-size: 16px;
  font-weight: 700;
  color: var(--color-text-primary);
  margin: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.modal-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}

.chars-info {
  font-size: 12px;
  color: var(--color-text-tertiary);
}

.action-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: none;
  background: transparent;
  color: var(--color-text-secondary);
  cursor: pointer;
  border-radius: 6px;
  padding: 4px;
}

.action-btn:hover {
  background: var(--color-bg-hover);
  color: var(--color-text-primary);
}

.modal-body {
  flex: 1;
  overflow-y: auto;
  padding: 16px 20px 24px;
}

.state-text,
.truncate-tip {
  margin: 0 0 12px;
  font-size: 12px;
  color: var(--color-text-tertiary);
  line-height: 1.6;
}
</style>
