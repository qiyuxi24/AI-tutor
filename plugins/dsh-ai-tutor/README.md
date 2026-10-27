# dsh-ai-tutor

把 [AI-tutor](https://github.com/deepseek-ai/AI-tutor) 的**只读知识库能力**接进
[DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)（DSH）：

- **模型工具**：会话里可调用 `rag_search` / `kg_snapshot` / `fetch_webpage`
  （模型侧名字是 `mcp__ai-tutor__<tool>`）。
- **知识图谱面板**：DSH 右栏多一个「知识图谱」图标，显示学科 / 节点 / 掌握度条 / 下一步建议。

本包不含领域逻辑：检索、用户隔离、图谱生命周期都在 AI-tutor 主仓的 Python 侧
（`backend/app/mcp_servers/kg_tools.py` → `gateway.py` → 各工具）。改工具不用改本包。

---

## 两种运行模式

| 模式 | 什么时候 | 引擎位置 | 身份 | 面板 |
|---|---|---|---|---|
| **本地单用户**（发布包） | 用户只装本插件、没装 AI-tutor | 包内 `engine/` + `runtime/`（自带 Python） | 固定 `--local-user <id>`（默认 1），**不需要 token** | ✅ 注册 `/ai-tutor/graph` |
| **自托管**（开发态 / 已有部署） | 本插件与 AI-tutor 仓库在同一台机器 | 仓库 `backend/`（用仓库 venv） | `AI_TUTOR_TOKEN` 必填（24h 过期） | ❌ 不注册（主仓自己有前端） |

判据只有一个：解析出来的 engine 目录**是否在插件包内**（`<插件目录>/engine`）。
装的是发布包 → 本地模式；在仓库里 `dsh plugin add plugins/dsh-ai-tutor` 且没构建
`engine/` → 自托管模式。

---

## 安装

### 发布包（普通用户：无需 Python）

```sh
# 1) 构建（打包者做一次；工程量大，约 180 MB）
cd plugins/dsh-ai-tutor
node ../../scripts/build-plugin.mjs   # 产出 engine/ + runtime/ + runtime/MANIFEST.json
pnpm pack                             # 产出 dsh-ai-tutor-0.1.0.tgz

# 2) 用户安装
dsh plugin add ./dsh-ai-tutor-0.1.0.tgz
# 重启 DSH；**开一个新会话**（工具在会话创建时绑定）
```

`dsh plugin add` 会因为本包 `package.json` 的 `dsh.bundle` 字段自动把
`dsh-ai-tutor` 追加进 profile 的 `dsh.profile.bundles`，不需要手改 profile 清单。

### 仓库开发态（自托管）

```sh
dsh plugin add D:/path/to/AI-tutor/plugins/dsh-ai-tutor

# token 放到 $DSH_HOME/.env（取自 AI-tutor POST /api/v1/auth/login 的 access_token，24h 有效）
AI_TUTOR_TOKEN=<access_token>
```

自托管模式需要仓库 `venv/Scripts/python.exe` 可用（见 `AGENTS.md` §0）。

### 装完怎么确认

```sh
cd plugins/dsh-ai-tutor
node bin/doctor.mjs                     # 10 项自检；全 ✅ 才继续
node bin/doctor.mjs --host http://127.0.0.1:55448   # 额外检查面板端点（可选）
```

模型侧工具是否生效，以 doctor 第 04/06 项（engine 能 import、MCP 握手成功）为准；
启动日志里成功时**没有**正面输出，失败才会出现 `1 entry did not activate`。

---

## 数据放在哪（重要）

| 模式 | 默认数据根 | 说明 |
|---|---|---|
| 本地单用户 | Windows `%LOCALAPPDATA%\dsh-ai-tutor\data`；Linux/macOS `~/.local/share/dsh-ai-tutor/data` | 首次激活自动创建；**卸载/升级不会删** |
| 自托管 | 仓库根 `data/` | 与主系统共用；token 用户的图谱/资料都在这里 |

覆盖顺序（两模式相同）：

1. profile 里本行 config `dataRoot`
2. 环境变量 `AI_TUTOR_DATA_ROOT`
3. 本地模式 → 平台默认目录；自托管 → 交给 engine（仓库 `data/`）

> ⚠️ **不要把数据根指到插件包目录里**（例如 `.../plugins/dsh-ai-tutor/data`）。
> `dsh plugin remove` 会删插件目录，数据一起没了。计划 `DSH插件MVP_施工计划.md` §D3 也是这么定的。

数据根下会自动生成一个 `secret_key`（64 字符 hex、权限 0600，首次激活时写入）：
它是本地模式子进程的 `SECRET_KEY`（engine 导入期要求非空，本地不做 HTTP 鉴权）。
每台机器一份、不出本机；用户显式配置 `SECRET_KEY` 时**不覆盖**。删掉它下次启动会重建。

副产物位置：日志默认在 engine 目录旁（本地模式为包内 `engine/logs`，可被 `LOG_DIR` 覆盖）。

想把已有 AI-tutor 图谱接进来：`AI_TUTOR_DATA_ROOT=<AI-tutor 仓库>/data` 即可。

---

## 配置

本包 `cordis.patch.yml` 只插一行、不写死任何路径；路径与身份在**激活时**解析。
要覆盖，就在 profile 自己的 `cordis.patch.yml` 里按 `id` 写（**后层整段替换 config，不是深合并**）：

```yaml
- id: ai-tutor
  name: dsh-ai-tutor
  config:
    backendDir: D:/GitHub/AI-tutor/backend
    token: <access_token>
```

| 字段 | 默认 | 说明 |
|---|---|---|
| `backendDir` | 自动探测 | engine 绝对路径（须含 `app/mcp_servers/kg_tools.py`）；给错立即报错 |
| `repoRoot` | 自动探测 | 仓库根，自动补 `/backend`；`backendDir` 优先 |
| `python` | 包内 runtime → `AI_TUTOR_PYTHON` → engine 的 venv → PATH | 显式配置**覆盖**自动探测（开发调试用） |
| `dataRoot` | 见上一节 | 数据根绝对路径 |
| `localUserId` | `1` | 仅本地模式；正整数 |
| `token` | `AI_TUTOR_TOKEN` | 仅自托管模式；必填 |
| `secretKey` | 数据根 `secret_key` 文件 | 仅本地模式；显式值优先（也可用 env `SECRET_KEY`），**不覆盖**用户已给的值 |
| `toolCallTimeoutMs` | `90000` | `rag_search` 要打嵌入 API，别调到 60s 以下 |
| `failOnStartupError` | `true` | 路径/token 错时让**本条目**明确失败，而不是留个连上却检索不到的空插件 |

环境变量（都能被上面的 config 覆盖）：

| 变量 | 作用 |
|---|---|
| `AI_TUTOR_HOME` | 仓库根（自动探测失败时用） |
| `AI_TUTOR_PYTHON` | 解释器绝对路径 |
| `AI_TUTOR_DATA_ROOT` | 数据根（会同时以 `DATA_ROOT` 下发） |
| `AI_TUTOR_LOCAL_USER` | 本地模式 user_id |
| `AI_TUTOR_TOKEN` | 自托管模式 access_token |
| `SECRET_KEY` | 本地模式显式密钥（优先于自动生成的 `secret_key`；自托管模式由 engine 自己读 `.env`） |

engine 探测顺序：`config.backendDir` → `config.repoRoot/backend` → 包内
`<插件>/engine` → `AI_TUTOR_HOME/backend` → 宿主 cwd 上溯 8 级。显式配置给错**立即抛**
（含已检查清单与修法），不会静默回退。

---

## 知识图谱面板

本地模式下，插件激活时注册宿主路由 `GET /ai-tutor/graph`（`kind: exact`）：

- 面板代码在 `lib/client.js`（手写 `window.__ModuleLoader__.load`，无打包器、无 JSX）；
  侧栏图标 + 主视图面板，每 **15s** 轮询一次。
- 第一次请求才懒启动本地 Python HTTP 后端
  （`<python> -m app.mcp_servers.local_http --local-user N`，只监听 `127.0.0.1:0`，
  stdout 打印唯一一行 `PORT=<n>`，日志走 stderr）。
- 面板卸载/插件重载时先 kill 子进程再关 stdin，不留孤儿进程。

转发失败语义（**永不抛给宿主**，面板错误只显示在面板内）：

| 情况 | HTTP | 响应体 |
|---|---|---|
| 正常 | 200 | 原样转发 local_http 的 `/graph` JSON（`subjects` / `nodeCount` / `next` / `dataRoot`） |
| 非 GET | 405 | `{ "error": "...只支持 GET...", "method": "POST" }` |
| engine 里没有 `local_http.py`（引擎版本旧） | 501 | `{ "error", "detail", "hint" }`，hint 说明如何处理 |
| 子进程起不来 / 转发异常 | 502 | `{ "error", "detail", "hint" }`，detail 带 stderr 尾部 |

面板坏**不影响**模型工具：工具走 MCP stdio，完全不经过这条 HTTP 链路。

---

## 完整面板（AI 导师，iframe）

除了只读的「知识图谱」摘要，插件还托管**主仓的完整前端**：DSH 面板里用 iframe 打开
`http://127.0.0.1:<随机端口>`，可视化图谱 / 文件上传 / 图谱生成 / RAG / 出题 / 对话都在里面。

依赖两样东西（都在发布包里；缺了只在面板内降级，不影响模型工具）：

| 需要 | 位置 | 说明 |
|---|---|---|
| 后端 | `engine/app/mcp_servers/panel_server.py` | 完整 FastAPI 应用（`app.main:app`）的本地托管入口 |
| 前端构建产物 | `plugins/dsh-ai-tutor/panel/`（`index.html` + `assets/`） | 由 `node scripts/build-panel.mjs` 从 `frontend/` 构建，**不进 git** |

进程模型（与 MCP 工具**完全独立**）：

- 宿主路由 `GET /ai-tutor/panel` → 200
  `{ "url": "http://127.0.0.1:<port>", "localUser": 1, "panel": { "available": true } }`；
  客户端拿到 `url` 后全屏 iframe，顶部工具条提供「刷新」与「在浏览器打开」。
- **第一次请求才懒启动**：`<python> -m app.mcp_servers.panel_server --local-user N`
  （cwd = `engine/`，只监听 `127.0.0.1` 的随机端口）；stdout 唯一一行 `PORT=<n>`，
  日志走 stderr；随后轮询 `GET /health` 返回 2xx 才判定就绪（启动上限 30s）。
  并发请求共用同一次启动，不会起两个进程。
- 子进程环境 = 本地模式环境 + `AI_TUTOR_PANEL_DIR=<插件>/panel` + `SECRET_KEY`
  （`<数据根>/secret_key`，不出本机）+ `.env` 透传（候选：`config.envFile` →
  `<数据根父目录>/.env` → `<engine 父目录>/.env`；KEY=VALUE 解析、**不覆盖已有环境变量、
  跳过 SECRET_KEY、绝不打印值**）+ `PYTHONDONTWRITEBYTECODE=1` / `PYTHONIOENCODING=utf-8`。
- 卸载 / 重载：先 kill 子进程再关 stdin（stdin EOF 是 panel_server 的优雅退出信号），
  不留孤儿进程。插件自己也**从不**接管真实端口之外的资源。

失败语义（**永不抛给宿主**；错误只显示在面板内，中文可操作、带 doctor 指引）：

| 情况 | HTTP | 响应体 |
|---|---|---|
| 正常 | 200 | `{ url, localUser, panel }` |
| 非 GET | 405 | `{ error, method } ` |
| 缺 `panel_server.py` 或 `panel/index.html` | 501 | `{ error, detail, hint }`，hint 指向 `scripts/build-panel.mjs` 与 doctor 第 11/12 项 |
| 启动 / 健康检查失败 | 502 | `{ error, detail, hint }`，detail 是脱敏后的 stderr 尾部 |

> 端口是随机的、进程重启后会变；「在浏览器打开」用的就是当前 `url`。
> 如果宿主对自身页面设了 `frame-src` 之类 CSP，iframe 可能被拦 —— 面板内会显示错误，
> 此时「在浏览器打开」仍可用。

---

## 自检：`node bin/doctor.mjs`

```sh
node bin/doctor.mjs                    # 13 项，逐项 ✅ / ❌ / SKIP + 修复建议
node bin/doctor.mjs --fix              # 额外做安全的创建类操作（只建数据根目录）
node bin/doctor.mjs --host http://127.0.0.1:55448   # 加测宿主 /ai-tutor/graph 与 /ai-tutor/panel
node bin/doctor.mjs --local-user 2     # 指定本地 user_id（第 6/12 项用）
```

| # | 检查 |
|---|---|
| 01 | `runtime/` 里的 Python 存在且 `--version` 可执行 |
| 02 | `runtime/MANIFEST.json`：版本与实际 Python 一致、包已装齐 |
| 03 | `engine/app/mcp_servers/kg_tools.py` 存在（并提示 `local_http.py` 有无） |
| 04 | engine 能在包内 Python 下 import gateway，并打印工具清单与白名单 |
| 05 | 数据根存在且可写（`--fix` 可创建） |
| 06 | 能 spawn MCP server 并完成 `initialize` + `tools/list` 握手（10s 超时） |
| 07 | 工具数 == 白名单长度（防止白名单与注册表漂移） |
| 08 | 插件所在分区剩余空间 ≥ 500 MB |
| 09 | `lib/client.js` 语法可解析（`new Function`）且含加载标记 |
| 10 | 宿主 `/ai-tutor/graph` 可达（需要 `--host`；没给就 SKIP） |
| 11 | 完整面板静态产物 `panel/index.html` 存在 |
| 12 | 完整面板后端能 spawn 且 `/health` 返回 200（离线；会用临时数据根真起一次再停） |
| 13 | 宿主 `/ai-tutor/panel` 可达，且返回的 `url` 能访问到 iframe 首页（需要 `--host`） |

退出码：任一项 ❌ → 1；否则 0。**有 ❌ 按「修复建议」处理后再跑一次**，不要跳过。
探测过程用临时 `DATA_ROOT`/`LOG_DIR`（包括第 12 项起的那次 panel server），不会碰真实数据。
若在受限沙箱里 python 只被允许写工作区，第 12 项会因临时目录不可写而失败 ——
用 `AI_TUTOR_DOCTOR_PROBE_ROOT=<可写目录>` 指一个可写位置再跑。

---

## 隐私

- **只读**：本包只暴露三个只读工具（`rag_search` / `kg_snapshot` / `fetch_webpage`）；
  看得到哪些工具由 engine 的 `gateway.ALLOWLIST` 决定，本包不持有白名单。
- **不上传**：插件本身没有遥测、不把图谱/资料内容发给 DSH 或任何第三方。
  数据只在本机数据根；网络请求只有两类：`rag_search` 的查询向量化（走用户在主系统
  配置的嵌入 API）与 `fetch_webpage` 抓取目标网页。
- **隔离**：数据按 `user_id` 分区；本地模式固定单用户，自托管模式 token 是谁就只看谁的。
- **子进程环境清洗**：密钥类变量（`KEY`/`PASSWORD`/`SECRET`/`TOKEN`）不会带进 Python
  子进程；本地模式用数据根下的 `secret_key`（每台机器随机生成、0600、不出本机）作为
  `SECRET_KEY`，用户显式配置的值优先、不被覆盖。自托管模式不注入，engine 自己读仓库 `.env`。
- **不污染插件包**：所有 Python 子进程（MCP、local_http）都带 `PYTHONDONTWRITEBYTECODE=1`，
  不会在 `engine/` 里生成 `__pycache__`（否则每次运行都多几 MB，还会被打进发布包）。
- **面板**：`/ai-tutor/graph` 只返回节点标题/掌握度等元信息，不返回节点正文。

---

## 断网能用吗

| 能力 | 断网 |
|---|---|
| `kg_snapshot`（图谱摘要） | ✅ 完全本地 |
| 知识图谱面板 | ✅ 完全本地（同源请求 127.0.0.1） |
| 完整面板（iframe） | ✅ 界面与图谱/资料全本地；出题、对话等模型功能需联网 |
| `rag_search` | ⚠️ 空库返回友好提示；**有内容时**需要联网调用嵌入 API |
| `fetch_webpage` | ❌ 必须联网 |

---

## 卸载

```sh
dsh plugin remove dsh-ai-tutor
```

- 插件目录、profile 里的 bundle 条目、宿主路由与本地 HTTP 子进程一起消失。
- **数据保留**：默认数据根在用户目录，不在插件目录内，卸载不会删。
  想彻底清掉就手动删 `%LOCALAPPDATA%\dsh-ai-tutor`（或 `~/.local/share/dsh-ai-tutor`）。
- 重装后数据仍在（计划 P4 验收项 6）。

---

## 常见错误

| 现象 | 原因 / 处理 |
|---|---|
| 启动 stderr `1 entry did not activate` + `initial connection or tool synchronization failed` | 看同段 Python 报错。本地模式多为 engine/runtime 不完整（跑 doctor 01–04）；自托管多为 token 失效或 `backendDir` 错 |
| doctor 01 ❌：找不到 runtime Python | 包没构建完整 → 重新 `node scripts/build-plugin.mjs` 或换发布包 |
| doctor 02 ❌：MANIFEST 版本不匹配 | `engine/` 与 `runtime/` 不是同一次构建的 → 重新构建 |
| doctor 04 ❌：`SECRET_KEY 环境变量未配置` | 直接用 runtime Python 手工跑 engine 会这样（插件链路会读/建 `<dataRoot>/secret_key` 后注入）。若真机仍报，检查是不是把本包当脚本直接跑了，或数据根不可写 |
| doctor 05 ❌：数据根不存在 | `node bin/doctor.mjs --fix`，或设 `AI_TUTOR_DATA_ROOT` |
| doctor 06 ❌：MCP 握手失败 | 看 detail 里的 stderr 尾部：缺依赖 / engine 版本旧 / `--local-user` 不支持（老 engine） |
| doctor 10 404 | 插件没装进当前 profile，或 DSH 没重启；本地模式才会注册这条路由 |
| 面板显示「面板后端还没就绪」（501） | engine 版本旧、缺 `local_http.py` → 重新构建 engine；模型工具不受影响 |
| 面板显示调用失败（502） | 本地 Python 起不来，看 detail；`node bin/doctor.mjs --host ...` 复检 |
| 面板空但模型能检索 | 面板只显示**当前 user_id** 的图谱；本地模式默认 user 1，可用 `--local-user` 对齐 |
| 工具名找不到 | 名字带前缀：`mcp__ai-tutor__rag_search`；`serverName` 固定 `ai-tutor`（改名即破坏历史与权限，不开放配置） |
| 检索结果为空 | 空结果不是错误；先确认该 user 在主系统里真有节点/已索引资料 |
| 装完没反应 | 宿主需要 app bundle（如 `@deepseek-ai/dsh-acp-app`）；只装 base 不会启动插件树 |

---

## 开发

```sh
# 语法检查（无构建步骤）
cd plugins/dsh-ai-tutor
for f in index.js lib/*.js bin/doctor.mjs; do node --check "$f"; done

# 插件壳冒烟（不连宿主；用假 engine + 假 local_http.py 断言解析/转发/清理逻辑）
cd <仓库根> && node .dsh_local/smoke-plugin-shell.mjs

# 构建发布产物（engine = backend/app 整包复制；runtime = 便携 Python + 13 个依赖）
node scripts/build-plugin.mjs

# 完整面板的前端产物（Vite 构建 frontend → 拷到 plugins/dsh-ai-tutor/panel/）
node scripts/build-panel.mjs
```

- `engine/` 与 `runtime/` 是**构建产物、不进 git**；源码改动后必须重新构建才会进包。
- 客户端面板改动：DSH 客户端插件 HMR 需要同一 checkout 里 `pnpm run dev:web` 在跑，
  否则改完要重启 DSH。
- 契约来源：`docs/架构/DSH插件MVP_施工计划.md`（P3/P5）、
  `docs/架构/DSH验证记录_2026-10-26.md`。

### 版本矩阵（实测）

DSH `0.2.1-rc.2/rc.3` + `@deepseek-ai/dsh-mcp-client` `0.1.5-rc.2` + Node ≥ 22。
`package.json` 声明 `engines.node >= 22`、peer 依赖 `@deepseek-ai/dsh-mcp-client >= 0.1.5-rc.2`；
profile 的 pnpm 配置 `autoInstallPeers: false`，peer 由宿主提供、不会去 registry 下载。
