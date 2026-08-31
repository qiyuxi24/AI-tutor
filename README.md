# AI Tutor——大模型驱动的主动交互导学系统

## 项目简介

AI Tutor 是一个基于大模型的智能导学系统，通过知识图谱 + 用户画像 + 多模式教学策略，实现个性化、结构化的自适应学习。系统支持多种教学模式（自适应引导、自由对话、递归式教学），并在后台自动维护知识图谱和用户能力画像。

## 技术栈

| 层级 | 技术 | 说明 |
|------|------|------|
| **前端** | Vue 3 + Vite + D3.js + Pinia | SPA 单页应用，D3 力导向图可视化知识图谱 |
| **后端** | Python FastAPI + Jinja2 + Uvicorn | RESTful API + SSE 流式端点 |
| **数据库** | SQLite (aiosqlite) | knowledge.db（知识图谱）+ conversations.db（对话历史） |
| **AI** | OpenAI 兼容接口（阿里云百炼） | qwen-plus / qwen3.6-35b-a3b，支持 function calling |
| **认证** | JWT (python-jose) + bcrypt | 用户注册/登录/Token 鉴权 |
| **分析** | pandas + scipy | 诊断算法（规则引擎 + 贝叶斯推断） |

## 核心功能

### 🎯 四种教学模式

| 模式 | 说明 |
|------|------|
| **自适应引导** (adaptive) | 合并原阶梯提问/先思后答/反向教学，根据学生画像自动调整引导强度 |
| **自由对话** (free_talk) | 无约束自然对话，适合开放式讨论 |
| **递归式教学** (recursive) | 基于知识图谱拓扑排序，从根节点自顶向下逐层讲解，严格框架约束，不脱离图谱 |
| **学习路径推荐** | LLM 拆解用户问题 → 生成骨架知识图谱 → 拓扑排序 → 推荐"下一步该学什么" |

### 📊 知识图谱

- **可视化**：D3.js 力导向图，支持拖拽、缩放、右键菜单（增删改节点/边）
- **自动生长**：对话后后台 function calling 自动分析是否需要新增/更新知识点
- **搜索**：即时搜索节点，键盘导航，高亮匹配，聚焦定位
- **拓扑排序**：Kahn 算法生成学习路径，`/knowledge/learning-path` API
- **权限控制**：区分 AI 创建/人工编辑，人类内容不被 AI 覆盖

### 👤 用户系统

- JWT 登录/注册，Token 自动刷新
- 用户画像（Markdown 格式持久化），对话中动态更新
- 对话历史跨会话持久化（SQLite）
- 首次使用引导（OnboardingGuide 卡片）

### ⚡ 两阶段流式对话

```
用户消息 → POST /api/v1/chat/stream
              │
    ┌─────────┴─────────┐
    ▼                   ▼
阶段1：流式文本         阶段2：后台工具调用
逐 token SSE 推送      function calling 操作知识图谱
打字动画展示            event_bus 推送 graph_updated
                       前端 SSE 自动刷新图谱
```

## 已实现功能

- [x] 前后端分离架构（Vue 3 + FastAPI）
- [x] 四种教学模式（自适应/自由对话/递归式/学习路径推荐）
- [x] 知识图谱可视化与管理（D3.js 力导向图 + CRUD API）
- [x] 知识图谱搜索与焦点跳转
- [x] 知识点间可点击跳转（AI 回复 + NodeDetail）
- [x] 拓扑排序学习路径推荐（Kahn 算法）
- [x] 问题拆解自动生成骨架图谱（LLM decompose_question）
- [x] 两阶段流式对话（文本 SSE + 后台 function calling）
- [x] 对话历史管理（SQLite 持久化，多轮上下文）
- [x] Markdown 渲染（marked 库实时渲染）
- [x] JWT 用户认证（注册/登录/Token 鉴权）
- [x] 用户画像系统（动态更新 + 诊断算法）
- [x] 进程内事件总线（解耦图谱更新通知）
- [x] 标准化错误码体系
- [x] 一键安装脚本（install.ps1）
- [x] 一键启动脚本（start.ps1）

## 规划中

- [ ] 学习进度仪表盘（掌握/学习中/未开始统计）
- [ ] AI 建议审核面板（待审核节点/边的前端展示）
- [ ] LLM 请求重试机制
- [ ] Toast 通知替代 alert
- [ ] CORS 配置化（从环境变量读取）
- [x] 单元测试与集成测试（pytest，`backend/tests/`，90+ 用例覆盖 RAG 检索链路）
- [ ] 生产环境部署（Nginx + Gunicorn）

