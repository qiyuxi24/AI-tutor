# TutorAgent — 知识图谱驱动的自适应导学 Agent

> 面向大学生的 AI 学习伙伴：不止是问答，而是**有地图（知识图谱）、有路径（学习规划）、有记忆（学情画像）、有反馈（AI 出题 + 进度仪表盘）**的主动学习系统。
>
> 解决三大痛点：**学习盲目碎片**（不知道知识结构、学完串不起来）、**学完没有反馈**（看不到掌握度）、**记笔记费劲**（对话即笔记，图谱节点自动沉淀）。
>
> 赛道：大学生 OPC 创新创业 AI agent（校内截止 2026-10-15）｜ 竞备清单见 [TODO.md](TODO.md)

---

## 核心功能

| 模块 | 说明 | 状态 |
|------|------|------|
| **多模式对话** | 自适应引导 / 自由对话 / 递归式教学 / 学习路径推荐 四模式 | ✅ |
| **两阶段流式响应** | Phase 1 SSE 逐 token 推文 → Phase 2 后台 function calling 自动操作图谱 | ✅ |
| **知识图谱** | D3 力导向可视化 + CRUD + 拓扑排序学习路径（Kahn）+ 搜索聚焦 | ✅ |
| **科技树化图谱** | 节点按掌握度四色着色 + 图例 + 学习路径高亮 + 薄弱点脉冲 | ✅ |
| **学习进度仪表盘** | 掌握/学习中/未学统计、平均掌握度、预估时长、薄弱点 + 学科进度 | ✅ |
| **学科切换** | 节点 subject 字段 + 前端学科选择器 + 按学科统计 | ✅ |
| **学情画像 v2** | 结构化 JSON（basic/goals/knowledge/learning/preferences/ai_notes）动态更新 | ✅ |
| **文件知识库 (KB)** | 资源管理器式目录树，上传 PDF/Word/PPT/图片/文本 → 解析分块向量化 | ✅ |
| **混合检索 RAG** | 向量 + Whoosh BM25 宽召回各 30 → RRF 融合 → path 溯源 + 父级扩展 | ✅ |
| **Agentic RAG** | `rag_search` 工具：对话中 LLM 按需检索图谱/知识库并引用来源 | ✅ |
| **AI 出题** | 依据教材 KB 混合检索出题（单选/多选/判断/填空/简答），规则/LLM 判分 | ✅ |
| **资源采集 (Collector)** | 从 Wikipedia/Wikibooks 等发现并入库学科资料（断点续传 + 版权双模式） | ✅ |
| **版权双模式** | 个人模式（合理使用）/ 商用模式（仅 L0 开放许可），检索层过滤 L2 | ✅ |

## 技术栈

| 层级 | 技术 | 说明 |
|------|------|------|
| **前端** | Vue 3 + Vite + Pinia + Element Plus + D3.js | SPA，科技树力导向图，Markdown/LaTeX 渲染 |
| **后端** | Python FastAPI + Uvicorn + Jinja2 | RESTful + SSE 流式，统一错误码 |
| **存储** | SQLite（图谱/对话/题库/采集）+ 用户节点 MD 文件 | 零配置，按用户隔离 |
| **检索** | text-embedding-v4 向量（SQLite）+ Whoosh BM25 + RRF 融合 | `backend/app/core/hybrid_search/` |
| **解析** | PyMuPDF / python-docx / python-pptx / RapidOCR | 注册表 + 策略模式，可选依赖降级 |
| **AI** | OpenAI 兼容接口（默认阿里云百炼 qwen-plus），function calling | LLM 重试（指数退避+抖动） |
| **认证** | JWT (python-jose) + bcrypt | 注册/登录/Token 鉴权 |
| **部署** | Docker 单容器（Nginx:80 + Uvicorn:8000）+ docker-compose | healthcheck + 数据卷 + SSE 反代 |

## 架构亮点

1. **两阶段流式对话**：文本先流式回复，图谱操作在后台静默执行，前端经 SSE 事件自动刷新——流畅无感知。
2. **RAG 去耦合管道**（`core/rag_pipeline/`）：`RagSource` 协议让图谱/知识库/采集源可插拔；纯规则路由（问候/过短跳过）+ 单源 8s 超时与异常隔离（失败静默降级）；一次 run 并行检索跨源融合，避免重复 embedding。
3. **混合检索**（`core/hybrid_search/`）：向量稠密 + Whoosh BM25 稀疏 → **宽召回各 30 → RRF 融合**（含加权融合对照实现）；chunk 带目录 `path` 溯源 + 父级上下文扩展（预算/块数双重约束）。
4. **Agentic RAG**：RAG 检索注册为 function calling 工具 `rag_search(query, source, top_k)`，LLM 自主决定何时查、查哪里，回答带来源标注。
5. **去耦合解析器**（`core/kb/parsers/`）：按扩展名注册表路由，30+ 种文本格式 + PDF（含扫描件 OCR 回退）+ docx/pptx + 图片 OCR，可选依赖未装自动降级。
6. **AI 建议权限控制**：区分 AI 创建/人工编辑，AI 不覆盖人类内容；图谱更新经事件总线解耦推送。

## 质量与测试

- **单元 + 集成测试 211 个全绿**（11s，全部 mock 掉 LLM/embedding，零网络依赖）：
  分块/融合/父级扩展/路由/pipeline 异常隔离/稀疏索引/图谱切片/rag_search 工具/上传检索全链路/用户隔离/采集注册表/商用过滤/B 端各模块。
