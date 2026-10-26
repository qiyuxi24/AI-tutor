# DSH 插件 MVP 施工计划

> 编写：2026-10-27 ｜ 执行者：AI-tutor 侧 agent ｜ 前置文档：`docs/架构/DSH插件化_改造计划.md`、`docs/架构/DSH验证记录_2026-10-26.md`
> **本计划的技术不确定性已在 2026-10-27 全部实测排除（见 §0），执行时不要再重做可行性论证。**

---

## 0. 已完成的可行性实测（实测于本机，2026-10-27）

| 验证项 | 结果 | 证据 |
|---|---|---|
| 只装最小依赖的干净 venv 能否 import 网关 | ✅ 成功 | `from app.core.agent_tools import gateway` 通过，仅 whoosh 的 SyntaxWarning（无害） |
| 最小依赖集体积 | **111.2 MB**（venv，含 mcp 的传递依赖） | `du` 实测 |
| `gateway.list_specs()` | ✅ 返回 3 个：`rag_search` / `search_resources` / `fetch_webpage` | 实测输出 |
| 全新 `DATA_ROOT`（无 knowledge.db） | ✅ `KnowledgeGraph(1)` **自动建库**，不抛异常 | 实测：自动创建 `data/knowledge/` |
| **零 API key** 调 `rag_search` | ✅ 正常返回「未检索到相关内容，请基于已有知识回答，或换个角度再试。」 | 实测；降级路径见 `core/rag/manager.py:325`（BM25 先写、向量腿失败只影响向量腿、两路都空走 `_keyword_search` 子串兜底） |
| 重依赖（onnxruntime / PyMuPDF / yt-dlp / tiktoken） | ✅ **不在只读路径的 import 链里** | sys.modules 探针：加载的第三方顶层包只有 24 个 |

**结论：MVP 没有未知的技术风险，剩下的全是工程活。** 依赖清单与体积已在 §4 D5 锁定。

---

## 1. 目标与边界

**一句话目标**：让用户在 DSH 里装一个插件，就获得「知识图谱 + 资料检索」能力——**零配置、零 API key、零后端、数据全在本机**。

| IN（本次做） | OUT（明确不做） |
|---|---|
| 3 个模型工具：`rag_search` / `kg_snapshot` / `fetch_webpage` | 主系统 Web 产品：对话循环、96 条 API 路由、Vue 图谱页面 |
| **客户端面板**：右侧边栏渲染知识图谱（可视化 + 检索） | SaaS、账号体系、PAT、计费 |
| **宿主 HTTP 路由**：面板 ⇄ 便携 Python 的数据桥 | 采集栈 / OCR / PDF / 视频（重依赖全不要） |
| 便携 Python 运行时内置（用户不装 Python） | 模型侧写工具（改图谱/出题/判分放面板侧，二期） |
| 背包内 engine（`backend/app/` 全量，4.3 MB） | 多人协作、实时同步、移动端 |
| doctor 自检 + `--fix` + 本地数据根 + 首启自动建库 | 客户端独立构建链（手写 ModuleLoader 包装，零构建） |

---

## 2. 锁定的事实（执行时不要重开讨论）

- **F1** 模型由 DSH 提供，插件不调 LLM → 不需要 LLM key。
- **F2** DSH 会按 `/KEY|PASSWORD|SECRET|TOKEN/i` 清洗父环境，**宿主 key 传不到子进程**（`DSH验证记录` 实测）→ 不要指望继承 DashScope key。
- **F3** `rag_search` 在无 embedding key 时**自动降级**为 BM25/关键词，仍可用（已实测）→ **保留它**，不砍。
- **F4** `search_resources` 的真实语义是「按学科发现**尚未入库**的网络候选」（`search_resources.py:1-10`），函数内 import `collector.manager`（采集栈，重）→ **本 MVP 不进白名单**。
- **F5** `fetch_webpage` 只 import `httpx` + `net_guard`（轻），但 handler 内会惰性 import `web_archive.archive_html`（连采集栈）→ 需在 P1 验证，若拖重则本 MVP 降为 2 工具。
- **F6** 组件顺序：`kg_tools.py`（MCP server）→ `gateway.py`（白名单+身份）→ `dispatch` → `tools/*.py`。
- **F7** 工具名 = `mcp__ai-tutor__<rawName>`，`SERVER_NAME = 'ai-tutor'` 是**兼容性契约**，不许改。
- **F8** 插件壳现有 207 行 `index.js`，已实现「backendDir 探测 / python 探测 / token 必填 / mcpClient.apply」，本次只做**增量改造**，不重写。

