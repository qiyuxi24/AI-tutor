# TutorAgent — 知识图谱驱动的自适应导学 Agent

> 面向大学生的 AI 学习伙伴：不止是问答，而是**有地图（知识图谱）、有路径（学习规划）、有记忆（学情画像）、有反馈（AI 出题 + 进度仪表盘）**的主动学习系统。

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

---

## 这是什么

TutorAgent 把「对话式 AI 家教」与「知识图谱」「个人学情画像」结合起来，让 AI 从"你问我答"升级为主动导学：它记得你学过什么、看得见知识全貌、能规划下一步学什么，并自动把聊天沉淀成结构化笔记。

它解决大学生自学三大痛点：

- **学习盲目碎片** —— 不知道知识结构，学完串不起来 → 知识图谱可视化全景 + 拓扑排序学习路径
- **学完没有反馈** —— 看不到掌握度，不知道自己差在哪 → 掌握度建模 + AI 出题自测 + 进度仪表盘
- **记笔记费劲** —— 边学边记手忙脚乱 → 对话即笔记，图谱节点与学情画像自动沉淀

相比通用对话工具（ChatGPT / Kimi）"对话完即忘"、知识管理工具（Notion / Obsidian）"只存不教"、MOOC 平台"千人一面"，TutorAgent 将**对话交互、知识图谱、学情画像、路径推荐**融为一体——它是主动的学习伙伴，不是被动的问答工具。

---

## 全景（Architecture）

### 分层架构

```
┌────────────────────────── 前端 Vue 3 SPA ─────────────────────────────┐
│  Home 对话 │ Knowledge 图谱 │ Dashboard 仪表盘 │ Quiz │ KB │ Collector │
│          Element Plus + D3 力导向科技树 + Markdown / LaTeX 渲染          │
└───────────────────▲──────────────────────┬───────────────────────────┘
            SSE 流式文本 / 事件刷新     REST 查询（图谱 / 题库 / KB / 画像）
┌───────────────────┴──────────────────────▼───────── FastAPI 后端 ──────┐
│  api/v1: auth chat conversations knowledge profile rag kb quiz collector│
│  chat_service：文本流式优先 → 后台 Agent Loop（LLM ↔ 工具 真多轮编排）   │
│    · 对话 / Agent 主模型 MiniMax-M3（OpenAI 兼容 + function calling）   │
│    · 工具集 = 图谱 CRUD/路径、画像 note、rag_search（图谱 | KB）         │
│  领域子系统                                                             │
│    knowledge_graph / graph_middleware(学科→板块切片) / graph_analyzer   │
│    rag_pipeline（RagSource 协议，多源并行检索融合）                      │
│    hybrid_search（向量 + Whoosh BM25 + RRF）＋ kb/parsers + graph_gen   │
│    quiz 出题 │ collector 资源采集 │ user_profile 画像 │ conversation    │
│  横切：event_bus │ error_codes │ rate_limiter │ llm_client │ config     │
└────────┬──────────────────────────┬────────────────────────┬───────────┘
   SQLite（图谱/对话/题库/采集）   节点 Markdown 文件        学情画像 JSON
```

### 两大核心闭环

1. **学习闭环（越用越懂）**：对话 → Agent Loop 调用图谱/画像工具 → 节点掌握度与学情画像更新 → 仪表盘与学习路径重算 → 下次对话据此做薄弱点引导与推荐。
2. **知识闭环（喂料→可查）**：上传教材（PDF/Word/PPT/图片/文本）或 Collector 采集网页 → 解析器按类型路由、可选依赖自动降级 → 分块 + 向量化 & BM25 建双索引 → 混合检索（宽召回 → RRF 融合 → 溯源 + 父级扩展）→ 对话中以 `rag_search` 工具带引用回答。

### 一次对话发生了什么

