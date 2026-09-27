# 采集模块 B3 收尾：网页正文 → Markdown 显示 方案

> 状态：**待评审，未实施**（本文只写方案，不改代码）
> 日期：2026-09-22（v2：需求已明确为「知识图谱节点直出」）
> 上游依据：`docs/教育资料采集模块_设计讨论.md`（决策 #5 正文抽取漏斗、§6.1 主流程、§8 不逆向对抗、§11 前端预览）
> 关联 TODO：`TODO_Collector.md` B3.1 / B3.2（现状见 §1）

> **⚠️ 版本说明（2026-09-22 晚，用户两次确认后的最终口径）**
> 需求：**双击知识图谱里的节点就能看到 Markdown；之前的东西保留不变，只添加。**
> - **主线 = §11「AI 抓取的网页 → 图谱节点」**（用户明确："抓取到的网页就是 **AI 一开始抓取到的**网页内容，变成 md"→ 数据源 = Agent 工具 `fetch_webpage`）。
> - §10（采集模块入库 → 建图谱节点）同属"图谱节点直出"，但数据源不同，**降级为备选第二调用方**。
> - §3（采集页 URL 预览弹窗）/ §4（已入库正文回看）为备选路线，本次默认不做。
> - 前端一律**零改动**（节点详情早已能渲染 MD，入口是**双击**——用户已确认）。
>
> **✅ §11 已按本方案实施完成（2026-09-22）**，实施记录见 §11.9。
> **✅ §4 的 P1（已入库正文回看）已于 2026-09-23 落地**，但改为知识库侧端点，实施记录见 §12。
> 下文 §1 的现状核实对三条路线都成立，保留。

---

## 1. 背景与现状核实（先纠正三处与文档不符的事实）

本次需求：**把抓取到的网页正文以 Markdown 形式在界面上显示出来**（可核对、可确认，再决定是否入库）。

先说三条已核实的现状（与 `TODO_Collector.md` 的勾选状态不一致，**代码已存在但 TODO 未更新**）：

| 事实 | 证据 |
|---|---|
| B3.1 题库导入**已实现**（TODO 未勾） | `backend/app/core/collector/adapters/dataset_quiz.py` 存在；`backend/tests/test_dataset_quiz_adapter.py` 存在（5.53 KB） |
| B3.2 网页正文抓取**已实现**（TODO 未勾） | `backend/app/core/collector/adapters/web_page.py`；`trafilatura>=1.8` / `readability-lxml>=0.8.1` 已在 `backend/requirements.txt`；测试 `backend/tests/test_web_page_adapter.py` |
| **但 web_page 从 API 到 UI 完全不可达** | `web_page.search()` 恒返回 `[]`（`web_page.py:152-154`），候选只能由 `candidate_from_url()` 构造（`web_page.py:168-188`），而该函数**全库零调用**（仅 `tests/test_web_page_adapter.py` 引用）→ 采集页永远搜不到、也输入不了网页类候选 |

即：**B3.2 的"取 MD"能力就绪，缺的是"给 URL 的入口" + "把 MD 显示出来"这两段**。本方案补的正是这两段。

其余关键现状（决定了方案落点）：

1. 正文抽取出口已是 Markdown：`web_page.py::extract_main_text()`（L96-106）→ trafilatura `output_format="markdown"` 主用，readability 后备产纯文本，**双失败返回 None**。
2. 前端已有统一 Markdown 渲染管线：`frontend/src/utils/markdown.js::renderMarkdown()`（marked + KaTeX + highlight.js + **DOMPurify 消毒**，L79-86），已用于 `MessageBubble.vue` / `NodeDetail.vue` / `UserProfile.vue`。采集页未使用。
3. 后端**没有任何"读正文"出口**：`backend/app/api/v1/collector.py` 只有 search / tasks / stats / stats-sources；`backend/app/api/v1/kb.py` 无文件内容读取端点（虽然底层 `kb_store.get_document_text(node_id)` 已存在，`kb_store.py:167-170`）。
4. 采集正文的真实落点：`kb.db documents.extract_text`（纯文本字段，MD 源文本经 `TextParser` 原样存入），关联键 `resources.file_node_id`（`store.py:73`）。`manager._ingest()`（`manager.py:251-287`）抓取后**不落原始 HTML**。
5. 采集页 `frontend/src/views/CollectorView.vue` 第 273 行文案写着"预览后勾选入库"，**但页面上没有任何预览**（只有 title/description/size），属"文案先行、功能缺失"。

---

## 2. 需求边界

> 注：§2–§9 描述的是**备选路线**（采集页预览 / 已入库正文回看）。主线需求见 §10。

### 2.1 做

- **P0**：用户粘贴 URL → 后端抓取 + 抽正文 → 返回 Markdown → 前端弹窗渲染显示 → 一键"加入候选并勾选" → 走既有入库流程。
- **P1**（配套）：已入库的采集文档回看正文（资源列表 + 内容读取端点，复用同一个预览弹窗）。

### 2.2 不做（明确写清，防后续膨胀）

| 不做 | 理由 |
|---|---|
| 落盘 / 持久化原始 HTML | 现状即不存；预览是只读探测，存 HTML 会引入体积与合规风险 |
| 预览结果落 `resources` 表 | 会污染采集覆盖率、来源排行、`UNIQUE(source_url)` 收养语义（详见 §3.2 决策 A） |
| 站内爬取 / 站点搜索通道 | 决策 §8「只用开放 API/白名单源，不逆向对抗」 |
| 预览内容可编辑 | 预览是核对，不是编辑器；要改内容应走知识库既有能力 |
| 给 `web_page` 补 `search()` | 保持"候选由用户 URL 给定"的既有决策 |
| 抽取失败的多级兜底 | 决策 #5 已定"双失败即弃"（YAGNI） |

---

## 3. P0 设计

### 3.0 链路总览

```
前端 CollectorView「粘贴网页链接」输入框
  → POST /api/v1/collector/preview { url, mode }        ← 新增（唯一后端改动点）
      → 授权/安全判定（复用 is_blocked_url / license_for_url / is_allowed，不新写校验）
      → WebPageAdapter.fetch(cand)                      ← 复用，零改动
          → CollectorHttp.fetch_text（UA/超时/重试/并发信号量已有）
          → extract_main_text → trafilatura Markdown → readability 后备 → None
      → 截断 + 组装响应（无任何写库副作用）
  → 前端弹窗 renderMarkdown(md) → v-html                 ← 复用统一渲染管线
  → 「加入候选并勾选」→ 复用既有 createCollectorTask 链路（零新增入库代码）
```

**新增文件**：无（后端只加一个端点 + 一个依赖注入点；前端加一个弹窗组件）。
**改动文件**：`backend/app/api/v1/collector.py`、`backend/app/core/error_codes.py`、`frontend/src/api/collector.js`、`frontend/src/views/CollectorView.vue`，新增 `frontend/src/components/CollectorPreviewDialog.vue`。

### 3.1 契约

```
POST /api/v1/collector/preview
body: { "url": "https://...", "mode": "personal" | "commercial" }

200 {
  "status": "ok",
  "candidate": {                       // 与既有 _cand_out 同形，可直接推入候选列表勾选
    "title": "...", "source_url": "...", "source": "web_page",
    "license_level": "L2", "description": "", "size_bytes": 1234
  },
  "markdown": "# 标题\n\n正文……",        // 截断后的 Markdown
  "chars": 12345,                      // 实际返回字符数
  "total_chars": 98765,                // 抽取出的完整字符数
  "truncated": true                    // chars < total_chars 时为 true
}

403  { "detail": "[E-COLL-003] 该链接指向内网/回环地址，已拒绝" }   // SSRF 拒绝
422  { "detail": "该站点授权等级 L3（personal 模式）不可采集" }       // 授权不可采
502  { "detail": "[E-COLL-003] 网页抓取或正文抽取失败" }             // 下载失败 / 双抽取失败
```

