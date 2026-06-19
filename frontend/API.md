# 前端 API 路由参考

> 所有 API 通过 Vite 代理转发到 `http://127.0.0.1:8000`（开发环境）或同源部署（生产环境）。
> 请求由 `apiClient`（axios）统一管理 auth 和错误处理，流式聊天使用原生 `fetch` + SSE。

---

## 一、认证模块

| 方法 | 路径 | 调用位置 | 说明 |
|------|------|----------|------|
| `POST` | `/api/v1/auth/register` | `authStore.register()` | 注册，返回 `{ token, user }` |
| `POST` | `/api/v1/auth/login` | `authStore.login()` | 登录，返回 `{ token, user }` |
| `GET` | `/api/v1/auth/me` | `authStore.checkAuth()` | 验证 token 有效性，返回 `{ user_id, username }` |

**错误码**：`[E-AUTH-xxx]` 前缀，401 时 `apiClient` 拦截器自动清除凭据并跳转登录页。

---

## 二、聊天模块

| 方法 | 路径 | 调用位置 | 说明 |
|------|------|----------|------|
| `POST` | `/api/v1/chat/stream` | `sendMessageStream()` → `chatStore.send()` | **SSE 流式对话**，body: `{ messages, mode }` |

**数据流**：
```
InputArea → chatStore.send()
  → sendMessageStream(messages, mode, { onToken, onDone, onError })
  → fetch POST /api/v1/chat/stream
  → SSE 逐 token 推送 → onToken 更新消息内容
  → [DONE] 信号 → onDone
```

**注意**：流式请求使用原生 `fetch`（不用 axios），因为需要 `ReadableStream`。但 401 处理已与 `apiClient` 统一到 `handleUnauthorized()`。

---

## 三、知识图谱模块

### 3.1 图谱数据

| 方法 | 路径 | 调用位置 | 说明 |
|------|------|----------|------|
| `GET` | `/api/v1/knowledge/graph` | `chatStore.fetchGraph()` | 获取完整图谱 `{ nodes, edges }` |
| `GET` | `/api/v1/knowledge/events` | `chatStore.connectSSE()` | SSE 监听后端图谱变更 |

**数据流**：
```
HomeView onMounted → store.fetchGraph()
  → apiClient.get('/api/v1/knowledge/graph')
  → 存储到 store.knowledgeNodes / store.knowledgeEdges
  → 通过 props 传入 ForceGraph → watch 触发 renderGraph
```

**SSE 自动刷新**：
```
后端图谱变更 → SSE event { type: "graph_updated" }
  → chatStore.refreshGraph() → 重新 GET /graph
  → props 更新 → ForceGraph 自动重渲染
```

### 3.2 节点 CRUD

| 方法 | 路径 | 调用位置 | 说明 |
|------|------|----------|------|
| `POST` | `/api/v1/knowledge/node` | `ForceGraph.handleDialogSubmit` (create-node) | 创建节点 `{ name, tags, content? }` |
| `PUT` | `/api/v1/knowledge/node/{id}/info` | `ForceGraph.handleDialogSubmit` (edit-node) | 编辑节点元信息 `{ name, tags }` |
| `PUT` | `/api/v1/knowledge/node/{id}` | `NodeDetail.handleSave()` | 编辑节点内容 `{ content }` |
| `DELETE` | `/api/v1/knowledge/node/{id}` | `ForceGraph.handleDeleteNode()` | 删除节点（级联删除关联边） |
| `GET` | `/api/v1/knowledge/node/{id}` | `HomeView.handleNodeDblClick()` | 获取节点详情（含内容） |

**CRUD 数据流**：
```
ForceGraph CRUD → apiClient.xxx() → 后端操作
  → emit('graph-changed')
  → HomeView 监听 → store.refreshGraph()
  → apiClient.get('/api/v1/knowledge/graph')
  → props 更新 → ForceGraph watch → renderGraph
```

### 3.3 边 CRUD

| 方法 | 路径 | 调用位置 | 说明 |
|------|------|----------|------|
| `POST` | `/api/v1/knowledge/edge` | `ForceGraph.handleDrawingTarget()` | 创建边 `{ from, to, relation, label }` |
| `PUT` | `/api/v1/knowledge/edge/{idx}` | `ForceGraph.handleDialogSubmit` (edit-edge) | 编辑边 `{ relation, label }` |
| `DELETE` | `/api/v1/knowledge/edge/{idx}` | `ForceGraph.handleDeleteEdge()` | 删除边（按索引） |

---

## 四、系统模块

| 方法 | 路径 | 调用位置 | 说明 |
|------|------|----------|------|
| `GET` | `/api/health` | `api/index.js: healthCheck()` | 健康检查（未使用） |

---

## 五、请求统一规范

### 5.1 HTTP 客户端

所有非流式请求统一使用 `apiClient`（axios 实例）：

```js
// src/api/index.js
export const apiClient = axios.create({ timeout: 300000 })
```

**拦截器**：
- **请求**：自动附加 `Authorization: Bearer <token>`
- **响应**：401 自动清除凭据 → 跳转 `/login`

### 5.2 localStorage 键名

