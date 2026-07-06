# AI-tutor 项目框架文档

> 本文档由代码 review 自动生成，记录 AI-tutor 项目的完整架构、核心设计决策、API 清单和 Review 报告。

---

## 一、项目概述

**AI-tutor** 是一个基于知识图谱的个性化 AI 辅导系统。学生与 AI 导师对话，AI 在对话中自动构建和维护知识图谱，并根据图谱进行苏格拉底式引导教学。

**技术栈：**
- 后端：FastAPI + Python 3.11+ + SQLite（WAL 模式）
- 前端：Vue 3 + Vite + Pinia + D3.js（力导向图）
- AI：阿里云千问 API（DashScope），支持流式输出 + Function Calling
- 认证：JWT（HS256，24 小时有效期）

---

## 二、目录结构

```
AI-tutor/
├── backend/
│   └── app/
│       ├── main.py                  # FastAPI 入口，注册路由，全局异常处理器
│       ├── core/
│       │   ├── auth.py             # JWT 签发/校验，密码 bcrypt 哈希
│       │   ├── knowledge_graph.py  # 核心：KnowledgeGraph 类，SQLite 操作
│       │   ├── llm_client.py       # 千问 API 客户端，流式/工具调用
│       │   ├── chat_service.py      # 对话编排：两阶段处理流程
│       │   ├── graph_analyzer.py   # 对话内容分析，自动提取图谱建议
│       │   ├── prompt_loader.py    # Jinja2 提示词模板加载器
│       │   ├── event_bus.py        # 轻量 SSE 事件总线（进程内）
│       │   ├── user_profile.py     # 用户画像管理（per-user Markdown 文件）
│       │   ├── conversation_store.py # 对话历史 SQLite 存储
│       │   ├── error_codes.py      # 错误码定义与结构化日志
│       │   └── rate_limiter.py    # 登录 IP 限流（滑动窗口）
│       ├── api/v1/
│       │   ├── auth.py            # POST /auth/register, /auth/login, /auth/me
│       │   ├── chat.py            # POST /chat, /chat/stream（SSE）
│       │   ├── knowledge.py       # 14 个知识图谱 CRUD + SSE 端点
│       │   ├── conversations.py    # 对话历史 5 个 REST 端点
│       │   └── profile.py         # GET/PUT/PATCH /profile
│       └── models/
│           └── schemas.py         # Pydantic v2 请求/响应模型
├── frontend/
│   └── src/
│       ├── api/index.js           # axios 实例，统一拦截器
│       ├── stores/
│       │   ├── authStore.js      # 登录状态 Pinia Store
│       │   └── chatStore.js      # 对话+图谱状态管理（单一数据源）
│       ├── views/
│       │   ├── LoginView.vue     # 登录/注册页
│       │   └── HomeView.vue      # 主界面（图谱+对话）
│       ├── components/
│       │   ├── ForceGraph.vue    # D3.js 力导向知识图谱
│       │   ├── ChatArea.vue      # 对话消息列表
│       │   ├── InputArea.vue     # 消息输入区
│       │   ├── Sidebar.vue       # 侧边栏（对话历史+模式切换）
│       │   ├── NodeDetail.vue    # 节点详情弹窗
│       │   ├── EditDialog.vue    # 节点/边编辑对话框
│       │   └── ...
│       └── utils/
│           └── errorCodes.js     # 前端错误码格式化
└── data/
    ├── knowledge/
    │   ├── knowledge.db         # 知识图谱 SQLite
    │   └── nodes/{user_id}/    # 按用户隔离的节点 MD 文件
    ├── conversations/
    │   └── conversations.db     # 对话历史 SQLite
    ├── profiles/
    │   └── {user_id}.md       # 用户画像 Markdown
    └── prompts/
        ├── system_prompt_common.j2       # 通用模板（含图谱摘要+用户画像）
        ├── system_prompt_adaptive.j2     # 自适应引导模式
        ├── system_prompt_free_talk.j2   # 自由对话模式
        └── system_prompt_recursive.j2   # 递归深入模式
```

---

## 三、数据库 Schema

