/**
 * 图谱面板的本地 HTTP 后端代理（宿主侧，Node）。
 *
 * 面板（lib/client.js）只认一个同源端点 GET /ai-tutor/graph；真正的数据结构由
 * engine 的 app/mcp_servers/local_http.py 提供（stdlib http.server，127.0.0.1:0，
 * stdout 打印 PORT=<n>）。本模块负责：
 *   1. **懒启动**子进程（第一次请求才起，不在插件激活时拖慢启动）；
 *   2. 读 stdout 里的 PORT= 一行拿到随机端口；
 *   3. 转发 GET /ai-tutor/graph[?query] → http://127.0.0.1:<port>/graph[?query]；
 *   4. 插件卸载时 kill 子进程（不留下孤儿进程占端口）。
 *
 * 失败语义：**绝不让宿主崩**。缺文件 → 501（并给出可操作文案）；启动/转发失败
 * → 502。模型侧的 MCP 工具不经过这条链路，面板坏不会连带工具坏。
 *
 * 子进程生命周期：local_http.py 在 **stdin EOF 时自行退出**，所以启动后 stdin
 * 必须保持打开（stdin 'pipe' 且不 end）；dispose 时"先 kill、再关 stdin"，
 * 保证不留下 python 孤儿进程。
 *
 * 为什么拆成独立模块：index.js 顶层 import 宿主注入的 dsh-mcp-client，普通 node
 * 里 import 不了；把代理放这里，冒烟测试脚本可以在宿主之外直接驱动它。
 *
 * @module dsh-ai-tutor/lib/local-http
 */

import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { join } from 'node:path'

import { LOCAL_HTTP_ENTRY_REL, dataEnv, localModeEnv, scrubEnv } from './paths.js'

/** 起 local_http 的模块名（cwd = engine 目录，故用 -m）。 */
const LOCAL_HTTP_ARGS = ['-m', 'app.mcp_servers.local_http']

/** 启动超时：冷启动要 import fastapi 那一串，给宽一点。 */
const START_TIMEOUT_MS = 20_000

/** 单次转发超时。 */
const FETCH_TIMEOUT_MS = 20_000

/** 错误信息里保留的 stderr 尾部长度（帮用户看到 Python traceback 的末段）。 */
const STDERR_TAIL_CHARS = 800

/** 缺 local_http.py 时的可操作文案（doctor 第 10 项会复述同一件事）。 */
export function missingLocalHttpMessage(engineDir) {
  const entry = join(engineDir, LOCAL_HTTP_ENTRY_REL)
  return {
    error: '本地面板后端还没就绪，知识图谱面板暂时读不到数据。',
    detail: '缺少文件：' + entry,
    hint: '这通常是 engine 版本落后（没有 app/mcp_servers/local_http.py）。'
      + '在插件目录运行 node bin/doctor.mjs 看第 3、10 项；'
      + '模型侧的 MCP 工具不受影响，可以照常用。',
  }
}

/**
 * 创建代理。返回的对象在插件生命周期内复用；stop() 之后不可再启动。
 *
 * @param {object} options - 参数。
 * @param {string} options.engineDir - engine 绝对路径。
 * @param {string} options.python - 解释器（已解析）。
 * @param {string} options.dataRoot - 数据根（可为空字符串 = 不显式下发）。
 * @param {number} options.localUserId - 本地单用户 user_id。
 * @returns {{ fetchGraph: Function, stop: Function, running: () => boolean, port: () => number }}
 */