---

## 3. 核心组件清单（9 个）

| # | 组件 | 路径（相对仓库根） | 类型 | 职责 | 完成标志 |
|---|---|---|---|---|---|
| C1 | 白名单网关 | `backend/app/core/agent_tools/gateway.py` | 改 | `ALLOWLIST` 改为 `("rag_search", "fetch_webpage", "kg_snapshot")` | 单测断言通过 |
| C2 | 图谱快照工具 | `backend/app/core/agent_tools/tools/kg_snapshot.py` | **新** | 只读返回用户图谱摘要（学科/节点数/掌握度/下一步） | 单测 + 真库调用非空 |
| C3 | 工具注册 | `backend/app/core/agent_tools/tools/__init__.py` | 改 | import `kg_snapshot`、加进 `NATIVE_SPECS` | `KG_TOOLS` 含新工具 |
| C4 | MCP server 本地身份 | `backend/app/mcp_servers/kg_tools.py` | 改 | 加 `--local-user`：无 token 且给该参数 → 以该 user_id 启动 | `--local-user 1` 能 `tools/list` |
| C5 | 插件壳 | `plugins/dsh-ai-tutor/index.js` | 改 | 加「包内 engine / 包内 runtime 优先」发现；本地模式免 token | 见 P3 验收 |
| C6 | 便携运行时 | `plugins/dsh-ai-tutor/runtime/` | **构建产物** | `python.exe` + `Lib/site-packages`（111 MB 依赖） | `runtime/python.exe` 能 import 网关 |
| C7 | 构建脚本 | `scripts/build-plugin.mjs` | **新** | 组装 engine + runtime，写 `MANIFEST.json`，实测体积 | 一条命令产出可打包目录 |
| C8 | 诊断 | `plugins/dsh-ai-tutor/bin/doctor.mjs` | **新** | 8 项检查 + `--fix` | 故意删 runtime 后报错准确 |
| C9 | 插件文档 | `plugins/dsh-ai-tutor/README.md` | 改 | 安装/数据位置/卸载/隐私声明/常见错误 | 一个新人照做能装 |
| C10 | 客户端面板 | `plugins/dsh-ai-tutor/lib/client.js` | **新** | React 组件注册进 `sidebar.panellist`：图谱学科/节点/掌握度可视化 | DSH 里点开能看到面板 |
| C11 | 宿主数据路由 | `plugins/dsh-ai-tutor/index.js` | 改 | `ctx.webServer.register` 暴露 `GET /ai-tutor/graph`，转发便携 Python | 面板数据拉通 |

辅助：单测 `backend/tests/test_gateway_allowlist.py`（改）、`backend/tests/test_tools_kg_snapshot.py`（新）。

---

## 4. 技术决策（已锁定，勿改）

- **D1 单包单 worker**：一个 npm 包 = 一个 MCP server 进程，不分拆。
- **D2 engine 整包复制 `backend/app/`（4.3 MB），不做裁剪**。理由：源码才 4.3 MB，裁剪会引入「改对了但没测到」的风险，不值得优化。
- **D3 数据根在用户目录**：`%LOCALAPPDATA%\\dsh-ai-tutor\\data`（Windows）/ `~/.local/share/dsh-ai-tutor/data`；环境变量 `AI_TUTOR_DATA_ROOT` 覆盖。**不要**写进插件包目录（卸载会删数据）。
- **D4 首启零配置**：`KnowledgeGraph(user_id)` 在空 DATA_ROOT 下自动建库（已实测）→ 不需要 seed 脚本。prompts 目录只在主系统对话链路需要，MCP 路径不需要（已实测 import 不依赖）。
- **D5 锁死依赖集与 Python 版本**：**Python 3.13**；依赖只这 13 个（`uv pip install` 会带入 uvicorn/starlette/truststore 等传递依赖）：
  `numpy Pillow cryptography python-jose bcrypt python-dotenv httpx whoosh fastapi python-multipart pygments mcp pydantic`
  实测 venv **111.2 MB**。C 扩展必须与 Python 版本严格匹配，**不得跨版本复用 runtime**。
