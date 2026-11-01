# DSH 插件化改造计划（单包单 worker · 契约兼容）

> 建立：2026-10-26 ｜ 性质：**改造计划 + 取舍记录**（本文不描述代码现状，现状以代码 docstring 与 `AGENTS.md` 为准）
> 目标宿主：**DeepSeek Harness (DSH) 优先**；MCP 作为通用退路。
> 口径：本文所有数字为 **2026-10-26 本机实测**（命令见 §9），非估算。
> 关联：`docs/架构/整体架构_问题清单与重构方案.md`（P0-2/P0-3 与本计划无依赖）、`AGENTS.md §1/§2`（硬约束）。

---

## 0. 一句话

**一个 npm 包、一个 Python worker、六份能力声明** —— 对外是「DSH 插件」，对内不重写任何领域逻辑；
契约（manifest）是唯一共享产物，**不克隆 Cordis 运行时**。

**非目标（明确不做）**：把 `agent/loop.py` 搬进 Cordis、TS 重写领域层、发布 UI 面板插件、多插件拆分、Python 版 Context/DI 容器。

---

## 1. 现状基线（实测，作为成本与验收的锚点）

| 项 | 数值 | 用途 |
|---|---|---|
| `backend/app` | 174 个 .py / 28,640 行；96 条路由 | 重写成本对照（本计划 **0 行重写**） |
| 测试 | 152 文件 / 21,947 行 / **1654 例**（2026-10-26 复测：非 `llm_api` **1784 例**） | 改造期间必须保持全绿，只增不改 |
| 工具 | 16 原生（+1 MCP `web_search`） | 对外暴露面 |
| 工具文本 | description 合计 1,930 字；guidance 6,230 字 | 外部宿主只吃 description+schema（≈2k tokens） |
| `agent/loop.py` | 647 行，外部依赖 12 个入口 | 「到 loop 的中间件」真实范围 |
| **冷启动** | `agent_tools` 累计 **5.36s**（`core.kb` 2.25s + `mcp` SDK 0.67s + 其余）→ P2-4 后 **1.21s**（见 §11） | P2 优化目标 ≤2s **已达成** |
| 身份 | JWT，`access_token_expire_minutes=1440`（24h） | 外部宿主不可用 → 需 PAT |
| 只读工具 | **3 个**：`rag_search` / `search_resources` / `fetch_webpage` | P1 白名单（不是 6 个） |

### 1.1 P0 实测补充（2026-10-26，详见 `DSH验证记录_2026-10-26.md`）

| 项 | 实测 | 影响 |
|---|---|---|
| DSH 版本 | `0.2.1-rc.2`（无稳定版） | ~~`dsh.supported`~~ **该字段不存在**（见 §11.3）：bundle 只声明 `dsh.bundle.patch`，兼容区间落 `engines.dsh` 且**不被强制**；rc 版本无法用 semver 可靠表达 → 本包只声明 `engines.node` |
| 首次 npx 安装 | 200.9 s / 969 MB | 别在自动化里裸调 `npx` |
| 宿主命令面 | 无 `mcp` / `doctor` / `console` | 诊断 = `--dump-config` + stderr |
| MCP 协议 | `2025-11-25`，工具名 `mcp__<serverName>__<rawName>` | serverName 是**兼容性契约**，改名即破坏历史 |
| MCP server 启动 | SDK import 1.30 s + 我们的 `agent_tools` 5.36 s | 我们的 server 首次 `tools/list` ≈**6.5 s** → P2-4 必做 |
| 卸载/dispose | 子进程残留 **0**，移除条目后 0 spawn | **G0 通过** |
| 桥 spawn | `python -u -m dsh_bridge.runtime <module>`，`cwd` 默认是宿主目录 | 必须显式传 `cwd` 或 `pythonPath` |
| 桥的环境清洗 | 含 `KEY/PASSWORD/SECRET/TOKEN` 与 `DSH_*` 的变量被剔除 | **PAT 必须显式 `env` 或 `--init-args`** |
| 桥的 schema | 模型可见 schema 由 **codegen** 提供，bridge 只给 manifest | P3 增加 codegen 步骤（+1–2 天） |

---

## 2. 目标架构

