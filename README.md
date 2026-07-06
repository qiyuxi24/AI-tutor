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
- [ ] 单元测试与集成测试
- [ ] 生产环境部署（Nginx + Gunicorn）

## 快速开始

### 前置要求

- Python 3.10+
- Node.js 18+

### 一键安装 & 启动

```powershell
# 安装所有依赖
.\install.ps1

# 编辑密钥配置
notepad backend\.env

# 启动
.\start.ps1
```

### 手动安装

```bash
# 后端
cd backend
python -m venv venv
venv\Scripts\activate          # Linux/Mac: source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env           # 编辑填入 DASHSCOPE_API_KEY
uvicorn app.main:app --reload --port 8000

# 前端（新终端）
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
| 默认管理员 | `admin / admin123`（需配置 DEFAULT_ADMIN_PASSWORD） |

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
