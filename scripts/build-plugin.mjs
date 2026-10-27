#!/usr/bin/env node
/**
 * scripts/build-plugin.mjs —— 组装 DSH 插件包（engine + 便携 runtime）
 *
 * 干什么（计划 `docs/架构/DSH插件MVP_施工计划.md` §P2 / §4 D5）：
 *   1. 清空构建产物 `plugins/dsh-ai-tutor/{engine,runtime}`
 *   2. engine：`backend/app/` 整包复制到 `plugins/dsh-ai-tutor/engine/app/`
 *      （只排除 `__pycache__` 与 `*.pyc`，其余一律复制，不裁剪 —— D2）
 *   3. runtime：用 uv 装 CPython 3.13，把它所在的**整个 uv 托管目录**
 *      （python.exe / python3XX.dll / Lib / DLLs / vcruntime，self-contained）复制到
 *      `plugins/dsh-ai-tutor/runtime/`；再 `uv pip install` 13 个锁定依赖进去。
 *      随后裁剪 runtime（标准库测试/GUI、__pycache__、三方 tests/静态库），删完必须重新校验。
 *      uv 不可用时回落到 python-build-standalone 的 install_only tarball 下载。
 *   4. 校验：cwd=`plugins/dsh-ai-tutor` 跑 `runtime/python.exe`，把 `engine` 加进
 *      sys.path 后 import `app.core.agent_tools.gateway` 并打印 `list_specs()` 的名字
 *      （D5/P1 锁定 3 个）。**校验失败 → 非 0 退出**（不带病产出包）。
 *   5. 写 `runtime/MANIFEST.json`：{pythonVersion, packages, builtAt, engineFiles,
 *      runtimeBytes, engineBytes}，并打印 engine / runtime 体积报告。
 *
 * 为什么这么写：
 *   - **零第三方依赖**（计划 §P3："客户端独立构建链"不做，构建脚本同理只用 `node:` 内置模块）。
 *   - **runtime 整目录复制**而不是只复制 python.exe：install_only 构建是自包含的，
 *     python.exe 靠同目录的 python3XX.dll / Lib / DLLs 才能起来；少复制一个目录就报
 *     "Python not found" 类错误，且要到干净机器上才暴露（开发机有系统 Python 兜不住）。
 *   - **校验必须真跑解释器**：C 扩展（numpy / cryptography / bcrypt / Pillow）与 Python
 *     小版本严格绑定，装完不 import 一次就等于没验证（计划 §7 风险表第二条）。
 *   - **构建产物不进 git**（§9 硬规则 7）：engine/ 与 runtime/ 已写进 .gitignore，
 *     删目录重跑即可回退。
 *
 * 用法：
 *   node scripts/build-plugin.mjs                       # 完整构建（uv 路径）
 *   node scripts/build-plugin.mjs --skip-runtime        # 只重建 engine（改了 backend/app 后刷新）
 *   node scripts/build-plugin.mjs --skip-engine --skip-deps   # 只重建 runtime 的 Python 本体
 *   node scripts/build-plugin.mjs --python-dir D:\\portable-python   # 用已有便携解释器目录（跳过下载）
 *   node scripts/build-plugin.mjs --python 3.13         # 指定 Python 小版本（默认 3.13）
 *   node scripts/build-plugin.mjs --skip-runtime --measure-tgz   # 复用 runtime，只测压缩包体积
 *
 * 环境变量：NO_PROXY/no_proxy 默认设成 "*" —— 本机注册表 ProxyEnable 残留会让
 *   pip/uv 报 ProxyError（AGENTS.md §1 "代理残留"）。
 */
import { spawnSync } from 'node:child_process'
import {
  cpSync,
  createWriteStream,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readdirSync,
  readFileSync,
  realpathSync,
  rmSync,
  statSync,
  writeFileSync,
} from 'node:fs'
import { tmpdir } from 'node:os'
import { basename, dirname, join, relative, resolve } from 'node:path'
import { Readable } from 'node:stream'
import { pipeline } from 'node:stream/promises'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const REPO_ROOT = resolve(HERE, '..')
const PLUGIN_DIR = join(REPO_ROOT, 'plugins', 'dsh-ai-tutor')
const ENGINE_DIR = join(PLUGIN_DIR, 'engine')
const RUNTIME_DIR = join(PLUGIN_DIR, 'runtime')
const SOURCE_APP_DIR = join(REPO_ROOT, 'backend', 'app')

/**
 * 便携 runtime 的锁定依赖（顺序无关）。
 *
 * P1 只读网关阶段是 13 个；P6「完整面板」起 runtime 还要把**整个 FastAPI 应用**
 * 跑起来（app.main + 文件上传解析 + 出题 + 对话），故补入下列 14 个：
 *   uvicorn（ASGI 服务器，base 版够用：无 WebSocket 路由，SSE 走 h11）/
 *   Jinja2 + PyYAML（提示词模板 + 配置）/ openai（LLM SDK）/
 *   PyMuPDF + python-docx + python-pptx（文档解析）/ tiktoken（token 预算）/
 *   ddgs + trafilatura + readability-lxml + zhconv（网页采集管线）/ yt-dlp（视频导入）/ segno（B 站二维码）。
 * **刻意不装**：rapidocr-onnxruntime + onnxruntime（扫描件 OCR 可选能力，体积大几十 MB；
 * 缺它时 parsers 自动降级，不阻断其它格式）。改这份清单要同步 PACKAGE_MODULES 与 README。
 */
export const PACKAGES = Object.freeze([
  'numpy',
  'Pillow',
  'cryptography',
  'python-jose',
  'bcrypt',
  'python-dotenv',
  'httpx',
  'whoosh',
  'fastapi',
  'python-multipart',
  'pygments',
  'mcp',
  'pydantic',
  // ── P6 完整面板（app.main 全量启动）所需 ──
  'uvicorn',
  'Jinja2',
  'PyYAML',
  'openai',
  'PyMuPDF',
  'python-docx',
  'python-pptx',
  'tiktoken',
  'ddgs',
  'trafilatura',
  'readability-lxml',
  'zhconv',
  'yt-dlp',
  'segno',
])

/**
 * 需要版本区间、不能直接写裸包名的依赖。
 *
 * 为什么单列一张表：openai 3.x 移除了本项目用到的废弃 API（主仓 requirements.txt
 * 就锁 <3），裸 'openai' 会装到不兼容的大版本 —— 而这种坏法要到真调 LLM 才暴露。
 * 安装时用本表的值，读版本/校验仍用 PACKAGES 里的分发名。
 */
const PACKAGE_SPECS = Object.freeze({
  openai: 'openai>=1.0.0,<3.0.0',
  // PyMuPDF 2.x 的 wheel 会连带 onnxruntime(43MB)+pymupdf4llm+pymupdf_layout，
  // 单它一家就 +140MB，tgz 直接顶破 npm 100MB；1.28 与 backend/venv 一致（实测 1.28.2）。
  PyMuPDF: 'PyMuPDF>=1.24,<2',
})