设计要点与理由：

1. **`candidate` 字段与 `_cand_out()`（`collector.py:85-94`）完全同形** → 前端把返回值直接 push 进 `candidates` 并 `selected`，入库路径（`createCollectorTask` → `manager.start_task`）**一行不改**。这是本方案能"薄"的关键。
2. **403 / 422 / 502 三分**：SSRF 是安全拒绝（403），授权是合规拒绝（422，语义对齐既有 `create_task` 的 422），抓取失败是上游故障（502）。超时与 4xx/网络错不细分——`CollectorHttp` 失败一律降级 `None`（`http.py:38-54`），要区分就得改公共模块，收益不值（YAGNI）。
3. **截断**：`PREVIEW_MAX_CHARS = 20_000`（模块级常量，便于测试与后续调）。不截断会让 200KB 网页的响应体拖垮前端渲染，也让 `marked` 卡顿。
4. **`chars` / `total_chars` 分开返回**：前端能显示"仅预览前 2 万字（共 9.9 万字）"，用户知道入库是全量的。

### 3.2 三个必须先定的决策

**决策 A：预览**不写任何库**（纯只读探测）。**
理由：① 预览是"可能被点很多次"的动作，写 `resources` 会产生假资源，污染 `GET /collector/stats` 的覆盖率（`collector.py:107-141` 按 `status in (indexed, duplicate)` 聚合）与 B3.3 来源排行；② `resources.source_url` 有 `UNIQUE` 约束（`store.py:79`），预览先插一行的话，真正入库时命中"URL 残留"分支被收养（`manager.start_task` 的既有语义），行为耦合、排查困难；③ `start_task` 里候选的 license/mode 校验（`collector.py:167-174`）在入库时**会再跑一遍**，预览不落库不影响合规防线。
代价：用户"预览→入库"会抓两次。可接受：抓取幂等，且入库侧有 `content_hash` 去重（`manager._ingest`），无非必要重复入库。

**决策 B：端点内自己组合判定函数，不给注册表里的 `web_page` 单例改 `mode`。**
`WebPageAdapter.mode` 是**构造参数**（`web_page.py:134-140`），而 `registry` 持有的是全局单例（`adapters/base.py:68`）。若预览时去改单例的 `mode`，并发请求会串模式（个人/商用互相覆盖），且污染后台采集任务。做法：`WebPageAdapter` 的 `name` 只用于注册，预览端点通过**依赖注入**拿到一个专用实例：

```python
def get_web_page_adapter() -> WebPageAdapter:
    """预览用适配器（独立实例，不共用 registry 单例的 mode 状态）；测试 override 为 MockTransport 版"""
    return WebPageAdapter(mode="personal")   # mode 由请求参数在端点内校验，不写死到实例
```

更简洁的等价做法：端点内直接用**公共纯函数**组合判定，再 `await` 一个惰性构造的 adapter 抓取。两个公共函数均已存在且是唯一实现：
- `is_blocked_url(url)`（`core/agent_tools/net_guard.py`，**全库唯一 SSRF 实现，禁止另写校验**）
- `license_for_url(url)` / `is_allowed(url, mode)`（`web_page.py:113-125`）

> 注意：**不要**为了预览去给 `registry` 注册第二个 `web_page` 实例（`register` 按名覆盖，`base.py:48-52`），会把后台采集用的那个顶掉。

**决策 C：正文抽取必须丢进线程池（`asyncio.to_thread`）。**
`trafilatura.extract` 与 `readability` 都是**同步 CPU 工作**，而本项目铁律是 `uvicorn --workers 1`（AGENTS.md §1）。直接在 async 端点里同步调用，会把整个事件循环卡住——同期进行中的对话 SSE、知识库检索请求全部停摆。做法：

```python
markdown = await asyncio.to_thread(extract_main_text, html)
```

**顺带登记同类既有问题**：`WebPageAdapter.fetch()`（`web_page.py:156-164`）在 async 里同步调 `extract_main_text`，`manager._ingest` 路径同样会阻塞 loop。它是既有已验证代码，**建议单独一笔小改**（同 `to_thread` 包一层 + 回归 `test_web_page_adapter.py`），不要混进本次预览提交。

### 3.3 后端实现清单

`backend/app/api/v1/collector.py` 追加：

- `class PreviewRequest(BaseModel)`：`url: str = Field(..., min_length=1)`、`mode: Mode = "personal"`。
- `def get_web_page_adapter()`：依赖注入点（默认实例；测试 `dependency_overrides` 替换为注入了 `httpx.MockTransport` 的实例 + `guard=lambda u: None`），与既有 `get_manager()` 同一套测试风格（见 `backend/tests/test_api_collector.py:10-13`）。
- `@router.post("/collector/preview")`：
  1. `blocked = is_blocked_url(url)` 非空 → 403；
  2. `license_for_url(url)` 不在 `ALLOWED_LICENSE[mode]` → 422；
  3. `cand = adapter.candidate_from_url(url)`；`None` → 422（兜底，理论上不会走到）；
  4. `md = await asyncio.to_thread(extract_main_text, html)`（`html` 来自 `adapter.fetch(cand)`），空 → 502；
  5. 截断 + 组装 `candidate/markdown/chars/total_chars/truncated`；
  6. 未预期异常 → `log_error(ErrorCode.COLL_PREVIEW_FAILED, ...)` + 502（禁止把内部异常原样透出）。
- 模块 docstring 的端点清单补一行（该文件 docstring 是端点级说明的既有惯例，`collector.py:1-24`）。

`backend/app/core/error_codes.py` 追加（对齐既有 `E-COLL-001/002` 编号风格，`error_codes.py:105-107`）：

```python
COLL_PREVIEW_FAILED = ("E-COLL-003", "网页抓取或正文抽取失败，请换一个链接或稍后重试")
```

- 异常信息以 `[E-XXX]` 开头并走 `log_error()`（AGENTS.md §2 硬约束）。
- `GET /collector/stats/sources` 等既有端点**不动**。

### 3.4 前端实现清单

`frontend/src/api/collector.js` 追加（并补文件头契约注释）：

```javascript
/** 抓取网页正文并转 Markdown 预览（只读，不落库）；超时给 60s（后端抓取 ~30s + 重试）*/
export const previewWebPage = (url, mode) =>
  apiClient.post('/api/v1/collector/preview', { url, mode }, { timeout: 60000 })
```

新增 `frontend/src/components/CollectorPreviewDialog.vue`（与 `MessageBubble.vue`/`NodeDetail.vue` 同级，符合现有扁平约定）：

- props：`visible`、`candidate`、`markdown`、`chars`、`total_chars`、`truncated`、`mode`；emits：`update:visible`、`adopt`（"加入候选并勾选"）。
- 正文渲染：**必须** `import { renderMarkdown } from '../utils/markdown.js'` + `v-html`。抓来的网页是不可信输入，这是本方案 XSS 风险最高点——`marked` 直接输出 HTML 而 DOMPurify 消毒在 `renderMarkdown` 内，**禁止绕过**。
- 头部显示：标题、`source_url`（可点开原页）、license 徽标（复用 `CollectorView.vue` 的 `LICENSE_META`）、`truncated` 提示（"仅显示前 N 字，共 M 字"）。
- 样式：正文区 `max-height: 60vh; overflow-y: auto`；配色只用既有 CSS 变量（`--color-*`）。
- 组件只负责展示与选择，不直接调 API、不自行入库。

`frontend/src/views/CollectorView.vue` 改动（三处，尽量小）：

1. 第一步卡片内（学科搜索行下方）加一行：URL 输入框 + 「抓取并预览」按钮 + 错误提示；沿用 `taskRunning` 禁用逻辑，避免采集进行中再发预览。
2. 新增预览态：`preview = ref(null)`、`previewLoading`；成功后 `preview.value = {...}` 打开弹窗；失败按 `e.response?.status` 分三类文案（403 内网拒绝 / 422 授权不可采 / 502 抓取失败）。
3. `adopt` 处理：把 `data.candidate` push 进 `candidates`（同一 `source_url` 去重）并 push 进 `selected`，关弹窗 → 用户随后点既有「开始采集」按钮。**不改 `handleStart`**。

