/**
 * markdown.js — 统一 Markdown 渲染入口
 *
 * 职责：
 *   - marked（GFM）Markdown 渲染
 *   - KaTeX 数学公式：$...$ 行内、$$...$$ 块级（含两侧无空格的写法），
 *     以及 \(...\) / \[...\]（见下方 latexKatex）
 *   - highlight.js 代码高亮（marked-highlight 官方扩展）
 *   - DOMPurify XSS 消毒（AI 回复 / 用户编辑内容直接 v-html，必须消毒）
 *
 * 用法：
 *   import { renderMarkdown } from '../utils/markdown.js'
 *   html = renderMarkdown(md)
 *
 * 说明：
 *   - 全局配置一次，所有组件共用同一套渲染管线，避免样式/行为不一致。
 *   - 代码高亮的配色主题在 `utils/mdTheme.js`（设置页可切换），本文件只负责出 HTML。
 */
import { marked } from 'marked'
import katex from 'katex'
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

// KaTeX 选项：throwOnError=false —— 流式渲染时公式可能不完整，禁止抛错中断（只标红）；
// strict=false —— 关掉「非标准 LaTeX 写法」的控制台告警（模型输出里很常见）。
const KATEX_OPTIONS = { throwOnError: false, strict: false, output: 'htmlAndMathml' }

/**
 * \(...\) 行内 / \[...\] 块级公式。
 * marked-katex-extension 只认 $ / $$，模型（及多数 LaTeX 习惯）却常直接吐圆括号/方括号分隔符，
 * 少了这条就原样显示成 \(x^2\)。做成 marked 扩展而非字符串预处理 —— 代码块与行内代码由 marked
 * 自己优先词法化，不会误伤里面的反斜杠。
 */
const LATEX_DELIMITERS = [
  { re: /^\\\(([\s\S]+?)\\\)/, displayMode: false },
  { re: /^\\\[([\s\S]+?)\\\]/, displayMode: true },
]

function latexKatex() {
  return {
    extensions: [
      {
        name: 'latexKatex',
        level: 'inline',
        start: (src) => {
          const i = src.search(/\\\(|\\\[/)
          return i === -1 ? undefined : i
        },
        tokenizer(src) {
          for (const { re, displayMode } of LATEX_DELIMITERS) {
            const m = src.match(re)
            if (m) return { type: 'latexKatex', raw: m[0], text: m[1].trim(), displayMode }
          }
        },
        renderer(token) {
          return katex.renderToString(token.text, {
            ...KATEX_OPTIONS,
            displayMode: token.displayMode,
          })
        },
      },
    ],
  }
}

marked.use(
  // KaTeX 数学公式扩展：$...$ / $$...$$
  // nonStandard=true 允许 "$x^2$" 这类两侧无空格的写法（中文行文常见），代价是
  // "$5 …… $10" 这种成对美元符号会被当成公式 —— 本产品面向数学学习，取舍后开启。
  katexExtension({ ...KATEX_OPTIONS, nonStandard: true }),
  // \(...\) / \[...\]
  latexKatex(),
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
  'mphantom', 'mpadded', 'mfenced', 'maction', 'merror',
  'mmultiscripts', 'mprescripts', 'none', 'mglyph',
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