/** PACKAGES 的 import 名。校验时真 import 一次：C 扩展（numpy/Pillow/cryptography/bcrypt/
 *  PyMuPDF）与 Python 小版本严格绑定，装完不 import 等于没验证（计划 §7 风险表）。 */
const PACKAGE_MODULES = Object.freeze({
  numpy: 'numpy',
  Pillow: 'PIL',
  cryptography: 'cryptography',
  'python-jose': 'jose',
  bcrypt: 'bcrypt',
  'python-dotenv': 'dotenv',
  httpx: 'httpx',
  whoosh: 'whoosh',
  fastapi: 'fastapi',
  'python-multipart': 'multipart',
  pygments: 'pygments',
  mcp: 'mcp',
  pydantic: 'pydantic',
  // ── P6 完整面板 ──
  uvicorn: 'uvicorn',
  Jinja2: 'jinja2',
  PyYAML: 'yaml',
  openai: 'openai',
  PyMuPDF: 'fitz',
  'python-docx': 'docx',
  'python-pptx': 'pptx',
  tiktoken: 'tiktoken',
  ddgs: 'ddgs',
  trafilatura: 'trafilatura',
  'readability-lxml': 'readability',
  zhconv: 'zhconv',
  'yt-dlp': 'yt_dlp',
  segno: 'segno',
})

/** 计划 §P1/D5 锁定的工具白名单长度（gateway.list_specs() 的期望条数）。 */
const EXPECTED_TOOL_COUNT = 3
const DEFAULT_PYTHON = '3.13'
/** 计划 §P2 验收的体积红线（目录总量；仅告警，不阻断构建）。
 *  P6 完整面板把整个后端跑起来，PyMuPDF / trafilatura / yt-dlp 等解析链加入后
 *  目录总量上限放宽到 240MB；真正的硬红线仍是 npm 单包 100MB（看 tgz）。 */
const TOTAL_SIZE_WARN_MB = 240
// 裁剪后的预算（超出则打印 runtime 顶层目录明细，交给人工判断，不激进删）。
// P6 前是 150（13 个只读网关依赖）；P6 加到 200，超出仍只告警不阻断。
const SIZE_BUDGET_MB = 200

class BuildError extends Error {}

// ────────────────────────────── 小工具 ──────────────────────────────

function log(msg = '') {
  process.stdout.write(msg + '\n')
}

function step(n, total, title) {
  log('')
  log('─'.repeat(72))
  log(`[${n}/${total}] ${title}`)
  log('─'.repeat(72))
}

function fail(message) {
  throw new BuildError(message)
}

/** 统一给子进程的环境：清掉代理残留（AGENTS.md §1）。 */
function childEnv(extra = {}) {
  return {
    ...process.env,
    // 校验会 import engine 与 13 个依赖：默认会往 engine/ 和 site-packages 里写 __pycache__，
    // 既让构建产物带上构建机的字节码（换机器后 pyc 失效、体积虚高），也会在裁剪后又长回来。
    PYTHONDONTWRITEBYTECODE: '1',
    NO_PROXY: '*',
    no_proxy: '*',
    ...extra,
  }
}

function formatMb(bytes) {
  return (bytes / 1024 / 1024).toFixed(1) + ' MB'
}

/** 递归遍历文件（跳过符号链接；构建产物里不应有链接）。 */
function* walkFiles(dir) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name)
    if (entry.isDirectory()) yield* walkFiles(full)
    else if (entry.isFile()) yield full
  }
}

/** 递归遍历目录（不含 root 自身；跳过符号链接与普通文件）。 */
function* walkDirs(dir) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue
    const full = join(dir, entry.name)
    yield full
    yield* walkDirs(full)
  }
}

function dirSizeBytes(dir) {
  if (!existsSync(dir)) return 0
  let bytes = 0
  for (const file of walkFiles(dir)) bytes += statSync(file).size
  return bytes
}

function countFiles(dir) {
  if (!existsSync(dir)) return 0
  let n = 0
  for (const _ of walkFiles(dir)) n++
  return n
}

function run(cmd, args, { capture = false, cwd, env, allowFail = false } = {}) {
  const res = spawnSync(cmd, args, {
    cwd: cwd ?? REPO_ROOT,
    env: env ?? childEnv(),
    stdio: capture ? ['ignore', 'pipe', 'pipe'] : 'inherit',
    encoding: 'utf8',
    windowsHide: true,
  })
  if (res.error) {
    fail(`无法执行 ${cmd}：${res.error.message}`)
  }
  if (res.status !== 0 && !allowFail) {
    const detail = capture ? `\nstdout:\n${res.stdout}\nstderr:\n${res.stderr}` : ''
    fail(`命令失败（退出码 ${res.status}）：${cmd} ${args.join(' ')}${detail}`)
  }
  return { status: res.status, stdout: res.stdout ?? '', stderr: res.stderr ?? '' }
}

/** 递归复制，排除 __pycache__ / *.pyc。 */
function copyTree(src, dst, { skipDir = () => false, skipFile = () => false } = {}) {
  mkdirSync(dst, { recursive: true })
  cpSync(src, dst, {
    recursive: true,
    dereference: true,
    filter: (source) => {
      const entry = basename(source)
      if (existsSync(source) && statSync(source).isDirectory()) {
        return !skipDir(entry, source)
      }
      return !skipFile(entry, source)
    },
  })
}

function isWindows() {
  return process.platform === 'win32'
}

/**
 * 构建临时目录根。
 *
 * 为什么不用 `os.tmpdir()`（2026-10-27 实测）：本机 DSH 沙箱里**被 spawn 的子进程**
 * 无法在 `%TEMP%` / `%LOCALAPPDATA%` 下建目录 —— Python 直接报
 * `PermissionError: [WinError 5] 拒绝访问`（node 自己 mkdtemp 能成功，所以只能靠
 * "子进程能不能写"来判断）。日志 handler 初始化失败、uv 也写不进去。
 * `.dsh_local/` 在仓库内（.gitignore 已忽略，AGENTS.md §0 的会话临时目录），子进程可写。
 * 默认放这里；`--tmp-dir` 可覆盖（CI / 别的工作区）。
 */
export function resolveTempRoot(opts = {}) {
  if (opts.tmpDir) {
    const dir = resolve(opts.tmpDir)
    mkdirSync(dir, { recursive: true })
    return dir
  }
  const preferred = join(REPO_ROOT, '.dsh_local', 'build-tmp')
  try {
    mkdirSync(preferred, { recursive: true })
    return preferred
  } catch {
    return tmpdir()
  }
}

function makeTempDir(base, prefix) {
  mkdirSync(base, { recursive: true })
  return mkdtempSync(join(base, prefix))
}

/** 在目录里找解释器可执行文件（Windows: python.exe；其它平台：bin/python3,x）。 */
export function findPythonExe(dir) {
  const candidates = isWindows()
    ? [join(dir, 'python.exe'), join(dir, 'bin', 'python.exe')]
    : [join(dir, 'bin', 'python3'), join(dir, 'bin', 'python')]
  for (const candidate of candidates) if (existsSync(candidate)) return candidate
  return null
}

