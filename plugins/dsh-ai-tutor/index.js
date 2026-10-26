/**
 * dsh-ai-tutor —— 把 AI-tutor 的只读知识库能力接进 DeepSeek Harness。
 *
 * ## 它是什么
 * 一个组合插件：自己不做协议、不做鉴权、不做检索，只负责把安装位置的差异
 * 翻译成 @deepseek-ai/dsh-mcp-client 的一份 config，然后交给它去连。
 * 真正干活的是 engine/app/mcp_servers/kg_tools.py（MCP server）→
 * app/core/agent_tools/gateway.py（白名单 + 身份 + 图谱生命周期）→ dispatch。
 * 所以本文件不含任何领域逻辑，工具增删改都不需要改这里。
 *
 * ## 两种运行模式（安装差异全吃在这一层）
 *   本地单用户（发布包）：engine 在 <PLUGIN_ROOT>/engine（包内）→ args 追加
 *     --local-user <config.localUserId | 1>，**不传 token**；额外注册图谱面板路由。
 *   自托管（仓库开发 / 现有部署）：engine 在包外（仓库 backend/）→ 原逻辑，
 *     AI_TUTOR_TOKEN 必填（token 错就明确失败，不静默空转）。
 * 判据 = isLocalMode(engineDir)（engine 是否在插件包内），见 lib/paths.js。
 *
 * ## 为什么包一层（而不是让 cordis.patch.yml 直接插 mcp-client 行）
 * mcp-client 的 config 里 command / cwd 必须是绝对路径，而 npm 包不可能预知
 * 用户把 AI-tutor 放在哪、venv 是 Scripts/python.exe 还是 bin/python。
 * 用 YAML 占位符会把「路径怎么拼」变成文档里的一段手工步骤（装完必须手改包内文件），
 * 那就不是「装一次就能用」了。这一层把差异吃在代码里：探测 + 环境变量 + 行 config 覆盖。
 *
 * ## 为什么直接调 mcpClient.apply(ctx, config) 而不 ctx.plugin(...)
 * mcp-client 是命名导出插件（export const name/inject + export async function apply），
 * 没有 default export。直接 await 它的 apply 语义最直白：它的 ctx.effect(...) 注册在
 * 我们的 fiber 上，卸载/重载随本插件一起 dispose，inject 由我们照抄（见下方 inject）。
 * 走 ctx.plugin(命名空间对象, config) 要依赖 cordis 对「无 default 的模块命名空间」
 * 的解析细节，多一层不必要的不确定。
 *
 * ## 三条自证规则（与主仓 gateway.py 一致的取向）
 * 1. 缺 token / 缺 engine 就抛，不静默空转 —— 宿主侧表现为「条目未激活 + 明确原因」；
 *    静默连上但检索不到东西是外部接入里最难排查的故障形态。
 * 2. serverName 固定 ai-tutor —— 模型侧工具名是 mcp__ai-tutor__<tool>，改名即破坏
 *    历史与权限，故不开放配置。
 * 3. 只读 —— 对外暴露哪些工具由 engine 的 gateway.ALLOWLIST 决定（本文件不持有白名单），
 *    写工具（改图谱 / 采集入库 / 出题判分）留在 AI-tutor 主系统内。
 *
 * ## 图谱面板（P5，仅本地单用户模式）
 * 注册宿主路由 GET /ai-tutor/graph → 懒启动 engine/app/mcp_servers/local_http.py
 * （127.0.0.1 随机端口，stdout 打印 PORT=<n>）→ 转发 /graph。缺 local_http.py 返回
 * 501 + 可操作文案，启动/转发失败返回 502，进程在 dispose 时 kill；
 * 这条链路坏掉不影响 MCP 工具（工具走 stdio，不经过它）。
 *
 * @module dsh-ai-tutor
 */

import { mkdirSync } from 'node:fs'

import * as mcpClient from '@deepseek-ai/dsh-mcp-client'

import { createGraphHandler, createLocalHttpProxy } from './lib/local-http.js'
import {
  dataEnv,
  defaultDataRoot,
  isLocalMode,
  localModeEnv,
  resolveDataRoot,
  resolveEngineDir,
  resolveLocalUserId,
  resolvePython,
} from './lib/paths.js'

/** Cordis 插件名（loader 诊断用）。 */
export const name = 'ai-tutor'

