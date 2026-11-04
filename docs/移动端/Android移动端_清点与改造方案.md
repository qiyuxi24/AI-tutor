# Android 移动端：清点、难度与改造方案

> 目标：2027-01「软件应用创新大赛」，产品形态对标腾讯 **ima**，**不需要真上架**（只需能讲清上架可行性）。
> 时间基准 2026-11-02，可用约 **10 周**。
> 依据：三份调研（前端逐页审计 `.dsh_local/mobile-audit-frontend.md`、打包与运行时 `.dsh_local/mobile-audit-android.md`、ima 功能拆解 `.dsh_local/ima-feature-map.md`）+ 本文件的代码复核。
> 本文只写结论与可执行清单；数字均可在仓库复核。

---

## 0. 三句话结论

1. **难度不在"做成 Android App"，而在"重写一套手机 UI"。** 壳只需 1–2 人日；真正吃掉 10 周的是 390px 下的视图层重写（估 **19–28 人日**）。
2. **路线要反过来排：先移动 Web（扫码即开），后 APK 套壳（加分项）。** 因为评委"打不开 = 0 分"，而扫码打开的是 Web 不是 APK；且现有 nginx 已经是"静态 + `/api` 反代"同域部署，**移动 Web 路线后端零改动**。
3. **对标 ima 只抄骨架，不抄功能清单。** 抄它 4 tab 的信息架构 + 引用式问答；**不抄**广场/生图/播客/copilot 记忆。立论点放在它没有的东西：**知识图谱 + 判分回写掌握度**。

---

## 1. 清点：现有资产 vs 移动端要求

### 1.1 已经能直接复用（不用重写）

| 资产 | 现状（实测） | 对移动端意味着 |
|---|---|---|
| 后端 API | `backend/app/api/v1/` **11 模块、103 个路由**（knowledge.py 32、kb.py 20、quiz.py 8、profile.py 8、agent_runs.py 7、collector.py 6、conversations.py 6、llm_models.py 6、auth.py 5、rag.py 3、chat.py 2） | 全部与 UI 解耦 → 移动端零后端改造（同域部署时） |
| 鉴权 | JWT 24h + RSA-OAEP（`utils/crypto.js`，node-forge）+ `localStorage['ai_tutor_token']` | 登录弹窗需重排（380px 溢出 360 屏），逻辑不动 |
| 对话流式 | `fetch` + `getReader()` 消费 SSE（`api/index.js:182-209`），**不是 EventSource**，带 Authorization | WebView/移动浏览器均可用，无需改协议 |
| 常驻事件 | `EventSource('/api/v1/knowledge/events?token=…')` + 退避 5s→60s + `notices/pending` 补拉 | 切后台会断，靠 onResume 重连 + 补拉（已有） |
| 图谱渲染 | d3 + **SVG**（`utils/forceGraphEngine.js:235`），`d3.zoom` scaleExtent(0.08,5) | **d3.zoom 原生支持触屏双指缩放**；渲染引擎可整体复用 |
| 图谱三种布局 | `utils/graphLayout.js`：force（ForceGraph 567 行）/ mindmap（GraphMindmap 271 行）/ brace（GraphBrace 107 行） | 移动端默认 mindmap 或 brace（力导向在手机上不好点） |
| 状态与数据层 | Pinia store（chatStore 等）+ `api/*.js` 全量 | **这是"单库"的核心价值：移动端视图换掉，store/api 一行不改** |
| 路由 | `createWebHashHistory()`，仅 `/` + catch-all | hash 路由在 WebView 和静态托管下都不会 404 → **可零成本加 `#/m/*` 移动入口** |
| Markdown/公式/高亮 | marked + katex + highlight.js + dompurify（`renderMarkdown`） | 消息渲染逻辑复用，只换样式 |
| 出题判分 | quiz.py 8 端点 + `attempts` 表已存 `correct(0/1)` | **"错题本"只需一条 SQL，不用建表**（见 §4.2） |
| 学习路径 | `/knowledge/learning-path`、`/main-path`、`/next-to-learn`（Kahn 拓扑） | 移动端"学习"页直接吃现成数据 |
| 学习统计 | `/knowledge/stats`（`compute_stats`，按学科聚合） | "我"页进度卡片直接吃 |
| 部署 | `nginx.conf`：前端静态 + `/api` → 127.0.0.1:8000，SSE 关 buffering，`client_max_body_size 100m` | **同域 → 相对路径 `/api` 可直接用，CORS 都不用改** |