### 3.1 用户表（users）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER | 主键，自增 |
| username | TEXT UNIQUE | 用户名 |
| password_hash | TEXT | bcrypt 哈希（截断 >72 字节密码） |
| created_at | REAL | Unix 时间戳 |

### 3.2 知识节点表（nodes）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | TEXT | 主键，英文下划线 ID |
| user_id | INTEGER | 所属用户 ID |
| name | TEXT | 中文名称 |
| tags | TEXT | JSON 数组，如 ["数据结构", "树"] |
| file_path | TEXT | 对应 MD 文件相对路径 |
| summary | TEXT | 一句话摘要 |
| difficulty | INTEGER | 难度 1-5 |
| estimated_minutes | INTEGER | 预估学习分钟数 |
| mastery | REAL | 掌握度 0.0-1.0 |
| added_by | TEXT | "human" 或 "ai" |
| created_at | REAL | 创建时间 |
| updated_at | REAL | 更新时间 |

### 3.3 知识关系表（edges）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER | 主键，自增（用于精确 CRUD） |
| user_id | INTEGER | 所属用户 ID |
| from_node | TEXT | 源节点 ID |
| to_node | TEXT | 目标节点 ID |
| relation | TEXT | prerequisite/related/confusion/extension |
| label | TEXT | 关系标签 |
| added_by | TEXT | "human" 或 "ai" |
| created_at | REAL | 创建时间 |

### 3.4 对话表（conversations，独立数据库）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | TEXT | 对话 ID（前端生成） |
| user_id | INTEGER | 所属用户 ID |
| title | TEXT | 对话标题 |
| messages | TEXT | JSON 字符串（消息列表） |
| created_at | REAL | 创建时间 |
| updated_at | REAL | 更新时间 |
| PRIMARY KEY | (id, user_id) | 复合主键 |

---

## 四、核心架构设计

### 4.1 两阶段对话处理（关键设计）

```
阶段 1（流式，低延迟）：
  前端 → POST /chat/stream
  → chat_service.py: process_message_stream()
  → LLM 流式输出 token（SSE 推送给前端）
  → 纯教学回复，不调用工具

阶段 2（后台，不阻塞）：
  → BackgroundTasks.add_task(process_background_tools)
  → 执行 AI 工具调用（add_node/add_edge/update_content/delete_node）
  → GraphAnalyzer 分析对话，生成图谱更新建议
  → publish("graph_updated") → SSE 推送前端刷新图谱
```

**设计理由**：Function Calling 会增加首 token 延迟，两阶段分离保证教学流畅性。

### 4.2 用户数据隔离策略

```
JWT token → Depends(get_current_user) → user_id
                ↓
                        KnowledgeGraph(user_id=user_id)
                                ↓
                    所有 SQL: WHERE user_id = ?
                    所有文件: nodes_dir / {user_id}/
                    SSE 端点: Depends(get_current_user_from_token)
```

- `added_by="human"` 的节点/边受 `_guard_human_content()` 保护，AI 不能修改/删除
- 前端 localStorage key 按 `user_id` 隔离（`ai_tutor_conversations_{userId}`）

### 4.3 知识图谱实时同步

```
后端 CRUD 操作 → publish("graph_updated")
                            ↓
                    event_bus._event_queue
                            ↓
                    SSE /knowledge/events → 前端
                            ↓
                    chatStore.js: connectSSE()
                            ↓
                    refreshGraph() → 重新 GET /knowledge/graph
```

- CRUD 操作后 3 秒内抑制 SSE 事件，避免双重刷新
- SSE 断线自动 5 秒重连

### 4.4 AI 工具定义（6 个 Function Calling 工具）

```json
[
  "add_node",        // 新增知识点节点
  "add_edge",        // 新增节点关系
  "update_content",   // 更新节点内容
  "delete_node",      // 删除节点（含边检查）
  "proceed_to_next", // 确认掌握，推进到前置节点
  "evaluate_mastery"  // AI 评估学生掌握度
]
```

- 工具调用在阶段 2（后台）执行，不阻塞流式输出
- `ai_suggestions.json` 存储在 `nodes_dir`（按用户隔离）

---