export function createLocalHttpProxy(options) {
  const engineDir = options.engineDir
  const python = options.python
  const dataRoot = options.dataRoot || ''
  const localUserId = options.localUserId

  let child = null
  let port = 0
  let starting = null
  let closed = false
  let gotPort = false

  /**
   * 关掉子进程 stdin。顺序很重要：local_http.py 把 stdin EOF 当退出信号，
   * 所以只允许在 kill 之后调用（否则子进程先优雅退出、kill 落空，但也不会有孤儿；
   * 反过来 kill 之后的 end 只是清理句柄）。
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
   * 确保子进程在跑并拿到端口（并发请求共用同一个启动 Promise）。
   *
   * @returns {Promise<number>} 端口。
   */
  function start() {
    if (closed) return Promise.reject(new Error('ai-tutor: 插件已卸载，本地面板后端不再启动'))
    if (port > 0) return Promise.resolve(port)
    // 注意：这里不校验 gotPort，因为 stop() 之后 port 归零且 closed 已经拦住重启用
    if (starting) return starting

    const entry = join(engineDir, LOCAL_HTTP_ENTRY_REL)
    if (!existsSync(entry)) {
      const err = new Error(missingLocalHttpMessage(engineDir).detail)
      err.code = 'LOCAL_HTTP_MISSING'
      return Promise.reject(err)
    }

    starting = new Promise((resolvePort, rejectPort) => {
      let settled = false
      let stderrTail = ''
      let stdoutBuffer = ''

      const proc = spawn(python, [...LOCAL_HTTP_ARGS, '--local-user', String(localUserId)], {
        cwd: engineDir,
        // 密钥类环境变量不下发（面板后端只读本地库，不需要任何 API key）；
        // 但 engine 的 auth.py 在导入期要求 SECRET_KEY 非空 → 本地模式读/建 <dataRoot>/secret_key，
        // 并加 PYTHONDONTWRITEBYTECODE=1（不往插件包里写 __pycache__）。
        env: scrubEnv(process.env, Object.assign(dataEnv(dataRoot), localModeEnv(dataRoot, process.env))),
        // stdin 必须保持打开：local_http.py 用 stdin EOF 作为退出信号（见文件头注释）
        stdio: ['pipe', 'pipe', 'pipe'],
        windowsHide: true,
      })
      child = proc
      if (proc.stdin) proc.stdin.on('error', () => { /* 子进程先退出时写管道会 EPIPE */ })

      const timer = setTimeout(() => {
        fail(new Error(
          '本地面板后端 ' + (START_TIMEOUT_MS / 1000) + ' 秒内没有打印 PORT=（python=' + python + '）'
            + (stderrTail.trim() ? '。stderr 尾部：' + stderrTail.trim() : '。stderr 为空。'),
        ))
      }, START_TIMEOUT_MS)

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
        fail(new Error('无法启动本地面板后端（' + python + '）：' + error.message
          + '。检查 python 路径：node bin/doctor.mjs 的第 1 项。'))
      })

      proc.stdout.setEncoding('utf8')
      proc.stdout.on('data', (chunk) => {
        // PORT= 之后 stdout 只用于排空管道，不再解析（协议约定 stdio 日志都走 stderr）
        if (gotPort) return
        stdoutBuffer += chunk
        let newline
        while ((newline = stdoutBuffer.indexOf('\n')) >= 0) {
          const line = stdoutBuffer.slice(0, newline).trim()
          stdoutBuffer = stdoutBuffer.slice(newline + 1)
          const matched = /^PORT=(\d+)$/.exec(line)
          if (matched) {
            settleOk(Number(matched[1]))
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
          + (stderrTail.trim() ? '。stderr 尾部：' + stderrTail.trim() : '')
        if (!settled) {
          fail(new Error('本地面板后端启动即退出' + detail
            + '。多半是 engine 缺依赖或 local_http.py 与当前 engine 版本不匹配；'
            + '跑 node bin/doctor.mjs 的第 1、4、6 项定位。'))
          return
        }
        // 运行中退出：作废端口，下次请求重启（不主动重启，避免崩溃循环刷屏）
        if (child === proc) {
          child = null
          port = 0
        }
        gotPort = false
        console.error('[ai-tutor] 本地面板后端退出' + detail)
      })
    })

    return starting
  }

  /**
   * 请求 local_http 的 /graph，把原始响应交回给调用方决定怎么写回。
   *
   * @param {string} search - 原始查询串（含前导 ?，可为空）。
   * @returns {Promise<{statusCode: number, contentType: string, body: string}>}
   */
  async function fetchGraph(search) {
    const portValue = await start()
    try {
      const response = await fetch('http://127.0.0.1:' + portValue + '/graph' + (search || ''), {
        method: 'GET',
        headers: { accept: 'application/json' },
        signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
      })
      const body = await response.text()
      return {
        statusCode: response.status,
        contentType: response.headers.get('content-type') || 'application/json; charset=utf-8',
        body,
      }
    } catch (error) {
      // 连不上 = 子进程可能已经死了：作废端口，下次请求重新拉起
      if (child && child.exitCode !== null) {
        child = null
        port = 0
      }
      throw error
    }
  }

  return {
    fetchGraph,
    stop,
    running: () => Boolean(child),
    port: () => port,
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
 * 生成 /ai-tutor/graph 的 handler。
 *
 * 约束：**永不抛出让宿主崩**（webServer 不保证 await handler 的 Promise，
 * 所以这里自己吞掉所有异常并写响应）。local_http.py 的存在性每次请求现查，
 * 这样重建 engine 后不必重装插件。
 *
 * @param {object} proxy - createLocalHttpProxy 的返回值。
 * @param {string} engineDir - engine 绝对路径（用于缺文件文案）。
 * @returns {(req: object, res: object) => void}
 */
export function createGraphHandler(proxy, engineDir) {
  return function graphHandler(req, res) {
    void (async () => {
      try {
        const method = String((req && req.method) || 'GET').toUpperCase()
        if (method !== 'GET') {
          sendJson(res, 405, { error: 'ai-tutor 面板端点只支持 GET /ai-tutor/graph', method })
          return
        }
        const entry = join(engineDir, LOCAL_HTTP_ENTRY_REL)
        if (!existsSync(entry)) {
          sendJson(res, 501, missingLocalHttpMessage(engineDir))
          return
        }
        const rawUrl = String((req && req.url) || '/ai-tutor/graph')
        let search = ''
        try {
          search = new URL(rawUrl, 'http://127.0.0.1').search
        } catch {
          search = ''
        }
        const proxied = await proxy.fetchGraph(search)
        res.writeHead(proxied.statusCode, {
          'content-type': proxied.contentType,
          'cache-control': 'no-store',
        })
        res.end(proxied.body)
      } catch (error) {
        const detail = error instanceof Error ? error.message : String(error)
        sendJson(res, 502, {
          error: '本地面板后端调用失败，知识图谱面板暂时读不到数据。',
          detail,
          hint: '在插件目录运行 node bin/doctor.mjs 定位（第 1、4、6、10 项）。'
            + '模型侧的 MCP 工具不受影响。',
        })
      }
    })()
  }
}