### 1.2 必须重写的部分（这是工时的全部来源）

| 项 | 现状（实测） | 移动端要求 | 判定 |
|---|---|---|---|
| **响应式体系** | 全项目 `@media` 仅 6 处，宽度断点仅 4 条（900/760/680/560），**无一条 <560px**；`styles/` 三文件（components 13.7KB / page 1.9KB / settings 6KB）**零断点 token** | 需要新建设计 token + 断点变量 | ❌ 从零建 |
| **导航 IA** | ActivityBar **6 入口**（chat/dashboard/graph/kb/lecture/quiz，`ActivityBar.vue:34-41`） | 底部 **4 tab**（参考 ima） | ❌ 重排 |
| **页面切换** | `HomeView.vue` 的 `viewMode` 状态机（:53、:217-218），7 个视图 `v-if` 挂载（:508/530/667/674/680/688/696） | 移动端需要自己的视图容器 + 二级页栈 | ❌ 新写 |
| **对话页布局** | `SidePanel` 默认 260px + `HomeView.vue:740` `height:100vh` + `.chat-layout` position:absolute | 390 屏内容区仅 338px → **聊天区只剩 66px**（360 屏 36px） | ❌ 重排为"列表→详情"两级 |
| **图谱页布局** | 左导航 240 + 右知识库 320 + 边距 24 = **584px** vs 338px；PathBoard 再叠 360px | 重叠 100% | ❌ 改为全屏图 + 底部抽屉 |
| **设置页布局** | `settings.css:20-21` 固定 `width:200px`+`min-width:200px`；`settings.css:248` `grid-template-columns: minmax(0,1fr) 220px` | 390 屏内容区 **58px** | ❌ 改为单列列表 |
| **桌面交互** | 右键菜单 4 处（ContextMenu / ForceGraph `:278` contextmenu / GraphSubjectBar / KbPanel）、:hover 约 96 行 / 31 文件、拖拽 7 处（SidePanel×2 / FloatingBall×3 / engine×2）、键盘 15 处、`window.innerWidth` 20+ 处、`d3.drag().filter(e=>e.button===0)` | 触屏：长按菜单、`:active` 反馈、命中区 ≥44px、无 hover | ❌ 逐处改 |
| **视口/安全区** | `index.html:8` 仅 `width=device-width`；**`safe-area-inset` / `100dvh` / `visualViewport` 全项目 0 命中** | `viewport-fit=cover` + `env(safe-area-inset-*)` + `100dvh` + visualViewport 监听 | ❌ 新增 |
| **Element Plus** | 5 个 el-dialog（LoginDialog 380px、KbPanel 320、LectureImport 300、ModelsPane 480、LecturePane）+ 1 个 el-drawer（QuizView 420px）+ ElMessage 约 100 处 | dialog 在 360 屏溢出（EP 不给 max-width）；drawer 420 > 375 | ⚠️ 加全局 `max-width` + 改 bottom sheet |
| **移动端断点开关** | `matchMedia` 全项目仅 3 处，且都是 `prefers-color-scheme`（`utils/theme.js:29,74`）；**无 isMobile / useMediaQuery** | 需要一个 `useIsMobile()` | ❌ 新增 |
| **构建目标** | `vite.config.js` 无 `build.target`（Vite 8 默认 ≈Chrome 107+）；`dist` 单块 HomeView 685KB | 低端 WebView（可能 Chrome 80 级）白屏 | ⚠️ 设 `target:'chrome87'` + 路由级懒加载 |

### 1.3 规模实数量级

| 指标 | 值 |
|---|---|
| `frontend/src` | 73 文件 / **19,912 行** |
| `.vue` | **41 个 / 14,366 行**（views 7、components 33） |
| views 逐个 | LectureImportView 997（已挂载）、QuizView 825、HomeView 810、CollectorView 615（**未挂载**）、DashboardView 370、SettingsView 124、KbView 54 |
| 最大组件 | UserProfile 836、NodeDetail 829、KbPanel 703、PathBoard 707、ForceGraph 567、MessageBubble 481、FloatingBall 591 |
| 构建产物 | `frontend/dist` 73 文件 / **4.61MB** |
| 前端测试 | 仅 2 文件 442 行（`chatStore.lifecycle.test.js`、`graphTree.test.js`），无组件/E2E |

---

## 2. 难度判定：分层给数