- **D6 本地单用户**：`--local-user <id>`（默认 1）。不为多用户、不做 token 体系。
- **D7 写 `dsh.client`，但客户端半必须防御式**：客户端半是**真实 React 组件**，通过 `ctx.slots.register({name:'sidebar.panellist',...}, Component)` 注册进右栏；单个组件在 render 路径抛出会清空所在面板 → ①每个 slot 注册包在 `ctx.slots.inject()` 里 ②组件套 RenderBoundary（getDerivedStateFromError）③`apply()` 整体 try/catch。**手写 ModuleLoader 包装，不引入构建链**（先例：`@liustack/modlens` 的 `dsh/client.js` 是手写纯 JS，无 JSX、无打包器）。
- **D8 `cordis.patch.yml` 用包名解析**（`name: dsh-ai-tutor`），不写绝对路径。
- **D9 安装差异全部吃掉在 `index.js` 里**：用户不该手改包内文件。
- **D10 旁路，不替换主系统**：`app/core|api` 永不 import DSH 侧代码；主仓 `requirements.txt` 不加依赖。

---

## 5. 分阶段施工

### P0 可行性验证 —— **已完成（见 §0），跳过**

### P1 网关与工具（1 天）

**改动**

1. `gateway.py`：`ALLOWLIST: tuple[str, ...] = ("rag_search", "fetch_webpage", "kg_snapshot")`
   —— 用 tuple 保序，不要用 set（`tools/list` 与错误文案需要稳定顺序）。
2. 新建 `tools/kg_snapshot.py`，照本目录五段式（DESCRIPTION / PARAMETERS / GUIDANCE / handler / SPEC），骨架：

```python
"""工具 `kg_snapshot` —— 只读返回当前用户知识图谱的摘要（学科 / 节点 / 掌握度 / 下一步）。

为什么需要它：外部宿主（DSH）里的模型看不到主系统的图谱界面，只有一份文本；
没有这个工具，模型只能靠 `rag_search` 猜"学生学过什么"，会重复讲已掌握内容。
只读无副作用 → 免确认（TIER_FREE）。
"""

from ..registry import TIER_FREE, _spec

PARAMETERS = {
    "type": "object",
    "properties": {
        "subject": {"type": "string", "description": "只看某个学科；省略则汇总全部学科"},
        "limit": {"type": "integer", "description": "每学科最多列出的节点数（1~30，默认 10）"},
    },
}

def kg_snapshot(subject: str = "", limit: int = 10, user_id=None) -> str:
    # 1) 学科列表：kg.get_subjects()
    # 2) 每学科节点：kg.get_nodes_by_subject(subject)
    # 3) 掌握度：节点上的 mastery 字段（读 core/kg/nodes.py 的实际字段名，不要猜）
    # 4) 下一步：kg.get_next_to_learn(...)（先读签名再调）
    # 返回中文文本，永不抛异常（照 tools/README.md 三条硬约定）
```

> 执行提示：`core/kg/nodes.py` 已有 `get_subjects` / `get_nodes_by_subject` / `get_learning_path` / `get_next_to_learn` / `count_mastery_events`，**只用现成只读方法**，不要新增查询 SQL。

3. `tools/__init__.py`：加 `kg_snapshot,` 到 import 列表 + `kg_snapshot.SPEC,` 到 `NATIVE_SPECS`（放「图谱工具」组）。
4. `mcp_servers/kg_tools.py`：

```python
parser.add_argument("--local-user", type=int, default=None,
                    help="本地单用户模式：无 token 时用该 user_id（默认读 AI_TUTOR_LOCAL_USER）")
```
启动逻辑改为：有 token → 照旧验签；无 token 但有 local-user → 直接用；**两者都无 → 非 0 退出**（保留现有「缺身份即退出」语义）。

