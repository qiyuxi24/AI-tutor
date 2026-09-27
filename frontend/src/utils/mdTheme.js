/**
 * mdTheme.js — Markdown 代码渲染主题（设置页可切换，纯前端偏好）
 *
 * 主题样式复用 highlight.js 自带样式表（已在依赖里，零新增包）：构建期用 `?inline`
 * 读成 CSS 字符串，运行期只写进唯一一个 <style id="md-theme-style">，切换即整体替换
 * （不会像动态 import CSS 那样越切越多）。偏好存 localStorage，与账号无关。
 *
 * 为什么要给主题 CSS 加前缀（见 scopeCss）：应用默认配色写在 style.css 的
 * `.markdown-body .hljs-keyword`（特异性 0,2,0），各组件的 scoped 样式又给 <pre>/<code>
 * 定了颜色（0,2,2）。主题表里的裸 `.hljs-keyword`（0,1,0）根本压不过，所以统一加上
 * `html[data-md-theme] .markdown-body ` 前缀，抬到 (0,3,1)/(0,3,3) 才生效。
 * 选「跟随应用主题」= 不注入、不设属性 → 完全沿用 style.css 现有配色（随深/浅色走）。
 */
import { ref } from 'vue'

const STORAGE_KEY = 'ai_tutor_md_theme'
const STYLE_ID = 'md-theme-style'
const DEFAULT_THEME = 'default'
const PREFIX = 'html[data-md-theme] .markdown-body'

// key = highlight.js/styles/<key>.css
import cssGithub from 'highlight.js/styles/github.css?inline'
import cssGithubDark from 'highlight.js/styles/github-dark.css?inline'
import cssAtomOneLight from 'highlight.js/styles/atom-one-light.css?inline'
import cssAtomOneDark from 'highlight.js/styles/atom-one-dark.css?inline'
import cssMonokai from 'highlight.js/styles/monokai.css?inline'
import cssNord from 'highlight.js/styles/nord.css?inline'
import cssTokyoNightDark from 'highlight.js/styles/tokyo-night-dark.css?inline'
import cssRosePineDawn from 'highlight.js/styles/rose-pine-dawn.css?inline'
import cssVs2015 from 'highlight.js/styles/vs2015.css?inline'

const THEME_CSS = {
  github: cssGithub,
  'github-dark': cssGithubDark,
  'atom-one-light': cssAtomOneLight,
  'atom-one-dark': cssAtomOneDark,
  monokai: cssMonokai,
  nord: cssNord,
  'tokyo-night-dark': cssTokyoNightDark,
  'rose-pine-dawn': cssRosePineDawn,
  vs2015: cssVs2015,
}

/** 设置页可选项（顺序即展示顺序）；default = 跟随应用深/浅色 */
export const MD_THEMES = [
  { value: DEFAULT_THEME, label: '跟随应用主题' },
  { value: 'github', label: 'GitHub 浅色' },
  { value: 'github-dark', label: 'GitHub 深色' },
  { value: 'atom-one-light', label: 'Atom 浅色' },
  { value: 'atom-one-dark', label: 'Atom 深色' },
  { value: 'monokai', label: 'Monokai' },
  { value: 'tokyo-night-dark', label: 'Tokyo Night' },
  { value: 'nord', label: 'Nord' },
  { value: 'rose-pine-dawn', label: 'Rosé Pine Dawn' },
  { value: 'vs2015', label: 'VS 2015' },
]

/**
 * 给主题 CSS 的每条选择器加 PREFIX 前缀。
 * 前提：highlight.js 主题表都是「一层平铺的 选择器 + 声明」、不含 @rule（候选主题已逐一核对）。
 */
function scopeCss(css) {
  return css
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(^|\})([^{}]+)\{/g, (_, brace, sel) => brace +
      sel.split(',').map((s) => `${PREFIX} ${s.trim()}`).join(',') + '{')
}

// 让主题接管整个代码块外观：<pre> 退成透明容器，背景/圆角画在 <code class="hljs"> 上。
// 否则主题底色只铺在 code 上，会被 <pre> 的内边距顶出一圈应用底色。
const NORMALIZE = `
${PREFIX} pre { background: transparent; padding: 0; }
${PREFIX} pre code.hljs { padding: 14px 16px; border-radius: 10px; }
`

function applyTheme(value) {
  let el = document.getElementById(STYLE_ID)
  if (!el) {
    el = document.createElement('style')
    el.id = STYLE_ID
    document.head.appendChild(el)
  }
  const css = THEME_CSS[value]
  if (!css) {
    el.textContent = ''
    document.documentElement.removeAttribute('data-md-theme')
    return
  }
  el.textContent = scopeCss(css) + NORMALIZE
  document.documentElement.setAttribute('data-md-theme', value)
}

function loadTheme() {
  const saved = localStorage.getItem(STORAGE_KEY)
  return saved && THEME_CSS[saved] ? saved : DEFAULT_THEME
}

const mdTheme = ref(loadTheme())
applyTheme(mdTheme.value)

/** 切换渲染主题（非法值忽略）并持久化到本机浏览器 */
export function setMdTheme(value) {
  if (value !== DEFAULT_THEME && !THEME_CSS[value]) return
  mdTheme.value = value
  applyTheme(value)
  try {
    if (value === DEFAULT_THEME) localStorage.removeItem(STORAGE_KEY)
    else localStorage.setItem(STORAGE_KEY, value)
  } catch {
    // 隐私模式等场景写入失败：本次会话内仍生效
  }
}

export function useMdTheme() {
  return { mdTheme, MD_THEMES, setMdTheme }
}
