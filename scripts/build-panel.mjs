#!/usr/bin/env node
/**
 * scripts/build-panel.mjs —— 把 frontend/ 构建成插件静态面板（plugins/dsh-ai-tutor/panel/）
 *
 * 干什么（计划 docs/架构/DSH插件MVP_施工计划.md 本轮任务 task-5）：
 *   1. 在 frontend/ 跑生产构建 npm run build（Vite，默认 base='/'）
 *   2. 清空 plugins/dsh-ai-tutor/panel/，把 frontend/dist/ 整目录复制过去
 *   3. 自检产物：index.html 不是开发态、index.html 引用的每个文件都真实存在、
 *      产物里没有硬编码的绝对后端地址
 *   4. 打印文件数 / 总字节 / 最大的 5 个文件；任何一步失败 → 非 0 退出
 *
 * 为什么这么写：
 *   - 零第三方依赖（与 scripts/build-plugin.mjs 同一纪律）：只用 node: 内置模块。
 *   - 面板由插件 Python 服务在根路径 / 托管（panel_server.py，iframe 同源打开），
 *     所以 Vite 默认的 base='/' 就是对的：产物里 /assets/... 是绝对路径，在
 *     http://127.0.0.1:<随机端口>/ 下正好命中。不需要改 frontend/vite.config.js。
 *   - 前端 API 层用相对路径 /api/...（axios 不设 baseURL）、路由是 hash 模式
 *     （createWebHashHistory），所以整包静态化不需要注入任何后端地址。
 *   - 构建目录整删重建而不是增量覆盖：Vite 的文件名带内容 hash，换一轮后旧文件
 *     若残留，panel/ 会无限膨胀、也难判断哪份是当前产物。
 *   - --skip-npm 复用已有 dist：只改复制/自检逻辑时不用再等一次完整前端构建。
 *
 * 用法：
 *   node scripts/build-panel.mjs              # 完整构建（npm run build + 复制 + 自检）
 *   node scripts/build-panel.mjs --skip-npm   # 复用已有 frontend/dist（不跑 npm）
 *   node scripts/build-panel.mjs -h
 *
 * 产物：plugins/dsh-ai-tutor/panel/（不进 git，删目录重跑即可回退）
 */
import { spawnSync } from 'node:child_process'
import {
  cpSync,
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  rmSync,
  statSync,
} from 'node:fs'
import { basename, dirname, extname, join, relative, resolve, sep } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const REPO_ROOT = resolve(HERE, '..')
const FRONTEND_DIR = join(REPO_ROOT, 'frontend')
const DIST_DIR = join(FRONTEND_DIR, 'dist')
const PLUGIN_DIR = join(REPO_ROOT, 'plugins', 'dsh-ai-tutor')
const PANEL_DIR = join(PLUGIN_DIR, 'panel')

/** 未压缩产物上限（npm 包 tgz 限额 100MB 下的经验红线）。超了只告警，不阻断构建。 */
const PANEL_SIZE_WARN_BYTES = 15 * 1024 * 1024

/**
 * 产物里不该出现的绝对后端地址：同源托管（/api 相对路径）一旦被写成绝对地址，
 * 换随机端口就坏。前端源码里确实一个都没有（2026-10-27 grep 确认），
 * 所以这里命中就是回归，直接失败比上线后现象诡异好。
 */
const FORBIDDEN_ADDRESSES = Object.freeze([
  '127.0.0.1:8000',
  'localhost:8000',
  '127.0.0.1:5173',
  'localhost:5173',
])

/** 开发态标记：出现即说明复制错了东西（dev server 的 HTML / 未构建的 src 入口）。 */
const DEV_MARKERS = Object.freeze(['/src/main.js', '@vite/client'])

/** 扫描文本类产物（字体/图片是二进制，不读）。 */
const TEXT_EXTS = Object.freeze(['.html', '.js', '.css', '.json', '.svg', '.map'])

const TOTAL_STEPS = 4

class BuildError extends Error {}

// ────────────────────────────── 小工具 ──────────────────────────────

function log(msg = '') {
  process.stdout.write(msg + '\n')
}

function step(n, title) {
  log('')
  log('─'.repeat(72))
  log('[' + n + '/' + TOTAL_STEPS + '] ' + title)
  log('─'.repeat(72))
}

function fail(message) {
  throw new BuildError(message)
}

function isWindows() {
  return process.platform === 'win32'
}

function formatMb(bytes) {
  return (bytes / 1024 / 1024).toFixed(1) + ' MB'
}

function toPosix(p) {
  return p.split(sep).join('/')
}

/** 递归遍历文件（跳过符号链接；构建产物里不应有链接）。 */
function* walkFiles(dir) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name)
    if (entry.isDirectory()) yield* walkFiles(full)
    else if (entry.isFile()) yield full
  }
}