5. 新增测试：`test_gateway_allowlist.py`（断言 tuple 内容与顺序、白名单外名字被拒）、`test_tools_kg_snapshot.py`（空库返回友好文案、有节点时含学科名、不抛异常）。

**验收**

```bash
cd backend
venv/Scripts/python.exe -m pytest tests -k "gateway or kg_snapshot" -q     # 全绿
venv/Scripts/python.exe -c "from app.core.agent_tools import gateway; print([s['name'] for s in gateway.list_specs()])"
# 期望 ['rag_search', 'fetch_webpage', 'kg_snapshot']（或 fetch_webpage 被 F5 判定砍掉后为 2 个）
venv/Scripts/python.exe -m app.mcp_servers.kg_tools --local-user 1
# 手工发 initialize + tools/list，stderr 出现 "tools/list -> 3 个工具"
```

**回退**：只改白名单与新增模块，主系统 `agent/loop.py` 不经过 gateway，删掉新模块即回到原状。

### P2 便携运行时构建（1 天）

**`scripts/build-plugin.mjs`（Node 24，零第三方依赖，用 `node:` 内置模块）**

步骤：

1. `rm -rf plugins/dsh-ai-tutor/engine plugins/dsh-ai-tutor/runtime`
2. **engine**：复制 `backend/app/` → `plugins/dsh-ai-tutor/engine/app/`（排除 `__pycache__`、`*.pyc`）
3. **runtime**：下载 python-build-standalone **install_only** 包（Windows x64 / CPython 3.13），解压到 `runtime/`，确认 `runtime/python.exe` 存在。
   URL 形如 `https://github.com/astral-sh/python-build-standalone/releases/download/<tag>/cpython-3.13.*-x86_64-pc-windows-msvc-install_only.tar.gz`
4. **依赖**：`uv pip install --python runtime/python.exe <D5 的 13 个包>`（有 `uv` 用它，无则 `runtime/python.exe -m pip`）
5. **校验**：`runtime/python.exe -c "import sys; sys.path.insert(0,'engine'); from app.core.agent_tools import gateway; print([s['name'] for s in gateway.list_specs()])"` 必须打印 3 个工具名 → 失败即中止构建
6. 写 `runtime/MANIFEST.json`：`{pythonVersion, packages, builtAt, engineFiles, totalBytes}`
7. 打印体积报告（engine / runtime 分开）

**验收**：上述第 5 步通过；`plugins/dsh-ai-tutor` 总大小 ≤ 180 MB（压缩后 tgz 预计 55–70 MB，npm 单包上限 100 MB）。

**回退**：构建产物不入 git；删目录重跑即可。

### P3 插件壳与 doctor（1 天）

**`index.js` 增量改造（不重写）**

1. 顶部加 `import { fileURLToPath } from 'node:url'`，`const PLUGIN_ROOT = dirname(fileURLToPath(import.meta.url))`
2. 新增 `resolveEngineDir(ctx, config)`：
   `config.backendDir` → `config.repoRoot+backend` → `PLUGIN_ROOT/engine`（判据同 `isBackendDir`）→ `AI_TUTOR_HOME/backend` → cwd 上溯 8 级
3. 新增 `resolvePython(ctx, config)` 的第一优先级：`join(PLUGIN_ROOT, 'runtime', 'python.exe')` / `join(PLUGIN_ROOT, 'runtime', 'bin', 'python')`
4. `apply()` 分两种模式：
   - **本地模式**（engine 来自 `PLUGIN_ROOT/engine` 且无 token）：`args: [...SERVER_ARGS, '--local-user', String(config.localUserId ?? 1)]`，`env` 不传 token
   - **自托管模式**（原有逻辑）：token 必填，照旧
   - 两种都要：`cwd: engineDir`、`serverName: 'ai-tutor'`、`failOnStartupError: true`

**`bin/doctor.mjs`**：8 项检查，逐项 ✅/❌ + 一句修复建议；`--fix` 只做安全的创建类操作。

