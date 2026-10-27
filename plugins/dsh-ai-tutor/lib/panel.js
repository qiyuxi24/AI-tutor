/**
 * 完整面板（AI-tutor 整套 FastAPI 应用）的本地服务 supervisor（宿主侧，Node）。
 *
 * 与 lib/local-http.js 的分工：
 *   - local-http.js 只跑 `app.mcp_servers.local_http`（stdlib http.server，只读 /graph）；
 *   - 本模块跑 `app.mcp_servers.panel_server`（完整 app.main:app + 托管静态前端），
 *     给 DSH 面板里的 iframe 用：可视化图谱 / 文件上传 / 建图 / RAG / 出题 / 对话。
 *
 * 职责与 local-http.js 同构：
 *   1. **懒启动**（第一次请求才起，不在插件激活时拖慢启动）；
 *   2. 读 stdout 里的 `PORT=<n>` 拿到随机端口（30s 超时）；
 *   3. 对 http://127.0.0.1:<port>/health 做健康检查（2xx 才算就绪）；
 *   4. 插件卸载时 kill 子进程（不留孤儿进程占端口）。
 *
 * 环境变量：完整应用需要 LLM key 等配置，所以这里比 local_http 多两步：
 *   - 把**根 .env 透传**给子进程（候选：config.envFile → <dataRoot 的父目录>/.env →
 *     <engineDir 的父目录>/.env；后者在仓库开发时就是仓库根 .env）；
 *   - 永不打印 .env 的值；SECRET_KEY 不从 .env 读（本地模式用 <dataRoot>/secret_key，
 *     保证面板自己的会话密钥稳定且每台机器不同）。
 *
 * 失败语义：**绝不让宿主崩**。缺 panel 产物 → 501（可操作文案）；启动/健康检查失败
 * → 502；dispose 后不再启动。模型侧 MCP 工具与「知识图谱」面板不经过这条链路。
 *
 * 子进程生命周期：stdin 保持打开（与 local_http 一致；panel_server 若以 stdin EOF
 * 为退出信号也能优雅收尾），dispose 时"先 kill、再关 stdin"。
 *
 * @module dsh-ai-tutor/lib/panel
 */

