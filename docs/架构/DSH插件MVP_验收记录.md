# DSH 插件 MVP 验收记录（2026-10-27）

> 对应施工计划：[DSH插件MVP_施工计划.md](DSH插件MVP_施工计划.md)（用户给定，未改动）
> 结论：**计划 §6「MVP done」6 条全部通过**（两处为代码级+部分真机验证，见 §5）。真机在 DSH Web 宿主（0.1.5-rc.2）里装插件、看 3 个工具、点开右栏「知识图谱」面板出 22 个真实节点，全程零 Python 安装、零 key、零后端部署。
> 状态（2026-10-27 更新）：已按用户拍板装进 **live profile = `~/.dsh/profiles/desktop`**（当前 GUI 127.0.0.1:55448）——原生安装、不引其它插件、不动其它包（做法见 §8）；改动已按文件族提交。装后**首验发现并修复了一个线上缺陷**（面板路由未注销 → 热重载后永久 502，见 §9），修复版已在隔离宿主复验 200，**待宿主重启后在 live 复验**。

## 1. 交付物

### 1.1 主仓（backend，3 个新功能文件 + 3 个新测试）

| 文件 | 行数 | 作用 |
|---|---|---|
| `backend/app/core/graph_snapshot.py` | 176 | 唯一快照实现：`snapshot(kg, subject, limit)` / `render_text()` / `clamp_limit()`；工具与本地 HTTP 共用，无第二份 |
| `backend/app/core/agent_tools/tools/kg_snapshot.py` | 59 | 新工具 `kg_snapshot`（TIER_FREE，guidance 自述免确认，handler 永不抛） |
| `backend/app/core/agent_tools/gateway.py` | 137 | 只读工具网关：`ALLOWLIST = (rag_search, fetch_webpage, kg_snapshot)`；`resolve_user()` 惰性导入 `core.auth`（零配置本地模式不付 SECRET_KEY 导入期校验的代价） |
| `backend/app/mcp_servers/kg_tools.py` | 190（改） | 加 `--local-user`（默认读 `AI_TUTOR_LOCAL_USER`）；日志 `只读网关启动：transport=… auth=… tools=…` |
| `backend/app/mcp_servers/local_http.py` | 209 | stdlib `ThreadingHTTPServer`，127.0.0.1:0，stdout 只打一行 `PORT=<n>`；`/health`、`/graph`；Host 回环校验 + 只反射回环 Origin 的 CORS |
| `backend/app/core/storage.py` | +25 | `_resolve_data_root()`：`AI_TUTOR_DATA_ROOT` > `DATA_ROOT` > `<repo>/data` |
| `backend/tests/test_tool_gateway.py` | 138 | 白名单/越权/未登录/惰性导入 |
| `backend/tests/test_tools_kg_snapshot.py` | 177 | 空库友好、分页、掌握度口径、注册表一致性 |
| `backend/tests/test_local_http.py` | 233 | PORT 握手、/health、/graph、Host/CORS、EOF 退出 |

另改动：`backend/app/core/agent_tools/tools/__init__.py`（+`kg_snapshot` 注册）、`backend/tests/test_storage_paths.py`（+62 行数据根优先级）、`.gitignore`（↓）。

### 1.2 插件包 `plugins/dsh-ai-tutor/`（+ `scripts/build-plugin.mjs`）

| 文件 | 行数 | 职责 |
|---|---|---|
| `index.js` | 217 | 只做编排：探测 engine/python → 本地模式 `--local-user` 免 token → `mcpClient.apply`；`webServer.register('/ai-tutor/graph')` 转发本地 HTTP |
| `lib/paths.js` | 386 | engine/python/dataRoot 四级解析；`<dataRoot>/secret_key` 生成与复用（0600）；密钥环境清洗 |
| `lib/local-http.js` | 333 | 懒启动 `python -m app.mcp_servers.local_http --local-user N`，读 `PORT=`，20s 超时，dispose 先 kill 再关 stdin |
| `lib/client.js` | 646 | 手写 `window.__ModuleLoader__.load` + React.createElement + ErrorBoundary；`sidebar.panellist` 图标 + `main` 面板双 slot；15s 轮询 |
| `bin/doctor.mjs` | 674 | 10 项环境自检，`--fix` 只建数据根，有 ❌ 退出码 1 |
| `package.json` / `cordis.patch.yml` / `README.md` | — | 打包清单、bundle 层（不含绝对路径）、安装/隐私/doctor/卸载/排错 |
| `scripts/build-plugin.mjs` | 994 | 一条命令构建 engine+runtime：复制 backend/app → 便携 CPython 3.13 + 13 个锁定依赖 → 白名单裁剪 → import/tools 校验 → MANIFEST |