检查项：① `runtime/python.exe` 存在且 `--version` 可执行 ② `runtime/MANIFEST.json` 版本匹配 ③ `engine/app/mcp_servers/kg_tools.py` 存在 ④ `runtime/python.exe -c "import sys;sys.path.insert(0,'engine');import app.core.agent_tools.gateway"` 退出码 0 ⑤ DATA_ROOT 可写 ⑥ 能 spawn MCP 并收到 `tools/list`（用 `--local-user 1` 起子进程，10s 超时）⑦ 工具数 == 白名单长度 ⑧ 磁盘剩余 ≥ 500 MB（首次解压需要）

**`package.json`**：加 `"files": ["index.js","cordis.patch.yml","engine","runtime","bin","README.md"]`、`"bin": {"dsh-ai-tutor-doctor": "bin/doctor.mjs"}`、`"engines": {"node": ">=22"}`。

**验收**

```bash
cd plugins/dsh-ai-tutor && pnpm pack           # 出 dsh-ai-tutor-0.1.0.tgz，记录体积
node bin/doctor.mjs                            # 8 项全 ✅
dsh plugin add ./dsh-ai-tutor-0.1.0.tgz
# 重启 DSH → 开新会话（工具在会话创建时绑定）→ 检查 mcp__ai-tutor__kg_snapshot 可调用
```

**回退**：`dsh plugin remove dsh-ai-tutor`；插件壳改动集中在 `index.js`，git 可回滚。

### P4 真机验收（0.5 天）

在**未装过 AI-tutor、没有 Python** 的干净用户环境里：

| # | 验收项 | 通过标准 |
|---|---|---|
| 1 | 一条命令装 | `dsh plugin add <tgz>` 成功，重启后 3 个工具出现 |
| 2 | `kg_snapshot` | 返回非空摘要（空库也应返回「图谱还是空的」友好文案） |
| 3 | `rag_search` | 空库返回友好提示不报错（已实测） |
| 4 | 断网 | 1/2/3 除 `fetch_webpage` 外仍可用 |
| 5 | 卸载 | `dsh plugin remove` 后零残留（插件目录、层配置） |
| 6 | 数据不丢 | 卸载重装后 `%LOCALAPPDATA%\\dsh-ai-tutor\\data` 仍在 |
| 7 | 主仓回归 | `cd backend && venv/Scripts/python.exe -m pytest tests -q` **1784 例全绿** |

---

### P5 客户端面板与宿主路由（2 天）

> **本节推翻早期的「不做客户端半」结论。** DSH 的客户端插件是真实 React 组件，源码证据（本机 `~/.dsh/profiles/web/node_modules`）：
> - `dsh-context` 用 `ctx.slots.inject('sidebar.panellist', () => ctx.slots.register({name:'sidebar.panellist', id, order, label, locale}, IconComponent))` 注册右栏图标与面板；用 `{name:'conversation.view', id, order, label}` 注册对话区页签；用 `{name:'main'}`、`{name:'conversation.input.overlay'}`、`{name:'conversation.chat.assistant-actions'}` 注册主视图/输入框浮层/消息旁操作。
> - `@liustack/modlens/dsh/client.js` 是**手写**的 `window.__ModuleLoader__.load({ id, factory: (require) => { ... exports.apply = apply; exports.inject = []; return module.exports } })`，宿主注入 `react` / `react-dom` / `react/jsx-runtime` / `@deepseek-ai/dsh-client-ui-primitives`。
> - 宿主侧配 HTTP 路由：`ctx.webServer.register({ name, kind:'exact', path:'/modlens/paste', handler:(req,res)=>{...} })`，需先 `ctx.inject(['webServer'], scope => ...)`。

**`lib/client.js` 骨架**（照 modlens 的包装格式，纯 JS + `React.createElement`，不装打包器）：