```
dsh-ai-tutor                      ① 发布单元：1 个 npm bundle（package.json → dsh.bundle → cordis.patch.yml）
└── ai-tutor                      ② 框架单元：1 个 Cordis 插件（ctx.tool / ctx.service / dispose）
    └── python -m ai_tutor_dsh    ③ 进程单元：1 个 worker（JSON-RPC over stdio）
        ├── capability kg         ④ 能力单元：6 份 manifest 条目（provides / requires / dispose）
        ├── capability kb
        ├── capability rag
        ├── capability quiz
        ├── capability llm
        └── tools × N             ⑤ 白名单 + tier 过滤（mode 决定 N）
```

**为什么四层都要**：①决定用户怎么装，②决定 DSH 怎么卸载，③决定内存与冷启动，④决定"哪一块坏了"。
把它们压成一层（一个大插件类）会同时失去卸载粒度、依赖声明与故障定位。

### 数据流（一次工具调用）

```
DSH 模型 tool_call
  → Cordis ctx.tool 分发（审批策略按 tier）
  → bridge（TS）JSON-RPC 写一行
  → worker 收到：校验 PAT → 取 user_id → KnowledgeGraph(user_id) → dispatch
  → 返回文本（已经是"给模型看的话"，无需再包装）
  → finally: kg.close()（连接归零，可断言）
```

### 与本项目现有对话流的关系

**旁路，不是替换。** 主系统 `loop.py` 仍直接调 `dispatch`；网关/worker 只是同一批 spec 的第二个投影。
约束：领域代码永不 import `dsh_bridge`；桥只 import `gateway`。删掉网关 → 主系统零影响。

---

## 3. 关键设计决策与取舍

| # | 决策 | 取舍（得到 / 放弃） |
|---|---|---|
| D1 | **契约兼容**（manifest），不做运行时兼容 | 得到：5–7 天、可回滚、不动领域代码；放弃：HMR / fork / isolate / 多 profile 语义 |
| D2 | **单包单 worker** | 得到：一次安装、一份兼容声明、共享 OCR/SQLite/索引句柄；放弃：局部卸载、局部升级；代价：一个 CPU-bound 调用会拖慢全部 → 必须线程池 |
| D3 | **默认 `mode=readonly`**（3 工具） | 得到：安全默认值、审批面小；放弃：装完即全能的演示效果 → 演示时显式切 `full` |
| D4 | **`tools/list` 用构建期导出的 `tools.json`** | 得到：冷启动 <1s（不必 import 全部工具）；放弃：运行时自省；代价：忘跑生成脚本会漂移 → **用测试锁死** |
| D5 | **身份用 PAT**（新增 `api_tokens` 表），不复用 24h JWT | 得到：外部宿主可长期驻留；放弃：多一张表 + 签发/吊销端点（+2 天） |
| D6 | `quiz_generate` 不进 manifest（首版） | 得到：规避 consent 无会话即放行、`broadcast` 进程内两个坑；放弃：外部 agent 不能出题 → P4 再开 |
| D7 | 中间件放 **gateway 层**，不改 `loop.py` | 得到：0 处领域改动、1654 测试不动；放弃：loop 内部可换实现（当前无需求） |
| D8 | **不新增审计表**，只写结构化日志 | 得到：不破坏 `agent_runs` 唯一写入方规则；放弃：外部调用没有 `agent_runs` 级回放 |
| D9 | **B 路必须跑 codegen**（`@peroxider/dsh-python-bridge-codegen`），MCP 路不需要 | 得到：模型可见的 TS schema；放弃：一条纯 Python 的路（引入 DSH peer deps，+1–2 天） |
| D10 | **身份显式下发**（`env` / `--init-args`），不依赖继承父环境 | 得到：符合宿主的清洗规则；放弃：靠 `.env` 自动透传的便利 |

---

## 4. 工作分解（WBS）

### P0 · 可行性验证（1 人日）——**先做，结果决定后面是否投入**

| 编号 | 动作 | 落点 | 验收 |
|---|---|---|---|
| P0-1 | 装 DSH，建 `trial` profile | 本机（`npx @deepseek-ai/dsh`） | `dsh plugin --profile list` 正常 |
| P0-2 | 20 行 echo MCP server（stdio），挂进 profile | 临时目录，**不进仓库** | 模型能调通 echo |
| P0-3 | 同一 echo 用 `dsh-python-bridge` 装饰器再挂一次 | 临时目录 | DSH 中出现**原生** tool；卸载后子进程消失 |
| P0-4 | 记录版本矩阵与坑 | `docs/架构/DSH验证记录_2026-10-26.md` | 一页：dsh / bridge / node 版本、dispose 行为、冷启动 |