// ────────────────────────────── 参数 ──────────────────────────────

function parseArgs(argv) {
  const opts = { skipNpm: false, help: false }
  for (const arg of argv) {
    switch (arg) {
      case '--skip-npm':
        opts.skipNpm = true
        break
      case '-h':
      case '--help':
        opts.help = true
        break
      default:
        fail('未知参数：' + arg + '（--help 看用法）')
    }
  }
  return opts
}

function printHelp() {
  log('用法：node scripts/build-panel.mjs [options]')
  log('')
  log('  --skip-npm   复用已有 frontend/dist（不跑 npm run build）')
  log('  -h, --help   显示本帮助')
  log('')
  log('产物：' + toPosix(relative(REPO_ROOT, PANEL_DIR)) + '/（不进 git；index.html + assets/）')
}

// ────────────────────────────── 1. 前端构建 ──────────────────────────────

function runNpmBuild() {
  if (!existsSync(join(FRONTEND_DIR, 'node_modules', 'vite', 'package.json'))) {
    fail('frontend/node_modules 不存在或不完整：本脚本不联网安装，请先在 frontend/ 装好依赖（npm ci）')
  }
  const env = { ...process.env, NODE_ENV: 'production', NO_PROXY: '*', no_proxy: '*' }
  let cmd
  let args
  if (isWindows()) {
    // Node >= 18.20 起 spawn('npm.cmd', args) 不带 shell 会 EINVAL；而 shell:true + args
    // 会触发 DEP0190 并需要自己转义。用 cmd.exe 显式包一层，参数全静态、无转义问题。
    cmd = process.env.ComSpec || 'cmd.exe'
    args = ['/d', '/s', '/c', 'npm.cmd run build']
  } else {
    cmd = 'npm'
    args = ['run', 'build']
  }
  log('$ npm run build   (cwd=' + toPosix(relative(REPO_ROOT, FRONTEND_DIR)) + ')')
  const res = spawnSync(cmd, args, { cwd: FRONTEND_DIR, env, stdio: 'inherit', windowsHide: true })
  if (res.error) fail('无法执行 npm：' + res.error.message)
  if (res.status !== 0) fail('npm run build 失败（退出码 ' + res.status + '）')
  if (!existsSync(join(DIST_DIR, 'index.html'))) {
    fail('npm run build 成功但没有生成 ' + toPosix(relative(REPO_ROOT, DIST_DIR)) + '/index.html')
  }
}

// ────────────────────────────── 2. 复制 dist → panel ──────────────────────────────

function copyDistToPanel() {
  const distIndex = join(DIST_DIR, 'index.html')
  if (!existsSync(distIndex)) {
    fail('缺少构建产物 ' + toPosix(relative(REPO_ROOT, DIST_DIR)) + '/index.html —— 先跑一次不带 --skip-npm 的构建')
  }
  // 下面要 rm -rf PANEL_DIR：路径常量被误改时先拦住，别删到 plugins/dsh-ai-tutor/ 别的产物。
  if (resolve(PANEL_DIR) !== resolve(join(PLUGIN_DIR, 'panel')) || basename(PANEL_DIR) !== 'panel') {
    fail('panel 目录常量不自洽（' + PANEL_DIR + '），拒绝清空')
  }
  rmSync(PANEL_DIR, { recursive: true, force: true })
  mkdirSync(PANEL_DIR, { recursive: true })
  cpSync(DIST_DIR, PANEL_DIR, { recursive: true, dereference: true })
  if (!existsSync(join(PANEL_DIR, 'index.html'))) fail('复制后 panel/index.html 不存在')
  log('已清空并复制：' + toPosix(relative(REPO_ROOT, DIST_DIR)) + '/ → ' + toPosix(relative(REPO_ROOT, PANEL_DIR)) + '/')
}

// ────────────────────────────── 3. 产物自检 ──────────────────────────────

