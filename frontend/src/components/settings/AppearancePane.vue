<script setup>
/**
 * AppearancePane.vue — 设置 · 外观
 *
 * 主题切换（深色 / 浅色 / 跟随系统）+ Markdown 渲染主题（代码高亮配色）+ 渲染预览。
 * 均为本机偏好（localStorage），不落后端。
 */
import { useTheme } from '../../utils/theme'
import { useMdTheme, MD_THEMES } from '../../utils/mdTheme'
import { renderMarkdown } from '../../utils/markdown.js'

const { mode, setTheme } = useTheme()
const { mdTheme, setMdTheme } = useMdTheme()

// 渲染主题预览样例：主题只改代码高亮的配色，所以必须给一段带多类 token 的代码；
// 顺带放个行内公式，说明数学公式走 KaTeX、不受主题影响。静态内容，算一次即可。
const MD_PREVIEW_SAMPLE = [
  '#### 渲染预览',
  '',
  '行内代码 `npm run dev`，行内公式 $a^2 + b^2 = c^2$。',
  '',
  '```python',
  '# 计算两数之和',
  'def add(a, b):',
  '    return a + b  # -> 3',
  '```',
].join('\n')

const mdPreviewHtml = renderMarkdown(MD_PREVIEW_SAMPLE)
</script>

<template>
  <section class="sc-section">
    <h3>外观</h3>
    <div class="sc-row">
      <div class="sc-row-info">
        <div class="sc-row-title">主题</div>
        <div class="sc-row-desc">切换深色 / 浅色 / 跟随系统，适配不同环境</div>
      </div>
      <div class="theme-toggle-group">
        <button
          class="theme-option"
          :class="{ active: mode === 'dark' }"
          @click="setTheme('dark')"
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
          </svg>
          深色
        </button>
        <button
          class="theme-option"
          :class="{ active: mode === 'light' }"
          @click="setTheme('light')"
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="12" cy="12" r="4" />
            <line x1="12" y1="2" x2="12" y2="4" />
            <line x1="12" y1="20" x2="12" y2="22" />
            <line x1="4.93" y1="4.93" x2="6.34" y2="6.34" />
            <line x1="17.66" y1="17.66" x2="19.07" y2="19.07" />
            <line x1="2" y1="12" x2="4" y2="12" />
            <line x1="20" y1="12" x2="22" y2="12" />
            <line x1="4.93" y1="19.07" x2="6.34" y2="17.66" />
            <line x1="17.66" y1="6.34" x2="19.07" y2="4.93" />
          </svg>
          浅色
        </button>
        <button
          class="theme-option"
          :class="{ active: mode === 'system' }"
          @click="setTheme('system')"
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <rect x="2" y="3" width="20" height="14" rx="2" />
            <line x1="8" y1="21" x2="16" y2="21" />
            <line x1="12" y1="17" x2="12" y2="21" />
          </svg>
          跟随系统
        </button>
      </div>
    </div>

    <div class="sc-row">
      <div class="sc-row-info">
        <div class="sc-row-title">Markdown 渲染主题</div>
        <div class="sc-row-desc">代码高亮配色方案；数学公式由 KaTeX 渲染，不受此影响</div>
      </div>
      <select
        class="md-theme-select"
        :value="mdTheme"
        @change="setMdTheme($event.target.value)"
      >
        <option v-for="t in MD_THEMES" :key="t.value" :value="t.value">{{ t.label }}</option>
      </select>
    </div>

    <!-- 预览：配色由 CSS 决定，切换后立即变化，无需重渲染 -->
    <div class="md-preview markdown-body" v-html="mdPreviewHtml"></div>
  </section>
</template>

<style scoped>
/* Markdown 渲染主题下拉：让原生弹层在深色下也是深色 */
.md-theme-select {
  flex-shrink: 0;
  padding: 7px 12px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: var(--color-bg-secondary);
  color: var(--color-text-primary);
  font-size: 13px;
  cursor: pointer;
}
html.dark .md-theme-select {
  color-scheme: dark;
}

/* Markdown 渲染主题预览：代码块观感对齐对话框（.markdown-body 全局规则只管 token 配色，
   容器底色在组件里，所以这里补一份）。选中具名主题时，mdTheme.js 那条带
   html[data-md-theme] 前缀的归一化规则特异性更高，会自动接管 <pre>/<code>。 */
.md-preview {
  margin-top: 10px;
  padding: 14px 18px;
  background: var(--color-bg-secondary);
  border: 1px solid var(--color-border);
  border-radius: 10px;
  font-size: 13px;
  line-height: 1.6;
  color: var(--color-text-primary);
}
.md-preview :deep(h4) {
  margin: 0 0 8px;
  font-size: 14px;
  font-weight: 600;
}
.md-preview :deep(p) {
  margin: 0 0 8px;
}
.md-preview :deep(p:last-child) {
  margin-bottom: 0;
}
.md-preview :deep(code) {
  background: var(--color-bg-hover);
  padding: 2px 6px;
  border-radius: 4px;
  font-family: 'Consolas', 'Courier New', monospace;
}
.md-preview :deep(pre) {
  background: var(--color-bg-tertiary);
  color: var(--color-text-primary);
  padding: 14px 16px;
  border-radius: 10px;
  overflow-x: auto;
  margin: 0;
}
.md-preview :deep(pre code) {
  background: none;
  padding: 0;
}
</style>