## 五、API 端点清单

### 5.1 认证（无需 token）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/v1/auth/register | 注册（密码 >=6 位，bcrypt 哈希） |
| POST | /api/v1/auth/login | 登录（返回 JWT token，IP 限流 5 次/60 秒） |
| GET | /api/v1/auth/me | 验证 token 有效性（需 Bearer token） |

### 5.2 对话（需认证）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/v1/chat | 一次性回复（兼容旧版） |
| POST | /api/v1/chat/stream | 流式 SSE 端点（**推荐**） |

### 5.3 知识图谱（需认证）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/v1/knowledge/graph | 获取完整图谱（节点+边） |
| GET | /api/v1/knowledge/node/{id} | 获取节点详情（含 MD 内容） |
| POST | /api/v1/knowledge/node | 创建新节点 |
| PUT | /api/v1/knowledge/node/{id} | 更新节点内容（MD） |
| PUT | /api/v1/knowledge/node/{id}/info | 更新节点名称/标签 |
| DELETE | /api/v1/knowledge/node/{id} | 删除节点（含关联边） |
| POST | /api/v1/knowledge/edge | 创建关系边 |
| PUT | /api/v1/knowledge/edge/{edgeId} | 更新边（用数据库 ID） |
| DELETE | /api/v1/knowledge/edge/{edgeId} | 删除边（用数据库 ID） |
| GET | /api/v1/knowledge/learning-path | 生成学习路径（Kahn 算法） |
| GET | /api/v1/knowledge/prerequisites/{id} | 查询前置依赖链（递归 CTE） |
| GET | /api/v1/knowledge/events | SSE 实时推送图谱变更 |

### 5.4 对话历史（需认证）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/v1/conversations | 获取对话列表（不含 messages） |
| GET | /api/v1/conversations/{id} | 获取完整对话 |
| POST | /api/v1/conversations/sync | 全量同步（前端 <-> 后端合并） |
| PUT | /api/v1/conversations/{id} | 更新对话 |
| DELETE | /api/v1/conversations/{id} | 删除对话 |

### 5.5 用户画像（需认证）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/v1/profile | 获取画像（不存在返回默认模板） |
| PUT | /api/v1/profile | 更新画像（op=replace 全量 / op=append 追加） |

---

## 六、前端核心状态管理

### 6.1 authStore（Pinia）

- `token`：JWT 字符串，持久化到 `localStorage['ai_tutor_token']`
- `user`：用户信息 `{id, username}`，持久化到 `localStorage['ai_tutor_user']`
- `checkAuth()`：初始化时验证 token 有效性
- 登录/注册成功后自动写入 token + user

### 6.2 chatStore（Pinia，单一数据源）

**对话状态：**
- `conversations`：所有对话列表
- `currentId`：当前对话 ID
- `mode`：引导模式（adaptive / free_talk / recursive）
- `loading`：流式发送中

**知识图谱状态：**
- `knowledgeNodes`：图谱节点（统一 `source/target` 字段映射）
- `knowledgeEdges`：图谱边
- `graphLoaded`：图谱是否已加载

**双写策略：**
- `localStorage` 为主（立即写入）
- 后端同步为辅（防抖 500ms）
- `localStorage` 为空时从后端加载

**SSE 实时更新：**
- `connectSSE()`：建立 EventSource 连接
- CRUD 操作后 3 秒抑制，避免双重刷新
- 断线自动重连（5 秒间隔）

---

## 七、安全设计

| 措施 | 说明 |
|------|------|
| JWT 认证 | 所有 API（除注册/登录）需 Bearer token，24h 过期 |
| SECRET_KEY 强随机 | 64 位十六进制，未配置拒绝启动（RuntimeError） |
| 密码 bcrypt | 自动截断 >72 字节密码，防止 DoS |
| IP 登录限流 | 5 次/60 秒，返回 Retry-After header |
| 用户数据隔离 | 所有 SQL 带 `WHERE user_id = ?`，文件按用户分目录 |
| AI 权限保护 | `added_by="human"` 的内容 AI 不能修改/删除 |
| 环检测 | `add_edge()` 中 BFS 检测是否会形成环 |
| 前端 token 校验 | 启动时的 `checkAuth()`，无效 token 自动清除 |