/** mcp-client 需要 ctx.tools；我们替它声明，因为它被直接调用而不是独立注册。 */
export const inject = ['tools']

/** 模型侧工具名前缀：mcp__<serverName>__<rawName>。**兼容性契约，勿改。** */
const SERVER_NAME = 'ai-tutor'

/** MCP server 入口（相对 engine 目录）。 */
const SERVER_ARGS = ['-m', 'app.mcp_servers.kg_tools']

/** 图谱面板端点（面板 lib/client.js 里 fetch 同一路径）。 */
const GRAPH_ROUTE = '/ai-tutor/graph'

/** 与主仓 agent loop 的单工具超时同量级：rag_search 要打嵌入 API。 */
const DEFAULT_TOOL_CALL_TIMEOUT_MS = 90_000

/**
 * 读一个环境变量：先看 launcher 的环境快照，再看进程环境。
 *
 * 快照优先的理由与社区 bundle（dsh-web-search-tavily）一致：launcher 只把 .env
 * 里**继承环境没定义**的名字物化进快照，所以 shell 里一个空的同名变量会盖掉用户在
 * .env 里写的那份；读快照拿到的是 launcher 真正看到的值。
 *
 * @param {object} ctx - 插件上下文。
 * @param {string} key - 变量名。
 * @returns {string} 值；未定义时为空字符串。
 */
function readEnv(ctx, key) {
  const fromLaunch = ctx.get('launchEnvironment')?.get(key)?.value
  if (fromLaunch !== undefined && fromLaunch.length > 0) return fromLaunch
  return process.env[key] ?? ''
}

/**
 * 本地模式首次激活时把数据根目录建出来。
 *
 * Python 侧的 manager 只建自己的子目录（data/kg、data/rag…），根目录不存在时首启会
 * 直接报错；这里先建出来让「装完就能用」。失败只告警 —— 只读盘等场景让 engine 报
 * 自己的错，不因为面板/数据目录问题挡掉 MCP 工具。
 *
 * @param {string} dataRoot - 数据根绝对路径。
 */
function ensureDataRootDirectory(dataRoot) {
  try {
    mkdirSync(dataRoot, { recursive: true })
  } catch (error) {
    console.warn('[ai-tutor] 数据根目录创建失败（' + dataRoot + '）：', error)
  }
}

/**
 * 注册图谱面板路由（只在本地单用户模式调用）。
 *
 * 用 ctx.inject(['webServer'], scope => ...) 而不是假设 webServer 一定存在：
 * 没有 web UI 的宿主（如纯 CLI profile）里这条 fiber 保持 pending，插件其余部分
 * 照常工作。注册与清理都挂在 scope 上 —— 插件卸载/重载时路由与子进程一起走。
 *
 * @param {object} ctx - 插件上下文。
 * @param {string} engineDir - engine 绝对路径。
 * @param {object} options - { python, dataRoot, localUserId }。
 */
function registerGraphRoute(ctx, engineDir, options) {
  try {
    const proxy = createLocalHttpProxy({
      engineDir,
      python: options.python,
      dataRoot: options.dataRoot,
      localUserId: options.localUserId,
    })
    const handler = createGraphHandler(proxy, engineDir)
    ctx.inject(['webServer'], (scope) => {
      // 先挂清理再注册：即使 register 抛错（重复路径），已创建的代理也能被回收
      scope.effect(() => () => proxy.stop())
      // 路由同样必须随插件卸载注销：webServer.register 返回的就是注销函数。
      // 漏掉这一步时（2026-10-27 线上复现）宿主热重载会留下旧路由 —— 旧 handler 的
      // 代理已 stop，新实例注册又因 duplicate 抛错被吞掉，面板就一直拿到
      // 502「插件已卸载，本地面板后端不再启动」，只有重启宿主才能恢复。
      scope.effect(() => {
        try {
          return scope.webServer.register({
            kind: 'exact',
            path: GRAPH_ROUTE,
            handler,
          })
        } catch (error) {
          console.error('[ai-tutor] 图谱面板路由注册失败（不影响 MCP 工具）：', error)
          return () => {}
        }
      })
    })
  } catch (error) {
    // 面板是附加能力：注册失败只记日志，绝不让 MCP 工具跟着失败
    console.error('[ai-tutor] 图谱面板路由注册失败（不影响 MCP 工具）：', error)
  }
}