用户消息 → 纯规则路由（问候/过短跳过 RAG）→ 注入学情上下文 + 按需检索 → **先流式吐出正文**（无感延迟）→ 后台 `agent_loop.run_agent_loop` 自动决定调哪些工具（建/改图谱节点、更新画像、查知识库）→ 图谱与仪表盘经 SSE 事件自动刷新，无需手动刷新页面。

---

## 核心功能

| 模块 | 说明 | 状态 |
|------|------|------|
| **对话 = Agent Loop** | 自适应引导/自由对话/递归式教学/路径推荐四模式；后台多轮工具循环（max 5 轮、单工具超时、trace 落盘） | ✅ |
| **流式 + 事件双通道** | SSE 逐 token 推文 + 后台图谱操作事件自动刷新 UI | ✅ |
| **知识图谱** | D3 力导向可视化 + CRUD + 拓扑排序学习路径（Kahn）+ 搜索聚焦 | ✅ |
| **科技树化图谱** | 节点按掌握度四色着色 + 图例 + 学习路径高亮 + 薄弱点脉冲 | ✅ |
| **学习进度仪表盘** | 掌握/学习中/未学统计、平均掌握度、预估时长、薄弱点 + 学科进度 | ✅ |
| **学情画像 v2** | 结构化 JSON（basic/goals/knowledge/learning/preferences/ai_notes）动态更新 | ✅ |
| **文件知识库 (KB)** | 目录树式管理，上传 PDF/Word/PPT/图片/文本 → 解析分块向量化 | ✅ |
| **混合检索 RAG** | 向量 + Whoosh BM25 宽召回各 30 → RRF 融合 → path 溯源 + 父级扩展 | ✅ |
| **Agentic RAG** | `rag_search` 工具：对话中 LLM 按需检索图谱/知识库并引用来源 | ✅ |
| **教材→图谱生成** | graph_generator 从学科教材自动生成图谱节点并语义去重 | ✅ |
| **AI 出题** | 依据教材 KB 混合检索出题（单选/多选/判断/填空/简答），规则/LLM 判分 | ✅ |
| **资源采集 (Collector)** | 从 Wikipedia/Wikibooks 等发现并入库学科资料（断点续传 + 版权双模式） | ✅ |
| **版权双模式** | 个人模式（合理使用）/ 商用模式（仅 L0 开放许可），检索层过滤 L2 | ✅ |

## 技术栈

| 层级 | 技术 | 说明 |
|------|------|------|
| **前端** | Vue 3 + Vite + Pinia + Element Plus + D3.js | SPA，科技树力导向图，Markdown/LaTeX 渲染 |
| **后端** | Python FastAPI + Uvicorn + Jinja2 | RESTful + SSE 流式，统一错误码 |
| **AI 对话** | MiniMax-M3（OpenAI 兼容，function calling） | 主模型可切阿里 qwen（见配置）；LLM 重试指数退避+抖动 |
| **嵌入** | 阿里 text-embedding-v4（固定，独立于对话模型） | 向量索引 / RAG 召回 |
| **检索** | SQLite 向量存储 + Whoosh BM25 + RRF 融合 | `backend/app/core/hybrid_search/` |
| **解析** | PyMuPDF / python-docx / python-pptx / RapidOCR | 注册表 + 策略模式，可选依赖降级 |
| **存储** | SQLite（图谱/对话/题库/采集）+ 节点 MD + 画像 JSON | 零配置，按用户隔离 |
| **认证** | JWT (python-jose) + bcrypt | 注册/登录/Token 鉴权 |
| **部署** | Docker 单容器（Nginx:80 + Uvicorn:8000）+ docker-compose | healthcheck + 数据卷 + SSE 反代 |

---

## 快速开始

### 前置要求

- Python 3.10+（开发推荐 3.11/3.13）
- Node.js 18+

### 一键安装 & 启动（Windows PowerShell）

```powershell
# 1. 安装依赖（后端 venv + 前端 node_modules）
.\install.ps1

# 2. 配置环境（根目录 .env 是唯一真值文件，本地与 Docker 共源）
copy .env.example .env
notepad .env        # 填 LLM_API_KEY(MiniMax) / DASHSCOPE_API_KEY / SECRET_KEY

# 3. 启动前后端
.\start.ps1
```