| 键 | 用途 | 读写位置 |
|----|------|----------|
| `ai_tutor_token` | JWT token | `authStore`, `apiClient` |
| `ai_tutor_user` | 用户信息 JSON | `authStore`, `chatStore`（取 user_id） |
| `ai_tutor_conversations_{uid}` | 对话历史 | `chatStore` |
| `ai_tutor_current_{uid}` | 当前对话 ID | `chatStore` |
| `ai_tutor_mode_{uid}` | 引导模式 | `chatStore` |

### 5.3 路由

| 路径 | 组件 | 权限 |
|------|------|------|
| `#/login` | `LoginView.vue` | 仅 guest（已登录自动跳 `/`） |
| `#/` | `HomeView.vue` | 需登录（未登录跳 `/login`） |

---

## 六、统一错误码体系

> 错误码定义在 `src/utils/errorCodes.js`，与后端 `backend/app/core/error_codes.py` 保持同步。

### 6.1 模块划分

| 模块前缀 | 来源 | 说明 |
|----------|------|------|
| `E-COMM-xxx` | 前端生成 | 前后端通信：网络、超时、HTTP 状态码 |
| `E-AUTH-xxx` | 后端返回 | 用户认证：登录、注册、Token |
| `E-LLM-xxx` | 后端返回 | 大模型调用：千问 API 错误 |
| `E-KG-xxx` | 后端返回 | 知识图谱：节点/边操作 |
| `E-GRAPH-xxx` | 后端返回 | 图谱分析器：JSON 解析、分析失败 |
| `E-CHAT-xxx` | 后端返回 | 对话服务：提示词构建、流程编排 |
| `E-EVENT-xxx` | 后端返回 | 事件总线：SSE 推送异常 |
| `E-CLIENT-xxx` | 前端生成 | 前端特有：表单校验、组件异常 |

### 6.2 错误码清单

#### E-COMM（前后端通信 — 前端生成）

| 错误码 | 消息 | 触发条件 |
|--------|------|----------|
| `E-COMM-001` | 请求超时，后端处理过慢或网络延迟 | axios `ECONNABORTED` |
| `E-COMM-002` | 无法连接到后端服务器，请确认后端已启动 | `fetch` network error / 无响应 |
| `E-COMM-003` | 后端网关错误 (502)，服务可能未就绪 | HTTP 502 |
| `E-COMM-004` | 后端服务不可用 (503)，请稍后重试 | HTTP 503 |
| `E-COMM-005` | 后端网关超时 (504)，AI调用可能仍在处理中 | HTTP 504 |
| `E-COMM-006` | 后端内部错误 (5xx)，请查看服务端日志 | HTTP 5xx（非 502/503/504） |
| `E-COMM-007` | 后端返回了未预期的响应 | HTTP 4xx（非 401）或其他非标准响应 |

#### E-CLIENT（前端特有 — 前端生成）

| 错误码 | 消息 | 触发位置 |
|--------|------|----------|
| `E-CLIENT-001` | 输入内容不符合要求 | `LoginView` 表单校验 |
| `E-CLIENT-002` | 知识图谱加载失败 | `chatStore.fetchGraph()` catch |
| `E-CLIENT-003` | 节点保存失败 | （预留） |
| `E-CLIENT-004` | 节点删除失败 | （预留） |
| `E-CLIENT-005` | 边操作失败 | （预留） |
| `E-CLIENT-006` | 节点详情加载失败 | `HomeView.handleNodeDblClick()` catch |
| `E-CLIENT-007` | 发生未知错误 | 所有未匹配的异常回退 |

### 6.3 错误处理流程

```
用户操作 → API 调用
  ├── 成功 → 正常流程
  └── 失败 → catch (err)
        ├── err.response?.data?.detail 存在 → 透传（后端已含错误码）
        ├── err.response.status → 映射 E-COMM-003~007
        ├── err.code === 'ECONNABORTED' → E-COMM-001
        ├── !err.response → E-COMM-002（网络断连）
        └── 无法识别 → E-CLIENT-007（兜底）

所有错误消息最终格式: [E-{模块}-{序号}] {描述消息}
```

### 6.4 使用方式

```js
import { formatError, clientError, ErrorDefs, fmt } from '@/utils/errorCodes'

// 方式1：自动识别错误（推荐，用于 catch 块）
catch (err) {
  const msg = formatError(err, { action: '删除节点' })
  alert(msg)  // "[E-KG-003] 目标知识点不存在"
}

// 方式2：前端校验错误
const msg = clientError('VALIDATION', '用户名至少需要 3 个字符')
// → "[E-CLIENT-001] 输入内容不符合要求 — 用户名至少需要 3 个字符"

// 方式3：直接格式化预定义错误
const msg = fmt(ErrorDefs.COMM.NETWORK_ERROR)
// → "[E-COMM-002] 无法连接到后端服务器，请确认后端已启动"
```

---

## 七、架构原则

1. **单一数据源**：图谱数据由 `chatStore` 统一管理，组件通过 props 消费
2. **单向数据流**：CRUD 操作 → emit 事件 → 父组件刷新 store → props 更新 → 自动重渲染
3. **统一 HTTP 客户端**：所有非流式 API 调用使用 `apiClient`（axios），享受统一的 auth 和错误处理
4. **SSE 不阻塞主线程**：图谱变更通过 EventSource 异步推送，不依赖轮询