// ────────────────────────────── 参数 ──────────────────────────────

function parseArgs(argv) {
  const opts = {
    python: DEFAULT_PYTHON,
    pythonDir: null,
    skipEngine: false,
    skipRuntime: false,
    skipDeps: false,
    tmpDir: null,
    measureTgz: false,
    help: false,
  }
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i]
    const needValue = () => {
      const value = argv[++i]
      if (!value) fail(`参数 ${arg} 需要一个值`)
      return value
    }
    switch (arg) {
      case '--python':
        opts.python = needValue()
        break
      case '--python-dir':
        opts.pythonDir = resolve(needValue())
        break
      case '--skip-engine':
        opts.skipEngine = true
        break
      case '--skip-runtime':
        opts.skipRuntime = true
        break
      case '--skip-deps':
        opts.skipDeps = true
        break
      case '--tmp-dir':
        opts.tmpDir = resolve(needValue())
        break
      case '--measure-tgz':
        opts.measureTgz = true
        break
      case '-h':
      case '--help':
        opts.help = true
        break
      default:
        fail(`未知参数：${arg}（--help 看用法）`)
    }
  }
  return opts
}

function printHelp() {
  log(`用法：node scripts/build-plugin.mjs [options]

  --python <ver>       Python 小版本（默认 ${DEFAULT_PYTHON}）
  --python-dir <dir>   使用已有便携解释器目录（含 python.exe），跳过 uv 下载
  --skip-engine        不重建 engine/（沿用上次产物）
  --skip-runtime       不重建 runtime/（沿用上次产物）
  --skip-deps          不重装 13 个依赖（沿用 runtime 里已装的 site-packages）
  --tmp-dir <dir>      构建临时目录根（默认 <repo>/.dsh_local/build-tmp）
  --measure-tgz        构建完用 tar -czf 测 engine+runtime 压缩体积（慢，验收用）
  -h, --help           显示本帮助

产物：${relative(REPO_ROOT, ENGINE_DIR)}/ 与 ${relative(REPO_ROOT, RUNTIME_DIR)}/（均不进 git）`)
}

// ────────────────────────────── engine ──────────────────────────────

function buildEngine() {
  if (!existsSync(SOURCE_APP_DIR)) fail(`源目录不存在：${SOURCE_APP_DIR}`)
  rmSync(ENGINE_DIR, { recursive: true, force: true })
  mkdirSync(join(ENGINE_DIR, 'app'), { recursive: true })
  log(`复制 ${relative(REPO_ROOT, SOURCE_APP_DIR)} → ${relative(REPO_ROOT, join(ENGINE_DIR, 'app'))}（排除 __pycache__ / *.pyc）`)

  const skipped = { pycacheDirs: 0, pycFiles: 0 }
  copyTree(SOURCE_APP_DIR, join(ENGINE_DIR, 'app'), {
    skipDir: (name) => {
      if (name === '__pycache__') {
        skipped.pycacheDirs++
        return true
      }
      return false
    },
    skipFile: (name) => {
      if (name.endsWith('.pyc')) {
        skipped.pycFiles++
        return true
      }
      return false
    },
  })

  // 兜底：复制后 engine 里不该再有任何缓存文件（cpSync filter 的目录语义在 Node 里有坑，
  // 与其相信它，不如复制完再断言一次，顺手清掉）。
  let leftovers = 0
  for (const file of [...walkFiles(join(ENGINE_DIR, 'app'))]) {
    if (basename(file) === '__pycache__' || file.endsWith('.pyc')) {
      rmSync(file, { force: true })
      leftovers++
    }
  }

  const files = countFiles(ENGINE_DIR)
  const bytes = dirSizeBytes(ENGINE_DIR)
  if (files === 0) fail('engine 复制后是空的')
  if (!existsSync(join(ENGINE_DIR, 'app', 'mcp_servers', 'kg_tools.py'))) {
    fail('engine 缺 app/mcp_servers/kg_tools.py —— 复制不完整')
  }
  log(`engine 完成：${files} 个文件 / ${formatMb(bytes)}（跳过 __pycache__ ${skipped.pycacheDirs} 个、*.pyc ${skipped.pycFiles} 个，兜底清理 ${leftovers}）`)
  return { files, bytes }
}

// ────────────────────────────── runtime：Python 本体 ──────────────────────────────

function uvAvailable() {
  const res = spawnSync('uv', ['--version'], { env: childEnv(), encoding: 'utf8', windowsHide: true })
  return !res.error && res.status === 0 ? res.stdout.trim() : null
}

/** uv install + find → 取 uv 托管解释器所在的**整个目录**（realpath 后是具体版本目录）。 */
function resolveUvPythonDir(request, tmpRoot) {
  log(`uv python install ${request}`)
  run('uv', ['python', 'install', request], { cwd: tmpRoot, env: childEnv() })

  // ⚠️ 必须带 --managed-python 且在临时目录执行：在仓库根跑，uv python find 会优先
  //    命中仓库里的 .venv（本机实测过：返回 .venv/Scripts/python.exe，那是 venv 不是
  //    自包含的 install_only 构建，整目录复制过去缺 DLL）。
  const found = run('uv', ['python', 'find', '--managed-python', request], {
    cwd: tmpRoot,
    env: childEnv(),
    capture: true,
  })
  const exe = found.stdout.trim().split(/\r?\n/).filter(Boolean).pop()
  if (!exe) fail(`uv python find --managed-python ${request} 没有输出解释器路径`)
  const realExe = realpathSync(exe)
  const dir = dirname(realExe)
  log(`uv 托管解释器：${realExe}`)
  log(`python 目录：${dir}`)
  return dir
}

/** 回落：从 python-build-standalone 找 Windows x64 / CPython <request> 的 install_only 资产。 */
export async function resolvePbsAsset(request) {
  if (!isWindows() || process.arch !== 'x64') {
    fail('python-build-standalone 回落路径目前只实现了 Windows x64（其它平台请用 --python-dir）')
  }
  const pattern = new RegExp(
    `^cpython-${request.replace(/\./g, '\\.')}\\.\\d+\\+\\d+-x86_64-pc-windows-msvc-install_only\\.tar\\.gz$`,
  )
  const res = await fetch('https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest', {
    headers: { 'user-agent': 'dsh-ai-tutor-build-script' },
  })
  if (!res.ok) fail(`查询 python-build-standalone 最新 release 失败：HTTP ${res.status}`)
  const release = await res.json()
  const assets = Array.isArray(release.assets) ? release.assets : []
  const asset = assets.find((a) => pattern.test(a.name))
  if (!asset) {
    const sample = assets.slice(0, 5).map((a) => a.name).join(', ')
    fail(`release ${release.tag_name ?? '?'} 里找不到匹配 ${pattern} 的资产（样本：${sample}）`)
  }
  return { tag: release.tag_name, name: asset.name, url: asset.browser_download_url, size: asset.size }
}

