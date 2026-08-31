/**
 * markdown.js — 统一 Markdown 渲染入口
 *
 * 职责：
 *   - marked（GFM）Markdown 渲染
 *   - KaTeX 数学公式：$...$ 行内、$$...$$ 块级，以及 \(...\) / \[...\]
 *   - highlight.js 代码高亮（marked-highlight 官方扩展）
 *   - DOMPurify XSS 消毒（AI 回复 / 用户编辑内容直接 v-html，必须消毒）
 *
 * 用法：
 *   import { renderMarkdown } from '../utils/markdown.js'
 *   html = renderMarkdown(md)
 *
 * 说明：
 *   - 全局配置一次，所有组件共用同一套渲染管线，避免样式/行为不一致。
 *   - throwOnError: false —— 流式渲染时公式可能不完整，禁止 KaTeX 抛错中断。
 */
import { marked } from 'marked'
import katexExtension from 'marked-katex-extension'
import { markedHighlight } from 'marked-highlight'
// highlight.js 按需注册常用语言（全量导入会打进 100+ 种语言，体积大）
import hljs from 'highlight.js/lib/core'
import javascript from 'highlight.js/lib/languages/javascript'
import typescript from 'highlight.js/lib/languages/typescript'
import python from 'highlight.js/lib/languages/python'
import java from 'highlight.js/lib/languages/java'
import cpp from 'highlight.js/lib/languages/cpp'
import c from 'highlight.js/lib/languages/c'
import go from 'highlight.js/lib/languages/go'
import rust from 'highlight.js/lib/languages/rust'
import sql from 'highlight.js/lib/languages/sql'
import json from 'highlight.js/lib/languages/json'
import xml from 'highlight.js/lib/languages/xml'
import css from 'highlight.js/lib/languages/css'
import bash from 'highlight.js/lib/languages/bash'
import markdown from 'highlight.js/lib/languages/markdown'
import plaintext from 'highlight.js/lib/languages/plaintext'
import DOMPurify from 'dompurify'

for (const [name, lang] of Object.entries({
  javascript, typescript, python, java, cpp, c, go, rust,
  sql, json, xml, css, bash, markdown, plaintext,
})) {
  hljs.registerLanguage(name, lang)
}

marked.use(
  // KaTeX 数学公式扩展
  katexExtension({
    throwOnError: false,
    output: 'htmlAndMathml',
  }),
  // 代码高亮扩展
  markedHighlight({
    langPrefix: 'hljs language-',
    highlight(code, lang) {
      const language = hljs.getLanguage(lang) ? lang : 'plaintext'
      return hljs.highlight(code, { language }).value
    },
  }),
)

// KaTeX 输出的 MathML 标签需显式放行（DOMPurify 默认会剥离 <math> 等）
const KATEX_MATHML_TAGS = [
  'math', 'annotation', 'semantics',
  'mtext', 'mn', 'mo', 'mi', 'ms', 'mspace',
  'mover', 'munder', 'munderover', 'mstyle', 'menclose',
  'msup', 'msub', 'msubsup', 'mfrac', 'mroot', 'msqrt',
  'mtable', 'mtr', 'mtd', 'mrow',
]

/**
 * 渲染 Markdown 为安全的 HTML 字符串。
 *
 * @param {string} md         Markdown 原文（可为空）
 * @param {object} [options]  { breaks: true, gfm: true }
 * @returns {string}          已消毒的 HTML
 */
export function renderMarkdown(md, options = {}) {
  if (!md) return ''
  const { breaks = true, gfm = true } = options
  const html = marked.parse(md, { breaks, gfm })
  return DOMPurify.sanitize(html, {
    ADD_TAGS: KATEX_MATHML_TAGS,
  })
}

export { marked }