import { spawn } from 'node:child_process'
import { existsSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'

import { PLUGIN_ROOT, dataEnv, localModeEnv, pickTutorEnv, scrubEnv } from './paths.js'

/** panel server 入口（相对 engine 目录）。 */
export const PANEL_ENTRY_REL = join('app', 'mcp_servers', 'panel_server.py')

/** 托管前端构建产物目录名（相对插件根）。 */
export const PANEL_DIR_NAME = 'panel'

/** 起 panel_server 的模块名（cwd = engine 目录，故用 -m）。 */
const PANEL_ARGS = ['-m', 'app.mcp_servers.panel_server']

/** 启动超时：完整应用要 import fastapi / uvicorn / 各业务模块，给宽一点。 */
const START_TIMEOUT_MS = 30_000

/** 健康检查总超时（端口已打印但 uvicorn 可能还没开始 accept）。 */
const HEALTH_TIMEOUT_MS = 8_000

/** 单次健康探测请求超时。 */
const HEALTH_REQUEST_TIMEOUT_MS = 2_000

/** 健康检查重试间隔。 */
const HEALTH_RETRY_MS = 250

/** 错误信息里保留的 stderr 尾部长度。 */
const STDERR_TAIL_CHARS = 800

/** .env 里绝不透传的变量（本地 secret 优先，避免两套密钥互相覆盖）。 */
const DOTENV_SKIP_KEYS = new Set(['SECRET_KEY'])

/** 从错误详情里抹掉密钥类变量的值（stderr 万一打印了环境）。 */
const SECRET_DETAIL_RE = /([A-Za-z0-9_]*(?:SECRET|TOKEN|KEY|PASSWORD)[A-Za-z0-9_]*)(\s*[=:]\s*)([^\s,;]+)/gi

/** 平台默认的 panel 目录。 */
export function panelDir(pluginRoot = PLUGIN_ROOT) {
  return join(pluginRoot, PANEL_DIR_NAME)
}

/**
 * 列出 panel 完整面板缺的安装产物（空数组 = 齐了）。
 *
 * 与 local_http 的 `existsSync(entry)` 一样每次请求现查：重建 panel/ 或升级
 * engine 后不必重装插件。
 *
 * @param {string} engineDir - engine 绝对路径。
 * @param {string} [pluginRoot] - 插件包根。
 * @returns {string[]} 缺失文件的绝对路径。
 */
export function findPanelInstallIssues(engineDir, pluginRoot = PLUGIN_ROOT) {
  const issues = []
  const entry = join(engineDir, PANEL_ENTRY_REL)
  if (!existsSync(entry)) issues.push(entry)
  const indexHtml = join(panelDir(pluginRoot), 'index.html')
  if (!existsSync(indexHtml)) issues.push(indexHtml)
  return issues
}

/**
 * 缺 panel 产物时的可操作文案（doctor 第 11、12 项会复述同一件事）。
 *
 * @param {string} engineDir - engine 绝对路径。
 * @param {string} [pluginRoot] - 插件包根。
 * @returns {{ error: string, detail: string, hint: string }}
 */
export function missingPanelMessage(engineDir, pluginRoot = PLUGIN_ROOT) {
  const issues = findPanelInstallIssues(engineDir, pluginRoot)
  return {
    error: '完整面板（AI 导师）还没就绪，暂时打不开。',
    detail: issues.length > 0 ? '缺少文件：' + issues.join('；') : '面板产物不完整。',
    hint: 'panel/ 是前端构建产物：在仓库根运行 node scripts/build-panel.mjs 生成；'
      + 'engine 需要含 app/mcp_servers/panel_server.py（用最新 backend 重建 engine/）。'
      + '在插件目录运行 node bin/doctor.mjs 看第 11、12 项。'
      + '模型侧 MCP 工具与「知识图谱」面板不受影响。',
  }
}

/**
 * 解析一个 .env 文件（KEY=VALUE；跳过注释/空行；去成对引号）。
 *
 * **只返回值表，不打印任何值**。文件不存在/不可读时返回 null。
 *
 * @param {string} file - .env 路径。
 * @returns {Record<string, string>|null}
 */
function parseEnvFile(file) {
  let text
  try {
    text = readFileSync(file, 'utf8')
  } catch {
    return null
  }
  const values = {}
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim()
    if (!line || line.startsWith('#')) continue
    const eq = line.indexOf('=')
    if (eq <= 0) continue
    const key = line.slice(0, eq).trim()
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(key)) continue
    if (DOTENV_SKIP_KEYS.has(key)) continue
    let value = line.slice(eq + 1).trim()
    const quoted = value.length >= 2
      && ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'")))
    if (quoted) value = value.slice(1, -1)
    values[key] = value
  }
  return values
}

/**
 * .env 候选路径（按优先级）：显式 config.envFile → <dataRoot 的父目录>/.env →
 * <engineDir 的父目录>/.env。
 *
 * 第三个在仓库开发时正是**仓库根 .env**（engineDir = backend），
 * 在发布包里是插件根（一般不存在，无副作用）。
 *
 * @param {{engineDir: string, dataRoot?: string, envFile?: string}} options - 参数。
 * @returns {string[]} 去重后的候选路径。
 */
function envFileCandidates(options) {
  const candidates = []
  const push = (value) => {
    if (typeof value !== 'string' || value.length === 0) return
    if (!candidates.includes(value)) candidates.push(value)
  }
  push(options.envFile)
  if (options.dataRoot) push(join(dirname(options.dataRoot), '.env'))
  push(join(dirname(options.engineDir), '.env'))
  return candidates
}

/**
 * 组装 panel_server 的子进程环境。
 *
 * 顺序：宿主环境（先 scrub 掉密钥类）→ .env 透传 → 数据根 → 本地 SECRET_KEY →
 * 托管目录 / 编码。**.env 不覆盖已存在的进程环境变量**；SECRET_KEY 一律以本地
 * secret 为准（不从 .env 读）。
 *
 * @param {{engineDir: string, dataRoot?: string, pluginRoot?: string, envFile?: string, secretKey?: string, baseEnv?: Record<string, string>}} options - 参数。
 *   baseEnv 是宿主环境基线（默认 process.env；doctor 传探测用临时环境，保证不碰真实 data/）。
 * @returns {Record<string, string>} 子进程 env。
 */