function verifyPanel() {
  const indexPath = join(PANEL_DIR, 'index.html')
  if (!existsSync(indexPath)) fail('panel/index.html 不存在')
  const html = readFileSync(indexPath, 'utf8')

  for (const marker of DEV_MARKERS) {
    if (html.includes(marker)) {
      fail('panel/index.html 含开发态标记 ' + marker + ' —— 复制到的是 dev 产物，不是 Vite 生产构建')
    }
  }

  // 1) index.html 里的同源引用必须真实存在（panel_server 按根路径 / 托管，绝对路径直接命中）。
  const refs = [...html.matchAll(/(?:src|href)="([^"]+)"/g)].map((m) => m[1])
  const assetRefs = []
  const missing = []
  for (const ref of refs) {
    if (/^https?:\/\//.test(ref) || ref.startsWith('data:')) continue
    if (!ref.startsWith('/')) {
      missing.push(ref + '（相对路径：hash 路由下会按当前 #/xxx 解析，必然 404）')
      continue
    }
    const target = join(PANEL_DIR, ref.slice(1))
    if (!existsSync(target)) missing.push(ref)
    else if (ref.startsWith('/assets/')) assetRefs.push(ref)
  }
  if (missing.length) fail('index.html 引用了产物里不存在的文件：\n  ' + missing.join('\n  '))
  if (assetRefs.length === 0) fail('index.html 没有引用任何 /assets/... —— 看起来不是 Vite 构建产物')
  log('index.html 同源引用全部命中：' + assetRefs.length + ' 个 /assets/... + ' + (refs.length - assetRefs.length) + ' 个其它引用')

  // 2) 不许硬编码绝对后端地址（同源相对路径才是契约）。
  const hits = []
  for (const file of walkFiles(PANEL_DIR)) {
    if (!TEXT_EXTS.includes(extname(file).toLowerCase())) continue
    const text = readFileSync(file, 'utf8')
    const found = FORBIDDEN_ADDRESSES.filter((needle) => text.includes(needle))
    if (found.length) hits.push(toPosix(relative(PANEL_DIR, file)) + ' → ' + found.join(', '))
  }
  if (hits.length) {
    fail('panel 产物里有硬编码的绝对后端地址（应走同源相对路径 /api/...）：\n  ' + hits.join('\n  '))
  }
  log('未发现硬编码后端地址（扫描 ' + FORBIDDEN_ADDRESSES.join(' / ') + '）')
  return { refs: refs.length, assets: assetRefs.length }
}

// ────────────────────────────── 4. 统计 ──────────────────────────────

function printStats() {
  const entries = []
  let total = 0
  for (const file of walkFiles(PANEL_DIR)) {
    const size = statSync(file).size
    total += size
    entries.push({ file, size })
  }
  entries.sort((a, b) => b.size - a.size)

  log('panel 目录：' + toPosix(relative(REPO_ROOT, PANEL_DIR)) + '/')
  log('文件数：' + entries.length)
  log('总字节：' + total + '（' + formatMb(total) + '）')
  log('最大的 5 个文件：')
  for (const entry of entries.slice(0, 5)) {
    log('  ' + (entry.size / 1024).toFixed(1).padStart(9) + ' KB  ' + toPosix(relative(PANEL_DIR, entry.file)))
  }
  if (total > PANEL_SIZE_WARN_BYTES) {
    log('⚠ 体积 ' + formatMb(total) + ' 超过 ' + PANEL_SIZE_WARN_BYTES / 1024 / 1024 + ' MB 预算（先看是不是带了 sourcemap / 旧 hash 残留）')
  } else {
    log('✓ 体积 ' + formatMb(total) + ' ≤ ' + PANEL_SIZE_WARN_BYTES / 1024 / 1024 + ' MB 预算')
  }
  return { files: entries.length, bytes: total }
}

// ────────────────────────────── main ──────────────────────────────

function main() {
  const opts = parseArgs(process.argv.slice(2))
  if (opts.help) {
    printHelp()
    return
  }
  const started = Date.now()

  step(1, '前端构建')
  if (opts.skipNpm) {
    const distIndex = join(DIST_DIR, 'index.html')
    if (!existsSync(distIndex)) {
      fail('--skip-npm 但没有可复用的 ' + toPosix(relative(REPO_ROOT, DIST_DIR)) + '/index.html')
    }
    log('--skip-npm：复用 ' + toPosix(relative(REPO_ROOT, DIST_DIR)) + '（index.html mtime ' + statSync(distIndex).mtime.toISOString() + '，可能已过期）')
  } else {
    runNpmBuild()
  }

  step(2, '复制产物')
  copyDistToPanel()

  step(3, '产物自检')
  const checked = verifyPanel()

  step(4, '统计')
  const stats = printStats()

  log('')
  log('─'.repeat(72))
  log('✓ panel 构建完成：' + stats.files + ' 个文件 / ' + formatMb(stats.bytes) + ' / ' + checked.assets + ' 个 hashed 资源，用时 ' + ((Date.now() - started) / 1000).toFixed(1) + 's')
  log('  产物：' + toPosix(relative(REPO_ROOT, PANEL_DIR)) + '/（不进 git；panel_server.py 经 AI_TUTOR_PANEL_DIR 指向它）')
  log('─'.repeat(72))
}

try {
  main()
} catch (err) {
  log('')
  if (err instanceof BuildError) {
    log('✗ 构建失败：' + err.message)
  } else {
    log('✗ 构建失败（未预期错误）：')
    log(err && err.stack ? err.stack : String(err))
  }
  process.exit(1)
}