> 前端无测试框架（`frontend/package.json` 只有 dev/build/preview，无 vitest）→ 前端验收按 §5.2 手工清单执行，不为此引入测试依赖。

---

## 4. P1 设计（可选，建议紧随 P0）

> **✅ 已于 2026-09-23 实施，但落点与本节的采集侧设计不同（改为知识库侧读正文）：见 §12。**

目标：已入库的采集文档也能"看正文 Markdown"（不止入库前预览），并把入口同时挂到知识库侧栏。

- `GET /collector/resources?status=&subject=&limit=` → 资源列表（字段：`id, title, source_url, source, license_level, subject, status, times_referenced, file_node_id, fetched_at`）。数据源：`store_mgr._get_store(user_id).list_resources(...)`（已存在，`store.py:136-149`）。
- `GET /collector/resources/{rid}/markdown` → `{title, source_url, license_level, markdown, chars, total_chars, truncated}`：
  1. `store.get_resource(rid)`，`file_node_id` 为空（未入库/失败）→ 404（注意：`resources` 表按用户库隔离，跨用户天然不可见）；
  2. 读正文：`kb_manager.get_document_text(user_id, node_id)` — 需在 `kb_manager.py` 加**一行薄委托**到既有 `kb_store.get_document_text`（`kb_store.py:167-170`），保持"读正文只有一个出口"；
  3. 同样截断（复用 `PREVIEW_MAX_CHARS`）。
- 前端：`KbPanel.vue` 点中文件节点后加「查看正文」；`CollectorView.vue` 任务完成后资源列表条目加同一入口；两者复用 P0 的 `CollectorPreviewDialog.vue`。
- 为什么放 P1 而非 P0：需要 2 个新端点 + `kb_manager` 薄委托 + 资源列表 UI，链条更长；P0 已能完整验证"网页 → MD → 显示"这一核心诉求。若评审认为"看已入库正文"才是本需求主诉求，则 P0/P1 对调顺序即可，技术方案不变。

---

## 5. 测试与验收

### 5.1 后端（离线，零真网，对齐项目既有手法）

新增 `backend/tests/test_api_collector_preview.py`（FastAPI `TestClient` + `dependency_overrides`，模式抄 `test_api_collector.py`）：

| 用例 | 构造 | 断言 |
|---|---|---|
| HTML → MD 成功 | `MockTransport` 返回含 `<h1>/<p>/<ul>` 的 HTML（trafilatura 已装即走主路径） | 200；`markdown` 含 `# `/`- `；`candidate.source == "web_page"`、`license_level == "L2"`（example.com 未登记） |
| SSRF 拒绝 | `guard=lambda u: "拒绝访问内网/回环地址"` | 403；detail 含拒绝原因 |
| L3 站点拒绝 | `monkeypatch.setitem(web_page._SITE_LICENSE, "paywall.com", "L3")` | 422 |
| 商用模式拦 L2 | `mode="commercial"` + example.com | 422 |
| 抓取失败 | `MockTransport` 返回 500 | 502；detail 以 `[E-COLL-003]` 开头 |
| 双抽取失败 | `MockTransport` 返回无正文 HTML + `monkeypatch` 让 `extract_main_text` 返回 `None`（或直接把 trafilatura/readability 置为不可用） | 502 |
| 超长截断 | HTML 正文 > `PREVIEW_MAX_CHARS` | 200；`truncated is True`；`len(markdown) <= PREVIEW_MAX_CHARS`；`total_chars > chars` |
| 空 URL | `{"url": ""}` | 422（pydantic `min_length=1`） |
| **无副作用** | 预览成功后查该用户 `resources` 表 | `list_resources()` 为空（决策 A 的回归锁） |

不重复测 adapter 层：抽取漏斗已由 `test_web_page_adapter.py` 覆盖，本文件只测端点编排。

### 5.2 手工验收（前端无测试框架）

1. 起后端（cwd=`backend`：`venv\Scripts\python.exe -m uvicorn app.main:app --port 8000 --workers 1`）+ 前端（cwd=`frontend`：`npm run dev`）。
2. 采集页粘贴一个未登记 L2 站点 URL → 抓取并预览 → 弹窗正常渲染标题/列表/代码块/公式（取一个带 LaTeX 的页面验证 KaTeX）。
3. 弹窗显示 license 徽标 = `L2 个人合理使用`；切到商用模式（设置 → 资源与版权）再预览同一 URL → 提示不可采，`422`。
4. 点「加入候选并勾选」→ 候选列表出现该条目且已勾选 → 开始采集 → 任务完成。
5. 知识库侧栏出现 `自动采集/L2/<学科>/<标题>.md`（可顺带验证 P1）。
6. **阻塞回归**：预览进行中，同时在对话页发一条消息，应正常流式返回（验证 `to_thread` 生效；若卡住说明抽取仍在 loop 里跑）。

### 5.3 回归命令（提交前必贴输出）

```bash
backend/venv/Scripts/python.exe -m pytest backend/tests -q -m "not llm_api"
```

基线口径：**2026-09-22 实测 `798 passed, 7 deselected, 258 warnings in 36.79s`**（AGENTS.md §6 里记的 `711 passed, 7 deselected` 是 2026-09-15 旧值，已过期；多会话并发写，动手前请重跑确认）。改完对比"新增用例全绿 + 既有用例数不减少、无转红"。

---

## 6. 实施顺序（按可独立提交的点拆）

| 步 | 内容 | 提交范围 |
|---|---|---|
| S1 | `error_codes.py` 加 `COLL_PREVIEW_FAILED` | 单文件 |
| S2 | `collector.py` 加 `get_web_page_adapter()` + `POST /collector/preview`（先写 `test_api_collector_preview.py`，TDD 顺序：测试 → 实现） | 2 文件（端点 + 测试） |
| S3 | 前端 `collector.js` + `CollectorPreviewDialog.vue` + `CollectorView.vue` 接线 | 3 文件（前端自成一批） |
| S4 | （可选小改）`web_page.fetch` 的 `to_thread` 包裹 + 既有测试回归 | 1 文件 + 测试 |
| S5 | 文档同步：`TODO_Collector.md` 新增 B3.4 条目并勾选 B3.1/B3.2（代码已在）；`docs/教育资料采集模块_设计讨论.md` §10 决策表补 2 条（预览不落库 / 抽取走线程池） | 文档 |

> 中大型改动 + 安全路径（SSRF、非可信输入渲染）→ 按 AGENTS.md §3 走 `test-driven-development`，不做"先写实现再补测试"。

---

## 7. 风险与对策

| 风险 | 级别 | 对策 |
|---|---|---|
| 抓来的网页内容经 `v-html` 注入 XSS | **高** | 只走 `renderMarkdown`（内含 DOMPurify）；组件评审时把"无裸 `v-html`"写进检查项 |
| 同步抽取阻塞单 worker 事件循环 | **高** | `asyncio.to_thread`（决策 C）+ 手工验收第 6 步主动验证 |
| 预览被当成"批量灌入库"的旁路 | 中 | 端点不写库（决策 A）+ 无副作用回归用例；入库仍走 `create_task` 的 license 复核 |
| SSRF 校验被新写一份 | 中 | 只用 `net_guard.is_blocked_url`（AGENTS.md「永不简化」项）；测试注入 `guard` 免 DNS |
| 交互式请求最坏等 ~90s（30s 超时 × 3 次尝试） | 中 | 预览用独立 `CollectorHttp(retries=1, timeout=15)`；前端 60s 超时 + loading 态 |
| 大页面响应体过大 | 中 | 截断 2 万字 + `truncated` 标记 |
| 多 AI 会话并发写同一仓库 | 中 | 提交按路径限定（`collector.py` / 前端三文件 / 文档三批），禁 `git add -A`（AGENTS.md §1） |