**门 G0**：P0-2 与 P0-3 至少一条稳定；**若 P0-3 的 dispose 不能杀干净子进程 → 放弃 B，只走 A（MCP）**。

### P1 · 只读可用（4–5 人日）

| 编号 | 动作 | 落点 | 验收 |
|---|---|---|---|
| P1-1 | 网关：白名单 / PAT→user_id / `KnowledgeGraph` 生命周期 / 错误分级复用 `dispatch._tool_error` | `backend/app/core/agent_tools/gateway.py`（新，~150 行） | 白名单外拒绝；跨用户不可见；异常不裸抛 |
| P1-2 | MCP server：`tools/list` + `tools/call`，stdio 与 streamable-http 双传输 | `backend/app/mcp_servers/kg_tools.py`（新，~120 行，照 `web_search.py`） | 两种传输各跑通 1 次 |
| P1-3 | 白名单 = `rag_search` / `search_resources` / `fetch_webpage` | 同上 | 三个工具各成功 1 次 |
| P1-4 ✅ | 测试 | `backend/tests/test_tool_gateway.py`（9 例，`KnowledgeGraph` 换注入 `tmp_path` 的工厂） | 白名单/schema 同源/失败文案可操作/`close()` 归零 四类断言各锁一条 |

**门 G1**：DSH 里「检索到我自己的图谱节点 + 已索引资料」，且无效 token 返回可操作提示（不是空结果）。

### P2 · 可对外（4 人日）

| 编号 | 动作 | 落点 | 验收 |
|---|---|---|---|
| P2-1 | manifest：6 组件 `provides/requires/dispose` + tools 从 `registry.all_specs()` 派生 | `backend/app/core/capabilities/manifest.py`（新，~200 行） | 新增工具未进 manifest → 测试红 |
| P2-2 | `tools.json` 构建期导出 + 一致性测试 | `backend/scripts/export_tool_specs.py` + 测试 | 导出与 registry 逐字段一致 |
| P2-3 | PAT：`api_tokens(user_id, token_hash, name, expires_at, revoked_at)` + 签发/吊销端点 + 网关校验 | `core/auth.py` + `api/v1/auth.py` | 过期/吊销即 401；明文不落盘 |
| P2-4 ✅ | 冷启动优化：`core/kb/parsers` 惰性注册 + MCP 工具注册推迟 + LLM 客户端/`openai` 惰性导入 | `core/kb/parsers/__init__.py`、`core/agent_tools/__init__.py`、`core/llm/{clients,__init__,fallback,embed,retry,stream}.py` | **4.66s → 1.21s**（§11.1） |

**门 G2**：新增一条 `mode=readonly` 的端到端测试；冷启动达标；1654 例仍全绿。

### P3 · DSH 原生插件（6–7 人日）

| 编号 | 动作 | 落点 | 验收 |
|---|---|---|---|
| P3-0 | **codegen**：从 Python 装饰器生成 TS Service/Tool（`@peroxider/dsh-python-bridge-codegen`），补 peer deps | `plugins/dsh-ai-tutor/codegen/` | 生成的 TS 能编译，模型侧能看到工具 |
| P3-1 | worker：读 manifest → 暴露 tools；`mode`/`dataRoot`/`token` 来自 initArgs | `backend/app/dsh_worker/__main__.py`（新） | 本地 `python -m dsh_worker` 可自测 |
| P3-2 | 适配层：`dsh_bridge` 装饰器只出现在此目录 | `backend/app/dsh_worker/adapter.py` | `grep -r dsh_bridge app/core` = 0 |
| P3-3 🟡 | npm 包：`package.json`（**`dsh.bundle.patch`**，无 `dsh.supported`）、`cordis.patch.yml`、`index.js`（组合插件：探测路径 + 显式下发 token）、README | `plugins/dsh-ai-tutor/` | 文件已就绪、`index.js` 语法与模块解析已验；**`dsh plugin add` 一条命令装好 待真机验证** |
| P3-4 | 硬化：dispose 归零、重装、卸载残留、版本不匹配诊断 | 同上 | 卸载后无孤儿进程 / 无残留 SQLite 句柄 |

