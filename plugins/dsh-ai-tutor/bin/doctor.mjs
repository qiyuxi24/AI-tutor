#!/usr/bin/env node
/**
 * dsh-ai-tutor doctor —— 装完/升级后跑一次的自检（§P3 的 8 项 + 客户端 2 项 + 完整面板 3 项）。
 *
 * 设计取向：
 *   - **离线优先**：第 1~9、11、12 项不需要 DSH 在运行（12 会真的 spawn 一次 panel server）；
 *     第 10、13 项没有宿主时明确标 SKIP。
 *   - **只读**：除 --fix 创建数据根目录外不改任何东西；探测用的 DATA_ROOT/LOG_DIR
 *     指向系统临时目录，绝不碰真实 data/（panel server 也用这份临时环境）。
 *   - **不崩**：每一项自己 try/catch；runtime/engine 半成品时输出明确的 ❌ + 修法，
 *     而不是抛栈。
 *
 * 用法（在插件目录）：
 *   node bin/doctor.mjs
 *   node bin/doctor.mjs --fix                     # 只创建缺失的数据根目录（安全操作）
 *   node bin/doctor.mjs --host http://127.0.0.1:55448   # 额外检查 /ai-tutor/graph 与 /ai-tutor/panel 可达
 *   node bin/doctor.mjs --local-user 1            # 第 6/12 项用的 user_id（默认 1）
 * 环境变量：DSH_WEB_URL（等价 --host）、AI_TUTOR_DATA_ROOT、AI_TUTOR_LOCAL_USER、
 * AI_TUTOR_DOCTOR_PROBE_ROOT（探测临时目录根，默认系统临时目录；在受限沙箱里
 * python 只能写工作区时，把它指到一个可写目录才能跑第 12 项）。
 *
 * 退出码：有 ❌ → 1；全部通过或有 SKIP → 0。
 */

import { spawn, spawnSync } from 'node:child_process'
import {
  accessSync,
  constants as fsConstants,
  existsSync,
  mkdirSync,
  readFileSync,
  rmSync,
  statSync,
  statfsSync,
  writeFileSync,
} from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'

import {
  DEFAULT_LOCAL_USER_ID,
  ENGINE_DIR,
  LOCAL_HTTP_ENTRY_REL,
  PLUGIN_ROOT,
  RUNTIME_DIR,
  SERVER_ENTRY_REL,
  defaultDataRoot,
  findBundledPython,
  localModeEnv,
  readProcessEnv,
  resolveDataRoot,
  scrubEnv,
} from '../lib/paths.js'
import {
  PANEL_ENTRY_REL,
  createPanelSupervisor,
  findPanelInstallIssues,
  panelDir,
} from '../lib/panel.js'

const VERSION = '0.1.0'
const MIN_FREE_BYTES = 500 * 1024 * 1024
const MCP_HANDSHAKE_TIMEOUT_MS = 10_000
const STDERR_TAIL_CHARS = 1_500

const results = []

function record(status, index, title, details, fixes) {
  results.push({ status, index, title })
  const icon = status === 'pass' ? '✅' : status === 'fail' ? '❌' : 'SKIP'
  console.log(icon + ' ' + String(index).padStart(2, '0') + ' ' + title)
  for (const line of details || []) console.log('     ' + line)
  if (status === 'fail' && fixes && fixes.length > 0) {
    console.log('   修复建议：')
    for (const line of fixes) console.log('     - ' + line)
  }
}

const pass = (index, title, details) => record('pass', index, title, details, [])
const fail = (index, title, details, fixes) => record('fail', index, title, details, fixes)
const skip = (index, title, details) => record('skip', index, title, details, [])

function mb(bytes) {
  return Math.round(bytes / 1024 / 1024)
}

function tail(text, limit) {
  const value = String(text || '').trim()
  if (!value) return ''
  return value.length > limit ? '…' + value.slice(-limit) : value
}

function runSync(command, args, options) {
  const settings = options || {}
  const result = spawnSync(command, args, {
    encoding: 'utf8',
    timeout: settings.timeoutMs || 30_000,
    windowsHide: true,
    env: settings.env || process.env,
    cwd: settings.cwd || PLUGIN_ROOT,
  })
  return {
    status: result.status,
    stdout: String(result.stdout || '').trim(),
    stderr: String(result.stderr || '').trim(),
    error: result.error ? result.error.message : '',
  }
}

function parsePythonVersion(text) {
  const matched = /Python (\d+\.\d+\.\d+)/.exec(String(text || ''))
  return matched ? matched[1] : ''
}

function usage() {
  console.log('')
  console.log('用法：node bin/doctor.mjs [--fix] [--host <url>] [--local-user <n>]')
  console.log('')
  console.log('  --fix            只创建缺失的数据根目录（安全操作；不动已有数据）')
  console.log('  --host <url>     额外检查面板端点（/ai-tutor/graph 与 /ai-tutor/panel，例如 http://127.0.0.1:55448）；也可用 DSH_WEB_URL')
  console.log('  --local-user <n> 第 6/12 项 spawn 时用的 user_id（默认 ' + DEFAULT_LOCAL_USER_ID + '）')
  console.log('')
  console.log('检查项：')
  console.log('  01 包内 Python（runtime/）可执行')
  console.log('  02 runtime/MANIFEST.json 完整且与实跑版本一致')
  console.log('  03 engine 目录含 MCP server 入口')
  console.log('  04 engine 能在包内 Python 下 import gateway')
  console.log('  05 数据根目录可写')
  console.log('  06 能 spawn MCP server 并完成 tools/list 握手')
  console.log('  07 工具数 == gateway 白名单长度')
  console.log('  08 磁盘剩余空间 >= 500MB')
  console.log('  09 lib/client.js 语法与加载标记（离线检查）')
  console.log('  10 宿主端点 /ai-tutor/graph 可达（无宿主时 SKIP）')
  console.log('  11 完整面板静态产物 panel/index.html 存在')
  console.log('  12 完整面板后端能 spawn 且 /health 返回 200（离线）')
  console.log('  13 宿主端点 /ai-tutor/panel 可达且返回的 url 能访问（无宿主时 SKIP）')
  console.log('')
}