---

## 八、提示词系统

提示词采用 **Jinja2 模板 + 两阶段渲染**：

```
prompt_loader.get_system_prompt(mode, student_message, graph_summary, user_profile)
        ↓
    1. 渲染通用模板 system_prompt_common.j2
       → 注入 knowledge_graph_summary + user_profile
        ↓
    2. 渲染模式模板（adaptive/free_talk/recursive）
       → 注入 student_message + current_node（递归模式）
        ↓
    拼接返回完整 system prompt
```

**通用约束（所有模式共享）：**
- 绝不直接给出答案
- 只能在知识图谱框架内教学
- 学生画像用于个性化调整教学风格

---

## 九、启动与部署

### 后端启动

```bash
cd backend
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

环境变量（`.env`）：
```
DASHSCOPE_API_KEY=sk-xxx        # 千问 API Key（必填）
SECRET_KEY=xxx                  # JWT 签名密钥（必填，已自动生成）
DATABASE_URL=                  # （可选）自定义 knowledge.db 路径
DEFAULT_ADMIN_PASSWORD=xxx     # （可选）启动时任选创建管理员
```

### 前端启动

```bash
cd frontend
npm install
npm run dev    # → http://localhost:5173
```

### 生产部署建议

- 后端：Gunicorn + Uvicorn Worker（`-w 4 -k uvicorn.workers.UvicornWorker`）
- 前端：`npm run build` → Nginx 静态文件 + 反向代理 API
- SQLite WAL 模式已启用，支持适度并发读写
- 建议定期备份 `data/` 目录

---

## 十、已修复的历史问题

### 10.1 安全类（P0）

1. **SECRET_KEY 弱默认值**：改为强制配置，生成 64 位随机密钥
2. **SSE 端点无认证**：`/knowledge/events` 改为强制 JWT 认证
3. **MD 文件跨用户覆盖**：改为 `data/knowledge/nodes/{user_id}/` 分目录
4. **localStorage 跨用户泄露**：key 改为按 `user_id` 隔离

### 10.2 一致性类

5. **前后端密码长度不一致**：前端改为 >=6 位，与后端对齐

### 10.3 代码质量类

6. **消息格式转换重复 3 处** → 提取 `_build_api_messages()`
7. **LLM 异常处理重复 3 处** → 提取 `_map_api_error()`
8. **两个 prompt 构建函数 90% 相同** → 合并为 `_build_system_prompt(inject_tools=)`
9. **`ai_suggestions.json` 未隔离** → 改用 `nodes_dir`
10. **错误事件发布重复 3 处** → 提取 `publish_error_event()`
11. **废弃字段 `ChatRequest.user_id`** → 已移除
12. **`conversation_store.py` `__import__("time")` 奇怪写法** → 改为顶部 `import time`
13. **`graph_analyzer.py` JSON 解析逻辑重复** → 提取 `_parse_json_response()` 公共方法
14. **`graph_analyzer.py` 策略3 贪婪匹配 bug** → 用括号计数法替代正则贪婪匹配

---

## 十一、前端代码 Review 报告

### 11.1 Review 范围

- `ForceGraph.vue` — D3 力导向知识图谱组件
- `EditDialog.vue` — 知识图谱编辑弹窗
- `ContextMenu.vue` — 右键菜单
- `NodeDetail.vue` — 节点详情弹窗
- `HomeView.vue` — 主视图编排层
- `chatStore.js` — Pinia 全局状态管理
- `api/index.js` — API 客户端（axios + fetch SSE）

### 11.2 整体评价：架构清晰，组件职责分明

前端采用「弱耦合」设计：
- `ForceGraph.vue` 只负责渲染 + 交互，不直接调用 API
- `HomeView.vue` 是唯一编排层，监听 `graph-action` → 调用 Store 方法
- Store 统一管理图谱数据和 CRUD 操作
- `NodeDetail.vue` 的保存操作通过 callback 回调通知父组件

### 11.3 功能完整性验证

| 功能 | 状态 | 说明 |
|------|------|------|
| 右键菜单（空白处/节点/边） | 正常 | `ContextMenu.vue` 正确区分 `targetType` |
| 创建节点 | 正常 | 名称 + 标签 + 初始内容 |
| 编辑节点 | 正常 | 名称 + 标签 |
| 删除节点 | 正常 | confirm 确认 |
| 添加关联边（拖拽连线） | 正常 | 先创建默认边，再弹窗编辑关系 |
| 编辑边 | 正常 | 关系类型 + 标签 |
| 删除边 | 正常 | confirm 确认 |
| 节点双击 → 详情弹窗 | 正常 | 通过 `store.fetchNodeDetail()` |
| 详情内编辑 Markdown 内容 | 正常 | 通过 callback 回调保存 |
| 掌握程度显示 | 正常 | 进度条 + 文字标签 |
| 前置知识/相关节点跳转 | 正常 | 关闭弹窗 + 聚焦图谱节点 |
| 图谱搜索 | 正常 | `GraphSearch.vue` 搜索 + 聚焦 |
| 主题切换（深色/浅色） | 正常 | CSS 变量 + `data-theme` 切换 |
| 对话/图谱视图切换 | 正常 | 滑入滑出动画 |

### 11.4 发现的问题

#### P1（影响功能）
暂无。

#### P2（边界情况，影响低）

1. **`ForceGraph.vue` `handleDrawingTarget` 竞态问题**
   - 如果用户在 5 秒内多次快速创建边，多个 watch 会同时存活
   - 修复方案：用模块级变量 `drawingWatchStop` 跟踪前一个 watch，进入时先取消
   - 当前影响：极低（正常操作不会触发）

2. **`HomeView.vue` `handleGraphSearchSelect` 硬编码 setTimeout 450ms**
   - 依赖视图切换动画时长，若动画配置改变会失效
   - 修复方案：用 `watch(() => viewMode)` 替代 setTimeout

#### P3（代码质量）

1. **`ForceGraph.vue` 第 582 行使用 `alert()`**
   - 浏览器原生 alert 样式与其他 UI 不一致
   - 建议：用自定义 toast 提示组件替换

2. **`EditDialog.vue` `handleSubmit` 使用 `alert()` 做表单验证**
   - 同上，建议改用内联错误提示

### 11.5 代码质量亮点

- D3 仿真与 Vue 响应式系统解耦（用 `simulation.nodes()` 直接操作数据）
- SSE 3 秒抑制机制设计巧妙，避免 CRUD 操作后的双重刷新
- localStorage 按 `user_id` 隔离，防止多账号数据泄露
- 对话持久化「双写策略」：localStorage 即时写入 + 后端异步同步
- 边的 `edgeId` 使用数据库主键（而非数组索引），避免竞态条件
- 图谱搜索组件支持键盘导航，体验优秀

---

## 十二、知识图谱手动修改功能验证

知识图谱的手动修改功能**完全正常**，验证结果如下：

| 操作 | 触发方式 | 数据流 | 状态 |
|------|----------|--------|------|
| 创建节点 | 右键空白处 → 填写名称/标签 | ContextMenu → emit → HomeView → Store.createNode | 正常 |
| 编辑节点 | 右键节点 → 编辑 | ContextMenu → EditDialog → Store.updateNodeInfo | 正常 |
| 删除节点 | 右键节点 → 删除 → confirm | ContextMenu → Store.deleteNode → API DELETE | 正常 |
| 创建边 | 拖拽节点连线 | ForceGraph drawing → 先创建默认边 → 弹窗编辑 | 正常 |
| 编辑边 | 右键边 → 编辑 | ContextMenu → EditDialog → Store.updateEdge | 正常 |
| 删除边 | 右键边 → 删除 → confirm | ContextMenu → Store.deleteEdge → API DELETE | 正常 |
| 编辑节点内容 | 双击节点 → 详情 → 编辑 MD | NodeDetail → callback → Store.updateNodeContent | 正常 |

---

*文档生成时间：2026-06-21*
*生成方式：全项目代码 Review 自动整理*
*Review 人：Senior Developer*