启动后访问 http://localhost:5173（首次启动自动创建管理员；**生产部署务必在根 `.env` 设置 `DEFAULT_ADMIN_PASSWORD`**，不要暴露默认口令 `admin/admin123`——见下方配置表）。

### Docker 部署（Linux 服务器）

```bash
# 根目录 .env 配置 DASHSCOPE_API_KEY / SECRET_KEY 后：
docker compose up -d --build
# 访问 http://<服务器IP>:8080
```

> 数据持久化挂载 `data/{knowledge,conversations,profiles}` + `backend/data`；.dockerignore 排除 .env，防止覆盖容器环境变量。

### 手动开发

```bash
# 后端（自动读根目录 .env，无需在 backend 下重复建 .env）
cd backend
python -m venv venv && venv\Scripts\activate   # Windows；Linux/mac: source venv/bin/activate
pip install -r requirements.txt
copy ..\.env.example ..\.env                     # Linux/mac: cp ../.env.example ../.env
uvicorn app.main:app --reload --port 8000

# 前端（另开终端）
cd frontend
npm install
npm run dev
```

### 服务地址

| 服务 | 地址 |
|------|------|
| 前端页面 | http://localhost:5173 |
| 后端 API / Swagger | http://localhost:8000 / /docs |
| 健康检查 | http://localhost:8000/api/health |

---

## 配置（根目录 .env，模板见 `.env.example`）

| 变量 | 必填 | 说明 |
|------|------|------|
| `LLM_API_KEY` | ✅* | 对话/Agent 主模型 Key（默认 MiniMax 国内站）；留空则回退 `DASHSCOPE_API_KEY` 走阿里 qwen |
| `LLM_BASE_URL` | ❌ | 默认 `https://api.minimaxi.com/v1`；切阿里时改百炼兼容地址 |
| `MODEL_NAME` | ❌ | 默认 `MiniMax-M3`（1M 上下文/多模态）；备选 `MiniMax-M2.7` / `M2.5` / `qwen-plus` |
| `DASHSCOPE_API_KEY` | ✅ | 阿里云百炼 Key：**text-embedding-v4 嵌入必需**（独立于对话模型） |
| `EMBED_BASE_URL` | ❌ | 嵌入端点，默认阿里百炼，通常无需修改 |
| `FALLBACK_LLM_API_KEY` | ❌ | 备用对话服务 Key（模型回退链）：主模型额度耗尽/认证失效/持续异常时自动**静默降级**到备用服务重发，对话不中断 |
| `FALLBACK_LLM_BASE_URL` | ❌ | 备用服务 OpenAI 兼容端点（须与 `FALLBACK_MODEL_NAME` 同填才生效） |
| `FALLBACK_MODEL_NAME` | ❌ | 备用模型名（建议选**不同供应商**的独立 Key 才有真实冗余；留空=单模型旧行为） |
| `LLM_TIMEOUT` | ❌ | 请求超时，默认 120s |
| `SECRET_KEY` | ✅ | JWT 签名密钥，缺失拒绝启动 |
| `CORS_ALLOW_ORIGINS` | ❌ | 逗号分隔白名单，默认仅本地 5173 |
| `DEFAULT_ADMIN_PASSWORD` | ❌ | 设置后首次启动自动建 admin |

---

## 项目结构