---

## 8. 待确认（不阻塞开工，按括号内默认可先做）

1. **预览入口位置**：默认放**采集页**（对齐设计文档 §6.1"候选列表(…预览)"与 §11）。若你更想让"已入库正文回看"在**知识库侧栏**为主入口，则 P0/P1 对调（§4 末段）。
2. **P1 是否本批做**：默认**本批做**（否则"抓取到的网页"只能在入库前看一次）。
3. **截断阈值**：默认 `20_000` 字符；需要更长直接调常量 + 用例。

---

## 9. 改动清单与污染隔离论证

> 本节的用途：开工前先自证"只动该动的"，评审时可直接对着逐条核对。

### 9.1 结论

本次是**纯增量**改动：**新增** 1 个端点、1 个错误码、1 个依赖注入点、1 个前端组件 + 1 个 API 封装 + 若干模板/状态片段；**不新增/不修改任何共享数据结构，不改任何既有公共函数签名或返回值语义**。唯一触碰既有代码行为的是 S4（`web_page.fetch` 内一行包 `to_thread`，输入输出等价）。无 DB schema 变更、无表列变更、无提示词/工具注册表改动、无采集存储语义改动。

### 9.2 逐文件改动（P0）

| 文件 | 类型 | 具体改动 | 是否触碰既有代码路径 |
|---|---|---|---|
| `backend/app/core/error_codes.py` | 改 | `ErrorCode` 末尾追加成员 `COLL_PREVIEW_FAILED = ("E-COLL-003", …)` | 否（纯新增行，既有 `E-COLL-001/002` 不动） |
| `backend/app/api/v1/collector.py` | 改 | 追加 `PreviewRequest`、`get_web_page_adapter()`、`@router.post("/collector/preview")`；模块 docstring 补一行端点说明 | 否（既有 `search`/`create_task`/`get_task`/`cancel`/`stats`/`source_stats` 六个函数体零改动） |
| `backend/tests/test_api_collector_preview.py` | 新增 | 9 个离线用例（§5.1） | 否 |
| `frontend/src/api/collector.js` | 改 | 追加 `previewWebPage()` + 文件头契约注释 | 否（既有 6 个导出不动） |
| `frontend/src/components/CollectorPreviewDialog.vue` | 新增 | 展示型弹窗组件（props/emits，不调 API） | 否（新文件；未被 import 前对全局零影响） |
| `frontend/src/views/CollectorView.vue` | 改 | 新增 URL 输入区块 + `preview/previewLoading` 状态 + `adopt` 处理 + 弹窗挂载 | **局部**：只加 template 块与 script 变量/函数；既有 `handleSearch`/`handleStart`/`pollTask`/`stopPoll` 等函数体不改 |

### 9.3 逐文件改动（P1 与 S4）

| 文件 | 类型 | 具体改动 | 是否触碰既有代码路径 |
|---|---|---|---|
| `backend/app/api/v1/collector.py` | 改 | 再追加 `GET /collector/resources`、`GET /collector/resources/{rid}/markdown` | 否（同上，纯追加） |
| `backend/app/core/kb/kb_manager.py` | 改 | 追加一行薄委托 `get_document_text(user_id, node_id)` → 既有 `kb_store.get_document_text` | 否（新方法；既有 `upload_and_index`/`build_tree`/`delete_node` 等不动） |
| `frontend/src/components/KbPanel.vue` | 改 | 加「查看正文」按钮 + 复用预览弹窗 | **局部**：tree 渲染、上传/删除逻辑不动 |
| `backend/app/core/collector/adapters/web_page.py`（S4，可选） | 改 | `fetch()` 内一行：`await asyncio.to_thread(extract_main_text, html)` | **是**（行为等价：同输入同输出，仅换执行线程；须回归 `test_web_page_adapter.py` 4 个既有用例） |

### 9.4 为什么不会污染其他模块（四条论证）

1. **不碰任何共享状态与数据**：无 DB schema / 表列变更（AGENTS.md §1"加列必须同步 `_migrate()`"这条硬约束本次根本不触发）；不写 `resources`/`tasks`/`documents` 任何一行（预览不落库，§3.2 决策 A）→ 采集覆盖率（`collector.py::_coverage_out`）、B3.3 来源排行（`store.source_reference_stats`）、`UNIQUE(source_url)` 收养语义（`manager.start_task`）行为完全不变。
2. **不改既有函数签名与返回值**：`extract_main_text`、`candidate_from_url`、`license_for_url`、`is_allowed`、`is_blocked_url`、`list_resources`、`get_document_text` 一律**只被调用、不被修改**。S4 是唯一例外，且是"同语义换线程"。
3. **路由纯追加且不遮蔽既有路由**：新路径 `/collector/preview`、`/collector/resources/{rid}/markdown` 与既有的 `/collector/tasks/{id}`、`/collector/stats`、`/collector/stats/sources` 无静态段歧义。既有的 `test_api_existing_stats_not_shadowed`（`backend/tests/test_source_stats.py:327-332`）已经为这类"同前缀新增端点"立了保护网，本次新增自动落进去。
4. **前端零共享改动**：`utils/markdown.js` 只被调用、不被修改（`MessageBubble`/`NodeDetail`/`UserProfile` 三处渲染行为不变）；新组件单点引用（仅 `CollectorView.vue` import）；`CollectorView.vue` 仅被 `HomeView.vue:33,412` 引用 → 改动不外溢到对话、知识库、图谱页面。

### 9.5 改动禁区（**仅适用于备选路线**；主线禁区见 §10.7）

- ✗ 不得改 `core/llm/*`、`core/agent/*`、`core/agent_tools/*`（工具注册表 + 提示词"工具调用指南"是全自动生成链，动一处三份产物漂移）
- ✗ 不得改 `core/rag*`、`core/knowledge*`（备选路线不碰检索/图谱；**主线 §10 会新增 `ORIGIN_NOTES` 一个键 + 一个可选参数，属加法，理由见 §10.3/§10.4**）
- ✗ 不得改 `core/kb/kb_manager.py` 的既有方法（P1 只允许**新增**一行委托）
- ✗ 不得在采集侧另写 URL 安全校验（SSRF 唯一实现 = `agent_tools/net_guard.py::is_blocked_url`，AGENTS.md「永不简化」项）
- ✗ 不得给 `resources`/`tasks` 表加列（预览不落库是设计前提）
- ✗ 不得在 collector 之外的 `api/v1/*.py` 里加端点
- ✗ 不得改 `frontend/src/utils/markdown.js`（一旦改动即影响对话/图谱/画像三处渲染）

### 9.6 验证与回滚

- **基线（2026-09-22 实测）**：`798 passed, 7 deselected, 258 warnings in 36.79s`
- **判据**：新增用例全绿；**既有用例数不减少**（798 → 798+新增）；无既有用例转红；`-m llm_api` 用例集不受影响（不涉及）。
- **路由面**：起后端后 `http://localhost:8000/docs` 可见 3 个新端点，且既有 collector 端点响应结构逐字段不变。
- **前端面**：新 UI 只出现在「资源采集」页；对话页流式、知识库树、图谱详情、画像页行为与样式无变化。
- **回滚**：S1–S5 每步独立可 `git revert`，步间无依赖，无数据迁移需回退（因为不改 schema、不写新表）。
- **提交切分（路径限定，禁 `git add -A` / `commit -a`）**：
  1. `error_codes.py` + `api/v1/collector.py` + `tests/test_api_collector_preview.py`
  2. `frontend/src/api/collector.js` + `components/CollectorPreviewDialog.vue` + `views/CollectorView.vue`
  3. （S4）`adapters/web_page.py` + `test_web_page_adapter.py` 回归输出
  4. （P1）`api/v1/collector.py` + `core/kb/kb_manager.py` + `components/KbPanel.vue`
  5. 文档：`TODO_Collector.md` / `docs/教育资料采集模块_设计讨论.md` / 本文件