function panelEnvironment(options) {
  const pluginRoot = options.pluginRoot || PLUGIN_ROOT
  const baseEnv = options.baseEnv || process.env
  const dotenv = {}
  for (const file of envFileCandidates(options)) {
    const parsed = parseEnvFile(file)
    if (!parsed) continue
    for (const [key, value] of Object.entries(parsed)) {
      // 已存在的进程环境变量优先（空字符串视为未设置，与 index.js::readEnv 口径一致）
      const current = baseEnv[key]
      if (current !== undefined && current !== '') continue
      dotenv[key] = value
    }
  }

  const extra = Object.assign(
    {},
    dotenv,
    dataEnv(options.dataRoot),
    localModeEnv(options.dataRoot, baseEnv, options.secretKey),
    {
      AI_TUTOR_PANEL_DIR: panelDir(pluginRoot),
      PYTHONDONTWRITEBYTECODE: '1',
      PYTHONIOENCODING: 'utf-8',
    },
  )
  // 关键：scrubEnv 会按名字清掉 *KEY*，而 engine 的 LLM_API_KEY / DASHSCOPE_API_KEY 正好命中 ——
  // 清洗之后必须把 AI-tutor 自己的配置项补回来（见 paths.js::pickTutorEnv），
  // 否则面板能开、对话却报 Missing credentials（2026-10-27 真机）。
  return Object.assign(scrubEnv(baseEnv, extra), pickTutorEnv(baseEnv, dotenv))
}

/** 抹掉错误详情里的密钥值（stderr 可能带 traceback / 环境片段）。 */
function sanitizeDetail(text) {
  return String(text == null ? '' : text).replace(SECRET_DETAIL_RE, '$1$2<已隐藏>')
}

/** 睡一下。 */
function sleep(ms) {
  return new Promise((resolveSleep) => setTimeout(resolveSleep, ms))
}

/**
 * 创建完整面板的 supervisor。返回对象在插件生命周期内复用；stop() 之后不可再启动。
 *
 * @param {object} options - 参数。
 * @param {string} options.engineDir - engine 绝对路径。
 * @param {string} options.python - 解释器（已解析）。
 * @param {string} [options.dataRoot] - 数据根（空字符串 = 不显式下发）。
 * @param {number} options.localUserId - 本地单用户 user_id。
 * @param {string} [options.pluginRoot] - 插件包根（默认 lib/paths.js 的 PLUGIN_ROOT）。
 * @param {string} [options.envFile] - 显式 .env 路径（config.envFile）。
 * @param {string} [options.secretKey] - 显式 SECRET_KEY（config.secretKey）。
 * @param {Record<string, string>} [options.baseEnv] - 宿主环境基线（默认 process.env；doctor 传临时探测环境）。
 * @param {number} [options.startTimeoutMs] - 启动超时覆盖（测试用）。
 * @param {number} [options.healthTimeoutMs] - 健康检查超时覆盖（测试用）。
 * @returns {{ ensureRunning: Function, stop: Function, running: Function, port: Function, url: Function, localUserId: number }}
 */
