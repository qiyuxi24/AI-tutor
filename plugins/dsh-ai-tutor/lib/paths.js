/**
 * 安装位置探测 —— index.js（宿主内）与 bin/doctor.mjs（纯命令行）共用。
 *
 * 为什么不把这些函数写在 index.js 里：index.js 顶层 import
 * `@deepseek-ai/dsh-mcp-client`，那是**宿主注入**的裸包，普通 node 里解析不到；
 * 而 doctor 必须在「宿主之外」跑（§P3 第 1~8 项就是离线自检）。探测逻辑待在
 * 一条不依赖宿主模块的链上 = 一份实现，不会两处漂移。
 *
 * 环境变量读取用「传入 readEnv」的方式：宿主要先看 launcher 的 launchEnvironment
 * 快照（见 index.js::readEnv），doctor 直接读 process.env。
 *
 * @module dsh-ai-tutor/lib/paths
 */

import { randomBytes } from 'node:crypto'
import { existsSync, mkdirSync, readFileSync, statSync, writeFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { dirname, isAbsolute, join, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

/** 插件包根（`index.js` 所在目录）。 */
export const PLUGIN_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')

/** 发布包内自带的 engine（= 主仓 backend 的复制品，含 app/）。 */
export const ENGINE_DIR = join(PLUGIN_ROOT, 'engine')

/** 发布包内自带的 CPython。 */
export const RUNTIME_DIR = join(PLUGIN_ROOT, 'runtime')

/** engine / backend 的判据文件（相对 engine|backend 目录）。 */
export const SERVER_ENTRY_REL = join('app', 'mcp_servers', 'kg_tools.py')

/** 图谱面板用的本地 HTTP 后端（P1 落地；缺失时面板降级为 501，不影响 MCP 工具）。 */
export const LOCAL_HTTP_ENTRY_REL = join('app', 'mcp_servers', 'local_http.py')

/** 数据目录的包名段。 */
export const APP_NAME = 'dsh-ai-tutor'

/** 本地单用户模式默认 user_id（与主仓 §D6 一致）。 */
export const DEFAULT_LOCAL_USER_ID = 1

/** 与宿主清洗子进程环境同一条正则（F2）：密钥类变量一律不下发。 */
export const SECRET_ENV_RE = /KEY|PASSWORD|SECRET|TOKEN/i

/** 默认环境变量读取器（doctor 用；宿主用自己带快照的版本）。 */
export function readProcessEnv(key) {
  const value = process.env[key]
  return value === undefined ? '' : value
}

/** 该目录是不是 AI-tutor 的 backend/ 或 engine/（含 MCP server 入口文件）。 */
export function isBackendDir(dir) {
  return existsSync(join(dir, SERVER_ENTRY_REL))
}

/** 是「存在的普通文件」而非目录/悬垂链接。 */
function isFile(path) {
  try {
    return existsSync(path) && statSync(path).isFile()
  } catch {
    return false
  }
}

function firstNonEmpty(values) {
  for (const value of values) {
    if (typeof value === 'string' && value.trim().length > 0) return value.trim()
  }
  return ''
}

/**
 * 解析 engine 目录（= 主仓 backend 的等价物）。
 *
 * 顺序（与 §P5 安装差异策略一致）：
 *   1. 行 config `backendDir`
 *   2. 行 config `repoRoot` + `/backend`（仓库开发态）
 *   3. 包内 `<PLUGIN_ROOT>/engine`（发布形态，装完即用）
 *   4. 环境变量 `AI_TUTOR_HOME` + `/backend`
 *   5. 宿主 cwd 逐级向上（最多 8 级；兼容从仓库任意子目录启动）
 *
 * 显式配置（1/2/4）给错就当场抛：静默回退会让「我明明配了」变成最难查的故障。
 *
 * @param {object} [config] - 本插件行的 config。
 * @param {(key: string) => string} [readEnv] - 环境变量读取器。
 * @param {string} [cwd] - 起点目录（默认 process.cwd()）。
 * @returns {string} 绝对路径。
 */
export function resolveEngineDir(config = {}, readEnv = readProcessEnv, cwd = process.cwd()) {
  const tried = []
  const miss = (label, dir) => {
    tried.push(label + ': ' + dir)
  }

  if (config.backendDir) {
    const dir = resolve(String(config.backendDir))
    if (isBackendDir(dir)) return dir
    throw new Error(
      'ai-tutor: config.backendDir 不可用（缺 ' + SERVER_ENTRY_REL + '）：' + dir
        + '\nengine 目录的判据是「含 app/mcp_servers/kg_tools.py」；仓库开发态应指向 <仓库根>/backend。',
    )
  }
  miss('config.backendDir', '（未配置）')

  if (config.repoRoot) {
    const dir = join(resolve(String(config.repoRoot)), 'backend')
    if (isBackendDir(dir)) return dir
    throw new Error(
      'ai-tutor: config.repoRoot 下没有可用的 backend：' + dir
        + '\n期望存在 ' + SERVER_ENTRY_REL + '。',
    )
  }
  miss('config.repoRoot', '（未配置）')

  if (isBackendDir(ENGINE_DIR)) return ENGINE_DIR
  miss('包内 engine', ENGINE_DIR)

  const home = firstNonEmpty([readEnv('AI_TUTOR_HOME')])
  if (home) {
    const dir = join(resolve(home), 'backend')
    if (isBackendDir(dir)) return dir
    throw new Error(
      'ai-tutor: AI_TUTOR_HOME 指向的目录下没有可用的 backend：' + dir
        + '\nAI_TUTOR_HOME 应指向 AI-tutor 仓库根（其下有 backend/app/mcp_servers/kg_tools.py）。',
    )
  }
  miss('AI_TUTOR_HOME', '（未设置）')

  let dir = resolve(cwd)
  for (let depth = 0; depth < 8; depth += 1) {
    const candidate = join(dir, 'backend')
    if (isBackendDir(candidate)) return candidate
    // 直接从 backend/ 里启动的场景（cwd 本身即 backend）
    if (isBackendDir(dir)) return dir
    const parent = dirname(dir)
    if (parent === dir) break
    dir = parent
  }
  miss('cwd 上溯 8 级', resolve(cwd))

  const bundledHint = existsSync(ENGINE_DIR)
    ? '\n注意：' + ENGINE_DIR + ' 存在但缺 ' + SERVER_ENTRY_REL
      + '（engine 是半成品/旧版本）→ 用 node scripts/build-plugin.mjs 重新构建，或从发布包重新安装。'
    : ''

  throw new Error(
    'ai-tutor: 找不到 AI-tutor 的 engine 目录。请任选其一：\n'
      + '  1) 设环境变量 AI_TUTOR_HOME=<AI-tutor 仓库根>\n'
      + '  2) 在 profile 的 cordis.patch.yml 里给本行加 config: { repoRoot: <仓库根> }（或 backendDir: <仓库根>/backend）\n'
      + '  3) 用发布包重新安装插件（本包自带 engine/）\n'
      + '已检查：\n'
      + tried.map((line) => '  - ' + line).join('\n')
      + bundledHint,
  )
}

/**
 * 插件包内自带的 Python（发布形态的唯一依赖）。
 *
 * @param {string} [runtimeDir] - runtime 目录。
 * @param {string} [platform] - 仅测试时覆盖。
 * @returns {string} 绝对路径；没有则空字符串。
 */
export function findBundledPython(runtimeDir = RUNTIME_DIR, platform = process.platform) {
  const candidates = platform === 'win32'
    ? [join(runtimeDir, 'python.exe'), join(runtimeDir, 'bin', 'python.exe')]
    : [join(runtimeDir, 'bin', 'python3'), join(runtimeDir, 'bin', 'python')]
  for (const candidate of candidates) {
    if (isFile(candidate)) return candidate
  }
  return ''
}

/**
 * 解析 Python 解释器。
 *
 * 优先级：
 *   1. 行 config `python`（**显式配置覆盖自动探测**：开发调试时指向仓库 backend/venv；
 *      这是任务书「包内 runtime 第一」的唯一例外，已写进 README）
 *   2. 包内 `runtime/`（发布形态；用户机器上不需要装 Python）
 *   3. 环境变量 `AI_TUTOR_PYTHON`
 *   4. engine 目录下的 venv（仓库开发态：backend/venv/Scripts/python.exe）
 *   5. PATH 上的 python/python3（容器/CI 兜底，找不到由 spawn 报 ENOENT）
 *
 * @param {object} [config] - 本插件行的 config。
 * @param {(key: string) => string} [readEnv] - 环境变量读取器。
 * @param {string} [engineDir] - 已解析的 engine 目录。
 * @returns {string} 解释器路径。
 */
export function resolvePython(config = {}, readEnv = readProcessEnv, engineDir = '') {
  if (config.python) {
    const path = resolve(String(config.python))
    if (!isFile(path)) throw new Error('ai-tutor: config.python 指向的文件不存在：' + path)
    return path
  }

  const bundled = findBundledPython()
  if (bundled) return bundled

  const fromEnv = firstNonEmpty([readEnv('AI_TUTOR_PYTHON')])
  if (fromEnv) {
    const path = resolve(fromEnv)
    if (!isFile(path)) throw new Error('ai-tutor: AI_TUTOR_PYTHON 指向的文件不存在：' + path)
    return path
  }

  if (engineDir) {
    const candidates = [
      join(engineDir, 'venv', 'Scripts', 'python.exe'),
      join(engineDir, 'venv', 'bin', 'python'),
      join(engineDir, '.venv', 'Scripts', 'python.exe'),
      join(engineDir, '.venv', 'bin', 'python'),
    ]
    for (const candidate of candidates) {
      if (isFile(candidate)) return candidate
    }
  }

  // 没有 venv：交给 PATH。不抛 —— 容器/CI 常用系统 python + 已装依赖。
  return process.platform === 'win32' ? 'python' : 'python3'
}

/**
 * engine 是否在插件包内（= 本地单用户模式）。
 *
 * 包内 engine 用 `--local-user <id>` 免 token；包外（仓库开发/自托管）仍走 token。
 *
 * @param {string} engineDir - 已解析的 engine 目录。
 * @param {string} [pluginRoot] - 仅测试时覆盖。
 * @returns {boolean}
 */
export function isLocalMode(engineDir, pluginRoot = PLUGIN_ROOT) {
  const rel = relative(pluginRoot, engineDir)
  if (rel === '') return true
  return !rel.startsWith('..') && !isAbsolute(rel)
}

/**
 * 解析本地单用户模式的 user_id。
 *
 * @param {object} [config] - 本插件行的 config。
 * @param {(key: string) => string} [readEnv] - 环境变量读取器。
 * @returns {number} 正整数。
 */
export function resolveLocalUserId(config = {}, readEnv = readProcessEnv) {
  const raw = config.localUserId !== undefined && config.localUserId !== null
    ? config.localUserId
    : readEnv('AI_TUTOR_LOCAL_USER')
  if (raw === undefined || raw === null || raw === '') return DEFAULT_LOCAL_USER_ID
  const value = Number(raw)
  if (!Number.isInteger(value) || value <= 0) {
    throw new Error('ai-tutor: localUserId 必须是正整数，收到 ' + JSON.stringify(raw))
  }
  return value
}

/**
 * 解析数据根目录。返回空字符串 = 「不显式下发，让 engine 用默认位置」。
 *
 * 来源顺序：行 config `dataRoot` → 环境变量 `AI_TUTOR_DATA_ROOT`。
 *
 * @param {object} [config] - 本插件行的 config。
 * @param {(key: string) => string} [readEnv] - 环境变量读取器。
 * @returns {string} 绝对路径或空字符串。
 */
export function resolveDataRoot(config = {}, readEnv = readProcessEnv) {
  const raw = firstNonEmpty([config.dataRoot, readEnv('AI_TUTOR_DATA_ROOT')])
  return raw ? resolve(raw) : ''
}

/**
 * 把数据根翻译成子进程环境变量。
 *
 * **两个名字都下发**：engine 的 `core/storage.py` 优先 `AI_TUTOR_DATA_ROOT`、其次
 * `DATA_ROOT`；而记录/日志等子系统只认 `DATA_ROOT`。只给一个会「一半数据落对、
 * 一半落错」，是最隐蔽的一类路径事故。
 *
 * @param {string} dataRoot - resolveDataRoot 的结果（可为空）。
 * @returns {Record<string, string>} 追加到子进程 env 的片段。
 */
export function dataEnv(dataRoot) {
  if (!dataRoot) return {}
  return { AI_TUTOR_DATA_ROOT: dataRoot, DATA_ROOT: dataRoot }
}

/** 本地 secret 文件名（放在数据根下，随数据一起保留/迁移）。 */
export const LOCAL_SECRET_FILE = 'secret_key'

/** 本地 secret 长度（字节；hex 后 64 字符，与 JWT HS256 常见要求一致）。 */
const LOCAL_SECRET_BYTES = 32

/**
 * 读本地 secret 文件；不存在则生成并写入（`<dataRoot>/secret_key`）。
 *
 * 为什么不再用写死的占位值：engine 的 `app/core/auth.py` 在导入期要求 SECRET_KEY 非空，
 * 以前给常量能让它起来，但同一个值出现在所有安装里 = 所有本地实例共享一把"密钥"。
 * 改成每台机器一份随机值、落在用户数据目录（权限 0600），语义正确且仍不做 HTTP 鉴权。
 *
 * 失败兜底：读/写失败（只读盘、权限不足）时**只在内存里生成**并继续 —— 本地模式
 * 宁可这次用临时密钥，也不能因为写不了文件而让整个插件起不来。
 *
 * @param {string} [dataRoot] - 数据根目录；为空时只生成内存值。
 * @returns {string} 64 字符 hex。
 */
export function readOrCreateLocalSecret(dataRoot) {
  const generate = () => randomBytes(LOCAL_SECRET_BYTES).toString('hex')
  if (!dataRoot) return generate()

  const secretPath = join(dataRoot, LOCAL_SECRET_FILE)
  try {
    const existing = readFileSync(secretPath, 'utf8').trim()
    if (existing) return existing
  } catch {
    // 不存在 / 不可读 → 下面尝试创建
  }

  const secret = generate()
  try {
    mkdirSync(dataRoot, { recursive: true })
    writeFileSync(secretPath, secret + '\n', { encoding: 'utf8', mode: 0o600 })
    return secret
  } catch (error) {
    console.warn('[ai-tutor] 无法写入本地 secret 文件 ' + secretPath + '（改用内存密钥，仅本次运行有效）：' + error.message)
    return secret
  }
}

/**
 * 本地模式要追加给子进程的环境变量：本地 SECRET_KEY + 禁止写 `__pycache__`。
 *
 * - `PYTHONDONTWRITEBYTECODE=1`：子进程 import engine 时会在**插件包内**生成
 *   `__pycache__`（engine 长大后每次升级都多出几 MB，还会被打进发布包）→ 一律禁止。
 * - `SECRET_KEY`：优先用调用方显式给的（config.secretKey）→ 宿主 `process.env.SECRET_KEY`
 *   → 数据根 secret 文件；三者都没有才新建。**绝不覆盖用户显式配置的值**。
 *   注意 scrubEnv 会剥掉宿主环境里的密钥类变量，所以这里必须在清洗**之前**取值、
 *   放进 extra 合并回来。
 *
 * @param {string} [dataRoot] - 数据根目录（secret 文件所在处）。
 * @param {Record<string, string|undefined>} [baseEnv] - 原始环境表（默认 process.env）。
 * @param {string} [configSecret] - 配置里显式给的 secret（最高优先级）。
 * @returns {Record<string, string>} 追加到子进程 env 的片段。
 */
export function localModeEnv(dataRoot, baseEnv = process.env, configSecret = '') {
  const explicit = String(configSecret || (baseEnv && baseEnv.SECRET_KEY) || '').trim()
  return {
    PYTHONDONTWRITEBYTECODE: '1',
    SECRET_KEY: explicit || readOrCreateLocalSecret(dataRoot),
  }
}

/**
 * 平台默认数据根（doctor --fix 会创建它；插件不主动创建）。
 *
 * Windows：`%LOCALAPPDATA%\\dsh-ai-tutor\\data`；其它：
 * `$XDG_DATA_HOME|~/.local/share` + `/dsh-ai-tutor/data`。
 *
 * @param {Record<string, string|undefined>} [env] - 环境变量表。
 * @param {string} [platform] - 仅测试时覆盖。
 * @returns {string} 绝对路径。
 */
export function defaultDataRoot(env = process.env, platform = process.platform) {
  const base = platform === 'win32'
    ? (env.LOCALAPPDATA || join(homedir(), 'AppData', 'Local'))
    : (env.XDG_DATA_HOME || join(homedir(), '.local', 'share'))
  return join(base, APP_NAME, 'data')
}

/**
 * 清洗环境变量：密钥类（KEY/PASSWORD/SECRET/TOKEN）一律不带进子进程。
 *
 * 与宿主对 MCP 子进程的清洗同一条正则（F2）。`extra` 在清洗之后合并 ——
 * 所以 self-hosted 模式显式下发的 `AI_TUTOR_TOKEN` 不会被这里误删。
 *
 * @param {Record<string, string|undefined>} [base] - 原始环境表。
 * @param {Record<string, string>} [extra] - 追加项。
 * @returns {Record<string, string>}
 */
export function scrubEnv(base = process.env, extra = {}) {
  const out = {}
  for (const [key, value] of Object.entries(base)) {
    if (value === undefined) continue
    if (SECRET_ENV_RE.test(key)) continue
    out[key] = value
  }
  return Object.assign(out, extra)
}

/**
 * engine 自己需要的配置项（本地单用户运行时配置，**不是**宿主/第三方凭证）。
 *
 * 为什么需要白名单：`scrubEnv` 按名字整片清掉 KEY/PASSWORD/SECRET/TOKEN，
 * 而 engine 的配置恰好是 `LLM_API_KEY` / `DASHSCOPE_API_KEY` 这种名字 ——
 * 本地模式漏传它们会让完整面板「对话正常但图谱生成/出题/嵌入全空」，报
 * `Missing credentials`（2026-10-27 真机踩到：dotenv 解析到了值却在最后一步被清掉）。
 * 所以清洗之后，再把这些**明确属于 AI-tutor 自己**的配置补回来。
 *
 * 不含 SECRET_KEY / TOKEN：那两个由本地 <dataRoot>/secret_key 与 self-hosted token 专项处理。
 */
export const TUTOR_ENV_PREFIXES = Object.freeze(['LLM_', 'FALLBACK_LLM_', 'DASHSCOPE_', 'EMBED_'])
export const TUTOR_ENV_KEYS = Object.freeze([
  'MODEL_NAME',
  'ASR_MODEL',
  'BILI_COOKIE',
  'BILI_COOKIE_FILE',
  'WEB_SEARCH_ENABLED',
  'SEARXNG_URL',
  'LOG_LEVEL',
  'ACCESS_TOKEN_EXPIRE_MINUTES',
])

/** 这个环境变量名是不是 engine 自己的配置（见 TUTOR_ENV_PREFIXES/KEYS）。 */
export function isTutorEnvKey(key) {
  return TUTOR_ENV_KEYS.includes(key) || TUTOR_ENV_PREFIXES.some((prefix) => key.startsWith(prefix))
}

/**
 * 从基础环境 + .env 值里挑出 engine 配置项（基础环境优先，与其它环境变量口径一致）。
 *
 * @param {Record<string, string|undefined>} [baseEnv] - 宿主环境。
 * @param {Record<string, string|undefined>} [dotenv] - .env 解析结果。
 * @returns {Record<string, string>} 只含 allowlist 内的键。
 */
export function pickTutorEnv(baseEnv = process.env, dotenv = {}) {
  const out = {}
  for (const [key, value] of Object.entries(baseEnv)) {
    if (value === undefined || value === '') continue
    if (!isTutorEnvKey(key)) continue
    out[key] = value
  }
  for (const [key, value] of Object.entries(dotenv)) {
    if (value === undefined || value === '') continue
    if (!isTutorEnvKey(key)) continue
    if (out[key] !== undefined) continue
    out[key] = value
  }
  return out
}