```js
window.__ModuleLoader__.load({
  id: 'dsh-ai-tutor',
  factory: (require) => {
    const React = require('react')
    const module = { exports: {} }
    const exports = module.exports

    const h = React.createElement
    const API = '/ai-tutor/graph'

    class ErrorBoundary extends React.Component {
      constructor(p) { super(p); this.state = { err: null } }
      static getDerivedStateFromError(err) { return { err } }
      render() {
        if (this.state.err) return h('div', { style: { padding: 12, color: '#c0392b' } }, '图谱面板出错了：' + String(this.state.err?.message ?? this.state.err))
        return this.props.children
      }
    }

    function useGraph() {
      const [data, setData] = React.useState(null)
      const [err, setErr] = React.useState(null)
      React.useEffect(() => {
        let alive = true
        const tick = () => fetch(API).then(r => r.json()).then(d => { if (alive) { setData(d); setErr(null) } }).catch(e => { if (alive) setErr(e) })
        tick()
        const t = setInterval(tick, 15000)
        return () => { alive = false; clearInterval(t) }
      }, [])
      return { data, err }
    }

    function GraphPanel() {
      const { data, err } = useGraph()
      if (err) return h('div', null, '读不到图谱：' + err.message)
      if (!data) return h('div', null, '加载中…')
      if (!data.subjects?.length) return h('div', null, '图谱还是空的。让模型用 kg_snapshot 或先导入资料。')
      return h('div', { style: { padding: 12, fontSize: 13, overflow: 'auto' } },
        h('div', { style: { fontWeight: 600, marginBottom: 8 } }, `知识图谱 · ${data.nodeCount} 节点`),
        data.subjects.map(s => h('div', { key: s.name, style: { marginBottom: 10 } },
          h('div', { style: { fontWeight: 500 } }, `${s.name}（${s.nodes.length}）`),
          s.nodes.slice(0, 12).map(n => h('div', { key: n.id, style: { display: 'flex', gap: 6, alignItems: 'center' } },
            h('span', { style: { flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' } }, n.title),
            h('span', { style: { width: 60, height: 4, background: '#eee', borderRadius: 2 } },
              h('span', { style: { display: 'block', width: `${Math.round((n.mastery ?? 0) * 100)}%`, height: 4, background: '#2ecc71', borderRadius: 2 } })))))))
    }

    function apply(ctx) {
      try {
        ctx.slots.inject('sidebar.panellist', () => ctx.slots.register({
          name: 'sidebar.panellist',
          id: 'ai-tutor-graph',
          order: 30,
          label: () => '知识图谱',
        }, () => h(ErrorBoundary, null, h('div', { style: { padding: '8px 12px', fontWeight: 600 } }, '知识图谱'), h(GraphPanel))))
      } catch (e) {
        console.error('[ai-tutor] slot register failed', e)
      }
    }

    exports.apply = apply
    exports.inject = []
    return module.exports
  },
})
```

**`index.js` 增补**（宿主半）：

