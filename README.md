# AI Tutor——大模型驱动的主动交互导学系统

## 项目简介
AI Tutor 是一个基于大模型的导学系统，旨在改变学生与AI的对话方式，从“直接给答案”转变为“引导式提问”。系统提供三种引导模式（阶梯提问、先思后答、反向教学），并通过知识图谱可视化展示知识点结构，未来将加入诊断算法实现自适应引导。

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
│   │   ├── components/   # ForceGraph, ChatArea, Sidebar, NodeDetail 等
│   │   ├── api/          # axios 请求封装
│   │   └── stores/       # Pinia 状态管理
│   └── ...
├── backend/           # FastAPI 后端
│   ├── app/
│   │   ├── api/v1/       # chat, knowledge 路由
│   │   ├── core/         # KnowledgeGraph, LLM客户端, 提示词加载器
│   │   └── services/     # 对话服务
│   └── ...
├── data/knowledge/    # 知识图谱数据
│   ├── index.json        # 节点和边定义
│   └── nodes/            # 知识点 MD 文件
└── README.md
```