function parseArgs(argv) {
  const options = {
    fix: false,
    host: readProcessEnv('DSH_WEB_URL').trim(),
    localUserId: DEFAULT_LOCAL_USER_ID,
    help: false,
  }
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index]
    if (arg === '--fix') {
      options.fix = true
    } else if (arg === '--help' || arg === '-h') {
      options.help = true
    } else if (arg === '--host') {
      index += 1
      options.host = String(argv[index] || '').trim()
    } else if (arg.startsWith('--host=')) {
      options.host = arg.slice('--host='.length).trim()
    } else if (arg === '--local-user') {
      index += 1
      options.localUserId = Number(argv[index])
    } else if (arg.startsWith('--local-user=')) {
      options.localUserId = Number(arg.slice('--local-user='.length))
    } else {
      console.error('未知参数：' + arg)
      usage()
      process.exit(2)
    }
  }
  if (!Number.isInteger(options.localUserId) || options.localUserId <= 0) {
    console.error('--local-user 必须是正整数')
    process.exit(2)
  }
  return options
}

/**
 * 探测用临时目录：DATA_ROOT/LOG_DIR 都指这里，绝不碰真实 data/。
 *
 * 默认落在系统临时目录；AI_TUTOR_DOCTOR_PROBE_ROOT 可覆盖（受限沙箱里 python
 * 只被允许写工作区时，第 12 项需要它）。
 */
function createProbeDirs() {
  const override = readProcessEnv('AI_TUTOR_DOCTOR_PROBE_ROOT').trim()
  const baseRoot = override ? resolve(override) : tmpdir()
  const root = join(baseRoot, 'dsh-ai-tutor-doctor-' + process.pid)
  const dataRoot = join(root, 'data')
  const logDir = join(root, 'logs')
  mkdirSync(dataRoot, { recursive: true })
  mkdirSync(logDir, { recursive: true })
  // 与真实本地模式一致：两个数据根名字 + 本地 SECRET_KEY（<dataRoot>/secret_key）
  // + PYTHONDONTWRITEBYTECODE=1。engine 的 auth.py 在导入期要求 SECRET_KEY 非空，不给会让
  // 第 04/06 项误报（它测的是插件能否拉起来，不是密钥配置）。
  const env = scrubEnv(process.env, Object.assign({
    DATA_ROOT: dataRoot,
    AI_TUTOR_DATA_ROOT: dataRoot,
    LOG_DIR: logDir,
    NO_PROXY: '*',
    no_proxy: '*',
  }, localModeEnv(dataRoot, process.env)))
  return { root, dataRoot, logDir, env }
}

/**
 * 第 6 项：spawn engine 的 MCP server，走一遍 initialize + tools/list 握手。
 * 返回 { ok, tools, reason, detail, stderr }。
 */
function probeMcpTools(options) {
  return new Promise((resolvePromise) => {
    const args = ['-m', 'app.mcp_servers.kg_tools', '--local-user', String(options.localUserId)]
    const child = spawn(options.python, args, {
      cwd: options.engineDir,
      env: options.env,
      stdio: ['pipe', 'pipe', 'pipe'],
      windowsHide: true,
    })
    let settled = false
    let stdoutBuffer = ''
    let stderrTail = ''

    const finish = (result) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      try {
        child.kill()
      } catch (error) {
        // 已退出
      }
      resolvePromise(result)
    }

    const timer = setTimeout(() => {
      finish({
        ok: false,
        tools: [],
        reason: 'timeout',
        detail: MCP_HANDSHAKE_TIMEOUT_MS / 1000 + ' 秒内没有完成 initialize + tools/list 握手',
        stderr: stderrTail,
      })
    }, MCP_HANDSHAKE_TIMEOUT_MS)

    child.on('error', (error) => {
      finish({ ok: false, tools: [], reason: 'spawn', detail: '无法启动（' + error.message + '）', stderr: '' })
    })
    child.stdin.on('error', () => {
      // server 提前退出时写管道会 EPIPE：由 exit 分支给出结论
    })
    child.stderr.setEncoding('utf8')
    child.stderr.on('data', (chunk) => {
      stderrTail = (stderrTail + chunk).slice(-STDERR_TAIL_CHARS)
    })
    child.stdout.setEncoding('utf8')
    child.stdout.on('data', (chunk) => {
      stdoutBuffer += chunk
      let newline
      while ((newline = stdoutBuffer.indexOf('\n')) >= 0) {
        const line = stdoutBuffer.slice(0, newline).trim()
        stdoutBuffer = stdoutBuffer.slice(newline + 1)
        if (!line) continue
        let message = null
        try {
          message = JSON.parse(line)
        } catch (error) {
          continue
        }
        if (message && message.id === 1) {
          if (message.error) {
            finish({ ok: false, tools: [], reason: 'rpc', detail: 'initialize 失败：' + JSON.stringify(message.error), stderr: stderrTail })
            return
          }
          child.stdin.write(JSON.stringify({ jsonrpc: '2.0', method: 'notifications/initialized', params: {} }) + '\n')
          child.stdin.write(JSON.stringify({ jsonrpc: '2.0', id: 2, method: 'tools/list', params: {} }) + '\n')
        } else if (message && message.id === 2) {
          if (message.error) {
            finish({ ok: false, tools: [], reason: 'rpc', detail: 'tools/list 失败：' + JSON.stringify(message.error), stderr: stderrTail })
            return
          }
          const tools = message.result && Array.isArray(message.result.tools) ? message.result.tools : []
          finish({ ok: true, tools: tools.map((tool) => tool.name), reason: '', detail: '', stderr: stderrTail })
        }
      }
    })
    child.on('exit', (code, signal) => {
      finish({
        ok: false,
        tools: [],
        reason: 'exit',
        detail: '进程退出（code=' + code + ' signal=' + signal + '）'
          + (stdoutBuffer.trim() ? '；最后输出：' + tail(stdoutBuffer, 300) : ''),
        stderr: stderrTail,
      })
    })

    child.stdin.write(JSON.stringify({
      jsonrpc: '2.0',
      id: 1,
      method: 'initialize',
      params: {
        protocolVersion: '2024-11-05',
        capabilities: {},
        clientInfo: { name: 'dsh-ai-tutor-doctor', version: VERSION },
      },
    }) + '\n')
  })
}