| 层 | 工作 | 难度 | 人日（乐观/悲观） | 说明 |
|---|---|---|---|---|
| A. 打包壳 | Capacitor 工程 + dist 进包 + 签名 | **低** | 1–2 | 本机 JDK21（JBR 21.0.10）、AGP 8.7.3、Gradle 8.13 缓存齐；唯一缺口 `platforms;android-36`（只有 35 / 36.1）→ 内测走 Cap 7（compile 35）即可 |
| B. 移动 Web 适配（同域部署） | 断点 token + 4 tab + 显式移动视图层 | **高（人力活）** | 12–18 | 全部工时在这里，见 §5 |
| C. 后端改造 | 同域部署：**0**；壳内（origin `https://localhost`）：CORS 白名单 + API baseURL | **低** | 0–2 | 后者才需要，见 §3.1 |
| D. ima 对标增量 | 结构化引用卡 + 错题筛选 + 范围限定 | **中** | 3–4 | 数据源已就位，见 §4.2 |
| E. 真机回归 | 小米/华为真机 + 低端机 | **中** | 3–4 | 必须留 |
| **合计（移动 Web 路线）** | | | **19–28** | 与前端审计 20–34 的口径吻合，压缩掉"给 40 个桌面组件加断点"的错误路径 |
| F. 上架合规 | 软著（60 日内）+ APP 备案（3–20 工作日）+ 含 AI 对话的算法备案/安全评估 | **硬阻塞** | 2–3 **个月** | **本次不做**，见 §6 |

**结论：工程上可行，且在 10 周内能做完；唯一不可压缩的是 E（真机回归）和 F（合规，直接砍掉）。**

---

## 3. 关键技术判断（决定了工时差一个量级）

### 3.1 同域 vs 壳内：这是全案最大的分叉

| | 移动 Web（同域 nginx） | Capacitor 壳（`https://localhost`） |
|---|---|---|
| 后端改动 | **0**（`/api` 相对路径照用，CORS 不触发） | CORS 加壳 origin（`config.py:84-86` 现仅 `http://localhost:5173`，且 `.env` 里 `CORS_ALLOW_ORIGINS` 被注释）+ 注入 `VITE_API_BASE` |
| 评委获取 | **扫码即开，0 安装** | 需装 APK（微信内不能装 / 需扫码下载 / 信任未知来源） |
| 更新 | 发版即生效 | 改前端必须重新发版 |
| 原生能力 | 浏览器能力 | 文件/分享/返回键/状态栏 |
| 演示风险 | 低 | **中高**（安装、权限、机型） |

→ **先做左列，右列作为"我们也有 App"的加分展示**。两者共享同一份 `dist`，先做 Web 再套壳是增量 1–2 人日，反过来不是。

### 3.2 "单库条件渲染" 不等于 "给桌面组件逐个加 @media"

前端审计推荐"单库"。这句话容易被误读成给 41 个 `.vue` 逐个加断点（那是 20–34 人日的悲观路径，且必然漏）。正确落地是**单仓库、双视图层**：

```
frontend/src/
  api/  stores/  utils/        ← 与端无关，一份，不动
  views/ components/           ← 桌面，一行不改（保证不回退）
  mobile/
    MobileApp.vue              ← 唯一移动入口（4 tab 壳 + 二级页栈）
    views/   MChatView / MKbView / MGraphView / MMeView
    components/ MMessageBubble / MSourceCard / MInputArea /
                MNodeSheet / MKbList / MPathList / ...
    mobile.css                 ← 断点 token + safe-area + 44px 命中区
```

**接入点（已核对代码，改动量极小）**：`App.vue` 只有 3 行（`<router-view />`），`router/index.js` 只有 2 条路由（`/` → 懒加载 `HomeView`，加 catch-all 重定向）。所以只在**路由层选组件**：

```js
const isMobile = () => matchMedia('(max-width: 768px)').matches || location.hash.startsWith('#/m')
{ path: '/', component: () => isMobile() ? import('../mobile/MobileApp.vue') : import('../views/HomeView.vue') }
```

登录墙不用重做：`HomeView.onMounted → authStore.ensureSession()`（`HomeView.vue:174`）触发 mandatory `LoginDialog`；`MobileApp` 照抄同一段即可（authStore 本就是共享的）。桌面代码零风险，移动端可独立迭代，**store/api/markdown/crypto 只有一份**（避开「SSE 事件三处同步」变六处的坑）。

### 3.3 图谱：渲染复用，交互重写