---

## 10. 【备选·第二调用方】采集模块入库 → 图谱节点直出

> 定位：与 §11 同一座桥（`create_node_from_webpage`）、**不同的数据源**（采集模块抓的维基/OI-wiki/网页）。
> 已确认的主线是 §11（AI 的 `fetch_webpage`），本节默认**不在本批做**，保留作为同一桥的第二调用方备选。

### 10.1 需求（用户原话 + 解读）

> "就是要在知识图谱里打开那个点就能看到 md，之前的东西**保留不变，只添加**。"

解读：抓到的网页正文要成为**知识图谱里的一个节点**（那个"点"），双击该节点，节点详情里直接看到抓取到的 Markdown 原文。既有图谱、节点、渲染、编辑等行为一律不动，只做**加法**。

### 10.2 现状核实：渲染链路本来就是通的，缺的是"采集 → 图谱"这一段

| 环节 | 现状 | 证据 |
|---|---|---|
| 图谱点开节点弹详情 | ✅ 已通（**双击**触发） | `ForceGraph.vue:503-508` 发 `node-click`/`node-dblclick`；`HomeView.vue:104-115` 只监听双击 → `chatStore.js:419-422` `GET /api/v1/knowledge/node/{id}` |
| 节点正文出口 | ✅ 已通：直读磁盘 MD **全文、无截断** | `api/v1/knowledge.py:128-170`（`kg.nodes_dir/{id}.md` → `content`） |
| 正文渲染为 MD | ✅ 已通（marked + KaTeX + highlight + DOMPurify，view/edit 双态） | `NodeDetail.vue:23, 41-43, 199, 205` |
| **采集 → 图谱** | ❌ **不存在** | 采集目录全目录 0 命中 `KnowledgeGraph`；`manager._ingest` 只调 `kb.upload_and_index`（`manager.py:285-287`）；`create_node_with_content` 的调用方只有 4 条且与采集无关（`knowledge_writer.py:191` / `graph_generator.py:445` / `knowledge.py:231` / `knowledge.py:521`） |
| KB → 图谱的间接通路 | ⚠️ 有，但不合适 | 只能用户在知识库页手动点「生成学科图谱」（`kb.py:161-193`）→ `graph_generator` LLM 提炼，每节点 content ≤300 字**讲解**（`graph_generator.py:101-103`），**不是网页原文** |
| 采集资源 ↔ 图谱节点映射字段 | ❌ 无 | 图谱 nodes 表无 `kb_node_id`/`source_url`（`knowledge_graph.py:91-105`）；`resources.file_node_id` 只连 KB |

**结论**：本需求 = 新增一条桥「采集入库成功 → 建一个图谱节点，正文 = 抓取到的 MD」。**前端零改动、图谱读取/渲染链路零改动**。

### 10.3 关键设计（5 条）

1. **新写路径，仍走唯一出口**：`KnowledgeGraph.create_node_with_content(node_data, content, origin="collect")`；`ORIGIN_NOTES` 新增一个键 `"collect": "由采集模块从网页抓取"`（`knowledge_graph.py:27-32`，纯新增键，`ORIGIN_DEFAULT` 仍为 `manual`）。
2. **节点 id 确定性 + 带 user_id**：`f"collect_{user_id}_{sha1(source_url)[:12]}"`。理由：① `nodes.id` 是**全局** TEXT 主键（AGENTS.md §1），只用 URL hash 会跨用户撞主键（`add_node` 会抛 `ValueError`，`knowledge_graph.py:707-708`）；② 确定性 id 让"重复采集同一 URL"天然幂等（先 `kg.get_node(nid)` 命中即返回），**因此不需要给 collector 表加任何列**。
3. **内容形态两态（沿用既有模板行为，不新增模板）**：`create_node_with_content` 对以 `#` 开头的 content 原样落盘（`knowledge_graph.py:755-756`），否则套模板头 `# {name}\n\n> {ORIGIN_NOTES[origin]}`。
   - **选项 A（最小，0 行 core 改动）**：接受两态（trafilatura 抽取的网页 MD 常以 `#` 开头 → 节点 MD 就是原文，没有"由采集模块抓取"标注）。
   - **选项 B（+2 行 core，推荐）**：`create_node_with_content` 追加可选参数 `source_note: str = ""`，在两种分支下都以脚注形式写入来源（`> 来源：{url}`）。默认值保证既有 4 条路径产物**字节级不变**——由既有用例 `test_skeleton_md_uses_single_template` / `test_full_markdown_written_verbatim`（`tests/test_node_write_paths.py:50-64`）锁死。
   - 无论 A/B：**禁止在桥接层自拼模板**（那是 AGENTS.md §2「MD 模板唯一来源 = ORIGIN_NOTES」的红线）。
4. **触发点**：`CollectorManager._ingest()` 在 `update_resource_status(res["id"], RESOURCE_INDEXED, ...)`（`manager.py:285-287`）**之后**追加一步；`CollectorManager.__init__` 增加可注入的 `kg_factory=None`（默认按 `user_id` 懒建 `KnowledgeGraph`，用毕 `close()`——AGENTS.md §1 要求"每次新建、用毕 close，别跨协程共享实例"）。
5. **图谱写入失败绝不影响采集**：整段 try/except + `logger.warning`（与 B3.3 引用计数"统计失败静默"同一策略）；写入用 `await asyncio.to_thread(...)`——sqlite + 文件写是同步阻塞，`--workers 1` 下不能占着事件循环（与 §3.2 决策 C 同一理由）。
   - **关键安全点**：节点变多会被 `graph_analyzer` 读进对话上下文，但它走 `get_node_content_preview(max_lines=30, max_chars=1000)`（`knowledge_graph.py:588-619`）→ 长网页正文**不会灌爆上下文**。

### 10.4 改动清单（全部是加法）

| 文件 | 类型 | 改动 | 是否影响既有行为 |
|---|---|---|---|
| `backend/app/core/knowledge_graph.py` | 改 | `ORIGIN_NOTES` 加一个键 `"collect"`；（选项 B）`create_node_with_content` 加可选参数 `source_note=""` + 两分支拼接 | 否（键为新增；参数有默认值，既有 4 条路径输出不变） |
| `backend/app/core/collector/graph_bridge.py` | **新增** | `build_node_data(...)`（确定性 id / name / tags / board / summary）+ `upsert_resource_node(kg, ...)`（幂等 upsert + 正文落盘） | 否（新文件） |
| `backend/app/core/collector/manager.py` | 改 | `__init__` 加 `kg_factory=None`；`_ingest` 末尾追加 3–5 行（`to_thread` + try/except） | **局部**：只在"入库成功"分支后追加；fetch / content_hash 去重 / 状态机 / cursor 全不动 |
| `backend/tests/test_collect_graph_bridge.py` | **新增** | 桥接层用例（见 §10.5） | 否 |
| `backend/tests/test_node_write_paths.py` | 改 | 按该文件既有"结构断言"补一条 `test_collect_bridge_origin`（新建第 5 条写路径 → 必须复用模板，origin 断言为 `"collect"`）；选项 B 需同步给其 monkeypatch lambda 加 `source_note=""` | 是（测试文件；**这正是 AGENTS.md §2 要求的落点**：加新写路径而不复用模板就会红） |
| 前端（`NodeDetail.vue` / `ForceGraph.vue` / `HomeView.vue`） | — | **零改动** | 否 |

### 10.5 测试（全部离线：不碰真网、不碰真 LLM、不碰真实 `data/`）