function describeLocalUserSupport(stderr) {
  const text = String(stderr || '')
  if (/unrecognized arguments[^\n]*--local-user/.test(text)) {
    return '当前 engine 的 kg_tools.py 还不支持 --local-user（P1 的改动没同步进 engine）。'
      + '修法：拉最新代码后重新构建 engine（node scripts/build-plugin.mjs）——'
      + '否则本地单用户模式下 MCP 启动会直接失败。'
  }
  return ''
}

async function main() {
  const options = parseArgs(process.argv.slice(2))
  if (options.help) {
    usage()
    return
  }

  console.log('dsh-ai-tutor doctor v' + VERSION)
  console.log('插件目录：' + PLUGIN_ROOT)
  console.log('engine：  ' + ENGINE_DIR)
  console.log('runtime： ' + RUNTIME_DIR)
  console.log('')

  const probe = createProbeDirs()
  const bundledPython = findBundledPython()
  let actualPythonVersion = ''

  // ---------------------------------------------------------------- 01
  if (!bundledPython) {
    fail('01', '包内 Python（runtime/）', [
      '未找到 runtime/python.exe（或 runtime/bin/python）',
      'runtime 目录：' + RUNTIME_DIR + (existsSync(RUNTIME_DIR) ? '（存在，但缺解释器）' : '（不存在）'),
    ], [
      '从仓库根运行 node scripts/build-plugin.mjs 重新构建（会下载/复制 CPython 与依赖）',
      '或从发布包（必须含 runtime/）重新安装插件',
    ])
  } else {
    const version = runSync(bundledPython, ['--version'])
    const versionText = version.stdout || version.stderr
    actualPythonVersion = parsePythonVersion(versionText)
    if (version.status === 0 && actualPythonVersion) {
      pass('01', '包内 Python（runtime/）', [bundledPython + ' → Python ' + actualPythonVersion])
    } else {
      fail('01', '包内 Python（runtime/）', [
        '执行失败：' + bundledPython + ' --version → exit ' + version.status,
        version.error ? '错误：' + version.error : '输出：' + tail(versionText, 300),
      ], [
        'runtime 里的 CPython 不完整（被杀毒软件隔离 / 拷贝中断）→ 重新构建或重新安装插件',
      ])
    }
  }

  // ---------------------------------------------------------------- 02
  const manifestPath = join(RUNTIME_DIR, 'MANIFEST.json')
  if (!existsSync(manifestPath)) {
    fail('02', 'runtime/MANIFEST.json', ['未找到：' + manifestPath], [
      '构建未完成或不是通过 scripts/build-plugin.mjs 产出的包',
      '从仓库根运行 node scripts/build-plugin.mjs 重新构建',
    ])
  } else {
    let manifest = null
    try {
      manifest = JSON.parse(readFileSync(manifestPath, 'utf8'))
    } catch (error) {
      manifest = null
      fail('02', 'runtime/MANIFEST.json', ['JSON 解析失败：' + error.message], ['MANIFEST 损坏 → 重新构建（node scripts/build-plugin.mjs）'])
    }
    if (manifest) {
      const problems = []
      const declaredVersion = typeof manifest.pythonVersion === 'string' ? manifest.pythonVersion : ''
      if (!declaredVersion) problems.push('缺 pythonVersion 字段')
      else if (actualPythonVersion && declaredVersion !== actualPythonVersion) {
        problems.push('声明的 Python ' + declaredVersion + ' 与实际 ' + actualPythonVersion + ' 不一致（runtime 与 MANIFEST 不是同一次构建的）')
      }
      const packageList = Array.isArray(manifest.packages) ? manifest.packages : []
      if (packageList.length === 0) problems.push('packages 为空')
      const installed = manifest.installedPackages && typeof manifest.installedPackages === 'object' ? manifest.installedPackages : null
      if (!installed) {
        problems.push('缺 installedPackages 字段（无法确认依赖是否装齐）')
      } else {
        const missing = Object.keys(installed).filter((name) => installed[name] === null || installed[name] === undefined)
        if (missing.length > 0) problems.push('依赖没装齐：' + missing.join(', '))
      }
      if (problems.length > 0) {
        fail('02', 'runtime/MANIFEST.json', problems, [
          '从仓库根运行 node scripts/build-plugin.mjs 重新构建（构建末尾会做 import 自检）',
        ])
      } else {
        pass('02', 'runtime/MANIFEST.json', [
          'pythonVersion=' + declaredVersion + '，packages=' + packageList.length
            + '，builtAt=' + String(manifest.builtAt || '?'),
          'engineFiles=' + String(manifest.engineFiles || '?')
            + '，体积 ' + mb(Number(manifest.runtimeBytes) || 0) + 'MB(runtime) + ' + mb(Number(manifest.engineBytes) || 0) + 'MB(engine)',
        ])
      }
    }
  }

  // ---------------------------------------------------------------- 03
  const kgToolsEntry = join(ENGINE_DIR, SERVER_ENTRY_REL)
  const localHttpEntry = join(ENGINE_DIR, LOCAL_HTTP_ENTRY_REL)
  if (!existsSync(kgToolsEntry)) {
    fail('03', 'engine 目录（MCP server 入口）', [
      '未找到：' + kgToolsEntry,
      existsSync(ENGINE_DIR) ? 'engine 目录存在但内容不完整（半成品/旧版本）' : 'engine 目录不存在：' + ENGINE_DIR,
    ], [
      '从仓库根运行 node scripts/build-plugin.mjs（会把 backend/app 复制成 engine/app）',
      '或从发布包重新安装插件',
    ])
  } else {
    pass('03', 'engine 目录（MCP server 入口）', [
      kgToolsEntry,
      existsSync(localHttpEntry)
        ? 'local_http.py 存在（图谱面板后端就绪）'
        : '缺 local_http.py → 图谱面板会返回 501（MCP 工具不受影响；重新构建 engine 即修）',
    ])
  }

  // ---------------------------------------------------------------- 04
  let engineSpecs = null
  let engineAllowlist = null
  if (!bundledPython && !existsSync(kgToolsEntry)) {
    skip('04', 'engine 能在包内 Python 下 import gateway', ['缺 runtime 与 engine，无法检查（先修 01/03）'])
  } else if (!bundledPython) {
    skip('04', 'engine 能在包内 Python 下 import gateway', ['缺包内 Python（先修 01）'])
  } else {
    const pythonProbeSource = [
      'import json, sys',
      'sys.path.insert(0, sys.argv[1])',
      'from app.core.agent_tools import gateway',
      'print("LIST_SPECS=" + json.dumps([spec["name"] for spec in gateway.list_specs()]))',
      'print("ALLOWLIST=" + json.dumps(list(getattr(gateway, "ALLOWLIST", []))))',
    ].join('\n')
    const probeResult = runSync(bundledPython, ['-c', pythonProbeSource, ENGINE_DIR], {
      env: probe.env,
      cwd: ENGINE_DIR,
      timeoutMs: 60_000,
    })
    const lines = probeResult.stdout.split('\n')
    for (const line of lines) {
      const trimmed = line.trim()
      if (trimmed.startsWith('LIST_SPECS=')) {
        try { engineSpecs = JSON.parse(trimmed.slice('LIST_SPECS='.length)) } catch (error) { engineSpecs = null }
      } else if (trimmed.startsWith('ALLOWLIST=')) {
        try { engineAllowlist = JSON.parse(trimmed.slice('ALLOWLIST='.length)) } catch (error) { engineAllowlist = null }
      }
    }
    if (probeResult.status === 0 && Array.isArray(engineSpecs)) {
      pass('04', 'engine 能在包内 Python 下 import gateway', [
        'LIST_SPECS=' + JSON.stringify(engineSpecs),
        'ALLOWLIST=' + JSON.stringify(engineAllowlist || []),
      ])
    } else {
      fail('04', 'engine 能在包内 Python 下 import gateway', [
        '退出码：' + probeResult.status + (probeResult.error ? '（' + probeResult.error + '）' : ''),
        tail(probeResult.stderr, STDERR_TAIL_CHARS) || '（stderr 为空）',
      ], [
        '看 stderr 里的 ModuleNotFoundError：多半是 runtime 依赖没装齐（修 02）或 engine 与 runtime 不同批次',
        '从仓库根运行 node scripts/build-plugin.mjs 重新构建',
      ])
    }
  }

  // ---------------------------------------------------------------- 05
  const configuredDataRoot = resolveDataRoot({}, readProcessEnv)
  const dataRoot = configuredDataRoot || defaultDataRoot()
  const dataRootSource = configuredDataRoot ? 'AI_TUTOR_DATA_ROOT' : '平台默认位置'
  if (!existsSync(dataRoot)) {
    if (options.fix) {
      try {
        mkdirSync(dataRoot, { recursive: true })
        writeFileSync(join(dataRoot, '.doctor-write-test'), 'ok')
        rmSync(join(dataRoot, '.doctor-write-test'), { force: true })
        pass('05', '数据根目录可写', ['--fix 已创建：' + dataRoot + '（来源：' + dataRootSource + '）'])
      } catch (error) {
        fail('05', '数据根目录可写', ['目录创建/写入失败：' + error.message], ['检查父目录权限，或把 AI_TUTOR_DATA_ROOT 指到一个可写目录'])
      }
    } else {
      fail('05', '数据根目录可写', [
        '目录不存在：' + dataRoot + '（来源：' + dataRootSource + '）',
      ], [
        '运行 node bin/doctor.mjs --fix 创建（只创建目录，不动数据）',
        '或设 AI_TUTOR_DATA_ROOT 指向已有数据根（想复用 AI-tutor 已有图谱就指到它的 data/）',
      ])
    }
  } else {
    try {
      const stats = statSync(dataRoot)
      if (!stats.isDirectory()) throw new Error('不是目录')
      accessSync(dataRoot, fsConstants.W_OK)
      writeFileSync(join(dataRoot, '.doctor-write-test'), 'ok')
      rmSync(join(dataRoot, '.doctor-write-test'), { force: true })
      pass('05', '数据根目录可写', [dataRoot + '（来源：' + dataRootSource + '）'])
    } catch (error) {
      fail('05', '数据根目录可写', ['写入测试失败：' + error.message], ['检查目录权限（是否为只读盘 / 被安全软件拦截），或改 AI_TUTOR_DATA_ROOT'])
    }
  }

  // ---------------------------------------------------------------- 06
  let mcpTools = null
  if (!bundledPython) {
    skip('06', 'MCP server 能 spawn 并完成 tools/list 握手', ['缺包内 Python（先修 01）'])
  } else if (!existsSync(kgToolsEntry)) {
    skip('06', 'MCP server 能 spawn 并完成 tools/list 握手', ['缺 engine 入口（先修 03）'])
  } else {
    const handshake = await probeMcpTools({
      python: bundledPython,
      engineDir: ENGINE_DIR,
      env: probe.env,
      localUserId: options.localUserId,
    })
    if (handshake.ok) {
      mcpTools = handshake.tools
      pass('06', 'MCP server 能 spawn 并完成 tools/list 握手', [
        '--local-user ' + options.localUserId + ' 启动成功，工具：' + JSON.stringify(handshake.tools),
      ])
    } else {
      const localUserHint = describeLocalUserSupport(handshake.stderr)
      const details = ['失败原因：' + handshake.reason + ' —— ' + handshake.detail]
      if (handshake.stderr && handshake.stderr.trim()) details.push('stderr 尾部：' + tail(handshake.stderr, STDERR_TAIL_CHARS))
      if (localUserHint) details.push(localUserHint)
      fail('06', 'MCP server 能 spawn 并完成 tools/list 握手', details, [
        '先修 01/02（runtime 缺解释器或依赖）与 03（engine 不完整），再重跑本命令',
        '若 stderr 提到 --local-user：engine 落后于 P1，重新构建 engine',
      ])
    }
  }

  // ---------------------------------------------------------------- 07
  if (!Array.isArray(engineAllowlist) || !Array.isArray(mcpTools)) {
    skip('07', '工具数 == gateway 白名单长度', ['04 或 06 未通过，无法比对'])
  } else if (engineAllowlist.length === mcpTools.length) {
    pass('07', '工具数 == gateway 白名单长度', [
      '白名单 ' + engineAllowlist.length + ' 个：' + JSON.stringify(engineAllowlist),
      'MCP tools/list ' + mcpTools.length + ' 个：' + JSON.stringify(mcpTools),
    ])
  } else {
    fail('07', '工具数 == gateway 白名单长度', [
      '白名单 ' + engineAllowlist.length + ' 个：' + JSON.stringify(engineAllowlist),
      'MCP tools/list ' + mcpTools.length + ' 个：' + JSON.stringify(mcpTools),
    ], [
      'engine 的 tools/__init__.py 与 gateway.ALLOWLIST 不同步（少注册/多注册）→ 重新构建 engine 后重跑',
    ])
  }

  // ---------------------------------------------------------------- 08
  try {
    const stats = statfsSync(PLUGIN_ROOT)
    const freeBytes = Number(stats.bavail) * Number(stats.bsize)
    if (!Number.isFinite(freeBytes) || freeBytes <= 0) {
      skip('08', '磁盘剩余空间 >= 500MB', ['宿主平台不支持 statfs，跳过'])
    } else if (freeBytes >= MIN_FREE_BYTES) {
      pass('08', '磁盘剩余空间 >= 500MB', ['可用 ' + mb(freeBytes) + 'MB（' + PLUGIN_ROOT + ' 所在分区，要求 >= 500MB）'])
    } else {
      fail('08', '磁盘剩余空间 >= 500MB', ['可用 ' + mb(freeBytes) + 'MB < 500MB'], ['清理磁盘后重试；插件与数据根都在本机'])
    }
  } catch (error) {
    skip('08', '磁盘剩余空间 >= 500MB', ['statfs 不可用：' + error.message])
  }

  // ---------------------------------------------------------------- 09
  const clientPath = join(PLUGIN_ROOT, 'lib', 'client.js')
  if (!existsSync(clientPath)) {
    fail('09', 'lib/client.js 语法与加载标记', ['未找到：' + clientPath], [
      '包不完整 → 从发布包重新安装，或从仓库重新打包',
    ])
  } else {
    let clientSource = ''
    try {
      clientSource = readFileSync(clientPath, 'utf8')
    } catch (error) {
      fail('09', 'lib/client.js 语法与加载标记', ['读取失败：' + error.message], ['检查文件权限'])
    }
    if (clientSource) {
      const problems = []
      const missingMarkers = ['__ModuleLoader__.load', 'sidebar.panellist', 'apply']
        .filter((marker) => !clientSource.includes(marker))
      if (missingMarkers.length > 0) problems.push('缺加载标记：' + missingMarkers.join(', '))
      try {
        // 只做语法解析（不执行）；客户端插件是手写 factory，不能是 ESM
        new Function(clientSource)
      } catch (error) {
        problems.push('语法错误：' + error.message)
      }
      if (problems.length > 0) {
        fail('09', 'lib/client.js 语法与加载标记', problems, [
          '修好后在插件目录运行 node --check lib/client.js 复验；写坏 client.js 只该影响面板，不该影响宿主',
        ])
      } else {
        pass('09', 'lib/client.js 语法与加载标记', [
          '语法解析通过，含 __ModuleLoader__.load / sidebar.panellist / apply',
        ])
      }
    }
  }

  // ---------------------------------------------------------------- 10
  const localHttpPresent = existsSync(localHttpEntry)
  if (!options.host) {
    skip('10', '宿主端点 /ai-tutor/graph 可达', [
      '未指定宿主地址（--host http://127.0.0.1:<port> 或 DSH_WEB_URL）',
      localHttpPresent
        ? 'engine 含 local_http.py：装上插件并启动 DSH 后，可用 --host 复检面板端点'
        : '注意：engine 里还没有 local_http.py → 即使装了插件，面板也会返回 501（先修 03 的提示）',
    ])
  } else {
    let graphUrl = ''
    try {
      graphUrl = new URL('/ai-tutor/graph?limit=1', options.host).toString()
    } catch (error) {
      graphUrl = ''
    }
    if (!graphUrl) {
      fail('10', '宿主端点 /ai-tutor/graph 可达', ['宿主地址不合法：' + options.host], ['用 --host http://127.0.0.1:<port> 指定 DSH 的 Web 地址'])
    } else {
      try {
        const response = await fetch(graphUrl, {
          headers: { accept: 'application/json' },
          signal: AbortSignal.timeout(5_000),
        })
        const text = await response.text()
        let payload = null
        try { payload = text ? JSON.parse(text) : null } catch (error) { payload = null }
        if (response.status === 200) {
          const nodeCount = payload && typeof payload === 'object' && payload.nodeCount !== undefined ? payload.nodeCount : '?'
          pass('10', '宿主端点 /ai-tutor/graph 可达', ['GET ' + graphUrl + ' → 200（nodeCount=' + nodeCount + '）'])
        } else if (response.status === 501) {
          fail('10', '宿主端点 /ai-tutor/graph 可达', [
            'GET ' + graphUrl + ' → 501（插件在，但本地 Python 面板后端没就绪）',
            payload && payload.detail ? String(payload.detail) : '',
            payload && payload.hint ? String(payload.hint) : '',
          ].filter(Boolean), [
            'engine 缺 app/mcp_servers/local_http.py → 重新构建 engine（node scripts/build-plugin.mjs）或装新版插件',
            'MCP 工具不受影响；面板先不显示数据',
          ])
        } else if (response.status === 502) {
          fail('10', '宿主端点 /ai-tutor/graph 可达', [
            'GET ' + graphUrl + ' → 502（面板后端启动/转发失败）',
            payload && payload.detail ? String(payload.detail) : '',
          ].filter(Boolean), [
            '重跑本命令的第 01/04/06 项定位 python 与依赖问题',
            '若 06 报 unrecognized arguments --local-user：重新构建 engine（P1 未同步）',
          ])
        } else if (response.status === 404) {
          fail('10', '宿主端点 /ai-tutor/graph 可达', ['GET ' + graphUrl + ' → 404（宿主没有这条路由）'], [
            '插件没装/没启用，或装完没重启 DSH',
            '安装：dsh plugin add ' + PLUGIN_ROOT + '（或把包名列进 profile 的 dsh.profile.bundles）',
          ])
        } else {
          fail('10', '宿主端点 /ai-tutor/graph 可达', [
            'GET ' + graphUrl + ' → ' + response.status,
            text ? tail(text, 300) : '',
          ].filter(Boolean), ['看 DSH 控制台日志里 [ai-tutor] 开头的报错'])
        }
      } catch (error) {
        fail('10', '宿主端点 /ai-tutor/graph 可达', ['无法连接 ' + options.host + '：' + error.message], [
          '确认 DSH 正在运行，--host 用它的 Web 地址（GUI 地址栏里的 http://127.0.0.1:<port>）',
        ])
      }
    }
  }

  // ---------------------------------------------------------------- 11
  const panelRoot = panelDir(PLUGIN_ROOT)
  const panelIndexPath = join(panelRoot, 'index.html')
  if (!existsSync(panelIndexPath)) {
    const panelIssues = findPanelInstallIssues(ENGINE_DIR, PLUGIN_ROOT)
    fail('11', '完整面板静态产物 panel/index.html', [
      '未找到：' + panelIndexPath,
      existsSync(panelRoot) ? 'panel/ 目录存在但缺 index.html（构建半成品）' : 'panel/ 目录不存在：' + panelRoot,
      panelIssues.length > 0 ? '当前缺失：' + panelIssues.join('；') : '',
    ].filter(Boolean), [
      '从仓库根运行 node scripts/build-panel.mjs 生成 panel/（构建 frontend 后拷进插件包）',
      '发布包应自带 panel/；缺它时「AI 导师」面板返回 501，MCP 工具与「知识图谱」面板不受影响',
    ])
  } else {
    let indexBytes = 0
    try {
      indexBytes = statSync(panelIndexPath).size
    } catch (error) {
      indexBytes = 0
    }
    pass('11', '完整面板静态产物 panel/index.html', [
      panelIndexPath + '（' + Math.max(1, Math.round(indexBytes / 1024)) + 'KB）',
    ])
  }

  // ---------------------------------------------------------------- 12
  const panelEntry = join(ENGINE_DIR, PANEL_ENTRY_REL)
  if (!bundledPython) {
    skip('12', '完整面板后端能 spawn 且 /health 返回 200', ['缺包内 Python（先修 01）'])
  } else if (!existsSync(panelEntry)) {
    fail('12', '完整面板后端能 spawn 且 /health 返回 200', [
      '未找到：' + panelEntry,
      existsSync(ENGINE_DIR) ? 'engine 目录存在但缺 panel_server.py（engine 落后）' : 'engine 目录不存在：' + ENGINE_DIR,
    ], [
      '从仓库根运行 node scripts/build-plugin.mjs 重新构建 engine（app/mcp_servers/panel_server.py 会一起拷进去）',
    ])
  } else {
    const panelSupervisor = createPanelSupervisor({
      engineDir: ENGINE_DIR,
      python: bundledPython,
      dataRoot: probe.dataRoot,
      localUserId: options.localUserId,
      pluginRoot: PLUGIN_ROOT,
      // 探测用临时环境（DATA_ROOT/LOG_DIR 都是 temp），绝不碰真实 data/
      baseEnv: probe.env,
      startTimeoutMs: 45_000,
      healthTimeoutMs: 15_000,
    })
    try {
      const started = await panelSupervisor.ensureRunning()
      pass('12', '完整面板后端能 spawn 且 /health 返回 200', [
        '--local-user ' + options.localUserId + ' 启动成功：GET ' + started.url + '/health → 200',
        existsSync(panelIndexPath)
          ? 'panel/index.html 就绪，iframe 可加载'
          : '注意：panel/index.html 缺失 → iframe 首页会 404（先修 11）',
      ])
    } catch (error) {
      const spawnMessage = error && error.message ? error.message : String(error)
      // 受限沙箱（python 子进程只能写工作区）会让探测根在系统临时目录时直接 PermissionError。
      // doctor 自身无法猜出"哪个目录可写"，只能明确把覆盖变量告诉用户（2026-10-27 实测）。
      // stderr 尾部常常只剩一行 traceback（真实 PermissionError 在上文），所以 code=3 也提醒一次。
      const probeDenied = !readProcessEnv('AI_TUTOR_DOCTOR_PROBE_ROOT').trim() &&
        (/PermissionError|WinError 5|拒绝访问|EACCES|EPERM/.test(spawnMessage) ||
          /启动即退出（code=3/.test(spawnMessage))
      fail('12', '完整面板后端能 spawn 且 /health 返回 200', [
        '启动失败：' + spawnMessage,
        error && error.code === 'PANEL_MISSING' ? '缺产物：panel_server.py 或 panel/index.html —— 见第 11 项与 README「完整面板」' : '',
        probeDenied ? '探测根在系统临时目录且子进程无权写入 → 设 AI_TUTOR_DOCTOR_PROBE_ROOT=<可写目录> 后重跑（受限沙箱/受控终端常见）' : '',
      ].filter(Boolean), [
        '先修 01/02（runtime 解释器与依赖）与 11（panel/ 产物）',
        'engine 与 runtime 不同批次时重新构建：node scripts/build-plugin.mjs',
        'MCP 工具与「知识图谱」面板不受影响；只有「AI 导师」完整面板打不开',
      ])
    } finally {
      panelSupervisor.stop()
    }
  }

  // ---------------------------------------------------------------- 13
  if (!options.host) {
    skip('13', '宿主端点 /ai-tutor/panel 可达且 url 能访问', [
      '未指定宿主地址（--host http://127.0.0.1:<port> 或 DSH_WEB_URL）',
      existsSync(panelEntry) && existsSync(panelIndexPath)
        ? 'engine 与 panel/ 都在：装上插件并启动 DSH 后，可用 --host 复检完整面板端点'
        : '注意：panel_server.py 或 panel/index.html 缺失 → 即使装了插件也会返回 501',
    ])
  } else {
    let hostPanelUrl = ''
    try {
      hostPanelUrl = new URL('/ai-tutor/panel', options.host).toString()
    } catch (error) {
      hostPanelUrl = ''
    }
    if (!hostPanelUrl) {
      fail('13', '宿主端点 /ai-tutor/panel 可达且 url 能访问', ['宿主地址不合法：' + options.host], ['用 --host http://127.0.0.1:<port> 指定 DSH 的 Web 地址'])
    } else {
      try {
        const response = await fetch(hostPanelUrl, {
          headers: { accept: 'application/json' },
          signal: AbortSignal.timeout(45_000),
        })
        const text = await response.text()
        let payload = null
        try { payload = text ? JSON.parse(text) : null } catch (error) { payload = null }
        if (response.status === 200) {
          const panelUrl = payload && typeof payload.url === 'string' ? payload.url : ''
          if (!/^http:\/\/(127\.0\.0\.1|localhost):\d+$/.test(panelUrl)) {
            fail('13', '宿主端点 /ai-tutor/panel 可达且 url 能访问', [
              'GET ' + hostPanelUrl + ' → 200，但返回的 url 不是本机回环地址：' + JSON.stringify(panelUrl),
              text ? tail(text, 300) : '',
            ].filter(Boolean), ['index.js 的 registerPanelRoute 组装返回体出错（这是插件 bug，把上面输出贴给维护者）'])
          } else {
            let pageStatus = 0
            let pageError = ''
            try {
              const page = await fetch(panelUrl.replace(/\/+$/, '') + '/', {
                headers: { accept: 'text/html' },
                signal: AbortSignal.timeout(15_000),
              })
              pageStatus = page.status
              await page.text()
            } catch (error) {
              pageError = error.message
            }
            if (pageStatus === 200) {
              pass('13', '宿主端点 /ai-tutor/panel 可达且 url 能访问', [
                'GET ' + hostPanelUrl + ' → 200（url=' + panelUrl + '）',
                'GET ' + panelUrl + '/ → 200（iframe 首页可加载）',
              ])
            } else {
              fail('13', '宿主端点 /ai-tutor/panel 可达且 url 能访问', [
                'GET ' + hostPanelUrl + ' → 200（url=' + panelUrl + '），但 iframe 首页访问失败',
                pageError ? '错误：' + pageError : 'HTTP ' + pageStatus,
              ], [
                '确认端口没有被防火墙/安全软件拦截；' + panelUrl + ' 是临时端口，重启后可能变化',
                '先修第 11 项（panel/index.html 缺失会让首页 404）',
              ])
            }
          }
        } else if (response.status === 501) {
          fail('13', '宿主端点 /ai-tutor/panel 可达且 url 能访问', [
            'GET ' + hostPanelUrl + ' → 501（插件在，但完整面板产物没就绪）',
            payload && payload.detail ? String(payload.detail) : '',
            payload && payload.hint ? String(payload.hint) : '',
          ].filter(Boolean), [
            'engine 缺 app/mcp_servers/panel_server.py 或 plugins/dsh-ai-tutor/panel/index.html → 先修 11/12',
            'MCP 工具与「知识图谱」面板不受影响',
          ])
        } else if (response.status === 502) {
          fail('13', '宿主端点 /ai-tutor/panel 可达且 url 能访问', [
            'GET ' + hostPanelUrl + ' → 502（本地面板服务启动失败）',
            payload && payload.detail ? String(payload.detail) : '',
            payload && payload.hint ? String(payload.hint) : '',
          ].filter(Boolean), [
            '重跑本命令的第 01/04/06/12 项定位 python、依赖与 panel_server.py 问题',
            'DSH 控制台里看 [ai-tutor] 开头的报错（stderr 会带 traceback 尾部）',
          ])
        } else if (response.status === 404) {
          fail('13', '宿主端点 /ai-tutor/panel 可达且 url 能访问', ['GET ' + hostPanelUrl + ' → 404（宿主没有这条路由）'], [
            '插件没装/没启用，或装完没重启 DSH',
            '安装：dsh plugin add ' + PLUGIN_ROOT + '（或把包名列进 profile 的 dsh.profile.bundles）',
          ])
        } else {
          fail('13', '宿主端点 /ai-tutor/panel 可达且 url 能访问', [
            'GET ' + hostPanelUrl + ' → ' + response.status,
            text ? tail(text, 300) : '',
          ].filter(Boolean), ['看 DSH 控制台日志里 [ai-tutor] 开头的报错'])
        }
      } catch (error) {
        fail('13', '宿主端点 /ai-tutor/panel 可达且 url 能访问', ['无法连接 ' + options.host + '：' + error.message], [
          '确认 DSH 正在运行，--host 用它的 Web 地址（GUI 地址栏里的 http://127.0.0.1:<port>）',
          '首次请求会懒启动本地面板服务，慢是正常的（最多约 30 秒）',
        ])
      }
    }
  }

  // ------------------------------------------------------------- 收尾
  try {
    rmSync(probe.root, { recursive: true, force: true })
  } catch (error) {
    // 临时目录清理失败无所谓
  }

  const passed = results.filter((item) => item.status === 'pass').length
  const failed = results.filter((item) => item.status === 'fail').length
  const skipped = results.filter((item) => item.status === 'skip').length
  console.log('')
  console.log('汇总：' + passed + ' 通过 / ' + failed + ' 失败 / ' + skipped + ' 跳过（共 ' + results.length + ' 项）')
  if (failed > 0) {
    console.log('有失败项：按上面的修复建议处理后再跑一次。模型侧工具是否可用，以第 04/06 项为准。')
    process.exitCode = 1
  } else {
    console.log('全部通过。')
  }
}

main().catch((error) => {
  console.error('doctor 自身崩溃（这是 bug，请把下面这段贴给维护者）：')
  console.error(error && error.stack ? error.stack : error)
  process.exitCode = 1
})