```
├── frontend/                 # Vue 3 前端
│   └── src/
│       ├── views/            # Home(对话)/Knowledge(图谱)/Dashboard/Quiz/Collector/Settings/Login
│       ├── components/       # ForceGraph(科技树)/ChatArea/NodeDetail/ActivityBar...
│       ├── stores/           # Pinia：authStore / chatStore
│       ├── api/              # axios + SSE 封装
│       └── utils/            # feedback / errorCodes / theme
│
├── backend/                  # FastAPI 后端
│   └── app/
│       ├── api/v1/           # auth/chat/conversations/knowledge/profile/rag/kb/quiz/collector
│       ├── services/         # chat_service（流式 + 后台 Agent Loop 编排）
│       └── core/
│           ├── agent_loop.py            # Agent 多轮循环（LLM ↔ 工具）
│           ├── knowledge_graph.py / graph_middleware.py / graph_analyzer.py
│           ├── rag_pipeline/            # RagSource 协议 + 路由 + 多源融合
│           ├── hybrid_search/           # 向量 + Whoosh BM25 + RRF/加权融合
│           ├── kb/                      # 目录树知识库 + parsers + graph_generator
│           ├── rag/  quiz/  collector/  # 图谱RAG / AI出题 / 资源采集
│           └── user_profile.py / event_bus.py / error_codes.py /
│               llm_client.py / rate_limiter.py / config.py / prompt_loader.py
│
├── data/                     # 运行时数据（不入库，按用户隔离）
│   ├── knowledge/            # 图谱 db + 节点 MD
│   ├── conversations/ profiles/ prompts/
│   └── collector/
├── Dockerfile / docker-compose.yml / nginx.conf / entrypoint.sh   # 生产部署
├── install.ps1 / start.ps1   # Windows 一键安装/启动
├── docs/                     # 调研、设计文档与 README 编写规范
└── COMPETITION.md            # 参赛规划总纲（工程清单见仓库 TODO.md）
```

## 质量与测试

- **零网络用例 254 个全绿**（mock 掉 LLM/embedding，无外网依赖；`pytest` 收集 467 项）：分块/融合/父级扩展/路由/pipeline 异常隔离/稀疏索引/图谱切片/rag_search 工具/上传检索全链路/用户隔离/采集注册表/商用过滤/各模块。
- **离线评测集**（复用 CMRC2018，256 文档/1000 查询）：`backend/scripts/eval_rag.py`
  mock 基线：vector R@1=0.470 / BM25 0.964 / hybrid(RRF) 0.766 / fuse(加权 α=0.6) 0.818（真实 text-embedding-v4 额度恢复后 `--embed api` 复跑）。
- **编码准则**：YAGNI 最小实现 + 提示词模板外置（Jinja2）+ 单一配置源（根 .env）+ 统一错误码。
- **方法学自证**：mock 仅替换 LLM / embedding 的**网络调用**，用于零外网、可重复的 CI 回归；分块、检索、图谱、Agent Loop 等链路逻辑全部真实。真实模型端到端（对话 / 流式 / 工具调用）与真实嵌入评测在含 API key 的部署环境单独执行，完整评测口径与评审证据模块见 [国创技术报告（校评支撑版）](docs/国创技术报告_校评支撑版.md)。

---

## 文档索引

- [README 编写规范](docs/README_编写规范.md) ｜ [参赛总纲（三赛同投）](COMPETITION.md)
- [RAG 去耦合与目录检索调研](docs/RAG_去耦合与目录检索调研.md) ｜ [RAG 召回与重排优化调研](docs/RAG_召回与重排优化调研.md) ｜ [Agentic RAG 调研](docs/RAG_参考资料与学习路线.md)
- [Agent Loop 重构设计](docs/AgentLoop_重构设计讨论.md) ｜ [Agent Loop 业界调研](docs/AgentLoop_业界调研与学习路线.md)
- [AI 出题逻辑调研](docs/QUIZ_出题逻辑调研.md) ｜ [资源采集设计讨论](docs/教育资料采集模块_设计讨论.md)
- [Docker 学习路径与工程化部署](docs/Docker_学习路径与工程化部署.md) ｜ [标杆项目对标分析](docs/标杆项目对标分析.md)

## 贡献

欢迎 Issue 与 PR：bug 报告、文档修正、新学科图谱数据、检索评测复跑等，请通过 GitHub Issues 提交。提交代码前请先跑通 `backend` 测试套件并遵循 YAGNI 最小实现原则。

## License

[MIT](LICENSE)