1. **幂等**：同 user + 同 URL 连续 upsert 两次 → 图谱只有一个节点，MD 不被第二次覆盖。
2. **确定性 id 与跨用户隔离**：同 user+URL → 同 id；不同 user + 同 URL → 两个不同 id（防全局主键冲突）。
3. **正文落盘**：节点 MD 含抓取原文片段；非 `#` 开头时含 `> 由采集模块从网页抓取`；以 `#` 开头时原样落盘（选项 B 下另断言来源脚注存在）。
4. **既有行为不变**：`test_node_write_paths.py` 的 4 条既有 origin 断言 + 两个模板行为用例全绿（证明模板头一字未改）。
5. **失败隔离**：注入一个必然抛异常的 `kg` → `manager._ingest` 仍把资源标为 `indexed`、任务不失败、异常不外抛。
6. **端到端（离线）**：伪 adapter + tmp_path KB + tmp_path 图谱 → 跑一次 `_ingest` → 读节点 MD 断言正文；图谱用 `KnowledgeGraph(user_id=..., data_dir=tmp_path)`（测试需先 `INSERT OR IGNORE INTO users`，见 `test_node_write_paths.py:24-31` 的现成写法）。

### 10.6 验收

1. 采集一条网页（或构造 resources 行走 `_ingest`）→ 图谱视图（`viewMode === 'graph'`）出现该节点（名字 = 网页标题）。
2. **双击**该节点 → 弹窗正文区显示**格式化后的** Markdown（标题/列表/代码块/公式）。
3. 既有节点行为不变（仍双击打开；`NodeDetail` 的编辑/保存/掌握度滑块照常工作）。
4. 回归：`pytest backend/tests -q -m "not llm_api"` → 基线 `798 passed` + 本次新增全绿。

### 10.7 本路线禁区（"只添加"的边界，越界即停）

- ✗ 不改 `NodeDetail.vue` / `ForceGraph.vue` / `HomeView.vue`（前端零改动）。若确实要让**单击**也弹详情，那是另一件事——d3 的 `click` 与 `dblclick` 会同时触发，需要防抖，**默认不做**。
- ✗ 不改既有 4 条写路径的调用与产物（`ORIGIN_DEFAULT` 仍 `manual`，模板头一字不改）。
- ✗ 不改 `resources`/`tasks` 表结构（幂等靠确定性 id，不靠新列）。
- ✗ 不改采集的失败/去重/断点续传语义（图谱写入异常必须吞掉 + warning）。
- ✗ 不在桥接层自拼 MD 模板（正文以 `#` 开头"原样落盘"是既有契约；要来源标注就走选项 B 的可选参数）。
- ✗ 不碰 `core/llm/*`、`core/agent/*`、`core/agent_tools/*`、`core/rag*`、既有检索链路。

### 10.8 与备选路线（§3/§4）的关系

- 本路线**不需要** `POST /collector/preview`，也**不需要**"读 KB 正文"端点；两者可以后续独立叠加（一个管"入库前先看"，一个管"图谱里回看"），互不冲突。
- 两条路线共用同一渲染管线（`utils/markdown.js::renderMarkdown`），但本路线**前端一行都不改**。

### 10.9 开放问题（给默认值，不阻塞开工）

1. **选项 A vs B**（是否把来源 URL 写进节点 MD）：默认 **B**（+2 行、可溯源）；若要求 core 零改动则取 A。
2. **节点激增**：一次采集任务可能入库几十条 → 图谱一次多几十个节点。默认**不加开关/上限**（YAGNI），用 `tags=["采集", <学科>, <适配器名>]` 便于识别与后续过滤；若你希望"只有显式勾选的资料才进图谱"，则把触发点从 `_ingest` 自动改为采集 API 的显式动作（`POST /collector/resources/{rid}/graph-node`），代价是多一次点击。

---

## 11. 【主线·已确认】AI 抓取的网页 → 图谱节点（`fetch_webpage` 落盘为 MD）

### 11.1 需求确认

> 用户原话："是双击，抓取到的网页就是 **AI 一开始抓取到的**网页内容，变成 md。"

→ 数据源**不是采集模块**，而是 **Agent 在对话里调用 `fetch_webpage` 抓到的网页正文**。期望：这份正文以 Markdown 形式存档，成为知识图谱里的一个节点，**双击**即可看到。既有行为（工具返回、截断、图谱渲染、节点编辑）全部保留，只做**追加**。

### 11.2 现状核实（`fetch_webpage`）

文件：`backend/app/core/agent_tools/tools/fetch_webpage.py`（135 行）

| 事项 | 现状 | 行号 |
|---|---|---|
| 抓取 | 同步 `httpx.Client` GET，10s 超时 | `L27, L77-79` |
| 转换 | `_html_to_text()`：正则剥 `script/style/nav/footer/…` + 全部标签 + 实体解码 → **纯文本**（**不是 Markdown**） | `L38-55` |
| 返回 | 截断到 `max_chars`（默认 3000，上限 20000）+ "已截断"提示 | `L70, L95-96` |
| 落盘 | **只读不落盘**（`tools/README.md:23` 同口径）→ 会话结束内容即丢 | 模块 docstring `L1-7` |
| 失败 | 回填友好文本、不抛异常（工具层约定） | `L99-107`（约定见 `dispatch.py:6-8`） |
| 执行位置 | handler 同步 `(args, kg)`；异步分发用 `asyncio.to_thread` 包住 → **handler 内落盘不会阻塞事件循环** | `L131-132` + `dispatch.py:109` |
| 测试现状 | 无独立测试文件；全库仅 `test_agent_loop.py:187-197` 把它当"慢工具"做超时护栏 | — |

### 11.3 设计（5 条）

1. **只追加副作用，既有返回一字不改**：模型看到的仍是 `_html_to_text` 文本 + 同样的 `max_chars` 截断。落盘用的 Markdown 走**另一条**转换——复用 `collector/adapters/web_page.py::extract_main_text()`（trafilatura `output_format="markdown"` 主用 → readability 后备，**全库唯一网页正文抽取实现**，`trafilatura` 已在 `requirements.txt`）。
   - ⚠️ **import 方向是本方案唯一结构性权衡**：`web_page.py:26` 已经 import 了 `agent_tools.net_guard`（collector → agent_tools）；若在 `fetch_webpage.py` 顶层 import collector，会形成**包级环** → 必须在 handler 内部**惰性 import**（函数内 `from app.core.collector.adapters.web_page import extract_main_text`），并在注释写明原因。
   - 备选（不推荐）：把 `extract_main_text` 搬到中立模块——那是"搬既有代码"，违背"只添加"。
   - 代价：同一份 HTML 抽两次（正则纯文本 + trafilatura），多几百毫秒 CPU；已在 `to_thread` 线程内，可接受。
2. **节点写入走"AI 写图谱层"的新函数**：`core/knowledge_writer.py` 新增 `create_node_from_webpage(kg, url, title, content) -> str`。
   - **不复用 `create_node_from_ai`**：它带"同名并轨 + 存在即追加内容"语义（`knowledge_writer.py:79-81, 108-155`），会把网页节点误并到同名知识点上，且 origin 固定为 `ai`。
   - 确定性 id：`web_{kg.user_id}_{sha1(url)[:12]}` → 天然幂等；带 `user_id` 是因为 `nodes.id` 是**全局**主键（`knowledge_graph.py:707-708`，冲突会抛 `ValueError`）。
   - **存在即跳过**（不追加、不覆盖）：同一 URL 反复抓取不重写节点，也不与人类编辑打架。
   - 字段：`name` = 页面 `<title>`（取不到则用 URL）、`tags` = `["网页", <host>]`、`summary` = 来源 URL、`added_by="ai"`；`origin="web"`。
   - MD 仍由 `KnowledgeGraph.create_node_with_content()` 唯一生成（**不自拼模板**，AGENTS.md §2 红线）。