async function downloadFile(url, dest) {
  log(`下载 ${url}`)
  const res = await fetch(url, { headers: { 'user-agent': 'dsh-ai-tutor-build-script' } })
  if (!res.ok) fail(`下载失败：HTTP ${res.status} ${url}`)
  if (!res.body) fail(`下载失败：响应没有 body（${url}）`)
  mkdirSync(dirname(dest), { recursive: true })
  await pipeline(Readable.fromWeb(res.body), createWriteStream(dest))
  log(`已下载 ${formatMb(statSync(dest).size)} → ${dest}`)
}

async function extractTarball(tarball, destDir) {
  const hasTar = spawnSync('tar', ['--version'], { encoding: 'utf8', windowsHide: true })
  if (hasTar.error || hasTar.status !== 0) {
    fail('系统没有可用的 tar 命令，无法解压 install_only 包（Windows 10+ 自带 tar.exe；或用 --python-dir）')
  }
  mkdirSync(destDir, { recursive: true })
  run('tar', ['-xzf', tarball, '-C', destDir])
  // install_only 包解开后是 python/ 子目录
  const candidate = join(destDir, 'python')
  return findPythonExe(candidate) ? candidate : destDir
}

/**
 * 去掉 PEP 668 的 `EXTERNALLY-MANAGED` 标记。
 *
 * 它本来只搭在 uv 托管目录里（内容："This Python installation is managed by uv and
 * should not be modified."），整目录复制后会跟着进 runtime/ → 连 `--system` 都被拒：
 *   error: The interpreter at ... is externally managed, and indicates the following:
 * （2026-10-27 实测，构建第一次就撞上。）
 * 语义上这层保护对**构建产物副本**不成立：runtime/ 是可删可重建的独立分发包，
 * 没有系统包管理器需要保护；真正的保护是"改依赖清单走计划 §4 D5"。
 * 兼容 unix 布局一并查（Windows 是 Lib/，POSIX 是 lib/python3.X/）。
 */
function removeExternallyManagedMarker(runtimeDir) {
  const candidates = [
    join(runtimeDir, 'Lib', 'EXTERNALLY-MANAGED'),
    ...(existsSync(join(runtimeDir, 'lib'))
      ? readdirSync(join(runtimeDir, 'lib'))
          .filter((name) => name.startsWith('python'))
          .map((name) => join(runtimeDir, 'lib', name, 'EXTERNALLY-MANAGED'))
      : []),
  ]
  for (const marker of candidates) {
    if (!existsSync(marker)) continue
    rmSync(marker, { force: true })
    log(`移除 PEP 668 标记：${relative(runtimeDir, marker)}（否则 uv/pip 拒绝写入这个副本）`)
  }
}

/** 从 uv / tarball / --python-dir 三条路拿到「一个自包含 python 目录」，再整目录复制到 runtime/。 */
async function stageRuntimePython(opts) {
  rmSync(RUNTIME_DIR, { recursive: true, force: true })
  mkdirSync(RUNTIME_DIR, { recursive: true })

  let sourceDir = null
  let sourceKind = ''

  if (opts.pythonDir) {
    sourceDir = opts.pythonDir
    sourceKind = '--python-dir'
    if (!findPythonExe(sourceDir)) fail(`--python-dir ${sourceDir} 里找不到 python.exe`)
  } else if (uvAvailable()) {
    log(`uv 可用：${uvAvailable()}`)
    sourceDir = resolveUvPythonDir(opts.python, opts.tmpRoot)
    sourceKind = 'uv-managed'
  } else {
    log('uv 不可用 → 回落 python-build-standalone install_only tarball')
    const workDir = makeTempDir(opts.tmpRoot, 'pbs-')
    const asset = await resolvePbsAsset(opts.python)
    log(`release ${asset.tag} / ${asset.name}（${formatMb(asset.size ?? 0)}）`)
    const tarball = join(workDir, asset.name)
    await downloadFile(asset.url, tarball)
    sourceDir = await extractTarball(tarball, join(workDir, 'extract'))
    sourceKind = 'python-build-standalone'
    if (!findPythonExe(sourceDir)) fail(`解压后仍找不到 python.exe（${sourceDir}）`)
  }

  log(`整目录复制 ${sourceDir} → ${RUNTIME_DIR}（含 python.exe / python3XX.dll / Lib / DLLs）`)
  copyTree(sourceDir, RUNTIME_DIR)
  const pythonExe = findPythonExe(RUNTIME_DIR)
  if (!pythonExe) fail(`runtime 里没有 python.exe（来源：${sourceDir}）`)
  removeExternallyManagedMarker(RUNTIME_DIR)

  // 自包含性检查：拷来的解释器必须真的能起（依赖同目录 DLL，不是系统 Python）。
  const version = run(pythonExe, ['--version'], { capture: true, cwd: RUNTIME_DIR }).stdout.trim()
  log(`runtime 解释器就绪：${pythonExe} (${version})，来源 = ${sourceKind}`)
  return { pythonExe, version, sourceKind, sourceDir }
}

// ────────────────────────────── runtime：裁剪 ──────────────────────────────

/**
 * Lead 2026-10-27 指令（插件包预算）：uv 装完立刻裁剪 runtime，再跑校验。
 *
 * 白名单保守删除，**只**删下面四类（Lib/ 下其它标准库目录一律不动）：
 *   1. 标准库测试/示例/GUI 工具：Lib/test、idlelib、lib2to3、ensurepip、turtledemo
 *   2. GUI：Lib/tkinter 包、tcl/ 目录、DLLs/tcl*.dll、DLLs/tk*.dll（MCP server 无 GUI）
 *   3. 字节码：所有 __pycache__/ 与 *.pyc（装完即定，不需要再编译）
 *   4. 三方包里的测试与链接期垃圾：site-packages 下 tests/（含 Tests/）、*.pdb、*.lib、*.a
 * 删完由下一步「校验」兜底：13 个包全部 import + gateway 起来，否则构建非 0 退出。
 * 注：site-packages 里的 pip / setuptools 与 .dist-info **不动**（保守；MANIFEST 要读版本）。
 */
const PRUNE_STDLIB_DIRS = Object.freeze(['test', 'idlelib', 'lib2to3', 'ensurepip', 'turtledemo', 'tkinter'])
const PRUNE_TCL_TK_DLL = Object.freeze([/^tcl.*\.dll$/i, /^tk.*\.dll$/i])
const PRUNE_SP_TEST_DIRS = Object.freeze(['tests', 'test'])
/** site-packages 里运行期不需要的整包（uv 安装完即可删；插件不在运行期装包）。 */
const PRUNE_SP_PACKAGES = Object.freeze(['pip', 'setuptools', 'wheel'])
const PRUNE_SP_JUNK_FILE = /\.(pdb|lib|a)$/i