**门 G3**：`mode=readonly` 全链路 ≤3s 首调用；卸载/重装 3 次无残留；DSH 升级一个 rc 版本仍可用（或明确拒绝并给出诊断）。

### P4 · 写工具开放（2 人日，可选）

| 编号 | 动作 | 验收 |
|---|---|---|
| P4-1 | `quiz_generate` 的 consent **显式化**（入参或宿主审批），不再依赖 `learning_sessions` 的"无会话即放行" | 外部调用未同意 → 返回"先问一句"而非出题 |
| P4-2 | 事件跨进程：`quiz_ready` 改走 `pending_notices` 拉取，或由插件 listener 转发 | 外部出题后前端能收到（或明确降级为拉取） |

---

## 5. 排期与成本

| 阶段 | 人日 | 累计 | 可交付 |
|---|---|---|---|
| P0 | 1 | 1 | 可行性结论（go / no-go） |
| P1 | 4–5 | 5–6 | DSH 里可检索（演示级） |
| P2 | 4 | 9–10 | 可对外（PAT + manifest + 冷启动） |
| P3 | 7–9 | **16–19** | 真正可发布的原生插件（含 codegen） |
| P4 | 2 | 18–21 | 写能力开放 |

单人约 **3.5–4 周**；P2 之前 A/B 不分叉，无法并行。仅做 A（MCP，不出插件）= **10 人日**。

---

## 6. 风险登记

| 风险 | 概率 | 影响 | 缓解 | 回滚 |
|---|---|---|---|---|
| `dsh-python-bridge` 0.0.3 破坏性变更 | 中高 | 插件不可用 | 只允许 `dsh_worker/adapter.py` 触碰；pin 版本 | 1 天切 MCP（同一 gateway） |
| DSH 0.2rc API 变动 | 高 | 需跟随适配 | `dsh.supported` 窄区间；领域零 DSH 概念 | 同上 |
| 冷启动 5.36s 拖慢宿主 | 确定 | 体验差 | P2-4 惰性导入 + `tools.json` | — |
| 外部调用绕过教学约束 | 高 | 教学数据被污染 | 默认 readonly；tier→审批；写工具走 P4 | 白名单回滚 |
| 单 worker 被 CPU-bound 卡住 | 中 | 全部工具超时 | PDF/OCR/yt-dlp 走线程池或子进程 | 降级为只读 |
| 长任务在升级时中断 | 中 | 出题/导入丢失 | 已有 `pending_notices` + `quiz.db` 可恢复；文档写明 | — |
| 安全（远程可写图谱） | 中 | 数据损坏 | PAT + tier + 速率限制（P2 起） | 关端口 |

---

## 7. 测试与验证口径

- 新增用例集中在 `backend/tests/test_tool_gateway.py`、`test_manifest.py`、`test_dsh_worker.py`；
- 全部用临时 `DATA_ROOT`（`monkeypatch`），**禁止**读写真实 `data/`；RAG 需 `conftest._isolate_rag_index`；
- 跑法沿用 `AGENTS.md §0`：`pytest backend/tests -q -m "not llm_api"`，后台套件单独一次调用；
- 不新增 `llm_api` 用例；
- 三条可测指标（写进 CI 或 P2 验收）：**冷启动 ≤2s / 首工具调用 ≤3s / dispose 后子进程与句柄归零**。

---

## 8. 待拍板

1. 发布形态：**本地能力包**（插件 spawn 本机 Python）还是**远程服务包**（薄壳调托管 API）？后者要 +PAT/配额/限速。
2. PAT 是否随首版发布？（否则 P2 提前到 1 天，仅复用 24h JWT）
3. npm 发布到公共 registry 还是私有？（公共需 README/许可证/版本策略）

---

## 9. 实测命令（复核本文件数字用）

```powershell
# 代码量
Get-ChildItem backend/app -Recurse -Filter *.py | Get-Content | Measure-Object -Line
# 冷启动
cd backend; venv/Scripts/python.exe -c "import time;t=time.perf_counter();import app.core.agent_tools;print(time.perf_counter()-t)"
# 工具文本量（description / guidance）
#   见 §1 表；脚本按 AST 读 tools/*.py 的 DESCRIPTION / GUIDANCE 常量求和
```