## 2. 端到端链路（真机实测跑通的那条）

```
DSH 宿主 (web profile)
 ├─ bundle 层：dsh-ai-tutor/cordis.patch.yml → insert { id: ai-tutor, name: dsh-ai-tutor }
 ├─ index.js apply(ctx)
 │   ├─ mcpClient.apply(ctx, { transport:'stdio', command:<包内 runtime python>,
 │   │        args:['-m','app.mcp_servers.kg_tools','--local-user','1'], cwd:<engine> })
 │   │        → initialize ai-tutor-kg 0.1.0 → tools/list: rag_search, fetch_webpage, kg_snapshot
 │   └─ webServer.register({ path:'/ai-tutor/graph', kind:'exact' })
 │            → 懒启动 python -m app.mcp_servers.local_http → 读 PORT= → 转发 GET /graph
 └─ client.js apply(ctx.slots)
     ├─ sidebar.panellist 注册「知识图谱」图标
     └─ main 面板 → fetch('/ai-tutor/graph') → 学科/节点/掌握度
```

## 3. 验收证据（可复跑）

运行环境：Windows 10，DSH **0.1.5-rc.2**（本机 `~/.dsh/profiles/node_modules`），隔离 `DSH_HOME=<repo>/.dsh_local/dshhome`（**不动 live profile**），数据根用 `AI_TUTOR_DATA_ROOT=<repo>/data`（仓库内真实图谱 22 节点）。

| # | 计划验收项 | 结果 | 证据 |
|---|---|---|---|
| 1 | 一条命令装 | ✅ | `dsh plugin --profile web3 add <tgz>` → pnpm `+117 packages` / `+ dsh-ai-tutor 0.1.0` / 29.3s；**bundles 自动追加** `dsh-ai-tutor`（profile package.json 实测） |
| 2 | 重启后 3 个工具出现 | ✅ | boot 日志：`只读网关启动：transport=stdio auth=local-user tools=rag_search,fetch_webpage,kg_snapshot` + `tools/list -> 3 个工具` |
| 3 | `kg_snapshot` 返回非空摘要 | ✅ | 打包 runtime 起 MCP 后 `tools/call kg_snapshot` → `isError=false`，中文快照含 13 学科/22 节点；空库分支有单测 |
| 4 | `rag_search` 空库友好 | ✅ | p1 单测 + 工具 handler 契约（永不抛，失败回中文） |
| 5 | 卸载零残留 | ✅ | `dsh plugin remove dsh-ai-tutor` → `-117 packages` / 819ms；deps 键删除、bundles 撤回、node_modules 无包目录 |
| 6 | 数据不丢 | ✅ | 数据根在 `<dataRoot>`（卸载只动 node_modules/package.json；`%LOCALAPPDATA%\\dsh-ai-tutor\\data` 由 doctor --fix 建好） |
| 7 | 主仓回归 | ✅ | `backend/venv/Scripts/python.exe -m pytest backend/tests -q -m "not llm_api"` → **1808 passed, 7 deselected, 602 warnings in 171.04s**（计划写 1784，仓库随后净增，见 §5） |
| 8 | 体积 | ✅ | tgz **48,597,907 B = 46.3 MiB**（npm 上限 100 MB）；engine 1.6 MB / runtime 132.9 MB / 合计 134.5 MB ≤ 180 MB |
| 9 | 面板显示学科/节点/掌握度 | ✅ | **真机 headless Chrome(CDP) 渲染**：右栏出现「知识图谱」按钮，点开主区标题 `知识图谱`、副标题 `22 个知识点 · 每 15 秒自动刷新 · 更新于 01:03:37`，13 个学科分组（AI/what/外语/对比分析/操作系统/核心概念/测试/测试标签/生活技能/算法思想/语言学习/进阶概念/鸿蒙）+ 每节点掌握度条；**0 个 React 异常、0 条 console error/warn**。截图：`.dsh_local/cdp-graph-panel.png`（本机工件，未入库） |
| 10 | `doctor` 30 秒内定位问题 | ✅ | 对运行中的宿主 `node bin/doctor.mjs --host http://127.0.0.1:3080` → **10 通过 / 0 失败 / 0 跳过**，第 10 项 `GET /ai-tutor/graph?limit=1 → 200（nodeCount=22）`；模拟缺 runtime/engine 时明确 ❌ 且退出码 1 |

