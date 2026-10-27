#!/usr/bin/env node
/**
 * npm pack 前置清理：删掉 runtime/ 与 engine/ 里的 __pycache__ / *.pyc。
 *
 * 为什么需要它：插件跑起来后任何 Python 子进程（MCP 网关、panel server、doctor 探测）
 * 只要没带 PYTHONDONTWRITEBYTECODE=1 就会把字节码写回安装目录 —— 构建机上的 .pyc
 * 换机器后既无效又白占十几 MB，还会把 tgz 顶到 npm 的 100MB 上限之上。
 * 挂在 package.json 的 prepack 上：`npm pack` / `npm publish` 都会自动先跑。
 *
 * 只删这两个目录下的字节码，不碰任何源码；失败不阻断打包（打印告警即可）。
 */
import { readdirSync, rmSync, statSync } from 'node:fs'
import { join } from 'node:path'

const ROOT = new URL('..', import.meta.url).pathname
// Windows 下 URL pathname 形如 /C:/... → 去掉前导斜杠
const PLUGIN_ROOT = process.platform === 'win32' && ROOT.startsWith('/') ? ROOT.slice(1) : ROOT
const TARGETS = ['runtime', 'engine']

let dirs = 0
let files = 0
let bytes = 0

function walk(dir) {
  let entries
  try {
    entries = readdirSync(dir, { withFileTypes: true })
  } catch {
    return
  }
  for (const entry of entries) {
    const full = join(dir, entry.name)
    if (entry.isDirectory()) {
      if (entry.name === '__pycache__') {
        let size = 0
        try {
          for (const f of readdirSync(full)) {
            try { size += statSync(join(full, f)).size } catch {}
          }
          rmSync(full, { recursive: true, force: true })
          dirs += 1
          bytes += size
        } catch {}
        continue
      }
      walk(full)
    } else if (entry.name.endsWith('.pyc')) {
      try {
        bytes += statSync(full).size
        rmSync(full, { force: true })
        files += 1
      } catch {}
    }
  }
}

for (const name of TARGETS) walk(join(PLUGIN_ROOT, name))

// 运行期才产生的目录（本机探测/doctor 会留下日志与临时数据根），一律不进包。
for (const rel of ['engine/logs', 'engine/data', 'runtime/logs', 'runtime/data']) {
  try {
    rmSync(join(PLUGIN_ROOT, rel), { recursive: true, force: true })
  } catch {}
}

const mb = (bytes / 1024 / 1024).toFixed(2)
console.log('[prepack] 清理字节码：' + dirs + ' 个 __pycache__、' + files + ' 个 .pyc，共释放 ' + mb + ' MB')