/**
 * 插件入口：解析安装差异 → 交给 MCP client 去连。
 *
 * 会阻塞到「连上并同步完工具列表」，所以激活完成时模型侧已能看到引擎暴露的工具；
 * failOnStartupError 默认 true —— 路径错/local_http 缺 --local-user/token 错时
 * **本条目明确失败**，而不是留下一个连上了却什么也检索不到的空插件。
 *
 * @param {object} ctx - 插件上下文（需 ctx.tools；有 webServer 时额外注册面板路由）。
 * @param {object} [config] - profile 里覆盖本行的 config（全部可选）。
 * @returns {Promise<void>}
 */
export async function apply(ctx, config = {}) {
  const readEnvVar = (key) => readEnv(ctx, key)

  // engine 目录候选顺序见 lib/paths.js（包内 engine 先于 AI_TUTOR_HOME，最后 cwd 上溯）
  const engineDir = resolveEngineDir(config, readEnvVar)
  const python = resolvePython(config, readEnvVar, engineDir)
  const localMode = isLocalMode(engineDir)

  // 数据根：显式 config.dataRoot / AI_TUTOR_DATA_ROOT 优先。
  // 本地模式（发布包）没有显式配置时落到**平台用户目录**（计划 D3：数据不能写进插件包，
  // 否则卸载/升级即丢数据）；自托管模式不注入，交给 engine 自己读仓库 .env 与 data/。
  const configuredDataRoot = resolveDataRoot(config, readEnvVar)
  const dataRoot = localMode && !configuredDataRoot ? defaultDataRoot(process.env) : configuredDataRoot
  if (dataRoot) ensureDataRootDirectory(dataRoot)

  // 两个数据根变量名同时下发（engine 优先 AI_TUTOR_DATA_ROOT，其它子系统认 DATA_ROOT）。
  // 所有 python 子进程都必须 PYTHONDONTWRITEBYTECODE=1：否则 import 会在**插件包内**写
  // __pycache__（engine 越长越大，还会被打进发布包）。本地模式再读/建 <dataRoot>/secret_key
  // 作为 SECRET_KEY（engine 的 auth.py 导入期要求非空；每台机器一份随机值，不覆盖用户显式配置）。
  const env = localMode
    ? Object.assign(dataEnv(dataRoot), localModeEnv(dataRoot, process.env, config.secretKey))
    : Object.assign({ PYTHONDONTWRITEBYTECODE: '1' }, dataEnv(configuredDataRoot))
  const args = [...SERVER_ARGS]

  if (localMode) {
    // 包内引擎 = 本地单用户：没有账号体系，用固定 user_id（默认 1），不传 token
    const localUserId = resolveLocalUserId(config, readEnvVar)
    args.push('--local-user', String(localUserId))
    registerGraphRoute(ctx, engineDir, { python, dataRoot, localUserId })
  } else {
    // 自托管：token 来自 AI-tutor 登录接口的 access_token（24h）
    const token = String(config.token ?? readEnv(ctx, 'AI_TUTOR_TOKEN')).trim()
    if (!token) {
      throw new Error(
        'ai-tutor: 缺少访问 token（自托管模式）。请任选其一：\n'
          + '  1) 在 $DSH_HOME/.env 写 AI_TUTOR_TOKEN=<access_token>\n'
          + '  2) 在 profile 的 cordis.patch.yml 里覆盖本行：config: { token: <access_token> }\n'
          + 'token 取自 AI-tutor 登录接口 /api/v1/auth/login 的 access_token（有效期 24h，过期需重取）。\n'
          + '如果用的是自带 engine 的发布包（本地单用户模式），不需要 token —— 检查 engine 目录是否解析到了包内 engine/。',
      )
    }
    env.AI_TUTOR_TOKEN = token
  }

  await mcpClient.apply(ctx, {
    transport: 'stdio',
    serverName: SERVER_NAME,
    command: python,
    args,
    // 不传 cwd → 宿主会用自己所在目录 → -m app.mcp_servers.kg_tools import 失败
    cwd: engineDir,
    env,
    toolCallTimeoutMs: config.toolCallTimeoutMs ?? DEFAULT_TOOL_CALL_TIMEOUT_MS,
    failOnStartupError: config.failOnStartupError ?? true,
  })
}