补充证据：
- `/ai-tutor/graph` 直连：HTTP 200 / 3004 B / `nodeCount=22` / `subjects=13`（web2 手装与 web3 `dsh plugin add` 两条安装路径都验过）。
- 客户端半确实被宿主加载：索引 HTML 的 `/plugins/??…,dsh-ai-tutor/client.js,…` 清单里出现本插件，且合并 bundle HTTP 200 / 11.1 MB 内含 `window.__ModuleLoader__.load({ id: "dsh-ai-tutor" …`。
- MCP 握手（打包 runtime，`SECRET_KEY` 由宿主注入）：`initialize → ai-tutor-kg 0.1.0`、`tools/list 3`、`tools/call kg_snapshot` 返回真实 22 节点正文。

## 4. 与计划/预期的偏差（以代码与实测为准）

1. **`/graph` 的 `mastery` 是 0~100 整数**，不是 0~1 小数 → 面板按百分比直接显示（仅当 `0<v<1` 才 ×100 兼容旧数据）。
2. **计划里的 `tests/test_gateway_allowlist.py` 不存在**，实际回归文件是 `backend/tests/test_tool_gateway.py`。
3. **计划 §6-5 的 `node scripts/typecheck.mjs` 在仓库里不存在**（`scripts/` 原本只有新增的 `build-plugin.mjs`）→ 用 `node --check` + `doctor` 第 9 项（new Function 语法检查）替代。
4. **SECRET_KEY**：`core/kg/schema.py` 建表会导入 `core.auth`，而 `auth` 导入期强制 `require_secret_key()` → 零配置下 `KnowledgeGraph` 根本建不起来。处理=双保险：主仓 gateway 惰性导入（已改）+ 宿主自动生成 `<dataRoot>/secret_key`（32 字节 hex，0600，显式 env 优先，且不上传给子进程白名单之外）。
5. **pytest 数**：计划写 1784，本次实测 **1808 collected / 7 deselected**（仓库在此后净增测试；全绿无 failed）。
6. **安装依赖 pnpm + 网络**：`dsh plugin add` 用 pnpm 解析插件的 peer 依赖（本次 `+117 packages`，其中 37 个从 registry 下载）。tgz 本身自包含 Python 与 engine，但**首次 add 需联网**；离线安装可退回手动解包（本次也验证：`tar -xzf → node_modules/dsh-ai-tutor` 后 boot 正常）。
7. **runtime `__pycache__`**：曾经 42 个目录（~1 MB）会进包；已在构建白名单裁剪阶段清掉（最新 tgz 46.3 MiB < 早先 49.0 MB）。
8. **面板路由必须随插件卸载注销**（真机缺陷，已修）：见 §9。

## 5. 未验证 / 已知限制（交付时说明，别当成"已做"）