- **离线评测集**（复用 CMRC2018，256 文档/1000 查询）：`backend/scripts/eval_rag.py`
  mock 基线：vector R@1=0.470 / BM25 0.964 / hybrid(RRF) 0.766 / fuse(加权 α=0.6) 0.818（真实 text-embedding-v4 需账户额度恢复后 `--embed api` 复跑）。
- **编码准则**：YAGNI 最小实现 + 提示词模板外置 + 单一配置源（`core/config.py` 集中读 env）。

## 快速开始

### 前置要求
- Python 3.10+（建议 3.11/3.13）
- Node.js 18+

### 一键安装 & 启动（Windows PowerShell）
```powershell
# 1. 安装依赖（后端 venv + 前端 node_modules）
.\install.ps1

# 2. 配置密钥（必填 DASHSCOPE_API_KEY / SECRET_KEY）
copy backend\.env.example backend\.env
notepad backend\.env

# 3. 启动前后端
.\start.ps1
```
启动后访问 http://localhost:5173（默认管理员 `admin / admin123`，需 .env 配置 `DEFAULT_ADMIN_PASSWORD`）。

### Docker 部署（Linux 服务器）
```bash
# 1. 根目录 .env 配置 DASHSCOPE_API_KEY / SECRET_KEY
# 2. 构建并启动（Nginx:80 对外，SSE 已配置）
docker compose up -d --build
# 访问 http://<服务器IP>:8080
```
> 数据持久化挂载 `data/{knowledge,conversations,profiles}` + `backend/data`；.dockerignore 排除 .env，防止覆盖容器环境变量。

### 手动开发
```bash
# 后端
cd backend
python -m venv venv && venv\Scripts\activate   # Windows；Linux/mac: source venv/bin/activate
pip install -r requirements.txt
copy .env.example .env                          # Linux/mac: cp
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

## 项目结构

```
├── frontend/                 # Vue 3 前端
│   └── src/
│       ├── views/            # LoginView/HomeView/KnowledgeView/QuizView/CollectorView/DashboardView/SettingsView
│       ├── components/       # ForceGraph(科技树)/ChatArea/MessageBubble/InputArea/NodeDetail/ActivityBar...
│       ├── stores/           # Pinia：authStore / chatStore
│       ├── api/              # axios + SSE 请求封装（index/kb/quiz/collector）
│       └── utils/            # feedback(toast) / errorCodes / theme
│
├── backend/                  # FastAPI 后端
│   └── app/
│       ├── api/v1/           # auth/chat/conversations/knowledge/profile/rag/kb/quiz/collector（55+ 端点）
│       ├── services/         # chat_service（两阶段流式编排）
│       ├── core/
│       │   ├── knowledge_graph.py / graph_middleware.py / graph_analyzer.py   # 图谱
│       │   ├── kb/           # 目录树知识库 + 解析器注册表 + graph_generator
│       │   ├── hybrid_search/  # 向量 + Whoosh BM25 + RRF/加权融合
│       │   ├── rag_pipeline/   # 去耦合检索管道（RagSource 协议 + 路由 + pipeline）
│       │   ├── rag/           # 图谱 RAG
│       │   ├── quiz/          # AI 出题
│       │   ├── collector/     # 资源采集
│       │   └── ...            # llm_client/user_profile/event_bus/error_codes/config
│       ├── models/schemas.py
│       └── scripts/          # seed_collector.py / eval_rag.py / 迁移脚本
│
├── data/                     # 运行时数据（不入库）
│   ├── knowledge/            # 图谱 db + 节点 MD
│   ├── conversations/ profiles/ prompts/（Jinja2 教学模板）
│   └── collector/            # seed 离线词条 + subjects.json
├── Dockerfile / docker-compose.yml / nginx.conf / entrypoint.sh   # 生产部署
├── install.ps1 / start.ps1   # Windows 一键安装/启动
├── COMPETITION.md            # 参赛规划与材料清单
├── TODO.md / TODO_Collector.md
└── docs/                     # 调研与设计文档（RAG/出题/Collector/Docker/BP 等）
```

## 环境变量（backend/.env）

| 变量 | 必填 | 说明 |
|------|------|------|
| `DASHSCOPE_API_KEY` | ✅ | 阿里云百炼 API Key（也可配其他 OpenAI 兼容网关） |
| `SECRET_KEY` | ✅ | JWT 签名密钥，缺失拒绝启动 |
| `MODEL_NAME` | ❌ | 默认 `qwen-plus` |
| `LLM_BASE_URL` / `LLM_TIMEOUT` | ❌ | 兼容网关地址（默认百炼）/ 超时（默认 120s） |
| `CORS_ALLOW_ORIGINS` | ❌ | 逗号分隔白名单，默认仅本地 5173 |
| `DEFAULT_ADMIN_PASSWORD` | ❌ | 设置后首次启动自动建 admin |

## 相关文档

- [参赛规划与五维评审对照](COMPETITION.md) ｜ [备赛时间线](docs/备赛时间线与计划.md) ｜ [改进清单](TODO.md)
- [RAG 去耦合与目录检索调研](docs/RAG_去耦合与目录检索调研.md) ｜ [RAG 召回与重排优化调研](docs/RAG_召回与重排优化调研.md)
- [AI 出题逻辑调研](docs/QUIZ_出题逻辑调研.md) ｜ [资源采集设计讨论](docs/教育资料采集模块_设计讨论.md)
- [Docker 学习路径与工程化部署](docs/Docker_学习路径与工程化部署.md) ｜ [标杆项目对标分析](docs/标杆项目对标分析.md)

## License

MIT