- 复用：`forceGraphEngine.js`（SVG + d3.zoom，原生支持触屏；`scaleExtent(0.08,5)`）。
- 重写触屏触点（约 200–300 行）：节点 hover 高亮 → tap 选中；`contextmenu` 建节点 → 长按 500ms；`d3.drag().filter(e=>e.button===0)` → 触屏拖拽；连线 `mousemove.drawing` → pointer 事件；空态文案"右键空白区域"→"长按空白区域"。
- 布局默认改用 **mindmap / brace**（依赖树比力导向好点），三布局已在 `graphLayout.js` 实现。

### 3.4 其他必修的运行时坑（清单，均已在代码中定位）

| # | 坑 | 位置 | 修法 |
|---|---|---|---|
| 1 | 软键盘遮挡输入框 | `HomeView.vue:740` `100vh`；全项目无 `visualViewport` | `100dvh` + `visualViewport` 监听 + `adjustResize` |
| 2 | 刘海/安全区 | `index.html:8` 无 `viewport-fit=cover` | 加 meta + `env(safe-area-inset-*)` |
| 3 | 上传 50MB 整文件进内存 | `kb.py:47` `MAX_UPLOAD_BYTES=50MB`；`:137` `await file.read()` | 移动端限 **10–20MB**（前端拦截）+ 后端流式落盘（可选） |
| 4 | 导出/原件下载 | `api/quiz.js:83-86`、`KbPanel.vue:358-377` 走 `createObjectURL` + `<a download>` / `window.open('')` | 移动浏览器尚可；壳内必坏 → 后端一次性签名 URL 交系统浏览器 |
| 5 | PDF 内联预览 | `KbTextPreviewDialog.vue` 用 `<iframe src=objectURL>` | 移动端改 pdf.js 或"跳原件 HTTPS URL"（同域可直接打开） |
| 6 | 大文件预览 OOM | exceljs ≈900KB + docx-preview ≈170KB 动态包 | 预览前按大小拦截（≤10MB），失败降级"下载后看" |
| 7 | 低端 WebView 白屏 | `vite.config.js` 无 `build.target` | `build.target:'chrome87'` + 路由级懒加载 |
| 8 | 首屏 4.6MB | HomeView 685KB 单块 | 拆分：移动端视图独立 chunk |
| 9 | 切后台断流 | SSE 常驻 + 对话流 | onResume 重连 + `/knowledge/notices/pending` 补拉（已实现） |
| 10 | el-dialog 溢出 | LoginDialog 380px / QuizView drawer 420px | 全局 `max-width: calc(100vw - 32px)` + drawer 改 bottom sheet |
| 11 | 系统大字体撑破布局 | 全项目未处理 | CSS 固定字号 + 必要时 `textZoom` |

---

## 4. 对标 ima：抄什么、不抄什么

### 4.1 IA 映射（我们的 6 入口 → ima 的 4 tab）

| ima 4 tab | 我们对应 | 桌面现状 | 移动端处理 |
|---|---|---|---|
| **ima（对话）** | 对话 | `viewMode='chat'` | 主 tab；侧栏会话列表 → 抽屉 |
| **知识库** | 知识库 + 视频导入 | `kb` + `lecture` | 主 tab；视频导入降级为"+"里的一个入口 |
| **笔记** | **无对应** → 用「学习」替代 | — | 主 tab = 图谱 + 学习路径 + 进度（**我们的差异化就在这里，不假装做笔记**） |
| **我** | 设置 + 学习进度（dashboard） | `settings` + `dashboard` | 主 tab；出题/错题/模型/主题都收进来 |

> 关键取舍：**不做"笔记"**。ima 的笔记是纯文本富媒体，我们从零做只能做成半成品；把「学习」放第 3 个 tab，答辩时正好用它讲"ima 只能存，我们能算"。

### 4.2 ima 的 P0 六项 × 我们的现状