- **断网**：`fetch_webpage` 必然要网；`rag_search` 用本地 whoosh 索引可离线，但若用户配了云端 embedding，索引阶段仍要网。本轮未做真实断网演练（无干净无网环境）。
- **「故意写坏 client.js 只坏面板」**：有 ErrorBoundary + `apply()` try/catch + doctor 语法检查，但本轮没有做"故意破坏后看其余界面"的真机演练（面板本身已真机渲染成功，且 0 React 异常）。
- **干净机器无 Python**：插件走包内便携 runtime（doctor 第 3 项、MCP spawn 均用 `plugins/dsh-ai-tutor/runtime/python.exe`），但本机系统 Python 仍在，未在真·无 Python 机器上复验。
- **后端套件**：只跑了 `backend/tests`；`backend-admin/tests` 未跑（本次未触碰该域，且两套件必须分开跑）。
- **live profile 已装但待重启复验**：真身是 `~/.dsh/profiles/desktop`（Electron 桌面版独占管理，CLI `--dump-config` 会拒绝：`profile "desktop" is managed exclusively by the Electron application`）；文件已装好，且 MCP 3 工具在 live 已真实可用，但面板 `/ai-tutor/graph` 因 §9 的路由残留仍 502，**需重启宿主**才恢复。
- **live 端到端面板渲染**：隔离宿主（web2/web3）已用 CDP 真机渲染过（§3 第 9 项）；live 55448 上的面板渲染要等重启后再截一次。

## 6. 复现步骤

```bash
# 1) 构建（首次 ~10 分钟，需 uv；之后改 backend 用 --skip-runtime 秒级）
node scripts/build-plugin.mjs                 # engine + runtime + 校验 + MANIFEST
node scripts/build-plugin.mjs --skip-runtime  # 只重建 engine

# 2) 打包与自检
cd plugins/dsh-ai-tutor && npm pack          # dsh-ai-tutor-0.1.0.tgz（46.3 MiB）
node bin/doctor.mjs                          # 10 项；--fix 只建数据根；--host <url> 验路由

# 3) 装进某个 profile（一条命令）
dsh plugin --profile <name> add ./dsh-ai-tutor-0.1.0.tgz
# 重启宿主 → 新会话应看到 3 个只读工具；右栏「知识图谱」图标点开即面板

# 4) 卸载
dsh plugin --profile <name> remove dsh-ai-tutor

# 5) 主仓回归
backend/venv/Scripts/python.exe -m pytest backend/tests -q -m "not llm_api"
```

## 7. 用户拍板记录（2026-10-27）

1. **装进 live profile**：同意（"可以直接装到我这个 dsh 里边，但注意要用最原生的能力，不要带上对其他插件里边"）。
2. **提交**：同意（"OK没问题这样可以"）。
3. **数据根**：直接复用（"嗯，直接复用就行"）→ live 用 patch config 指向主仓 `<repo>/data`，不再用平台默认根。

## 8. live 安装（原生契约，不走 pnpm、不联网、不动其它插件）

`dsh plugin add` 会 pnpm 解析 peer 依赖（下 117 个包）并改 live `node_modules`，与"最原生、不带上其它插件"的要求不符，故改用 DSH profile 自身的三处契约：

1. **包目录**：`tar -xzf dsh-ai-tutor-0.1.0.tgz -C ~/.dsh/profiles/desktop/node_modules/dsh-ai-tutor --strip-components=1`（4396 个文件 / 144 MB）。插件只依赖宿主原生 `@deepseek-ai/dsh-mcp-client`，无第三方插件依赖（`index.js` 的 import 已核）。
2. **bundle 层**：`~/.dsh/profiles/desktop/package.json` 的 `dsh.profile.bundles` 末尾追加 `dsh-ai-tutor`（21 → 22 条），`dependencies` 不动。改前备份 `package.json.bak-ai-tutor-<ISO 时间戳>`。
3. **配置层**：`~/.dsh/profiles/desktop/cordis.patch.yml` 追加 `- id: ai-tutor` / `  name: dsh-ai-tutor` / `  config:` / `    dataRoot: C:/Users/34239/Documents/GitHub/AI-tutor/data`（patch 层语义 = 对所有 bundle 层之后生效的 id 定向覆盖）。

回滚：还原 `package.json.bak-ai-tutor-*` → 删 patch 里的 ai-tutor 块 → `rm -rf ~/.dsh/profiles/desktop/node_modules/dsh-ai-tutor`。