---

## 10. 落地方式：**主仓内目录边界，不新开仓库**

### 结论

- **P0–P2 全部落在本仓库**（`backend/app/...` + `backend/tests/...`）；
- **P3 的分发层（npm 壳）落在 `plugins/dsh-ai-tutor/`**，与 `frontend/`、`frontend-admin/` 同级，自带 `package.json`；
- **物理拆成独立仓库推迟到 P3 通过之后**——届时只需搬 ~300 行（TS 壳 + YAML + README），成本近乎为零；反过来（先拆、后合并）要付双倍同步成本。

### 为什么不是“另开一个小项目”

| 维度 | 独立小仓 | 主仓内目录（选定） |
|---|---|---|
| 复用 `registry` / `dispatch` / `KnowledgeGraph` | 需打包成库或用 `PYTHONPATH` 硬指，路径一漂移就断 | 直接 import，一条命令可跑 |
| 测试 | 两套夹具，`DATA_ROOT` 隔离要重写一遍 | 复用 `tests/conftest.py`（含 `_isolate_rag_index`） |
| DSH 0.2rc 期间 API 反复 | 每改一次要两边同步 | 一处改完 |
| pytest 全绿口径 | 需要额外保证“主仓不受污染” | 天然成立（`plugins/` 不在 `backend/tests`） |
| 独立发版 | 天然 | 也成立（`plugins/dsh-ai-tutor/package.json` 自带 version） |
| 公开 npm 发布的“门面” | 更好 | 后期拆出即可 |

判定：**在 API 未冻结阶段，同步成本 > 隔离收益**。真正需要独立仓的只有“对外公开发布 + 独立维护者”这两个条件，P3 之前都不成立。

### 目录与依赖方向（硬约束）

```
AI-tutor/                     ← 主仓（Python，1654 测试，零 DSH 依赖）
├── backend/app/core/agent_tools/gateway.py      ← 能力层（唯一新增领域侧文件）
├── backend/app/core/capabilities/manifest.py    ← 契约（唯一共享产物）
├── backend/app/mcp_servers/kg_tools.py          ← A 路（MCP）
├── backend/tests/test_tool_gateway.py ...
└── plugins/dsh-ai-tutor/                        ← 分发层（TS/npm + worker 壳）
    ├── package.json  (dsh.bundle / dsh.supported)
    ├── cordis.patch.yml
    ├── worker/       (只有它允许 import dsh_bridge)
    └── README.md
```

四条不许破的规则：

1. `app/core/**` 与 `app/api/**` **永不 import** `dsh_bridge` / cordis / 任何 npm 物；
2. `plugins/**` 可以 import `app.*`（单向依赖）；
3. **主仓 `requirements.txt` 不新增 Python 依赖**（`dsh_bridge` 只出现在 `plugins/worker/requirements.txt`，或单独 venv）；
4. **pytest 不依赖 Node**：未装 pnpm/npx 时 `backend/tests` 仍须全绿（用 `skipif` 隔离任何可选集成测试）。

### 什么时候改判

出现以下任一条件 → 立刻把 `plugins/dsh-ai-tutor/` 拆成独立仓库：

- 要在公共 npm registry 发布并对外维护版本；
- 出现第二个维护者/第二个宿主适配包（如 `dsh-ai-tutor-cursor`）；
- `plugins/` 开始需要自己的 CI 与发布流水线。

拆分动作：目录整体迁出 → 主仓只在 README 与 `AGENTS.md` 留一行指针 → 不需要动任何 Python 代码。

---

## 11. 落地记录（2026-10-26 晚，P1-4 / P2-4 / P3-3）

### 11.1 冷启动：三项实测（同一台机器，改前 → 改后）

| 指标 | 改前 | 改后 | 怎么测的 |
|---|---|---|---|
| `import app.core.agent_tools` | 4.66s | **1.21s** | `python -c "import time;t=…;import app.core.agent_tools"` |
| MCP server `spawn → initialize` | 7.9–8.2s | **1.99s** | 真起子进程走 JSON-RPC（initialize → `tools/list` → 1 次只读调用） |
| 惰性工具集首次展开 | （原本导入期就付） | +1.19s | 取 `KG_TOOLS` 时连 MCP，17 个工具（16 原生 + `web_search`） |