| ima P0 | 我们有没有 | 缺口 | 成本 |
|---|---|---|---|
| 多格式上传+解析+OCR | ✅ 有（parsers 注册表 10+ 格式，RapidOCR） | 移动端选文件/拍照 UI | 1 人日 |
| 知识库列表与资料管理 | ✅ 有（`/kb/tree` 等 20 端点） | 树 → 卡片列表 | 1–2 人日 |
| 基于知识库问答 + **引用溯源** | ⚠️ 半有 | **数据结构已就位**：`RagHit{source, content, score, heading, path, metadata}`（`rag_pipeline/types.py`），但 `rag_search` 只拼成纯文本喂给 LLM，**没有结构化 citations 回传前端**。补：tool_result 里序列化 `(source, path, heading)` → SSE 事件 → `MSourceCard` 引用卡 → 点开原件 | **2–3 人日（ROI 最高）** |
| 范围限定 `@库 #标签` | ⚠️ 半有 | kb 是**文件夹树无标签**；`/kb/search` 已支持 `node_id` 限定范围，`/kb/context` 已能收集目录范围 → 移动端做"选文件夹 = 限定范围"即可 | 1–2 人日 |
| 笔记摘录 | ❌ 无 | 砍（见 §4.1） | 0 |
| **AI 解读 = 出题判分** | ✅ **强于 ima** | ima 只生成一份小测验；我们有 生成→判分→`attempts.correct`→`mastery_events`→掌握度→路径重排 的闭环 | 1 人日（只剩 UI） |

**额外一条低成本高感知**：错题本。`attempts` 表已有 `correct(0/1)`、`question_id`、`created_at` → 只需 `GET /quiz/questions?only_wrong=1`（新增一个查询参数）+ 一个列表页。

### 4.3 差异化叙事（答辩口径）

| 维度 | ima | AI-tutor |
|---|---|---|
| 知识组织 | 扁平：库 → 资料 → 标签 | **图谱：节点 / 边 / 掌握度** |
| 回答 | 带引用 | 带引用 + **可落到图谱节点**（点进节点看小节正文） |
| 练习 | AI 解读生成一份小测验 | **出题 → 判分 → 掌握度事件 → 学习路径重排** |
| 个性化 | copilot 记忆（黑盒） | 画像 + 掌握度事件（**可回放：这 20 分哪来的**） |
| 你的进度 | 无 | 拓扑排序的下一站 |

---

## 5. 方案：移动端视图清单与工时

### 5.1 骨架（2–3 人日）

- `frontend/src/mobile/` 目录 + `MobileApp.vue`
- `useIsMobile()`（`matchMedia` + `#/m/` 前缀双判据）+ `main.js` 分流
- `mobile.css`：断点 token（`--bp-m: 768px`）、safe-area、`100dvh`、`:active` 反馈、44px 命中区、`overscroll-behavior-y: contain`
- 底部 TabBar（4 tab）+ 二级页栈（`v-if` + 返回键/滑动返回）
- `index.html` 加 `viewport-fit=cover`
- `vite.config.js` 加 `build.target:'chrome87'`

### 5.2 四个 tab（12–18 人日）

| 视图 | 复用 | 新写 | 人日 |
|---|---|---|---|
| **M 对话** | chatStore、`sendMessageStream`、`renderMarkdown`、工具/thinking 渲染 | MMessageBubble、MInputArea（键盘适配）、会话抽屉、**MSourceCard 引用卡** | 4–5 |
| **M 知识库** | `/kb/*` 20 端点、上传逻辑 | 卡片列表 + 上传/拍照 + 预览（pdf.js 或跳原件 URL） | 3–4 |
| **M 学习（图）** | `forceGraphEngine`、`graphLayout`、`/knowledge/learning-path`、`/next-to-learn` | 全屏图 + 触屏触点重写 + 节点底部抽屉（正文/小节/出题）+ 路径列表 | 4–5 |
| **M 我** | `/knowledge/stats`、`/quiz/*`、settings 各 Pane | 进度卡 + 错题列表 + 模型/主题设置（单列） | 3–4 |

### 5.3 套壳（1–2 人日，可选）

`@capacitor/core|cli|android` + `@capacitor/app`（返回键）+ `@capacitor/keyboard` + `@capacitor/status-bar|splash-screen`；`androidScheme:'https'` → **记得加 CORS**；锁竖屏；`windowSoftInputMode=adjustResize`。

### 5.4 排期（10 周，1 主力 + 1 协助的口径）

| 周 | 内容 | 里程碑（可验） |
|---|---|---|
| W1 | 骨架 + 断点体系 + 4 tab 壳 + 真机跑通登录 | 手机上能登录、能切 4 个空 tab |
| W2–3 | M 对话（含引用卡 V1：只显示来源文件名） | 手机上流式对话 + 工具过程可见 |
| W4–5 | M 知识库（上传/列表/预览） | 手机上传 PDF → 解析 → 列表出现 |
| W5–7 | M 学习（图谱 + 触点 + 节点抽屉 + 路径） | 手机双指缩放图谱、点节点出抽屉、出题被判分 |
| W7–8 | M 我（进度/错题/设置） | 掌握度变化在"我"页可见 |
| W8–9 | 引用卡 V2（点回原文）+ 错题筛选 + 范围限定 | 点引用跳原件；错题能重练 |
| W9 | Capacitor 壳 + APK 内测包 | APK 装上能跑（加分展示） |
| W10 | 真机回归（小米/华为/低端机）+ 8 分钟演示录制 + 合规口径 | 扫码可开、录屏成片 |