## 项目规模与质量评估（2026-08 统计）

### 代码规模：中小型项目（约 1.7 万行）

| 模块 | 文件数 | 代码行数 | 说明 |
|------|--------|----------|------|
| 后端 Python | 58 | 8,783 | FastAPI 应用（api / core / models / services） |
| 迁移脚本 | 4 | 475 | 数据格式迁移（JSON→SQLite 等） |
| 前端 Vue 组件 | 20 | 6,389 | 对话、图谱、侧边栏等 |
| 前端 JS | 9 | 1,250 | API 封装、Pinia、路由、工具函数 |
| **合计** | **91** | **≈16,900** | 另有 46 个 REST API 路由 |
| 提示词模板 | 4 | 105 | Jinja2 系统提示词 |
| 调研/设计文档 | 6 | ≈1,100 | docs/ + 项目框架文档 + API.md |

**依赖规模**：Python 依赖 20 个，npm 依赖 9 个（7 运行时 + 2 开发），依赖面克制、无重型框架。

### 质量优势 ✅

- **架构清晰**：前后端分离；后端按 `api / core / models / services` 分层，core 内再拆子模块（kb、rag、quiz、hybrid_search、rag_pipeline），职责单一，RAG 模块已从主链路解耦（见 `docs/RAG_去耦合调研`）
- **工程化配套完整**：一键安装/启动脚本、`.env.example` 模板、4 个数据迁移脚本、标准化错误码体系 + `error_codes.md` 速查表、速率限制、JWT 鉴权、Swagger 文档自动生成
- **设计文档齐全**：`docs/` 下 5 篇调研文档（RAG 召回优化、出题逻辑、OPENMAIC 借鉴方案等）+ 项目框架文档，设计先于实现、决策有据可查（含 35+ 个外部参考引用）
- **可维护性好**：提示词模板外置（Jinja2）、事件总线解耦图谱更新、数据与代码分离（`data/` 目录）

### 待改进点 ⚠️（对应「规划中」清单）

- **测试与评测体系已建立**：`backend/tests/` 已有 101 个 pytest 用例（单元 90 + 端到端集成 11：分块/融合/父级扩展/路由/pipeline 异常隔离/稀疏索引/图谱切片/rag_search 工具/上传检索全链路/目录过滤/path 溯源/用户隔离），全部 mock 掉 LLM/embedding 零网络依赖。另有**离线评测集**（复用开源 CMRC2018，256 文档/1000 查询，`backend/scripts/eval_rag.py`）量化检索质量：mock 基线 vector R@1=0.470 / bm25 0.964 / RRF hybrid 0.766 / 加权 fuse 0.818；真实 text-embedding-v4 评测待账户充值后运行（`--embed api`）。待办：HyDE 查询扩展
- **部署能力**：仅支持本地开发模式（uvicorn --reload + vite dev），无 Docker / Nginx / Gunicorn 生产配置，CORS 白名单硬编码在 `main.py`
- **健壮性**：无 LLM 请求重试机制、前端异常提示依赖 alert；存在 `ForceGraph.vue.bak` 等备份文件，建议用 git 管理替代手工备份文件

## 快速开始

### 前置要求

- Python 3.10+
- Node.js 18+

### 一键安装 & 启动（Windows PowerShell）

```powershell
# 1. 安装所有依赖（后端虚拟环境 + 前端 node_modules）
.\install.ps1

# 2. 配置密钥（必填项：DASHSCOPE_API_KEY、SECRET_KEY）
copy backend\.env.example backend\.env
notepad backend\.env

# 3. 一键启动前后端（脚本会自动检查环境、按需补装依赖）
.\start.ps1
```

启动成功后访问 http://localhost:5173 ，按 Ctrl+C 停止所有服务。

### 手动安装（Linux / Mac / Windows 通用）

**后端：**

```bash
cd backend
python -m venv venv
venv\Scripts\activate          # Windows PowerShell
# source venv/bin/activate     # Linux / Mac
pip install -r requirements.txt

# 配置环境变量（Windows 用 copy，Linux/Mac 用 cp）
copy .env.example .env         # cp .env.example .env
# 编辑 .env，填入 DASHSCOPE_API_KEY 和 SECRET_KEY

uvicorn app.main:app --reload --port 8000
```

**前端（另开一个终端）：**

```bash
cd frontend
npm install
npm run dev
```

访问 http://localhost:5173

### 服务地址