```js
ctx.inject(['webServer'], (scope) => {
  scope.webServer.register({
    name: 'ai-tutor-graph',
    kind: 'exact',
    path: '/ai-tutor/graph',
    handler: async (req, res) => {
      try {
        const r = await fetch(`http://127.0.0.1:${pyPort}/graph`)   // 便携 Python 的只读 HTTP
        const body = await r.text()
        res.writeHead(r.ok ? 200 : 502, { 'content-type': 'application/json' })
        res.end(body)
      } catch (e) {
        res.writeHead(502, { 'content-type': 'application/json' })
        res.end(JSON.stringify({ error: String(e?.message ?? e) }))
      }
    },
  })
})
```

**Python 侧**：`app/mcp_servers/` 下新增一个极小的 `local_http.py`（**stdlib `http.server` 即可，不引入 fastapi 之外的新依赖**），监听 `127.0.0.1:0`（随机端口，把端口号打到 stdout 让 Node 读到），暴露：
- `GET /graph?subject=&limit=` → 与 `kg_snapshot` 同一份数据结构（**复用同一个函数，不要写两遍**）
- `GET /health` → `{ok:true, dataRoot}`

**验收**

```bash
cd plugins/dsh-ai-tutor && node bin/doctor.mjs       # 增第 9 项：client.js 可解析（new Function 语法检查）+ 路由可达
dsh plugin add ./dsh-ai-tutor-0.1.0.tgz             # 重启 DSH
# 右栏出现「知识图谱」图标 → 点开 → 面板显示学科与节点；故意把 client.js 写坏 → 只该面板报错，其它界面正常
```

**回退**：删 `dsh.client` 字段与 `lib/client.js`，宿主路由保留（不注册即无副作用）。

---

## 6. 验收口径（MVP done）

1. 干净机器上「装插件 → 重启 → 新会话看到 3 个工具」全程**无需安装 Python、无需任何 key、无需部署后端**。
2. `kg_snapshot` / `rag_search` 在断网下可用。
3. 数据只落在用户本地目录，插件不上传任何内容。
4. tgz ≤ 100 MB；`doctor` 能在 30 秒内定位环境问题。
5. 主仓 pytest 1784 例全绿；`node scripts/typecheck.mjs` 不因本次改动变红。
6. 右栏出现「知识图谱」面板，点开能看到学科/节点/掌握度；**故意让 client.js 抛错时，只有该面板显示错误条，DSH 其余界面正常**。

---

## 7. 风险与回退

| 风险 | 影响 | 缓解 |
|---|---|---|
| python-build-standalone 下载失败（网络/代理） | 构建卡住 | 备用 `uv python install 3.13` 后的解释器目录整目录复制（不 portable 但同机可用）；构建脚本支持 `--python-dir <已有目录>` |
| C 扩展与 Python 版本不匹配 | 运行期 ImportError | D5 锁死 3.13；构建后必须跑第 5 步校验命令 |
| npm 包超 100 MB | 发不出去 | 若超限，改「首启下载运行时」（`dsh-video-understand` 模式）；MVP 先实测 tgz 体积 |
| 杀软误报便携 Python | 装完跑不起来 | doctor 明确报「被拦截」；README 写清来源与哈希 |
| Windows 长路径 | 解压失败 | 安装目录用短路径 `%LOCALAPPDATA%`；构建时排除深目录 |
| `fetch_webpage` 的 `web_archive` 拖入采集栈 | 体积/依赖膨胀 | P1 验证；超预期则本 MVP 降为 2 工具，`fetch_webpage` 留二期 |
| DSH 0.2rc API 变动 | 插件失效 | 宿主接触面收敛在 `index.js`（`mcpClient.apply` + `webServer.register`）与 `client.js`（`slots.register`）两处；回退只删这两段 |
| 客户端半的 slot API 在 0.2.x 变动 | 面板不出现 | slot 名用字符串常量集中定义；`ctx.slots.inject` 包裹使失败静默降级（面板不出现，但工具照常工作） |
| 客户端半导致面板白屏 | 用户体验受损 | ErrorBoundary + `apply()` try/catch + 提交前用 `node --check` 语法校验；**客户端半永不抛到 render 路径之外** |

---

## 8. 不做清单（防需求蔓延）

- ❌ 不做 `search_resources`（语义是"发现网络候选"，不是检索已有资料，且拖采集栈）
- ❌ 不做 `collect_resources` / `download_resource` / `quiz_generate` / 任何写工具
- ❌ 不做多用户 / token / PAT / 计费
- ❌ 不做 SaaS、不改主系统 `agent/loop.py`、不动 96 条 API 路由
- ❌ 不为"以后再接 embedding"预留抽象层（YAGNI，降级路径已够用）

---

## 9. 硬规则（违反即返工）

1. `app/core` / `app/api` **永不** import DSH 侧任何代码或 SDK。
2. 主仓 `requirements.txt` **不加**新依赖（本轮 13 个包只进 runtime，不进主仓）。
3. `pytest` 不得依赖 Node。
4. 工具 handler **永不抛异常**，失败返回给模型看的中文文本（照 `tools/README.md`）。
5. 所有新增只读工具必须 `tier=TIER_FREE` 并在 guidance 里自述「免确认」。
6. `SERVER_NAME = 'ai-tutor'` 与工具名不得改（模型侧工具名是兼容性契约）。
7. 构建产物（`engine/`、`runtime/`）不进 git，加进 `.gitignore`。
