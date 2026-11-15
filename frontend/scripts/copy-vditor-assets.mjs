/**
 * copy-vditor-assets.mjs — 把 Vditor 的**运行时资源**从 node_modules 拷到 public/vditor/
 *
 * 为什么必须自托管：Vditor 的 Markdown 引擎（Lute，3.6MB）不是 npm 依赖的一部分，
 * 它默认用 `<script src="https://unpkg.com/vditor@<ver>/dist/js/lute/lute.min.js">`
 * 从 CDN 动态加载（`Constants.CDN` 硬编码在 dist/index.js 里）。本项目是本地/内网
 * 部署的教学工具，离线或访问不了 unpkg 时编辑器会**直接白屏**，所以把资源放到
 * 自己的静态目录，并把 `cdn` 选项指到 `/vditor`。
 *
 * 为什么用脚本拷而不是提交进 Git：这些文件是 vditor 包内容的子集（约 6.5MB，含字体），
 * 拷进仓库既臃肿又会在升级 vditor 时悄悄过期。脚本在 `npm run dev` / `npm run build`
 * 前自动跑（package.json 的 predev / prebuild），永远与安装的版本一致。
 *
 * 只拷必需的五类：
 *   js/lute          Markdown 引擎（**必需**，否则编辑器起不来）
 *   js/icons         工具栏图标
 *   js/i18n          界面文案（默认 lang=zh_CN，缺了工具栏全是 undefined）
 *   js/highlight.js  代码块高亮 + 主题
 *   js/katex         数学公式 + 字体
 *   css              content-theme（正文排版主题）
 *   images/emoji     emoji 面板
 * 其余（mermaid / mathjax / echarts / graphviz / abcjs / markmap / smiles，
 * 合计约 14MB）本项目用不到，故不拷。
 */
import { cp, mkdir, rm, stat } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = join(HERE, '..')
const SRC = join(ROOT, 'node_modules', 'vditor', 'dist')
const DEST = join(ROOT, 'public', 'vditor', 'dist')

/** 相对 dist/ 的必需子项（见文件头注释的理由） */
const ITEMS = [
  'js/lute',
  'js/icons',
  'js/i18n',
  'js/highlight.js',
  'js/katex',
  'css',
  'images/emoji',
]

async function exists(p) {
  try {
    await stat(p)
    return true
  } catch {
    return false
  }
}

async function main() {
  if (!(await exists(SRC))) {
    console.warn('[vditor] 未安装 vditor，跳过资源拷贝（npm i vditor 后重试）')
    return
  }
  // 整目录重建：升级 vditor 后旧图标/旧引擎不能残留，否则页面加载的是上一版
  await rm(join(ROOT, 'public', 'vditor'), { recursive: true, force: true })
  for (const item of ITEMS) {
    const from = join(SRC, item)
    if (!(await exists(from))) {
      console.warn(`[vditor] 资源缺失：${item}（vditor 版本变动？）`)
      continue
    }
    const to = join(DEST, item)
    await mkdir(dirname(to), { recursive: true })
    await cp(from, to, { recursive: true })
  }
  console.log(`[vditor] 运行时资源已就位：public/vditor/dist（${ITEMS.length} 项）`)
}

main().catch((e) => {
  console.error('[vditor] 资源拷贝失败：', e)
  process.exitCode = 1
})
