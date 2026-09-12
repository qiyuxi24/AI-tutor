# 贡献指南（Contributing to TutorAgent）

**简体中文** ｜ [English](CONTRIBUTING.en.md)

感谢你对 TutorAgent 的兴趣 —— 无论你是来报 bug、修文档，还是想加一门学科的知识图谱数据，这份指南的目标只有一个：**让你在 10 分钟内把项目跑起来，并知道改动该怎么提**。

---

## 1. 欢迎 + 能贡献什么

| 贡献类型 | 典型内容 | 需要先开 Issue 吗 |
|---|---|---|
| Bug 报告 | 复现步骤 + 环境 + 期望/实际行为 | 不需要，直接开 Issue |
| 文档修正 | 错别字、失效链接、命令过期、双语不同步 | **不需要**，可直接提 PR |
| 测试 | 补充边界用例、修复不稳定的用例 | 小改动不用；新增测试框架/依赖先讨论 |
| 代码 | 新功能、重构、性能优化、适配新模型 | **需要**：先开 Issue 对齐方案，避免白做 |
| 数据 | 新学科图谱、评测集、术语表 | 需要，先说明数据来源与授权 |
| 生态 | 提示词模板、工具接入、部署脚本 | 需要 |

> 动手前请先搜索**已开启与已关闭**的 Issue，避免重复讨论；想接手某个 Issue 时，先评论一句认领。

---

## 2. 开发环境

### 前置要求

| 事项 | 要求 |
|---|---|
| Python | 3.10+（本项目开发与 CI 使用 3.13） |
| Node.js | 18+（前端 Vite） |
| 数据库 | 无需安装，SQLite 零配置 |

### 最快路径（Windows PowerShell）

```powershell
.\install.ps1                 # 建 venv + 装后端依赖 + 装前端 node_modules
copy .env.example .env        # 根 .env 是唯一真值文件
notepad .env                  # 填 LLM_API_KEY / DASHSCOPE_API_KEY / SECRET_KEY
.\start.ps1                   # 启动前后端 → http://localhost:5173
```

### 手动方式

```bash
# 后端（cwd = backend）
cd backend
python -m venv venv
venv\Scripts\activate          # Linux/macOS: source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# 前端（另开终端，cwd = frontend）
cd frontend
npm install
npm run dev
```

### 本项目的 4 条硬约束（踩了就报错）

1. **必须用 `backend/venv/Scripts/python.exe`**：系统 Python（3.11）装的是过旧版 FastAPI / Starlette，会直接导入失败。
2. **`uvicorn` 必须 `--workers 1`**：事件总线（EventBus）的用户队列与进程内定时 GC 依赖单进程；多 worker 会各持一份队列，事件丢失。
3. **配置只有一份真值：根目录 `.env`**（由 `backend/app/core/config.py` 读取）。不要在 `backend/` 下另建 `.env`，也不要提交 `.env`。
4. **模型与嵌入是两套独立配置**：对话模型（`LLM_API_KEY` + `LLM_BASE_URL` + `MODEL_NAME`）与嵌入（`DASHSCOPE_API_KEY`，固定阿里 text-embedding-v4）互不影响，改一个不要动另一个。

---

## 3. 跑测试（提交前的唯一硬门槛）

零网络单元 / 集成测试（mock 掉 LLM 与嵌入的网络调用，不需要任何 API key）：

```bash
cd backend
venv\Scripts\python.exe -m pytest tests -q -m "not llm_api"
```

**期望输出**：

```
517 passed, 7 deselected
```

- `7 deselected` = 标记为 `llm_api` 的真实 API 用例（`tests/test_agent_loop_real_api.py`）：需要真实 key + `pytest-asyncio`，本地无 key 时不要强行运行；CI 用同一条命令排除它们。
- 提交 PR 时**请贴出这条命令的输出**，它同时也是 CI（`.github/workflows/backend-tests.yml`）的验收口径。
- 改动检索相关逻辑时，另跑离线评测（`mock` 模式无需 key）：

```bash
cd backend
venv\Scripts\python.exe scripts/eval_rag.py --embed mock
```

---

## 4. 编码规范与硬约束

总原则：**跟随仓库既有风格与分层，其次才是个人偏好**。

| 主题 | 约定 |
|---|---|
| 分层 | `api/v1/` 只做 HTTP 薄壳；编排放 `services/`；领域逻辑放 `core/`。不要从 `core/` 反向 import `api/`。 |
| 单一事实源 | 配置 = 根 `.env`；运行记录 = `core/agent_run_store.py`；嵌入调用 = `core/llm/embed.py::embed_texts`；工具注册 = `core/agent_tools.py::_TOOL_SPECS`。**新增能力要挂到既有入口，不要另起一份。** |
| 最小实现 | YAGNI：先问"这功能现在真需要吗 / 标准库或已有代码能不能解决"，不要为假想需求加抽象层与新依赖。 |
| 错误处理 | 错误码集中在 `core/error_codes.py`；异常消息以 `[E-XXX]` 开头并走 `log_error()`；工具向模型返回"操作失败: …"这类文本，不抛裸异常。 |
| 用户隔离 | 图谱 / 画像 / 知识库 / 运行记录全部按 `user_id` 分区；`KnowledgeGraph(user_id)` 每次新建、用毕 `close()`，不要跨协程共享实例。 |
| `user_id` 来源 | 一律由 JWT 解析（`Depends(get_current_user)`），**不要**在 body / query 里传。 |
| 提示词 | 外置为 Jinja2 模板（`data/prompts/*.j2`），不要把长 prompt 硬编码进 Python 字符串。 |
| 新增 LLM 调用 | 走 `core/llm/`（回退链与重试内建），不要在业务代码里直接 `httpx.post` 模型端点。 |
| 新增工具 | 在 `_TOOL_SPECS` 加一条 spec 并写 handler；重型实现放 `rag_tool.py` / `web_tool.py`。 |
| 注释 | 只在"为什么"不明显时写；不要逐行解释代码在做什么。 |