| 服务 | 地址 |
|------|------|
| 前端页面 | http://localhost:5173 |
| 后端 API | http://localhost:8000 |
| 健康检查 | http://localhost:8000/api/health |
| Swagger 文档 | http://localhost:8000/docs |
| 默认管理员 | `admin / admin123`（需在 `.env` 中配置 `DEFAULT_ADMIN_PASSWORD`） |

### 环境变量说明（backend/.env）

| 变量 | 必填 | 说明 |
|------|------|------|
| `DASHSCOPE_API_KEY` | ✅ | 阿里云百炼 API Key |
| `MODEL_NAME` | ❌ | LLM 模型，默认 `qwen-plus` |
| `SECRET_KEY` | ✅ | JWT 签名密钥，请替换为随机字符串 |
| `DEFAULT_ADMIN_PASSWORD` | ❌ | 设置后首次启动自动创建 admin 账户 |

## 项目结构

```
AI-Tutor/
├── frontend/                  # Vue 3 前端
│   └── src/
│       ├── components/        # ChatArea, ForceGraph, Sidebar, MessageBubble,
│       │                        InputArea, NodeDetail, ContextMenu, GraphSearch,
│       │                        UserProfile, EditDialog, OnboardingGuide, Settings
│       ├── views/             # HomeView, LoginView
│       ├── api/               # axios + fetch SSE 请求封装
│       ├── stores/            # Pinia 状态管理（authStore, chatStore）
│       ├── router/            # Vue Router 路由配置
│       └── utils/             # 工具函数
│
├── backend/                   # FastAPI 后端
│   └── app/
│       ├── main.py            # 入口：CORS + 路由注册 + 启动事件
│       ├── api/v1/            # REST API 路由
│       │   ├── auth.py        # 注册/登录/JWT
│       │   ├── chat.py        # 对话接口（含 /chat/stream SSE 流式）
│       │   ├── knowledge.py   # 知识图谱 CRUD + 拆解 + 学习路径
│       │   ├── conversations.py  # 对话历史同步
│       │   └── profile.py     # 用户画像
│       ├── core/              # 核心模块
│       │   ├── knowledge_graph.py  # 图谱引擎（拓扑排序/学习路径/权限控制）
│       │   ├── graph_analyzer.py   # LLM 图谱分析（问题拆解）
│       │   ├── llm_client.py       # OpenAI 兼容 LLM 客户端
│       │   ├── chat_service.py     # 两阶段流式对话编排
│       │   ├── prompt_loader.py    # Jinja2 提示词模板加载
│       │   ├── user_profile.py     # 用户画像管理
│       │   ├── conversation_store.py # 对话持久化
│       │   ├── auth.py             # JWT 认证
│       │   ├── event_bus.py        # 进程内事件总线
│       │   ├── error_codes.py      # 统一错误码
│       │   └── rate_limiter.py     # 速率限制
│       ├── models/
│       │   └── schemas.py     # Pydantic 数据模型
│       └── services/
│           └── chat_service.py
│
├── data/                      # 数据与模板
│   ├── knowledge/             # 知识图谱数据（knowledge.db + 节点 MD）
│   ├── conversations/         # 对话历史（conversations.db）
│   ├── profiles/              # 用户画像 MD 文件
│   ├── prompts/               # Jinja2 系统提示词模板
│   │   ├── system_prompt_common.j2       # 通用教师角色 + 框架约束
│   │   ├── system_prompt_adaptive.j2     # 自适应引导模式
│   │   ├── system_prompt_free_talk.j2    # 自由对话模式
│   │   └── system_prompt_recursive.j2    # 递归式教学模式
│   └── error_codes.md         # 错误码速查表
│
├── install.ps1                # 一键安装依赖
├── start.ps1                  # 一键启动前后端
├── TODO.md                    # 改进清单
└── README.md
```

## 配置参考

需要修改地址/端口/网关时，参考以下文件：

| 配置项 | 文件 | 行号/字段 |
|--------|------|-----------|
| 前端端口 | `frontend/vite.config.js` | `port: 5173` |
| 代理目标（后端地址） | `frontend/vite.config.js` | `target: 'http://127.0.0.1:8000'` |
| 后端端口 | `start.ps1` | `--port 8000` |
| CORS 白名单 | `backend/app/main.py` | `allow_origins=["http://localhost:5173"]` |
| LLM API 网关 | `backend/app/core/llm_client.py` | `base_url` |
| LLM 模型 | `backend/.env` | `MODEL_NAME` |
| API Key | `backend/.env` | `DASHSCOPE_API_KEY` |
| JWT 密钥 | `backend/.env` | `SECRET_KEY` |

## License

MIT