3. `knowledge_graph.py` 的 `ORIGIN_NOTES` 新增一个键：`"web": "由 AI 联网抓取"`（`knowledge_graph.py:27-32`，纯新增，`ORIGIN_DEFAULT` 仍为 `manual`）。
4. **失败隔离 + 体积上限**：
   - 整段落盘逻辑 try/except + `logger.warning` → **落盘失败绝不影响工具返回**；HTML→MD 双抽取都拿不到正文时**跳过落盘**，仍返回既有文本。
   - 落盘上限 `_NODE_MAX_CHARS = 50_000`（超出截断 + 末尾注明），防超长页面把节点 MD 撑到数 MB（前端 `marked` 渲染也吃力）。
5. **给模型看的文案保持不变**（关键取舍）：`DESCRIPTION` / `GUIDANCE`（`fetch_webpage.py:110-128`）里那句"只想临时看一眼用本工具；要长期留存资料请用 `download_resource`"**不改** —— 对模型而言分工语义没变（它仍不是"入库后能被 `rag_search` 检索"的正规途径），改了反而会让模型把它当 `download_resource` 用。**只更新给人看的注释/文档**（模块 docstring、`tools/README.md:23` 的"只读，不落盘"、`download_resource.py:3-7` 的分工说明），否则文档与行为漂移。

### 11.4 改动清单（全部加法；前端零改动）

| 文件 | 类型 | 改动 | 是否影响既有行为 |
|---|---|---|---|
| `backend/app/core/knowledge_writer.py` | 改 | 新增 `create_node_from_webpage()`，`__all__` 加一个名字 | 否（新函数） |
| `backend/app/core/knowledge_graph.py` | 改 | `ORIGIN_NOTES` 加 `"web"` 一个键 | 否（纯新增键） |
| `backend/app/core/agent_tools/tools/fetch_webpage.py` | 改 | handler 内追加"落盘"步骤（惰性 import + try/except）；模块 docstring 补一句 | **局部**：返回文本 / 截断 / 参数 / SPEC 全不变 |
| `backend/app/core/agent_tools/tools/README.md` | 改 | 表格 `fetch_webpage` 那行的"**只读**，不落盘" → "只读；抓到的正文同时存档为图谱节点" | 否（文档） |
| `backend/app/core/agent_tools/tools/download_resource.py` | 改 | 顶部 `L3-7` 分工说明同步一句 | 否（注释） |
| `backend/tests/test_webpage_node_bridge.py` | **新增** | 桥接 + 失败隔离 + 幂等用例（§11.5） | 否 |
| `backend/tests/test_node_write_paths.py` | 改 | 补第 5 条写路径的 origin 结构断言（`"web"`） | 是（测试侧，AGENTS.md §2 要求的落点） |
| 前端（`NodeDetail` / `ForceGraph` / `HomeView`） | — | **零改动**（双击 → 详情早已 `renderMarkdown`） | 否 |

### 11.5 测试（离线：不碰真网、不碰真 LLM、不碰真实 `data/`）

1. **正常**：monkeypatch `httpx.Client` 返回固定 HTML → 调 `handler({"url": …}, kg)` → 返回文本与既有逻辑一致（不变），且 `nodes_dir/web_*.md` 存在、含正文 Markdown、含 `> 由 AI 联网抓取`。
2. **幂等**：同 URL 调两次 → 只有一个节点，MD 未被追加/覆盖。
3. **跨用户**：同 URL、两个不同 `kg.user_id` → 两个不同 id（防全局主键冲突）。
4. **失败隔离**：注入必抛异常的 kg → handler 仍正常返回正文文本、不抛；仅留 warning。
5. **转换失败**：HTML 无正文（双抽取均 None）→ 不建节点，仍返回既有文本。
6. **既有契约不变**：`test_tools_registry.py`（spec 一致性）与 `test_agent_loop.py` 全绿——因为 `DESCRIPTION/PARAMETERS/SPEC` 未动。
7. `test_node_write_paths.py` 新增 `"web"` origin 断言 → 新写路径必须复用唯一模板，而不是内联第二份。

### 11.6 验收

1. 对话里让 AI 抓一个网页（或直接调 `handler`），随后打开图谱视图。
2. 出现新节点（名字 = 页面 title），**双击** → 详情正文区显示格式化 Markdown（标题/列表/代码/公式）。
3. 既有节点行为不变；`download_resource` 语义不变（仍入 KB、仍可被 `rag_search` 检索）。
4. 回归：`pytest backend/tests -q -m "not llm_api"` → 基线 `798 passed` + 本次新增全绿。

### 11.7 禁区（"只添加"的边界，越界即停）

- ✗ 不改 `fetch_webpage` 的返回文本、`max_chars` 默认值/上限、`_html_to_text`、`PARAMETERS`、`DESCRIPTION`、`GUIDANCE`（**给模型看的契约一律不动**）
- ✗ 不改 `create_node_from_ai` 的同名并轨/追加语义（网页节点不走它）
- ✗ 不在工具 handler 里自拼 MD 模板（仍走 `create_node_with_content`）
- ✗ 不给图谱 `nodes` 表加列（幂等靠确定性 id）
- ✗ 不改前端（单击不弹详情是既有设计，本次不动）
- ✗ 不做"每次抓取都刷新节点正文"（默认跳过；要刷新另开选项）
- ✗ 不把网页同时塞进 KB（那是 `download_resource` 的职责；用户只要求"图谱里能看到 MD"）

### 11.8 开放问题（给默认值，不阻塞开工）

1. 节点正文上限默认 **50,000 字符**（超出截断并注明）；要"全量原文"就调大该常量。
2. 是否把 §10 的采集模块也接成第二调用方（同一 `create_node_from_webpage` 加个 `source` 参数即可）——默认**本批不做**。
3. 给人看的注释/README 是否同步（默认**同步**，避免双源漂移）。

### 11.9 实施记录（2026-09-22，已完成）

| 文件 | 实际改动 |
|---|---|
| `backend/app/core/knowledge_graph.py` | `ORIGIN_NOTES` 新增 `"web": "由 AI 联网抓取"`（+1 行） |
| `backend/app/core/knowledge_writer.py` | 新增 `_web_node_id()` / `create_node_from_webpage()`；`_WEB_NODE_MAX_CHARS = 50_000`、`_WEB_NODE_NAME_MAX_LEN = 120`；`__all__` 加名字 |
| `backend/app/core/agent_tools/tools/fetch_webpage.py` | `fetch_webpage(url, max_chars, kg=None)` 追加存档步骤；新增 `_html_title()` / `_archive_webpage()`（**惰性 import** 避包级环）；handler 传 `kg=kg`；模块 docstring 同步 |
| `backend/app/core/agent_tools/tools/README.md` | `fetch_webpage` 那行改为"只读返回；正文同时存档为图谱节点" |
| `backend/app/core/agent_tools/tools/download_resource.py` | 分工说明同步（网页存档**不进知识库、不被 rag_search 检索**） |
| `backend/tests/test_webpage_node_bridge.py` | **新增 10 例**：落盘字段/来源标注、原样落盘（`#` 开头）、幂等、跨用户不撞主键、超长截断、空 URL、端到端存档 + 返回文本不变、不传 kg 零副作用、图谱写库失败不影响返回、抽取失败不建节点 |
| `backend/tests/test_node_write_paths.py` | 补第五条第写路径 origin 断言 `test_webpage_archive_origin`（`origin == "web"`），docstring 四条→五条 |

**验证证据**：
- `pytest backend/tests/test_webpage_node_bridge.py backend/tests/test_node_write_paths.py -q` → `19 passed`
- `pytest backend/tests -q -m "not llm_api"` → **`809 passed, 7 deselected`**（基线 798 + 新增 11，**无既有用例转红**）
- `python -c "import app.main"` → 应用正常导入；`KG_TOOLS` 中 `fetch_webpage` 仍在（无环导入、spec 未被破坏）
- 未改动前端任何文件；`frontend/vite.config.js`、`start.ps1`、`start.cmd` 的改动属**其他会话的既有 WIP**，本次未触碰。