> 想改架构（拆模块、动契约、改数据表结构）→ 先读根目录 [`AGENTS.md`](AGENTS.md) 的架构契约与"已知耦合与坑"，再开 Issue 讨论。

---

## 5. 提交与 PR

### 分支命名

| 前缀 | 用途 |
|---|---|
| `feature/简短描述` | 新功能 |
| `fix/简短描述` | Bug 修复 |
| `docs/简短描述` | 文档 |
| `refactor/简短描述` | 重构（不改行为） |

### commit 格式

```
<type>: <一句话说明>
```

| type | 含义 |
|---|---|
| `feat` | 新功能 |
| `fix` | 修复缺陷 |
| `docs` | 仅文档 |
| `refactor` | 重构，无行为变化 |
| `test` | 测试新增 / 修正 |
| `chore` | 构建、依赖、工具链 |

要求：

- 一个 commit 只做一件事；**不要把不相关的改动混进同一个 PR**（否则整体会被拖住）。
- 按**文件族**拆分提交（每个提交都是能跑通的中间态），不要按时间或主题跨文件拆，避免出现 import 失败的历史提交。

### PR 说明模板

```markdown
## 改了什么
（一句话概括 + 关键文件）

## 为什么
（对应 Issue：Closes #xx；或说明痛点）

## 怎么验证
- [ ] `backend/venv/Scripts/python.exe -m pytest tests -q -m "not llm_api"` → 517 passed, 7 deselected
- [ ] 手动路径：（描述你点过 / 调过的界面或端点）

## 影响面
- [ ] 是否改动架构契约 / 数据表结构 / 环境变量（改了请同步 AGENTS.md 与 .env.example）
- [ ] 是否已同步中英文两份文档（见第 6 节）
```

### 不要提交这些

| 路径 | 原因 |
|---|---|
| `.env` 与任何密钥文件 | 已在 `.gitignore` 中；泄露后果严重 |
| `data/`（图谱 / 画像 / 对话 / KB 索引）、`backend/data/` | 本地运行时数据，可重建 |
| `logs/`、`*.bak`、`whoosh_index/` | 生成物 |
| `.research/` | 外部对标仓库（clone 的第三方源码） |
| `docs/比赛/官方材料/` | 一手材料的版权与用途限制 |
| `venv/`、`node_modules/`、`dist/` | 依赖与构建产物 |

> 例外：`data/prompts/*.j2`（提示词模板）与 `data/*.md`（文档）是**需要入库**的。

---

## 6. 文档与双语义务（本仓库特有）

门面文档维护中英两份：

| 主语言（事实源） | 派生镜像 |
|---|---|
| `README.md` | `README.en.md` |
| `CONTRIBUTING.md` | `CONTRIBUTING.en.md` |

规则（完整规范见 [`docs/README_编写规范.md`](docs/README_编写规范.md) 第 6 章）：

1. **只改主语言文件是不够的**：章节增删改、命令、端口、环境变量、测试数字，都必须**在同一个 commit 同步镜像**。
2. 镜像的**章节编号 / 顺序 / 表格行数 / 代码块**必须与主文件一一对应。
3. 代码块内容逐字符相同（命令、路径、URL、徽章语法**不翻译**）。
4. 同步后更新镜像顶部的 `<!-- base: README.md @ <commit> (日期) -->` 注释。
5. 纯排版改动可以不追，但下次同步时一并处理。

> 改架构 / 命令 / 数字只需多花 5 分钟同步，否则英文版三个月后会停留在旧架构上。

---

## 7. AI 辅助贡献

用 AI 生成或改写的补丁**可以提交**，但：

- 你必须自己**读过并跑通过**它：命令能执行、测试通过、符合本仓库分层与 YAGNI 约定。
- 提交前请核实数字与断言真实（不要贴 AI 编造的性能数据或路径）。
- PR 中请说明哪些部分由 AI 辅助生成，便于 reviewer 有重点地看。
- **贡献者始终对提交内容负责**，AI 不是免责理由。

---

## 8. 沟通与响应

- 一切讨论走公开的 Issue / PR 评论（安全问题例外，请私下联系维护者）；公开讨论本身就是贡献。
- 报告问题请给足上下文：环境（OS / Python / Node 版本）、复现步骤、期望行为与实际行为、完整报错。
- 维护者是志愿者，请耐心等待；超过一周没有回应，可以在同一 Issue 里礼貌催一次。
- 被要求修改时请回应；如果无法继续，说明一声，方便别人接手。
- 分歧时尊重维护者的最终决定；不满意可以 fork 自行维护。

---

感谢你为 TutorAgent 花的时间，祝你改得顺手。
