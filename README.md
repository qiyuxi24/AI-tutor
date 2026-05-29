# AI Tutor——大模型驱动的主动交互导学系统

## 项目简介
AI Tutor 是一个基于大模型的导学系统，旨在改变学生与AI的对话方式，从"直接给答案"转变为"引导式提问"。系统提供三种引导模式（阶梯提问、先思后答、反向教学），并通过知识图谱可视化展示知识点结构，未来将加入诊断算法实现自适应引导。

## 技术栈
- **前端**：Vue 3 + Vite + D3.js + ECharts + Pinia
- **后端**：Python FastAPI + Jinja2 + pandas + scipy
- **AI 接口**：阿里云百炼（qwen-plus），OpenAI 兼容客户端
- **数据**：JSON + localStorage + YAML 知识体系

## 已完成功能 ✅
- **前后端分离架构**：Vue 3 前端通过 RESTful API 与 FastAPI 后端通信，支持 CORS 代理。
- **三种引导对话模式**：
  - 阶梯提问：逐步拆解问题，每次只提一个跟进问题
  - 先思后答：强制学生先输入自己的思路
  - 反向教学：学生向AI讲授，AI追问澄清（费曼学习法）
- **对话历史管理**：自动保存对话记录至 localStorage，支持多轮上下文记忆。
- **Markdown 渲染**：AI 回复支持 Markdown 格式，使用 `marked` 库实时渲染。
- **知识图谱可视化**：
  - 力导向图（D3.js）展示知识点节点与前置依赖关系
  - 支持节点拖拽、缩放平移、右键菜单（创建/编辑/删除节点和边）
  - 点击节点查看详情（Markdown 内容渲染）
- **知识图谱管理 API**：提供节点和边的 CRUD 接口（GET/POST/PUT/DELETE），数据持久化在 `index.json`。
- **视图切换**：对话模式与知识图谱模式之间平滑动画切换。
- **节点详情面板**：右侧滑出面板显示知识点 MD 文档，支持编辑保存（PUT 接口）。
- **后端知识图谱核心**：`KnowledgeGraph` 类封装了 `index.json` 读写、前置依赖查询等操作。
- **诊断算法**：规则引擎 + 贝叶斯推断，用于动态评估学生知识点掌握程度。
- **知识点掌握图可视化**：基于 ECharts 的个人掌握度雷达图/热力图。

### 🆕 两阶段流式对话架构（2026-05-30）

实现了流式对话与知识图谱自动更新的分离架构，大幅提升用户体验：

**架构概览**
```
前端 sendMessageStream() ──POST──▶ /api/v1/chat/stream
                                      │
                                      ▼
                              process_message_stream()
                              call_llm_stream() 逐 token yield
                                      │
                           SSE ───────┘ (逐 token 推送)
                           "data: {\"token\":\"...\"}\n\n"
                                      │
                           "data: [DONE]\n\n"
                                      │
                           BackgroundTasks:
                           process_background_tools()
                              │
                              ├── call_llm_tools() (带 KG_TOOLS)
                              └── _analyze_and_apply() (图谱分析)
                                      │
                              publish("graph_updated") ──SSE──▶ 前端自动刷新图谱
```

**阶段1：流式文本回复**
- LLM 调用不带 tools，确保纯文本流式输出，逐 token 通过 SSE 推送
- 前端用 `fetch` + `ReadableStream` 解析 SSE，实时追加到气泡中
- 气泡内三点跳动打字动画 + 全局"AI 思考中…"指示器

**阶段2：后台工具调用**
- 流式结束后，FastAPI `BackgroundTasks` 触发后台线程
- 后台再次调用 LLM（带 `KG_TOOLS` function calling），判断是否需要操作知识图谱
- 支持 5 种工具：`add_knowledge_node`、`update_node_content`、`update_mastery`、`add_edge`、`delete_node`
- 工具执行完成后通过 `event_bus` 发布 `graph_updated` 事件，前端 SSE 自动刷新图谱
- 后台任务失败不影响主对话流程

**新增/修改文件**

| 文件 | 变更 |
|------|------|
| `backend/app/core/llm_client.py` | 新增 `call_llm_stream()` 流式纯文本生成器、`call_llm_tools()` 后台带 tools 调用 |
| `backend/app/services/chat_service.py` | 新增 `_build_stream_prompt()` 流式提示词、`process_message_stream()` SSE 生成器、`process_background_tools()` 后台工具调度 |
| `backend/app/api/v1/chat.py` | 新增 `/chat/stream` SSE 端点（两阶段），保留 `/chat` 兼容旧版 |
| `backend/app/core/event_bus.py` | 新增进程内事件总线，`publish()`/`subscribe()` 解耦图谱更新通知 |
| `frontend/src/api/index.js` | 新增 `sendMessageStream()` 基于 fetch + ReadableStream 的 SSE 客户端 |
| `frontend/src/stores/chatStore.js` | `send()` 改为流式调用，`onToken` 用 splice 替换触发响应式更新 |
| `frontend/src/components/ChatArea.vue` | 新增三条 watch（消息数、对话切换、流式内容长度）自动滚动到底部 |
| `frontend/src/components/MessageBubble.vue` | 新增 `isStreaming` 状态 + 三点跳动打字动画 |

**SSE 事件格式**
```json
// 流式 token
data: {"token": "递归是"}

// 流式结束
data: [DONE]

// 图谱更新（通过 /api/v1/knowledge/events 长连接推送）
data: {"type": "graph_updated"}

// 错误事件
data: {"type": "error", "code": "E-LLM-001", "message": "...", "module": "llm"}
```

## 未完成/规划中 🔧
- **自适应引导引擎**：根据诊断结果自动切换引导模式。
- **AI 知识图谱自动生长（Kiwi）**：对话后分析新概念，建议添加节点/边，经审核后自动更新图谱。
- **对照实验系统**：实验数据采集、统计分析（pandas/scipy），验证引导效果。
- **用户认证与多用户支持**：目前仅通过 localStorage 区分用户，无后端认证。
- **部署与运维**：生产环境部署配置（Nginx + Gunicorn），目前仅开发模式可用。
- **单元测试与集成测试**。

## 快速启动
```bash
# 后端
cd backend
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env  # 填写 DASHSCOPE_API_KEY
uvicorn app.main:app --reload --port 8000

# 前端
cd frontend
npm install
npm run dev
```
访问 `http://localhost:5173` 即可使用。

## 项目结构
```
AI-Tutor/
├── frontend/          # Vue 3 前端
│   ├── src/
│   │   ├── components/   # ForceGraph, ChatArea, Sidebar, MessageBubble 等
│   │   ├── api/          # axios + fetch SSE 请求封装
│   │   └── stores/       # Pinia 状态管理（含流式对话）
│   └── ...
├── backend/           # FastAPI 后端
│   ├── app/
│   │   ├── api/v1/       # chat, knowledge 路由（含 /chat/stream SSE）
│   │   ├── core/         # KnowledgeGraph, LLM客户端(流式+工具), 提示词加载器, event_bus
│   │   └── services/     # 对话服务（两阶段流式编排）
│   └── ...
├── data/knowledge/    # 知识图谱数据
│   ├── index.json        # 节点和边定义
│   └── nodes/            # 知识点 MD 文件
└── README.md
```