/** runtime 里标准库 / site-packages 的位置（Windows install_only 是 Lib/；POSIX 是 lib/python3.13/）。 */
function findRuntimeLayout(runtimeDir) {
  if (existsSync(join(runtimeDir, 'Lib'))) {
    const stdLib = join(runtimeDir, 'Lib')
    return {
      stdLib,
      sitePackages: join(stdLib, 'site-packages'),
      dlls: join(runtimeDir, 'DLLs'),
      tcl: join(runtimeDir, 'tcl'),
    }
  }
  const libRoot = join(runtimeDir, 'lib')
  if (existsSync(libRoot)) {
    const pyDir = readdirSync(libRoot).find((name) => /^python\d+\.\d+$/.test(name))
    if (pyDir) {
      const stdLib = join(libRoot, pyDir)
      return { stdLib, sitePackages: join(stdLib, 'site-packages'), dlls: null, tcl: join(runtimeDir, 'tcl') }
    }
  }
  return null
}

/**
 * 删除目录树里的 __pycache__ 与 *.pyc（构建产物不该带构建机的字节码）。
 * 尽力而为：别的进程可能正在用 runtime 跑 MCP（Windows 会锁 pyc），删不掉就记账跳过，不让构建失败。
 */
function cleanBytecode(root) {
  const dirs = [...walkDirs(root)]
    .filter((dir) => basename(dir) === '__pycache__')
    .sort((a, b) => a.length - b.length)
  let removedDirs = 0
  let skippedDirs = 0
  for (const dir of dirs) {
    if (!existsSync(dir)) continue
    try {
      rmSync(dir, { recursive: true, force: true })
      removedDirs++
    } catch {
      skippedDirs++
    }
  }
  const files = [...walkFiles(root)].filter((file) => file.endsWith('.pyc'))
  let removedFiles = 0
  let skippedFiles = 0
  for (const file of files) {
    try {
      rmSync(file, { force: true })
      removedFiles++
    } catch {
      skippedFiles++
    }
  }
  return { dirs: removedDirs, files: removedFiles, skipped: skippedDirs + skippedFiles }
}

/** 裁剪 runtime；返回删了哪些项 / 共多少字节（供体积报告与 MANIFEST 对账）。 */
function pruneRuntime(runtimeDir) {
  const layout = findRuntimeLayout(runtimeDir)
  if (!layout) fail('裁剪失败：runtime 里既没有 Lib/ 也没有 lib/pythonX.Y/，目录结构不认识')
  const items = []
  const remove = (target, label) => {
    if (!existsSync(target)) return
    const st = statSync(target)
    const bytes = st.isDirectory() ? dirSizeBytes(target) : st.size
    rmSync(target, { recursive: st.isDirectory(), force: true })
    items.push({ label, bytes })
  }

  // 1) 标准库测试/示例/GUI 工具
  for (const name of PRUNE_STDLIB_DIRS) remove(join(layout.stdLib, name), 'Lib/' + name)
  // 2) tcl/tk（tkinter 包已在上面删；DLL 一起删才自洽）
  remove(layout.tcl, 'tcl/')
  if (layout.dlls && existsSync(layout.dlls)) {
    for (const name of readdirSync(layout.dlls)) {
      if (PRUNE_TCL_TK_DLL.some((re) => re.test(name))) remove(join(layout.dlls, name), 'DLLs/' + name)
    }
  }
  // 3) __pycache__ 与 *.pyc（按路径长度升序：父目录先删，子项存在性检查会跳过）
  const pycacheDirs = [...walkDirs(runtimeDir)]
    .filter((dir) => basename(dir) === '__pycache__')
    .sort((a, b) => a.length - b.length)
  for (const dir of pycacheDirs) remove(dir, relative(runtimeDir, dir) + '/')
  for (const file of [...walkFiles(runtimeDir)].filter((file) => file.endsWith('.pyc'))) {
    remove(file, relative(runtimeDir, file))
  }
  // 4) 三方包的测试目录与链接期垃圾
  if (existsSync(layout.sitePackages)) {
    const testDirs = [...walkDirs(layout.sitePackages)]
      .filter((dir) => PRUNE_SP_TEST_DIRS.includes(basename(dir).toLowerCase()))
      .sort((a, b) => a.length - b.length)
    for (const dir of testDirs) remove(dir, relative(runtimeDir, dir) + '/')
    for (const file of [...walkFiles(layout.sitePackages)].filter((file) => PRUNE_SP_JUNK_FILE.test(file))) {
      remove(file, relative(runtimeDir, file))
    }
  }
  // 5) 运行期明确不需要的：C 头文件（只给编译用）、pip/setuptools/wheel（runtime 由 uv
  //    --system 安装，插件运行期不会再装包）。P6 完整面板让 tgz 逼近 npm 100MB 上限，
  //    这两个是零风险的一刀（~10MB），别再为了体积动 pymupdf/numpy 这些真依赖。
  remove(join(runtimeDir, 'include'), 'include/')
  if (existsSync(layout.sitePackages)) {
    for (const name of PRUNE_SP_PACKAGES) remove(join(layout.sitePackages, name), 'site-packages/' + name)
  }
  return { items, bytes: items.reduce((sum, item) => sum + item.bytes, 0), sitePackages: layout.sitePackages }
}