live 首验（未重启即生效，说明宿主对 bundle 变更会 live 吸收）：
- MCP 侧 ✅ 本会话工具清单出现 `mcp__ai-tutor__kg_snapshot` / `rag_search` / `fetch_webpage`，`kg_snapshot({})` 返回「13 个学科 / 22 个知识点，已掌握 0 个」+ 各学科分节（`intro_prompt_eng`、`new_001`、`german_learning_path`、`test_node_ai`、`cooking_basics`、`divide_and_conquer`、`german_du_sie` 掌握度 25 等）。
- 面板侧 ❌ `GET /ai-tutor/graph?limit=1` → 502，detail = `ai-tutor: 插件已卸载，本地面板后端不再启动` → 定位为 §9 的缺陷。

## 9. 线上缺陷：面板路由未注销（已修）

**现象**：live 宿主里 `/ai-tutor/graph` 恒 502（`detail=ai-tutor: 插件已卸载，本地面板后端不再启动`），`doctor --host http://127.0.0.1:55448` 9 通过 / 1 失败（第 10 项），而 MCP 3 工具正常 → 只有面板通路坏。

**根因**（定位到行）：
- `plugins/dsh-ai-tutor/index.js` 的 `registerGraphRoute()` 起初只对代理挂了 `scope.effect(() => () => proxy.stop())`，**没有注销路由**。
- 宿主 `@deepseek-ai/dsh-host-webserver/lib/index.js:176 register(route)` 的契约是：重复 `(kind, path)` 抛错，且**返回值就是注销函数**。
- 宿主热重载本插件时：旧实例 dispose → 代理 stop（`lib/local-http.js:122` 那句 "插件已卸载" 就是由此而来）+ **旧路由残留** → 新实例 register 撞 duplicate 抛错、被 try/catch 吞成 `console.error` → 请求继续打到旧 handler → 永久 502，只有重启宿主能恢复。

**修复**：保留停代理的 effect，再加一个 effect 注册路由并把 `register()` 的返回值作为注销函数返回（内部 try/catch + 中文注释记录该现象）；测试侧把 `.dsh_local/smoke-plugin-shell.mjs` 的假 webServer 换成**状态化**替身（重复路径抛错、disposer 摘路由、`effect` 工厂立即执行），断言 `effects.length === 2`、卸载后 `routes.size === 0`、**卸载后重新 apply 能成功挂上唯一一条路由**。

**证据**：
- `node .dsh_local/smoke-plugin-shell.mjs` → **36 通过 / 0 失败**（含上述重载回归）。
- 修复版 tgz `48,597,811 B` / 4396 files，包内 `index.js` 含注销逻辑（`tar -xOzf … package/index.js | grep 注销` 命中）。
- 隔离宿主（web2，全新解包修复版）boot → `GET http://127.0.0.1:3080/ai-tutor/graph?limit=1` → **HTTP 200 / 2236 B**，真实 13 学科数据。
- **live 配置形态已在隔离宿主复刻验证**：新建隔离 profile `liveclone`（拷 web2 的 node_modules，bundles = base + web-app + dsh-ai-tutor，`cordis.patch.yml` 与 live 完全同形：`- id: ai-tutor` / `name: dsh-ai-tutor` / `config:` / `dataRoot: <repo>/data`）→ CLI boot 后 `doctor --host http://127.0.0.1:3080` **10 通过 / 0 失败**，`/ai-tutor/graph` **200**、`nodeCount 22`，且响应里 `dataRoot` 正确回显 `C:\Users\34239\Documents\GitHub\AI-tutor\data` → **patch 配置写法本身没问题，不会造成重复注册**；live 的 502 是"改配置触发插件重载 + 旧路由未注销"。
- live 55448 上仍是 502（旧进程里的泄漏路由无法从外部摘除）→ **重启宿主后复验**。

**顺带记一条坑**：在 **CLI 宿主**里改 profile 的 `cordis.patch.yml` 会按 `patchReload: live` 重载**整棵插件树**，webserver 二次 listen 撞端口 → `failed to apply loader entry webserver: listen EADDRINUSE 127.0.0.1:3080`（进程退出码 1）。即：CLI profile 里不要用"改 patch 文件"当热重载手段；桌面宿主对同样变更的处理不同（不崩，但会走重载并留下泄漏路由 —— 本次 502 就是这样产生的）。