**降级预案（若只剩 3 周）**：只做骨架 + M 对话 + M 学习（图谱只读）+ 引用卡 V1 = **能演的 MVP**（约 9–11 人日）；知识库/我 tab 走桌面版，答辩时用"移动端 V1"口径说明。

---

## 6. 上架可行性（结论：本次不做，但要有据可答）

| 门槛 | 周期 | 判断 |
|---|---|---|
| 《计算机软件著作权证书》 | 官方审查 **60 日内** | 1 月前拿到 = 现在就要提交，且只有一个应用名额 |
| **APP 备案**（接入商代备 → 管局） | **3–20 工作日** | 未备案不得上架 |
| **AI 算法备案 + 安全评估**（含 AI 对话必查） | 数周–数月 | **本项目最大合规成本** |
| 教育类 APP 备案 | 并行 | 需教育资质 |
| ICP 备案（域名/80/443） | 已有服务器则已有 | 前置 |

**结论**：1 月前完成上架在关键路径上不成立 → **不上架**，走「扫码网页 + APK 内测包」。

**答辩口径（照这个说，别说错）**：
- ✅「工程上已具备上架条件：现成 APK 打包链路、包体约 6–7MB、HTTPS 部署方案已就绪；合规侧需软著 + APP 备案 + AI 算法备案，关键路径 2–3 个月」
- ⛔ 不说「已上架」「已备案」「已有真实用户」（`docs/架构/演示口径与操作卡.md` §1.6 红线）
- ⛔ 不说并发数、准确率/召回率、CI 绿

---

## 7. 风险与对策

| # | 风险 | 影响 | 对策 |
|---|---|---|---|
| 1 | 视图重写是纯人力活，无法压缩 | 延期 | **W1 结束即冻结范围**；P0 只做 4 tab，笔记/广场/生图/播客直接列"知道但不做" |
| 2 | 评委扫码打不开 = 0 分 | **致命** | 演示前一日走 `deploy/tutor-demo/` 全流程；备 APK 离线包 + 手机热点 + 本地 `start.ps1` 兜底 |
| 3 | 低端 WebView 白屏 | 部分机型废 | `build.target:'chrome87'`；W1 就用真机（不是只有模拟器）验证 |
| 4 | 首屏 4.6MB，移动网慢 | 体验差 | 移动端视图独立 chunk + 路由懒加载；首屏只加载对话 |
| 5 | 软键盘/返回键/长按菜单三件套漏改 | 手感像"网页套壳" | 已列 §3.4 清单，W1 就一次性做完 |
| 6 | 桌面端被移动改造改坏 | 主场演示崩 | 桌面代码一行不改，`mobile/` 独立目录；回归 `npm run build` + 桌面 8 分钟脚本 |
| 7 | 工作量低估 | 赶工砍功能 | 每周一个"手机可验"里程碑；W10 前不接新需求 |

---

## 8. 下一步（待决策 3 项）

1. **确认主力人力**（决定 §5.4 排期是 10 周还是压缩到 MVP）：1 人 = 按上表；2 人并行 = 可提前 2–3 周完成并加"我"tab 的错题重练。
2. **确认是否要 APK**：只做移动 Web 可省 1–2 人日 + 演示风险，但失去"我们有 App"的展示分。
3. **确认首次真机验证时间**：建议 W1 内完成（一次性验掉白屏、软键盘、返回键、图谱触屏四个高风险项）。

---

## 附：三份调研的边界（诚实声明）

- 前端审计的 5 项"未验证"：d3-drag 触屏行为、WebView 键盘遮挡幅度、SVG 300+ 节点帧率、EL dialog 实际溢出、docx/excel 大文件耗时。
- Android 调研**未实跑 `npx cap add android` / `gradlew assembleDebug`**（任务禁止安装，且本会话 shell TLS 不可用）→ "可构建"是版本/缓存比对后的静态推断，需一次真跑确认。
- ima 调研的 UI 细节为**摘要级证据**（App Store 6 张截图未能读取），4 tab 结论有三条独立交叉验证但无截图铁证；会员价格与隐私条款未获取。