export function createPanelSupervisor(options) {
  const engineDir = options.engineDir
  const python = options.python
  const dataRoot = options.dataRoot || ''
  const localUserId = options.localUserId
  const pluginRoot = options.pluginRoot || PLUGIN_ROOT
  const startTimeoutMs = options.startTimeoutMs || START_TIMEOUT_MS
  const healthTimeoutMs = options.healthTimeoutMs || HEALTH_TIMEOUT_MS

  let child = null
  let port = 0
  let starting = null
  let closed = false
  let gotPort = false

  /**
   * 关掉子进程 stdin。顺序很重要：只允许在 kill 之后调用（与 local-http.js 一致）。
   *
   * @param {object|null} proc - 子进程。
   */
  function closeStdin(proc) {
    const stdin = proc ? proc.stdin : null
    if (!stdin) return
    try {
      stdin.end()
    } catch {
      // 管道可能已因进程退出而关闭
    }
    try {
      stdin.destroy()
    } catch {
      // 同上
    }
  }

  /** 停掉子进程（幂等；插件的 disposer 与异常路径都会调）。 */
  function stop() {
    closed = true
    const proc = child
    child = null
    port = 0
    starting = null
    gotPort = false
    if (!proc) return
    try {
      if (proc.exitCode === null && proc.signalCode === null) proc.kill()
    } catch {
      // 已经退出 / 句柄已断：无需处理
    }
    closeStdin(proc)
  }

  /**
   * 健康检查：轮询 GET /health，2xx 才算就绪。
   *
   * @param {number} portValue - 刚拿到的端口。
   * @returns {Promise<void>}
   */
  async function checkHealth(portValue) {
    const deadline = Date.now() + healthTimeoutMs
    let lastError = null
    for (;;) {
      let response = null
      try {
        response = await fetch('http://127.0.0.1:' + portValue + '/health', {
          method: 'GET',
          headers: { accept: 'application/json' },
          signal: AbortSignal.timeout(HEALTH_REQUEST_TIMEOUT_MS),
        })
      } catch (error) {
        lastError = error
      }
      if (response) {
        if (response.ok) {
          try {
            await response.text()
          } catch {
            // 响应体不影响就绪判定
          }
          return
        }
        lastError = new Error('/health 返回 HTTP ' + response.status)
        try {
          await response.text()
        } catch {
          // 同上
        }
      }
      if (Date.now() >= deadline) break
      await sleep(HEALTH_RETRY_MS)
    }
    const detail = lastError && lastError.message ? lastError.message : String(lastError || '未知错误')
    throw new Error(
      '本地面板服务已监听到端口 ' + portValue + '，但 /health 在 '
        + Math.round(healthTimeoutMs / 1000) + ' 秒内没有返回 2xx：' + sanitizeDetail(detail),
    )
  }

  /**
   * 确保子进程在跑、拿到端口、并通过健康检查（并发请求共用同一个启动 Promise）。
   *
   * @returns {Promise<number>} 端口。
   */
  function start() {
    if (closed) return Promise.reject(new Error('ai-tutor: 插件已卸载，完整面板不再启动'))
    if (port > 0) return Promise.resolve(port)
    if (starting) return starting

    const issues = findPanelInstallIssues(engineDir, pluginRoot)
    if (issues.length > 0) {
      const missing = missingPanelMessage(engineDir, pluginRoot)
      const err = new Error(missing.detail)
      err.code = 'PANEL_MISSING'
      return Promise.reject(err)
    }

    starting = new Promise((resolvePort, rejectPort) => {
      let settled = false
      let stderrTail = ''
      let stdoutBuffer = ''

      let proc
      try {
        proc = spawn(python, [...PANEL_ARGS, '--local-user', String(localUserId)], {
          cwd: engineDir,
          env: panelEnvironment({
            engineDir,
            dataRoot,
            pluginRoot,
            envFile: options.envFile,
            secretKey: options.secretKey,
            baseEnv: options.baseEnv,
          }),
          // stdin 保持打开：stdin EOF 是给 panel_server 的优雅退出信号（见文件头）
          stdio: ['pipe', 'pipe', 'pipe'],
          windowsHide: true,
        })
      } catch (error) {
        starting = null
        rejectPort(new Error('无法启动完整面板后端（' + python + '）：' + error.message))
        return
      }
      child = proc
      if (proc.stdin) proc.stdin.on('error', () => { /* 子进程先退出时写管道会 EPIPE */ })

      const timer = setTimeout(() => {
        fail(new Error(
          '本地面板后端 ' + Math.round(startTimeoutMs / 1000) + ' 秒内没有打印 PORT=（python='
            + python + '）'
            + (stderrTail.trim() ? '。stderr 尾部：' + sanitizeDetail(stderrTail.trim()) : '。stderr 为空。'),
        ))
      }, startTimeoutMs)

      function settleOk(value) {
        if (settled) return
        settled = true
        clearTimeout(timer)
        gotPort = true
        port = value
        starting = null
        resolvePort(value)
      }

      function fail(error) {
        if (settled) return
        settled = true
        clearTimeout(timer)
        try {
          proc.kill()
        } catch {
          // 进程可能已退出
        }
        closeStdin(proc)
        if (child === proc) child = null
        port = 0
        gotPort = false
        starting = null
        rejectPort(error)
      }

      proc.on('error', (error) => {
        fail(new Error('无法启动完整面板后端（' + python + '）：' + error.message
          + '。检查 python 路径：node bin/doctor.mjs 的第 1 项。'))
      })

      proc.stdout.setEncoding('utf8')
      proc.stdout.on('data', (chunk) => {
        // PORT= 之后 stdout 只排空管道（协议约定日志走 stderr）
        if (gotPort) return
        stdoutBuffer += chunk
        let newline
        while ((newline = stdoutBuffer.indexOf('\n')) >= 0) {
          const line = stdoutBuffer.slice(0, newline).trim()
          stdoutBuffer = stdoutBuffer.slice(newline + 1)
          const matched = /^PORT=(\d+)$/.exec(line)
          if (matched) {
            const portValue = Number(matched[1])
            // 先占住端口再健康检查；检查失败由 fail() 回收
            port = portValue
            checkHealth(portValue).then(
              () => settleOk(portValue),
              (error) => fail(error instanceof Error ? error : new Error(String(error))),
            )
            return
          }
        }
      })

      proc.stderr.setEncoding('utf8')
      proc.stderr.on('data', (chunk) => {
        stderrTail = (stderrTail + chunk).slice(-STDERR_TAIL_CHARS)
      })

      proc.on('exit', (code, signal) => {
        const detail = '（code=' + code + ' signal=' + signal + '）'
          + (stderrTail.trim() ? '。stderr 尾部：' + sanitizeDetail(stderrTail.trim()) : '')
        if (!settled) {
          fail(new Error('本地面板后端启动即退出' + detail
            + '。多半是 engine 缺依赖或 panel_server.py 与当前 engine 版本不匹配；'
            + '跑 node bin/doctor.mjs 的第 1、4、12 项定位。'))
          return
        }
        // 运行中退出：作废端口，下次请求重启（不主动重启，避免崩溃循环刷屏）
        if (child === proc) {
          child = null
          port = 0
        }
        gotPort = false
        // 插件卸载（stop）导致的退出是预期行为，不刷错误日志
        if (closed) return
        console.error('[ai-tutor] 本地面板后端退出' + detail)
      })
    })

    return starting
  }

  /**
   * 确保完整面板在跑，返回 iframe 该用的本机 URL。
   *
   * @returns {Promise<{ url: string, port: number }>}
   */
  async function ensureRunning() {
    const portValue = await start()
    return { url: 'http://127.0.0.1:' + portValue, port: portValue }
  }

  return {
    ensureRunning,
    stop,
    running: () => Boolean(child),
    port: () => port,
    url: () => (port > 0 ? 'http://127.0.0.1:' + port : ''),
    localUserId,
  }
}