**代价的转移方向**：这笔钱没有消失，只是从"每个进程启动都付"变成"真要用 LLM / 真要用完整
工具集时才付"。只读网关（`gateway.py`）走 `registry.all_specs()`，**永不**触发 MCP 与 openai。

### 11.2 改在哪（4 处，均为"不改变语义的推迟"）

| 位置 | 改法 | 省 |
|---|---|---|
| `core/llm/clients.py` | 三个 `AsyncOpenAI` 单例改 PEP 562 `__getattr__`（`from …clients import client` 写法不变） | 1.0s（openai 包）+ 2 × 0.7s（构造） |
| `core/llm/{__init__,fallback,retry,stream}.py` | 去掉顶层 `from openai import …`（错误分类才需要）；`LLM_CANDIDATES` 改惰性 `default_candidates()` | 已在上一行内 |
| `core/kb/parsers/__init__.py` | 内置解析器首次调用才注册（`_ensure_builtin`），省掉 rapidocr + onnxruntime | 0.96s |
| `core/agent_tools/__init__.py` | `KG_TOOLS` / `TOOLS_PROMPT` 改 `__getattr__`，MCP 工具注册推迟到首次需要完整工具集 | 1.27s |

**两处必须记住的约束**（改这些文件前先看）：
- `embed.py` 保留模块级 `embed_client = None` 打桩点：`monkeypatch.setattr(embed_mod, "embed_client", fake)` 是 6 个单测的零网络契约；属性**不存在**时 monkeypatch 直接 `AttributeError`，改成"直接读 `clients.embed_client`"会让打桩在记录旧值时**当场构造真客户端**（无 key 环境即炸）。
- 主系统启动时机**没有变**：`agent/loop.py` 顶层取 `KG_TOOLS`、`chat_service.py` 顶层取 `TOOLS_PROMPT`，所以后端启动照样在此刻连 MCP。

### 11.3 npm 包的契约是**从 DSH 源码核实的**，不是猜的

| 原计划写法 | 真实契约（证据） |
|---|---|
| `dsh.supported` | **不存在**。合法字段是 `dsh.bundle.patch`（`packages/util/package-manifest/src/types.ts::DshBundleManifest`）；兼容区间在 `engines.dsh`，且按文档"declarative until a reader enforces it" |
| 用 semver 表达 rc 区间 | 做不到：`0.2.1-rc.3` **不满足** `>=0.1.5-rc.2 <0.3.0`（node-semver 只在同 `major.minor.patch` 元组允许预发布）→ 本包只声明 `engines.node >= 20`，实测矩阵写进 README |
| patch 行直接插 `@deepseek-ai/dsh-mcp-client` | 可行但**不可分发**：`command`/`cwd` 必须是绝对路径。改为一层 40 行组合插件（`plugins/dsh-ai-tutor/index.js`）：探测 backend/venv，`AI_TUTOR_HOME`/`AI_TUTOR_PYTHON`/`AI_TUTOR_TOKEN` 兜底，再 `await mcpClient.apply(ctx, config)` |
| `ctx.plugin(...)` 挂 MCP client | 不用：`@deepseek-ai/dsh-mcp-client` 是**命名导出插件**（`export const name/inject` + `export async function apply`，无 default），直接 await 它的 `apply` 语义最确定；`inject = ['tools']` 我们自己声明 |

### 11.4 回归

`1784 passed / 0 failed / 0 error`（`-m "not llm_api"`，159–331s，JUnit 计数）。新增
`backend/tests/test_tool_gateway.py`（9 例）。⚠️ 本机跑 pytest 必须把 `TEMP`/`TMP` 指到工作区内
（沙箱禁止 Python 写系统 Temp），否则全部用例在 `tmp_path` 夹具上 setup 失败 —— 表现为
"100% 但不打印汇总行、退出码 1"，**极易被误读成全绿**；以 `--junitxml` 的计数为准。

### 11.5 还没做

- `dsh plugin --profile add` 真机装一次（桌面版 rc3 的 CLI 不可直接调，见 README「已知限制」）。
- P2-1 manifest / P2-2 `tools.json` / P2-3 PAT（当前 token 仍复用 24h JWT）。
- P3-0 codegen（B 路）与 P4 写工具开放。