/** 超预算时列 runtime 各顶层子目录体积（Lead 要求：先说明谁占的，不激进删）。 */
function runtimeBreakdown(runtimeDir, topN = 12) {
  const rows = readdirSync(runtimeDir, { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .map((entry) => ({ name: entry.name, bytes: dirSizeBytes(join(runtimeDir, entry.name)) }))
    .sort((a, b) => b.bytes - a.bytes)
    .slice(0, topN)
  const rootFiles = readdirSync(runtimeDir, { withFileTypes: true })
    .filter((entry) => entry.isFile())
    .reduce((sum, entry) => sum + statSync(join(runtimeDir, entry.name)).size, 0)
  if (rootFiles > 0) rows.push({ name: '（根目录散文件）', bytes: rootFiles })
  return rows
}

/** 打临时 tgz 测真实压缩体积（npm 发布看的是 tgz，不是目录）—— 仅 --measure-tgz 时跑。 */
function measureTgz(tmpRoot) {
  const workDir = makeTempDir(tmpRoot, 'tgz-')
  const outName = 'engine-runtime.tgz'
  const out = join(workDir, outName)
  log('打包测体积：tar -czf（engine + runtime，临时目录）…')
  // -f 必须用相对文件名 + cwd=workDir：GNU tar 把 'C:...' 当远端主机（Cannot connect to C），
  // Windows 自带 bsdtar 不认 --force-local，所以干脆不给绝对路径。
  const res = run('tar', ['-czf', outName, '-C', PLUGIN_DIR, 'engine', 'runtime'], { cwd: workDir, allowFail: true })
  // tar 退出码 1 = 打包过程中有文件在变（例如别的会话正在用 runtime 跑 MCP server），
  // 包本身仍完整；只有「没有产物 / 产物小得离谱」才算失败。
  const ok = existsSync(out) && statSync(out).size > 1024 * 1024
  const bytes = ok ? statSync(out).size : 0
  const warned = res.status !== 0
  rmSync(workDir, { recursive: true, force: true })
  if (!ok) return null
  return { bytes, warned }
}


// ────────────────────────────── runtime：13 个依赖 ──────────────────────────────

function installDeps(pythonExe) {
  // 安装规格 = PACKAGE_SPECS 覆盖后的列表（裸包名与版本区间混排，pip/uv 都接受）。
  const specs = PACKAGES.map((name) => PACKAGE_SPECS[name] ?? name)
  log(`安装 ${PACKAGES.length} 个锁定依赖：${specs.join(' ')}`)
  const uv = uvAvailable()
  if (uv) {
    log(`用 uv pip install --python <runtime>/python.exe --system（${uv}）`)
    // runtime 不是 venv（是 install_only 的裸解释器）→ 必须 --system 才允许写入。
    run('uv', ['pip', 'install', '--python', pythonExe, '--system', ...specs], { env: childEnv() })
  } else {
    log('uv 不可用 → 回落 runtime/python.exe -m pip install')
    // --break-system-packages：万一来源是"pip 认作系统 Python"的安装，PEP 668 也会挡写入。
    run(
      pythonExe,
      ['-m', 'pip', 'install', '--no-input', '--disable-pip-version-check', '--break-system-packages', ...specs],
      { env: childEnv() },
    )
  }
}

/** 读回实际装上的版本（写进 MANIFEST，便于 doctor 比对）。 */
function readInstalledVersions(pythonExe) {
  const snippet = [
    'import json, sys',
    'from importlib.metadata import version, PackageNotFoundError',
    'out = {}',
    'for name in ' + JSON.stringify(PACKAGES) + ':',
    '    try:',
    '        out[name] = version(name)',
    '    except PackageNotFoundError:',
    '        out[name] = None',
    'print("VERSIONS=" + json.dumps(out))',
  ].join('\n')
  const res = run(pythonExe, ['-c', snippet], { capture: true, cwd: RUNTIME_DIR })
  const line = res.stdout.split(/\r?\n/).find((l) => l.startsWith('VERSIONS='))
  if (!line) fail('读不到依赖版本（importlib.metadata 无输出）')
  const versions = JSON.parse(line.slice('VERSIONS='.length))
  const missing = Object.entries(versions).filter(([, v]) => !v).map(([name]) => name)
  if (missing.length) fail(`这些包装完后仍不存在：${missing.join(', ')}`)
  for (const name of PACKAGES) log(`  ${name.padEnd(18)} ${versions[name]}`)
  return versions
}

// ────────────────────────────── 校验 ──────────────────────────────

/** 计划 §P1 锁定、P1 落地后 gateway.list_specs() 应返回的 3 个工具（只用于"是否还是旧白名单"的告警）。 */
const EXPECTED_TOOLS = Object.freeze(['rag_search', 'fetch_webpage', 'kg_snapshot'])

/**
 * 校验用占位密钥。
 *
 * 为什么需要它：engine 复制的是 backend/app/ 原样，而 app/core/auth.py:19 在**模块导入期**
 * 就调 config.settings.require_secret_key()（没配 SECRET_KEY → RuntimeError），
 * gateway.py 顶部又 from app.core.auth import get_current_user_from_token → 干净机器上
 * 仅 import gateway 就会挂。构建校验只验"解释器 + engine + 13 个包能互相 import"，
 * 不该被部署期环境变量挡住；但**这也说明真机启动必须注入 SECRET_KEY**（见构建告警）。
 * 真实密钥由插件 host 生成/持有（P3 侧），本脚本绝不写进产物。
 */
const SECRET_KEY_PLACEHOLDER = 'build-smoke-test-only-placeholder-secret'

function runValidateOnce(pythonExe, env, snippet) {
  return spawnSync(pythonExe, ['-c', snippet], {
    cwd: PLUGIN_DIR,
    env,
    encoding: 'utf8',
    windowsHide: true,
  })
}

/**
 * 用 runtime 解释器把 engine 加进 sys.path，import 网关并打印 list_specs() 名字。
 * 失败（非 0 退出 / 条数不对）→ 抛错，由 main 以非 0 退出。
 *
 * 返回 { names, warnings }：warnings 进 MANIFEST 与构建结论（不阻断构建），
 * 阻断条件只有"退出码非 0 / 条数不对 / 没有 rag_search"。
 */
function validate(pythonExe, tmpRoot) {
  const workDir = makeTempDir(tmpRoot, 'validate-')
  const baseEnv = {
    // 校验进程不碰真实数据/日志：DATA_ROOT 指到临时目录，LOG_DIR 也别写进 engine/
    // （engine 里 import app 会初始化日志，默认会创建 engine/logs/tutor.log）。
    DATA_ROOT: join(workDir, 'data'),
    LOG_DIR: join(workDir, 'logs'),
  }
  const snippet = [
    'import json, sys',
    'sys.path.insert(0, "engine")',
    'from app.core.agent_tools import gateway',
    'names = [s["name"] for s in gateway.list_specs()]',
    'print("LIST_SPECS=" + json.dumps(names, ensure_ascii=False))',
    // 锁定依赖一次性真 import（含 C 扩展）—— 任何 ABI 不匹配都会在这里炸。
    'import ' + Object.values(PACKAGE_MODULES).join(', '),
    'print("IMPORTS=OK")',
    // P6：完整面板要跑整个 FastAPI 应用，只 import 网关不足以证明依赖齐 —— 这里真 import
    // app.main（会连带 api/v1/* 与 services 的全部模块级导入）。导入期需要 SECRET_KEY，
    // 由下面的占位重试兜住（与 gateway 的 SECRET_KEY 处理同一条路径）。
    'import app.main',
    'print("APP_MAIN_OK")',
  ].join('\n')

  log('运行校验：cd ' + relative(REPO_ROOT, PLUGIN_DIR) + ' && runtime/python.exe -c "<import gateway; list_specs()>"')
  let res = runValidateOnce(pythonExe, childEnv(baseEnv), snippet)
  let secretKeyPlaceholderUsed = false
  const combined = (r) => (r.stderr ?? '') + (r.stdout ?? '')
  if (res.status !== 0 && /SECRET_KEY/.test(combined(res))) {
    log('')
    log('⚠️  第一次校验失败：engine 里 app.core.auth 在导入期要求 SECRET_KEY（干净环境没有 .env）。')
    log('    构建校验改用占位 SECRET_KEY 重试一次（只验 import 链路，不改变产物）。')
    secretKeyPlaceholderUsed = true
    res = runValidateOnce(pythonExe, childEnv({ ...baseEnv, SECRET_KEY: SECRET_KEY_PLACEHOLDER }), snippet)
  }
  const stdout = res.stdout ?? ''
  const stderr = res.stderr ?? ''
  if (stdout.trim()) log('stdout:\n' + stdout.trimEnd())
  if (stderr.trim()) log('stderr:\n' + stderr.trimEnd())
  rmSync(workDir, { recursive: true, force: true })

  if (res.error) fail('校验进程无法启动：' + res.error.message)
  if (res.status !== 0) {
    fail('校验失败：runtime 解释器 import gateway 退出码 ' + res.status + '（stderr 见上）')
  }
  const line = stdout.split(/\r?\n/).find((l) => l.startsWith('LIST_SPECS='))
  if (!line) fail('校验失败：没有拿到 LIST_SPECS 输出（gateway 导入可能中途退出）')
  const names = JSON.parse(line.slice('LIST_SPECS='.length))
  log('list_specs() = ' + JSON.stringify(names))
  if (!stdout.includes('IMPORTS=OK')) {
    fail('校验失败：' + PACKAGES.length + ' 个锁定依赖没能全部 import（stderr 见上）')
  }
  if (!stdout.includes('APP_MAIN_OK')) {
    fail('校验失败：app.main 没能 import（完整面板起不来；缺包见 stderr）')
  }
  log(PACKAGES.length + ' 个锁定依赖全部 import 成功，app.main 可导入。')

  const warnings = []
  if (names.length !== EXPECTED_TOOL_COUNT) {
    fail(
      '校验失败：list_specs() 有 ' + names.length + ' 个工具，期望 ' + EXPECTED_TOOL_COUNT + ' 个（' +
        names.join(', ') + '）。白名单见 backend/app/core/agent_tools/gateway.py::ALLOWLIST' +
        '（P1 完成后应为 ' + EXPECTED_TOOLS.join('/') + '）。',
    )
  }
  if (!names.includes('rag_search')) fail('校验失败：list_specs() 里没有 rag_search（实际：' + names.join(', ') + '）')
  if (!EXPECTED_TOOLS.every((name) => names.includes(name))) {
    warnings.push(
      'list_specs() = [' + names.join(', ') + ']，与计划 §P1 锁定的 [' + EXPECTED_TOOLS.join(', ') +
        '] 不一致 —— engine 复制的是 P1 之前的 gateway.py（ALLOWLIST 仍含 search_resources）。' +
        'P1 落地后重跑本脚本即对齐；本脚本只硬性要求条数=' + EXPECTED_TOOL_COUNT + ' 且含 rag_search。',
    )
  }
  if (secretKeyPlaceholderUsed) {
    warnings.push(
      '校验用了占位 SECRET_KEY（' + SECRET_KEY_PLACEHOLDER + '）：app/core/auth.py 在导入期调 ' +
        'settings.require_secret_key()，gateway.py 顶部 from app.core.auth import get_current_user_from_token ' +
        '→ 干净机器上仅 import 网关就会 RuntimeError。**真机启动必须注入 SECRET_KEY**' +
        '（插件 host 生成并持久化；或 P1 把该 import 改成惰性），否则 MCP server 起不来。',
    )
  }
  log('校验通过：runtime 能 import engine 的网关，工具白名单 ' + names.length + ' 个。')
  return { names, warnings }
}

// ────────────────────────────── MANIFEST ──────────────────────────────

function pythonVersionOf(pythonExe) {
  const out = run(pythonExe, ['--version'], { capture: true }).stdout.trim()
  const match = /Python\s+(\d+\.\d+\.\d+)/.exec(out)
  if (!match) fail(`读不到 Python 版本：${out}`)
  return match[1]
}

function writeManifest({ pythonExe, versions, engine, runtimeBytes, validation, prunedBytes, warnings }) {
  const manifest = {
    pythonVersion: pythonVersionOf(pythonExe),
    packages: [...PACKAGES],
    builtAt: new Date().toISOString(),
    engineFiles: engine.files,
    runtimeBytes,
    engineBytes: engine.bytes,
    // 本轮裁剪掉的字节数（上面的 runtimeBytes 是裁剪后的值，便于对账）。
    prunedBytes,
    installedPackages: versions,
    validation: {
      toolNames: validation.names,
      secretKeyPlaceholderUsed: validation.warnings.some((w) => w.includes('占位 SECRET_KEY')),
    },
    warnings: [...warnings],
    builtBy: 'scripts/build-plugin.mjs',
  }
  const path = join(RUNTIME_DIR, 'MANIFEST.json')
  writeFileSync(path, JSON.stringify(manifest, null, 2) + '\n', 'utf8')
  log(`写入 ${relative(REPO_ROOT, path)}`)
  return manifest
}

// ────────────────────────────── main ──────────────────────────────

async function main(argv) {
  const opts = parseArgs(argv)
  if (opts.help) {
    printHelp()
    return
  }

  log('DSH 插件构建（engine + 便携 runtime）')
  log(`仓库根：${REPO_ROOT}`)
  log(`插件目录：${PLUGIN_DIR}`)
  // 临时目录根：见 resolveTempRoot 的注释（沙箱里子进程只能写仓库内）。
  opts.tmpRoot = resolveTempRoot(opts)
  log(`临时目录：${opts.tmpRoot}`)
  if (!existsSync(PLUGIN_DIR)) fail(`插件目录不存在：${PLUGIN_DIR}`)
  if (!existsSync(SOURCE_APP_DIR)) fail(`backend/app 不存在：${SOURCE_APP_DIR}`)

  // 面板产物（前端 dist 镜像）：不在这里构建（= node scripts/build-panel.mjs），
  // 但必须让构建日志明确它有没有被带进包 —— 缺了它完整面板只剩提示页。
  const PANEL_DIR = join(PLUGIN_DIR, 'panel')
  if (!existsSync(join(PANEL_DIR, 'index.html'))) {
    log('⚠️ plugins/dsh-ai-tutor/panel/index.html 不存在：完整面板只能显示降级提示页。')
    log('   先跑 node scripts/build-panel.mjs（前端构建产物），再重跑本脚本。')
  } else {
    log(`panel：${formatMb(dirSizeBytes(PANEL_DIR))}（${countFiles(PANEL_DIR)} 个文件）`)
  }

  const totalSteps = (opts.skipEngine ? 0 : 1) + (opts.skipRuntime ? 0 : 1) + (opts.skipDeps ? 0 : 1) + 3
  let stepNo = 0

  // 1) engine
  let engine
  if (opts.skipEngine) {
    if (!existsSync(ENGINE_DIR)) fail('--skip-engine 但 engine/ 不存在（先完整构建一次）')
    engine = { files: countFiles(ENGINE_DIR), bytes: dirSizeBytes(ENGINE_DIR) }
    log(`跳过 engine 重建（沿用：${engine.files} 个文件 / ${formatMb(engine.bytes)}）`)
  } else {
    step(++stepNo, totalSteps, 'engine：backend/app 整包复制')
    engine = buildEngine()
  }

  // 2) runtime（Python 本体）
  let pythonExe
  let runtimeSource = null
  if (opts.skipRuntime) {
    if (!existsSync(RUNTIME_DIR)) fail('--skip-runtime 但 runtime/ 不存在（先完整构建一次）')
    pythonExe = findPythonExe(RUNTIME_DIR)
    if (!pythonExe) fail('--skip-runtime 但 runtime/ 里没有 python.exe')
    runtimeSource = 'reused'
    log(`跳过 runtime 重建（沿用：${pythonExe}）`)
  } else {
    step(++stepNo, totalSteps, 'runtime：便携 Python 3.13（uv / python-build-standalone / --python-dir）')
    const staged = await stageRuntimePython(opts)
    pythonExe = staged.pythonExe
    runtimeSource = staged.sourceKind
    const expectedMinor = opts.python
    const actualVersion = pythonVersionOf(pythonExe)
    if (!actualVersion.startsWith(expectedMinor + '.')) {
      fail(`runtime Python 版本是 ${actualVersion}，与期望的 ${expectedMinor}.x 不符（C 扩展会装错 ABI）`)
    }
  }

  // 3) 依赖
  if (opts.skipDeps) {
    log('跳过依赖安装（沿用 runtime/Lib/site-packages）')
  } else {
    step(++stepNo, totalSteps, `runtime：安装 ${PACKAGES.length} 个锁定依赖（uv pip install --system）`)
    installDeps(pythonExe)
  }

  // 3.5) 裁剪（Lead 2026-10-27：uv 装完、校验之前；删完必须靠下一步重新校验兜住）
  step(++stepNo, totalSteps, 'runtime：裁剪（标准库测试/GUI + 字节码 + 三方测试/静态库）')
  const pruned = pruneRuntime(RUNTIME_DIR)
  log(`裁剪删除 ${pruned.items.length} 项 / ${formatMb(pruned.bytes)}`)

  // 4) 校验（失败非 0 退出）
  step(++stepNo, totalSteps, '校验：runtime 解释器 import engine 网关 + list_specs()')
  const validation = validate(pythonExe, opts.tmpRoot)
  const toolNames = validation.names
  // 兜底：engine 是构建产物（计划 §P2 只排除 __pycache__/*.pyc），校验若留下字节码就清掉再计量。
  const engineBytecode = cleanBytecode(ENGINE_DIR)
  if (engineBytecode.dirs || engineBytecode.files) {
    log(`清理 engine 里的字节码：${engineBytecode.dirs} 个 __pycache__、${engineBytecode.files} 个 .pyc`)
  }
  engine = { files: countFiles(ENGINE_DIR), bytes: dirSizeBytes(ENGINE_DIR) }
  // runtime 同理：并发跑着的 MCP server（别的会话）会往 site-packages 里写 pycache，构建收尾时清掉，
  // 保证 MANIFEST/体积报告描述的是「干净产物」；删不掉的记 skipped，不因此失败。
  const runtimeBytecode = cleanBytecode(RUNTIME_DIR)
  if (runtimeBytecode.dirs || runtimeBytecode.files) {
    log(`清理 runtime 里的字节码：${runtimeBytecode.dirs} 个 __pycache__、${runtimeBytecode.files} 个 .pyc`)
  }
  if (engineBytecode.skipped || runtimeBytecode.skipped) {
    log(`⚠️ 有 ${engineBytecode.skipped + runtimeBytecode.skipped} 项字节码删不掉（多半有进程正在用 runtime），已跳过`)
  }

  // 5) MANIFEST + 体积报告
  step(++stepNo, totalSteps, 'MANIFEST.json + 体积报告')
  const versions = readInstalledVersions(pythonExe)
  const runtimeBytes = dirSizeBytes(RUNTIME_DIR)
  const sitePackagesBytes = dirSizeBytes(pruned.sitePackages)
  const manifest = writeManifest({
    pythonExe,
    versions,
    engine,
    runtimeBytes,
    validation,
    prunedBytes: pruned.bytes,
    warnings: validation.warnings,
  })

  const engineBytes = engine.bytes
  const totalBytes = runtimeBytes + engineBytes
  log('')
  log('════════════════ 体积报告 ════════════════')
  log(`engine  ${formatMb(engineBytes).padStart(9)}  （${engine.files} 个文件）`)
  if (existsSync(join(PLUGIN_DIR, 'panel', 'index.html'))) {
    log(`panel   ${formatMb(dirSizeBytes(join(PLUGIN_DIR, 'panel'))).padStart(9)}  （前端构建产物，iframe 完整面板用）`)
  }
  log(`runtime ${formatMb(runtimeBytes).padStart(9)}  （Python 本体 ${formatMb(runtimeBytes - sitePackagesBytes)} + site-packages ${formatMb(sitePackagesBytes)}）`)
  log(`裁剪    ${formatMb(pruned.bytes).padStart(9)}  （${pruned.items.length} 项已从 runtime 删除）`)
  log(`总计    ${formatMb(totalBytes).padStart(9)}  （来源：${runtimeSource}）`)
  log(`工具：  ${JSON.stringify(toolNames)}`)
  log(`Python：${manifest.pythonVersion}`)
  if (pruned.items.length) {
    const top = [...pruned.items].sort((a, b) => b.bytes - a.bytes).slice(0, 8)
    log('裁剪明细（前 8 大）：' + top.map((item) => item.label + ' ' + formatMb(item.bytes)).join('；'))
  }
  if (totalBytes > TOTAL_SIZE_WARN_MB * 1024 * 1024) {
    log(`⚠️ 目录总量超过计划 §P2 的 ${TOTAL_SIZE_WARN_MB} MB 红线（npm 单包上限看的是 tgz，§6.4 为 100 MB）`)
  }
  if (totalBytes > SIZE_BUDGET_MB * 1024 * 1024) {
    log('')
    log(`⚠️ 裁剪后仍超 ${SIZE_BUDGET_MB} MB 预算 —— runtime 顶层明细（不激进删，先看是谁占的）：`)
    for (const row of runtimeBreakdown(RUNTIME_DIR)) log(`  ${row.name.padEnd(14)} ${formatMb(row.bytes)}`)
  }
  if (opts.measureTgz) {
    const tgz = measureTgz(opts.tmpRoot)
    log(tgz
      ? `tgz 体积（engine + runtime）：${formatMb(tgz.bytes)}（npm 单包上限 100 MB）` +
          (tgz.warned ? '（tar 报「文件在变」警告：打包时有别的进程在用 runtime，体积仅供参考）' : '')
      : '⚠️ tar 打包失败，tgz 体积未测')
  }
  if (validation.warnings.length) {
    log('')
    log('════════════════ 构建告警（不阻断，但真机集成要看） ════════════════')
    for (const warning of validation.warnings) log('⚠️  ' + warning)
  }
  log('构建成功。')
  return manifest
}

const invokedDirectly =
  process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)
if (invokedDirectly) {
  main(process.argv.slice(2)).catch((err) => {
    log('')
    log('❌ 构建失败：' + (err instanceof BuildError ? err.message : err?.stack ?? String(err)))
    process.exitCode = 1
  })
}