/** 写一个 JSON 响应（面板端统一按 JSON 解析错误）。 */
function sendJson(res, statusCode, payload) {
  const body = JSON.stringify(payload)
  res.writeHead(statusCode, {
    'content-type': 'application/json; charset=utf-8',
    'content-length': Buffer.byteLength(body),
    'cache-control': 'no-store',
  })
  res.end(body)
}

/**
 * 生成 /ai-tutor/panel 的 handler。
 *
 * 约束：**永不抛出让宿主崩**（webServer 不保证 await handler 的 Promise，
 * 所以这里自己吞掉所有异常并写响应）。panel 产物与入口文件的存在性每次请求现查，
 * 这样重建 panel/ 或升级 engine 后不必重装插件。
 *
 * @param {object} supervisor - createPanelSupervisor 的返回值。
 * @param {string} engineDir - engine 绝对路径（用于缺文件文案）。
 * @param {string} [pluginRoot] - 插件包根（用于 panel/ 定位）。
 * @returns {(req: object, res: object) => void}
 */
export function createPanelHandler(supervisor, engineDir, pluginRoot = PLUGIN_ROOT) {
  return function panelHandler(req, res) {
    void (async () => {
      try {
        const method = String((req && req.method) || 'GET').toUpperCase()
        if (method !== 'GET') {
          sendJson(res, 405, { error: 'ai-tutor 完整面板端点只支持 GET /ai-tutor/panel', method })
          return
        }
        if (findPanelInstallIssues(engineDir, pluginRoot).length > 0) {
          sendJson(res, 501, missingPanelMessage(engineDir, pluginRoot))
          return
        }
        const running = await supervisor.ensureRunning()
        sendJson(res, 200, {
          url: running.url,
          localUser: supervisor.localUserId,
          panel: { available: true },
        })
      } catch (error) {
        if (error && error.code === 'PANEL_MISSING') {
          sendJson(res, 501, missingPanelMessage(engineDir, pluginRoot))
          return
        }
        const detail = sanitizeDetail(error instanceof Error ? error.message : String(error))
        sendJson(res, 502, {
          error: '本地面板服务启动失败，AI 导师面板暂时打不开。',
          detail,
          hint: '在插件目录运行 node bin/doctor.mjs 看第 11、12 项：确认 panel/ 已构建、'
            + 'engine 含 app/mcp_servers/panel_server.py、python 可用。'
            + '模型侧 MCP 工具与「知识图谱」面板不受影响。',
        })
      }
    })()
  }
}