**已知边界（保留而不修的既有设计）**：
- 网页节点不做同名并轨，也不做 LLM 学科判定 → 学科派生为 `「网页」`（`tags = ["网页", <host>]`）；
- 同 URL 第二次抓取**跳过**（不刷新正文）——要"刷新"另开接口；
- `extract_main_text` 在 `to_thread` 工作线程内被调用（`dispatch.py:109` 已包住同步 handler），不阻塞事件循环。

### 11.10 补充实施（2026-09-22 晚）：联网搜索后**自动**存档结果页

**为什么补**：实测发现断点在"搜"而不是"存"——AI 联网走的是 `mcp__websearch__web_search`，
它只返回**标题/链接/摘要**，从不打开结果页；而 `fetch_webpage` 要模型主动决定才会调。
于是"AI 联网了"却没有任何正文可存档 → 图谱里当然没有网页节点。
（排查证据：真实图谱库 608 节点中 `web_*` 为 0，磁盘无 `web_*.md`；debug 日志里的 fetch_webpage 痕迹是我跑测试留下的。）

**做法（纯追加）**：新增能力层 `core/agent_tools/web_archive.py`，让"HTML → Markdown → 图谱节点"只有一份实现，被两条路径共用：

| 文件 | 改动 |
|---|---|
| `core/agent_tools/web_archive.py` | **新增**：`page_urls_from_search_text()`（从搜索结果文本抽结果页 URL，去重+截断）、`archive_html()`（HTML→MD→节点）、`fetch_and_archive()`（下载+存档，SSRF 用 `net_guard` 唯一实现）、`archive_search_results()`、`schedule_archive_from_search()`（daemon 线程 + 另建 kg + 用毕 close） |
| `core/agent_tools/mcp_host.py` | `run_mcp_tool(..., kg=None)` 新增 kg 形参；`web_search` 返回成功后 → `_schedule_search_archive(text, kg)`；handler 透传 kg。`kg=None` 时语义与从前完全一致（既有测试 `test_mcp_web_search.py::test_execute_kg_tool_routes_to_mcp` 就是这条路径） |
| `core/agent_tools/tools/fetch_webpage.py` | `_archive_webpage` 改为调用 `web_archive.archive_html`（去掉本地重复的 title/抽取/落盘逻辑） |
| `core/config.py` | 新增 `web_archive_max_pages: int = 2`（`.env` 的 `WEB_ARCHIVE_MAX_PAGES`，**0 = 关闭自动存档**） |
| `core/knowledge_graph.py` | `ORIGIN_NOTES["web"]` 措辞改为 `"由 AI 联网抓取"`（两条路径共用，不再只提 fetch_webpage） |
| `tests/test_web_archive.py` | **新增 16 例**：URL 抽取/去重/上限/关闭开关、archive_html 成功与抽不出正文、SSRF 拒绝**不发请求**、非 200 / 非 HTML 静默跳过、后台线程逐个存档、宿主接线（kg=None 不触发、非 websearch 前缀不触发） |

**行为要点**：存档在 **daemon 线程**里做（不拖慢本轮回答）、单页失败不影响其它页、失败一律 warning 不抛；每次搜索最多抓 `WEB_ARCHIVE_MAX_PAGES`（默认 2）个结果页，同 URL 幂等跳过。

**验证证据（真实网络，落临时目录）**：`run_mcp_tool("mcp__websearch__web_search", …)`
→ 搜索返回正常文本；后台抓取后临时图谱里出现 `web_*.md` 节点，正文是结果页原文 Markdown，
头部为 `# <网页标题>` + `> 由 AI 联网抓取`。

**回归**：`pytest backend/tests -q -m "not llm_api"` → **`825 passed, 7 deselected`**（基线 798 + 11 + 16）。

---

## 12. 【已实施】P1 正文预览（2026-09-23）：知识库节点 → 看正文

> 用户口径：截图里那些 `自动采集/L0/<学科>/x.md`（采集入库的百科条目）在界面上看不到正文 → **要加预览**。

### 12.1 与 §4 设计的偏差（仅此一处）

§4 原打算走**采集侧**（`GET /collector/resources` + `GET /collector/resources/{rid}/markdown`，经 `resources.file_node_id` 映射到 KB 节点）。实施时改为**直接从知识库侧读**：

| | §4 原设计 | 实际实施 |
|---|---|---|
| 端点 | `GET /collector/resources/{rid}/markdown` | `GET /kb/node/{node_id}/text` |
| 入参 | 采集资源 id（前端要先去采集页拿列表） | KB 目录树节点 id（前端手上就有） |
| 覆盖面 | 只有采集资源 | **采集 + 手动上传**的全部 KB 文件 |
| 改动面 | 2 个端点 + 新的资源列表 UI | 1 个端点 + 1 个弹窗组件 + `KbPanel` 两个入口 |

理由：用户要看的入口是**知识库左栏**，那里的节点 id 本身就是 KB `node_id`；绕道采集资源表既多一跳，又覆盖不到手动上传的文件。§4 里"采集页资源列表条目"这一半**未做**（采集页当前没有资源列表 UI，另开一项）。

### 12.2 实际改动（纯加法）

| 文件 | 改动 |
|---|---|
| `backend/app/core/kb/kb_manager.py` | 新增 `get_document_text(user_id, node_id)` —— **读正文唯一出口**的薄委托 |
| `backend/app/api/v1/kb.py` | `KB_PREVIEW_MAX_CHARS = 20_000` + `GET /kb/node/{node_id}/text`（不存在 404 / 是文件夹 400 / 无正文 404） |
| `backend/tests/test_kb_node_text_api.py` | **新增 8 例**：正常读取、超长截断、文件夹 400、不存在 404、同 id 跨用户不串号、空正文 404、未认证 401、薄委托 |
| `frontend/src/api/kb.js` | 新增 `getKbNodeText(nodeId)` |
| `frontend/src/components/KbTextPreviewDialog.vue` | **新增**：只读预览弹窗（走 `renderMarkdown` + DOMPurify，禁止绕过） |
| `frontend/src/components/KbPanel.vue` | 文件节点加「查看正文」按钮 + **双击文件**亦可打开；弹窗挂载 + 失败提示 |

**契约**（`GET /kb/node/{id}/text`）：`{ status, node_id, name, markdown, chars, total_chars, truncated }`；`chars < total_chars` 即"看到的不是全文"，弹窗顶部提示"仅显示前 N 字"。

### 12.3 实施中被测试纠正的一处（记录以免重犯）

原以为要防"跨用户读别人正文"，故在委托层加了 `node["user_id"] != user_id` 校验。用例一跑就红，原因是**该场景在本项目不存在**：`KbManager._get_store(user_id)` 指向该用户自己的 `kb.db`，node_id 也在各自库里独立编号（USER 与 OTHER 的首个节点都是 `1`）。那段校验是**永不可达的死代码**，已按 §4 原意退回"一行薄委托"；用户隔离由**分库**保证，用例改成断言"同 id 不串号"。

### 12.4 验证证据（2026-09-23）

- `pytest backend/tests/test_kb_node_text_api.py -q` → **8 passed**
- `pytest backend/tests -q -m "not llm_api"` → **867 passed, 7 deselected**（基线 859 + 本次 8，无既有用例转红）
- `npm run build`（cwd=`frontend`）→ **✓ built in 6.75s**（SFC 编译通过）
- 真实库只读核对：`KbManager(Path('backend/data/kb')).get_document_text(1, 13)` → **9471 字符**，首行「在計算機科學中，**樹**（）…」，`name = 树 (数据结构).md`（对齐截图里那棵树节点）

### 12.5 仍没做

| 未做 | 说明 |
|---|---|
| 采集页资源列表 + 该入口预览 | §4 的另一半，需要新的资源列表 UI，另开一项 |
| 采集前 URL 预览（P0 / §3） | 要联网抓取 + 新端点，独立一步 |
| 正文清洗（`__NOTOC__` / 空列表项 / 简繁混排等维基残留） | 属解析侧质量项；本次只做"看得见"，不改入库文本 |
