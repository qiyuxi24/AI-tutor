# 单条对话「过程账本 + 用量底栏」前端改造 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> ⚠️ 本项目 `AGENTS.md §1` 规定 **不主动 commit/push**；`§3` 规定写代码前加载 `ponytail`、数据层/并发路径走 `test-driven-development`、宣称完成前走 `verification-before-completion`。本计划所有任务**最后一步是验证，不是提交**；skill 里的 "frequent commits" 在本仓库不适用（`AGENTS.md §3` 冲突裁决：外部 skill 与本文件冲突时以本文件为准）。

**Goal:** 把每一轮 AI 回复的 token 用量 / 缓存命中做成**所有对话共用**的底部组件，并把「思考 / 工具调用 / 网页搜索」从折叠堆叠改成逐行纵向排列的账本，同时去掉聊天成功态的绿色。

**Architecture:** 数据侧只做两点最小改动——`AGENT_DONE` 事件带上 `TokenUsage.to_dict()`（三处 SSE 白名单/分发现成，无需改动），并把同一份用量随 `append_turn` 落进 `conversations.messages` 这个 JSON blob（零迁移，保证刷新 / `adoptRemote` / 换端后底栏不消失）。展示侧新建纯逻辑 helper（可单测，不引 `@vue/test-utils`）+ 一个共用 `<TurnFooter>`，把 `MessageBubble.vue` 的工具/思考区重构成统一竖线账本，成功态改用聊天局部 `--turn-*` token（墨色），**不动全局 `--color-success`**（图谱掌握度/知识库命中是另一套语义）。

**Tech Stack:** Vue 3 `<script setup>` + 原生 CSS 变量（`frontend/src/style.css` 是设计 token SSOT）、Vitest（jsdom，**不挂载 SFC**）、Python 3 + FastAPI + pytest、`TokenUsage` dataclass。

**Spec:** 本计划 §0「设计系统」（随计划同行，不另起设计文档）；用户原始需求见会话 m00146；现状取证见 §1。

---

## Global Constraints（逐条抄自 AGENTS.md，每个任务默认继承）

- **不主动 commit/push**；本仓库常有多个 AI 会话并发写 → 真要提交必须**路径限定**（禁 `git add -A` / `commit -a`），内容与预期不符先查 mtime。
- `uvicorn` 必须 `--workers 1`（EventBus 用户队列依赖单进程）。
- **SSE 事件改动三处同步**：`core/agent/events.py` 发射 → `chat_service._consume_agent_events` 白名单（if/elif，**无 else**，不加就静默丢弃）→ `frontend/src/api/index.js` 回调分发；运行类事件**必带 run_id**；请求内事件只能走 `run_agent_loop(event_queue=...)` 的请求私有队列，**禁止经 `event_bus`**。
- **对话历史的真相在服务端**：`/chat*` 按 `conv_id` 从 `conversations.db` 组装历史；前端 `localStorage` 只是离线缓存，`persist()` **不再上行**（`sync` 只做首次迁移）；删除是墓碑（`deleted_at`）。
- `conversations.messages` 是**单个 JSON TEXT 数组** → 加字段**零迁移**；但 `adoptRemote` 会用库里的 `messages` **整段替换**本地副本，所以用量必须服务端也落一份。
- 记录型库（`agent_runs`/`llm_usage`/`agent_debug_logs`）加列必须写进 `store._COLUMN_MIGRATIONS` 由 `records.ensure_columns` 补。**本计划不碰任何表结构**，规避全部迁移风险。
- 后端 Python 只用 `backend/venv/Scripts/python.exe`；pytest 口径 `pytest backend/tests -q -m "not llm_api"` → 基线 **1983 collected / 7 deselected**（1982 passed + 1 skipped，90.75s）；前端 `npm test` 基线 **47 passed (3 files)**（本会话实测 50，WIP +3）。
- 前端 `frontend/vitest.config.js` **`environment:'jsdom'`、不挂载 SFC、无 `plugins:[vue()]`、无 `@vue/test-utils`** → 纯逻辑抽成 `src/utils/*.js` 单测；组件改动只靠构建 + 手工验收。
- 设计 token SSOT = `frontend/src/style.css`（`:root` :26-196、`html.dark` :199-291、`prefers-reduced-motion` :413-419；`frontend/src/main.js:13` 引入，`utils/theme.js` 切 `html.dark`）。`--color-success: #10b981`（浅）/ `#34d399`（dark）；`--color-green` 是它的别名（:137）。
- `AGENTS.md §5` 记「`frontend/` 桌面 Web 本届冻结（只修不扩）」——**但用户对话中的显式指令 > 本文件**（`AGENTS.md` 冲突裁决），本任务按用户指令执行；实现时在回复里标注这一张力。
- **不改** `frontend-admin/`（运维后台）、`backend-admin/`、图谱组件的绿色（`ForceGraph.vue` / `PathBoard.vue` / `NodeDetail.vue` 的 `.dot-mastered` 等属"掌握度"语义，动它会误伤）。
- ⚠️ 并发 WIP：**行号会漂移**（本会话 `MessageBubble.vue` 从 1073 → 1057 行，`chat_service.py` 的 `_format_agent_sse` 从 :1341 → :1513）。所有定位**以函数/类名为准**，行号只作近似参考；动手前重新 `read` 一次。

---

## §0 设计系统（Spec）

**Brief**：AI 家教桌面 Web 的**单条 AI 回复**。主角是「这一轮 AI 做了什么、花了多少」，不是营销页。受众是正在学习的学生；主任务 = 过程可回溯、成本可感知，**且不抢正文**。

### Color（克制账本，6 个命名值；全部是 `style.css` 既有 token 的局部别名）

| 角色 | 浅色 | dark | 用途 |
|---|---|---|---|
| `--turn-ink` | `#111827` | `#e5e7eb` | 步骤标签、**成功态**、主数字 |
| `--turn-muted` | `#6b7280` | `#9ca3af` | 次要标签、时间、参数摘要 |
| `--turn-rule` | `#e5e7eb` | `#313244` | 左侧竖线、底栏分隔线（**1px**，不是 2px） |
| `--turn-wash` | `#f3f4f6` | `rgba(255,255,255,.06)` | 行 hover，**无阴影** |
| `--turn-accent` | `#8b5cf6`（品牌紫，沿用） | 同 | **只**给「进行中」spinner 与底栏「缓存命中」那一个数字 |
| `--turn-error` | `#ef4444` | `#f87171` | **只**给真失败，绝不给成功 |

**为什么不用绿色**：用户明确说「每次成功给你之后绿色并不好看」。且**不重定义全局 `--color-success`** —— 它在图谱掌握度、知识库命中里是另一套语义，改了会误伤；新建 chat 局部 token 把「成功」与「绿色」解耦。这是本决策的「因为」，不是随意换色。

### Type

- **不加字体**（YAGNI，不引 webfont）。标签 `--font-size-xs: 12px` / weight 500 / **sentence case**，禁 ALL-CAPS eyebrow、禁标题上方多余标签、禁单字变色强调。
- **不用 monospace**：整排 mono 小字是 AI 生成页的通用默认，且 `Consolas` 与中文混排会掉字。改用 `font-variant-numeric: tabular-nums` 让数字跨多条消息竖排对齐（**真实用途**：一列 token 数要能扫读）。
- 正文行宽沿用 `.message-list` 既有 760px 居中，不改动。

### Layout（现状 vs 目标，全左对齐）

现状 —— 折叠总览 + 点击才展开，过程「叠在一块」：
```
[ ✓ 已完成   12.3s                              ▾ ]  ← 一行总览，过程藏在折叠里
   (点开后才出现 .tool-steps 竖列；思考是另一个独立盒子)
   ────────────────────────────────
   正文 markdown …
   [复制] [时间]                                      ← 悬停才出现；无任何用量
```
目标 —— 统一纵向账本 + 常驻底栏：
```
│ ·  思考        先看用户问的是二重积分…                 ← 一行一事，同一条 1px 竖线串起
│ ◎  联网搜索    「二重积分换序」              1.2s    ← 网页搜索独立行、专属字形
│ •  知识库检索  「换序法」                  0.3s
│ •  添加节点    「换序法」                  0.1s
│
│ 正文 markdown …（过程不再压住正文）
│ [复制] [时间]                                      ← 既有悬停工具栏，保持不动
│ ───────────────────────────────────────────────  ← 1px 分隔
│ 1.3k tokens │ 缓存命中 63% │ 3 次工具 │ 1.6s      ← 最底部，共用组件，恒定可见
```
底栏**左对齐、全宽、无卡片、无圆角、无阴影**；过程块共用一条 `border-left: 1px solid var(--turn-rule)`。

### Principles

1. **一行一事**：思考 / 每个工具 / 每次网页搜索 = 独立纵行，按发生顺序排下来（参考截图的**结构**即此）。
2. **状态靠字形 + 文案，不靠红绿灯**：完成 = 墨色小点 `•`（网页类 `◎`）、进行中 = 紫色环、失败 = `✕`（`--turn-error`）。色盲可辨，且天然去掉绿色勾。
3. **用量下沉**：底栏是**所有 AI 消息共用**的一个组件，恒定在最底部，不与正文争抢。
4. **一处出彩**：只有「缓存命中」数字用 `--turn-accent`；其余全墨/灰/线。
5. **质量地板**：真 `<button>` + `:focus-visible`、`prefers-reduced-motion` 关掉动画、窄屏不横向溢出（`flex-wrap` + `min-width:0` + `text-overflow:ellipsis`）。

### 对照通用默认后的修订（frontend-design 第二遍；说明改了什么、为什么）

- 差点沿用「SaaS 卡片套件 + 统一圆角 + 统一软阴影 + 亮绿勾」—— 现状就偏这个，用户已否。**改**：去掉折叠胶囊/卡片/阴影，改 1px 竖线账本，成功改墨点。
- 差点给底栏加 ALL-CAPS 小标签、`A · B · C` 中点串、`→` 尾巴、整排 mono。**改**：中文句首大写标签 + 1px 竖线分隔，数字只用 `tabular-nums`。
- 差点照抄截图近黑底（`#090B16`）+ 深蓝选中行（`#2E5181`）。**改**：沿用项目既有明暗主题，不引入第三套底色；截图只取「逐行排列」的结构与「全图 GREEN_TOTAL=0」的配色结论。
- 差点用渐变/进度条表现缓存命中率。**改**：一个紫色数字，不加条、不加渐变（Chanel：出门前摘掉一件配饰）。

---

## §1 现状取证（只读结论，实现前已逐条核对代码）

- **「tok 计数 + 缓存命中」在主前端并不存在**：`frontend/src` 穷举 `token_usage|prompt_tokens|cached_tokens|cache_hit|agent/usage` 零命中。用户以为存在的那个面板只在**独立运维后台** `frontend-admin/src/views/Usage.vue`（`GET /api/v1/admin/usage`，全站 7 天时间窗聚合，按 kind/model/day/user 分组，`rateColor()` 用 `#f56c6c`/`#e6a23c`/`#67c23a`）。主系统 `GET /api/v1/agent/usage` 也是**用户级时间窗聚合**（`backend/app/core/llm/usage.py:122-168`，顶层 `{since_hours, group_by, total, groups}`），**非 per-turn**。→ 本任务是**新建 per-message 展示**，不是「搬一个已有组件」。
- **真值已在内存但未上 SSE**：`AgentRunResult`（`backend/app/core/agent/loop.py:158-170`）含 `token_usage: TokenUsage` / `context_tokens` / `total_llm_calls` / `stop_reason`；`_build_result`（:360-392）是**三处终止点（自然结束 / 预算耗尽 / 中途失败）共用的唯一收口**。`TokenUsage.to_dict()`（`backend/app/core/token_counter.py:154-161`）= `{prompt_tokens, completion_tokens, total_tokens, cached_tokens, reasoning_tokens}`；`cached_tokens` 源自供应商 `usage.prompt_tokens_details.cached_tokens`（:196-199）。
- **当前 `AGENT_DONE` 不带任何 token 数**：`loop.py:385-386` `emitter.emit(AGENT_DONE, rounds=len(rounds), total_llm_calls=llm_calls, stop_reason=stop_reason)`；`AgentEventEmitter.emit`（`events.py:126-134`）注入 `{"type":..., "run_id":..., **data}`。→ 精确 payload = `{type, run_id, rounds, total_llm_calls, stop_reason}`。
- **SSE 三处其实都不用改**：`chat_service.py::_format_agent_sse`（:1513-1528）对 `agent_done` 走 `json.dumps(event, ensure_ascii=False)` **整 dict 原样透传**；`frontend/src/api/index.js:263-264` 已 `onAgentDone?.(parsed)`；`chatStore.js:1512-1517` 已收口（**忽略参数**）。→ 只差「事件多带一个 `usage` 字段」+「`onAgentDone` 消费它」。
- **刷新后不丢的关键**：`chat_service.py::_persist_turn(user_id, conv_id, user_text, result)` 收到的就是完整 `AgentRunResult`（**不需要 run_id**），`ConversationStore.append_turn`（`conversation_store.py:326-378`）把 assistant 写成 `{"role":"assistant","content":...,"ts":秒}` + 可选 `tools`，`messages` 是单个 JSON TEXT 数组。`_normalize_messages`（:76-92）只归一化 `ts`，`ensure_conversation`（:380-403）只剥 `failed` → **挂 `usage` 零迁移且全程不被清洗掉**。前端 `adoptRemote`（`chatStore.js:232-282`）在 `remoteAt > localAt` 时 `local.messages = r.messages` 整段替换 → 服务端不落 usage，底栏刷新即消失。**故必须双写**。
- **当前工具渲染**：`MessageBubble.vue` AI 分支（:254-497）依次 = `.tool-panel`（`:263-376`，`.tool-header` 折叠总览 + `v-show="toolsExpanded"` 的 `.tool-steps` 竖列）、`.thinking-panel`（:379-389，独立折叠盒子）、`.markdown-body`、`.msg-toolbar`（:429-495，仅复制 + `.msg-time`，无用量）。`toolsExpanded` 默认 `!!running || errorCount>0` → **全部成功时是折叠的**，这正是「叠在一块」的体感来源。`TOOL_NAMES`（:83-102）含 `mcp__websearch__web_search:'联网搜索'`；**联网搜索没有专属 UI**，结果只是 `.tool-detail-text` 里 120 字摘要。
- **绿色位置**：`MessageBubble.vue:618-620`（`.tool-head-icon.is-done`）、`:702-704`（`.tool-dot.is-done`）、`:863-865`（`.msg-action-btn.is-done`）全用 `var(--color-success)`；`QuizCard.vue:176,192` 用 `var(--color-green, #22a06b)`。聊天组件内无硬编码绿 hex。
- **参考截图**：`C:\Users\34239\AppData\Local\Temp\modlens-dsh-paste\p-83CnPW\paste.png`（1132×583）经 OCR 确认是 **DSH 本会话的 agent 执行日志视图**（深色），每一步（思考/查找文件/运行命令/查看子智能体/上下文注入）是一条独立纵向行、逐行排列；配色 `#090B16`/`#131C37`/`#2E5181`/`#ADB2B8`，**全图绿色像素 = 0**。→ 用户要的是「排列下来」的**结构**；「绿色不好看」指本项目 UI 而非截图。
- ⚠️ **歧义（已在 §7 列为待确认）**：截图是正面参照还是反面参照；「很雷霆」疑为「很雷同」或「很吓人」。本计划按「结构正面、配色不照抄」+「既不模板化也不用力过猛」处理。

---

## §2 File Structure（先定边界，再拆任务）

**Create**
- `frontend/src/utils/turnUsage.js` — 纯函数：把后端 `usage` dict 归一化成展示模型 + 格式化。单一职责，**不依赖 Vue**，Vitest 可直接测。
- `frontend/src/utils/turnUsage.test.js` — 上者的单测（9 例）。
- `frontend/src/components/TurnFooter.vue` — **所有对话共用**的底部用量组件。只消费 `turnUsage.js`，无 store 依赖。

**Modify**
- `backend/app/core/agent/loop.py::_build_result` — `AGENT_DONE` 多带 `usage`。
- `backend/app/services/chat_service.py` — 新增 `_usage_summary(result)`；`_persist_turn` 传 `usage=`。
- `backend/app/core/conversation_store.py::append_turn` — 新增 `usage` 形参，attach 到 assistant dict。
- `frontend/src/stores/chatStore.js::send` 的 `onAgentDone` 回调 — 把 `usage` patch 到占位消息。
- `frontend/src/style.css` — `:root` 对话区新增 `--turn-*` 6 个别名。
- `frontend/src/components/MessageBubble.vue` — 工具/思考区重构为 `.turn-process` 竖线账本；删除折叠相关死代码；成功态换墨色；末尾挂 `<TurnFooter>`。
- `frontend/src/components/QuizCard.vue` — 2 处 `--color-green` → `--turn-done`。
- `backend/tests/test_agent_loop.py` — +1 例（AGENT_DONE 带 usage）。
- `backend/tests/test_conversation_authority.py` — +2 例（append_turn 落 usage / 无 usage 不写空对象）。
- `frontend/src/stores/chatStore.lifecycle.test.js` — +1 例（onAgentDone 落 usage 到消息）。

**明确不改**（写进计划防止执行者顺手改坏）
- `frontend/src/api/index.js`、`backend/app/services/chat_service.py::_format_agent_sse`、`backend/app/core/agent/events.py` —— 现成通路，无需改动。
- `frontend-admin/*`、`backend-admin/*`、图谱组件绿色、全局 `--color-success` / `--color-green` 定义、`conversations` 表结构、`agent_runs`/`llm_usage` 表结构。

**接口契约（跨任务唯一真值，实现者只看到自己那一任务）**
```
TokenUsage.to_dict() → { prompt_tokens:int, completion_tokens:int, total_tokens:int,
                         cached_tokens:int, reasoning_tokens:int }
AGENT_DONE payload   ← 上者作为 `usage` 键，外加既有 {type, run_id, rounds, total_llm_calls, stop_reason}
append_turn(..., usage: dict|None)  → assistant 消息多一个 `usage` 键（仅当 dict 非空）
message.usage        ← chatStore 从 onAgentDone(data).usage 写入 assistant 消息
normalizeUsage(raw)  → { total:int, cached:int, completion:int, hitRate:float } | null
formatTurnUsage(raw) → { tokensLabel:string, cachedLabel:string, hasCache:boolean } | null
TurnFooter props     → { usage:Object|null, toolCount:Number, durationMs:Number }
```

---

## Task 1: `AGENT_DONE` 带上 token 用量（后端 TDD）

**Files:**
- Modify: `backend/app/core/agent/loop.py::_build_result`（约 :360-392，以函数名为准）
- Test: `backend/tests/test_agent_loop.py`

**Interfaces:** Consumes `TokenUsage.to_dict()`（`backend/app/core/token_counter.py:154-161`）、`_CollectEmitter`（`test_agent_loop.py:385-393`）。Produces `AGENT_DONE` payload 多一个 `usage` 键。

- [ ] **Step 1: 写失败测试**（追加到 `test_agent_loop.py`，`test_stop_reason_reported_to_event` 之后）

```python
def test_agent_done_carries_usage(monkeypatch):
    """AGENT_DONE 带 token_usage 明细，供前端每条消息底栏渲染（三处 SSE 通路现成）。"""
    _install_fake_chat(monkeypatch, [_resp(_msg(content="答"))], [])
    fake = _CollectEmitter()

    result = asyncio.run(run_agent_loop("sys", [{"role": "user", "content": "x"}],
                                        kg=object(), emitter=fake))

    done = fake.events[-1]
    assert done[0] == AGENT_DONE
    assert done[1]["usage"] == result.token_usage.to_dict()
    assert done[1]["usage"]["total_tokens"] > 0
    assert done[1]["stop_reason"] == result.stop_reason   # 既有字段不能丢
```

- [ ] **Step 2: 跑，确认失败**

Run: `backend/venv/Scripts/python.exe -m pytest backend/tests/test_agent_loop.py::test_agent_done_carries_usage -q -m "not llm_api" -p no:cacheprovider --basetemp=.dsh_local/pytestrun/tmp`
Expected: FAIL — `KeyError: 'usage'`。

- [ ] **Step 3: 最小实现**（`loop.py::_build_result`，**只改 emit 一行**；`_build_result` 是三处终止点共用收口，一处覆盖全部路径）

```python
    emitter.emit(AGENT_DONE, rounds=len(rounds), total_llm_calls=llm_calls,
                 stop_reason=stop_reason, usage=token_usage.to_dict())
```

- [ ] **Step 4: 跑，确认通过 + 回归**

Run: `backend/venv/Scripts/python.exe -m pytest backend/tests/test_agent_loop.py -q -m "not llm_api" -p no:cacheprovider --basetemp=.dsh_local/pytestrun/tmp`
Expected: PASS（含既有 `test_stop_reason_reported_to_event`、`test_agent_emitter_silent_without_user` 等全绿）。

- [ ] **Step 5: 验证契约未破**（不提交）

Run: `backend/venv/Scripts/python.exe -m pytest backend/tests/test_event_bus.py backend/tests/test_event_contract.py -q -m "not llm_api" -p no:cacheprovider --basetemp=.dsh_local/pytestrun/tmp`
Expected: PASS —— 证明 `agent_done` 仍走请求私有队列、`run_id` 仍由 `emit()` 注入、未触碰 `event_bus` 白名单。按 `AGENTS.md §1` **不 commit**。

---

## Task 2: 服务端把用量落进 `conversations.messages`（零迁移，TDD）

**Files:**
- Modify: `backend/app/services/chat_service.py::_persist_turn`（约 :452-468）+ 新增 `_usage_summary`
- Modify: `backend/app/core/conversation_store.py::append_turn`（约 :326-378）
- Test: `backend/tests/test_conversation_authority.py`

**Interfaces:** Consumes Task 1 同源的 `AgentRunResult.token_usage`。Produces `assistant` 消息上的 `usage` 键；`_persist_turn` 收到的 `result` **本身就是 `AgentRunResult`，无需 run_id**。

- [ ] **Step 1: 写失败测试**（追加到 `test_conversation_authority.py`）

```python
def test_append_turn_persists_usage_on_assistant(store):
    """用量随 assistant 落进 messages JSON blob；刷新/换端 adoptRemote 后底栏仍在。"""
    store.append_turn(
        "c1", "问", "答",
        usage={"prompt_tokens": 1000, "completion_tokens": 284, "total_tokens": 1284,
               "cached_tokens": 630, "reasoning_tokens": 0},
    )
    msgs = store.get_conversation("c1")["messages"]
    assert msgs[1]["usage"]["cached_tokens"] == 630
    assert msgs[1]["usage"]["total_tokens"] == 1284
    assert "usage" not in msgs[0], "user 消息不该带用量"
    assert 1e9 < msgs[1]["ts"] < 1e11, "ts 仍须是秒"


def test_append_turn_without_usage_keeps_message_clean(store):
    """旧路径（无 usage）不得往 JSON blob 里塞空对象。"""
    store.append_turn("c1", "问", "答")
    assert "usage" not in store.get_conversation("c1")["messages"][1]
```

- [ ] **Step 2: 跑，确认失败**

Run: `backend/venv/Scripts/python.exe -m pytest backend/tests/test_conversation_authority.py -q -m "not llm_api" -p no:cacheprovider --basetemp=.dsh_local/pytestrun/tmp`
Expected: FAIL — `TypeError: append_turn() got an unexpected keyword argument 'usage'`。

- [ ] **Step 3: 最小实现**（`conversation_store.py::append_turn`）

```python
    def append_turn(self, conv_id: str, user_content: str, assistant_content: str,
                    tools: list[dict] | None = None,
                    usage: dict | None = None) -> None:
```
docstring 的 `:param` 段落补一行：`:param usage: 本轮 token 用量明细（前端每条消息底栏用）；None 不写入（messages 是 JSON blob，零迁移）`

在 `assistant = {...}` 之后、`msgs.append(assistant)` 之前插入：
```python
            if usage:
                assistant["usage"] = usage
```
> 不动幂等比对（只比 `content`）、不动 `_normalize_messages` / `ensure_conversation`、不动任何 SQL / 表结构。

- [ ] **Step 4: `chat_service.py` 接线**（新增 helper + 改一行调用）

```python
def _usage_summary(result) -> dict | None:
    """本轮 token 用量（前端底栏用）。全 0 → None，不往 JSON blob 里塞空对象。"""
    tu = getattr(result, "token_usage", None)
    if tu is None:
        return None
    d = tu.to_dict()
    return d if d.get("total_tokens") else None
```
`_persist_turn` 内 `store.append_turn(...)` 改为：
```python
            store.append_turn(conv_id, user_text, getattr(result, "text", "") or "",
                              tools=_tools_summary(result), usage=_usage_summary(result))
```
> 流式（`chat_service.py:1423`）与非流式（`:1308`）两条路径都调 `_persist_turn`，故一处覆盖两条。

- [ ] **Step 5: 跑，确认通过 + 回归**（不提交）

Run: `backend/venv/Scripts/python.exe -m pytest backend/tests/test_conversation_authority.py backend/tests/test_event_bus.py backend/tests/test_chat_kb_scope.py -q -m "not llm_api" -p no:cacheprovider --basetemp=.dsh_local/pytestrun/tmp`
Expected: PASS。按 `AGENTS.md §1` **不 commit**。

---

## Task 3: 纯逻辑 helper `turnUsage.js` + 单测（前端 TDD）

**Files:**
- Create: `frontend/src/utils/turnUsage.js`
- Create: `frontend/src/utils/turnUsage.test.js`

**Interfaces:** Produces `normalizeUsage` / `formatTokens` / `formatHitRate` / `formatTurnUsage`（签名见 §2）。**无 Vue 依赖**，Vitest jsdom 下可直接 import。

- [ ] **Step 1: 写失败测试**

```js
import { describe, it, expect } from 'vitest'
import { normalizeUsage, formatTokens, formatHitRate, formatTurnUsage } from './turnUsage.js'

describe('normalizeUsage', () => {
  it('把后端 token_usage 归一化为展示模型并算出命中率', () => {
    expect(normalizeUsage({ prompt_tokens: 1000, completion_tokens: 284,
      total_tokens: 1284, cached_tokens: 630, reasoning_tokens: 0 }))
      .toEqual({ total: 1284, cached: 630, completion: 284, hitRate: 0.63 })
  })
  it('total_tokens 缺失时按 prompt+completion 补算', () => {
    expect(normalizeUsage({ prompt_tokens: 100, completion_tokens: 50, cached_tokens: 0 }).total).toBe(150)
  })
  it('空 / 全 0 / 非对象一律返回 null（底栏不渲染）', () => {
    expect(normalizeUsage(null)).toBe(null)
    expect(normalizeUsage({ prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 })).toBe(null)
    expect(normalizeUsage('x')).toBe(null)
    expect(normalizeUsage(undefined)).toBe(null)
  })
  it('cached > prompt 时命中率封顶 1（脏数据不显示 >100%）', () => {
    expect(normalizeUsage({ prompt_tokens: 10, completion_tokens: 1, cached_tokens: 99 }).hitRate).toBe(1)
  })
  it('prompt=0 时命中率 0，不除零', () => {
    expect(normalizeUsage({ prompt_tokens: 0, completion_tokens: 20, total_tokens: 20 }).hitRate).toBe(0)
  })
})

describe('formatTokens', () => {
  it('千位缩写为人类可读', () => {
    expect(formatTokens(999)).toBe('999')
    expect(formatTokens(1284)).toBe('1.3k')
    expect(formatTokens(12345)).toBe('12k')
  })
})

describe('formatHitRate', () => {
  it('四舍五入为百分数', () => {
    expect(formatHitRate(0.634)).toBe('63%')
    expect(formatHitRate(0)).toBe('0%')
  })
})

describe('formatTurnUsage', () => {
  it('产出底栏两段文案 + 是否强调缓存', () => {
    expect(formatTurnUsage({ prompt_tokens: 1000, completion_tokens: 284,
      total_tokens: 1284, cached_tokens: 630 }))
      .toEqual({ tokensLabel: '1.3k tokens', cachedLabel: '缓存命中 63%', hasCache: true })
  })
  it('无数据返回 null', () => { expect(formatTurnUsage(null)).toBe(null) })
})
```

- [ ] **Step 2: 跑，确认失败**

Run: `cd frontend && npm test -- src/utils/turnUsage.test.js`
Expected: FAIL — `Failed to resolve import "./turnUsage.js"`。

- [ ] **Step 3: 最小实现**

```js
/**
 * 单轮 token 用量 → 展示模型。后端真值是 TokenUsage.to_dict() 的 5 个 int；
 * 这里只做「防御 + 命中率」，不猜金额（主系统 /agent/usage 本就不含成本字段）。
 */
export function normalizeUsage(raw) {
  const u = raw && typeof raw === 'object' ? raw : null
  if (!u) return null
  const num = (v) => { const n = Number(v); return Number.isFinite(n) && n > 0 ? n : 0 }
  const prompt = num(u.prompt_tokens)
  const completion = num(u.completion_tokens)
  const cached = num(u.cached_tokens)
  const total = num(u.total_tokens) || prompt + completion
  if (!total) return null
  return { total, cached, completion, hitRate: prompt ? Math.min(1, cached / prompt) : 0 }
}

export function formatTokens(n) {
  const v = Number(n) || 0
  if (v < 1000) return String(v)
  return (v >= 10000 ? (v / 1000).toFixed(0) : (v / 1000).toFixed(1).replace(/\.0$/, '')) + 'k'
}

export function formatHitRate(rate) {
  return Math.round((Number(rate) || 0) * 100) + '%'
}

export function formatTurnUsage(raw) {
  const u = normalizeUsage(raw)
  if (!u) return null
  return {
    tokensLabel: formatTokens(u.total) + ' tokens',
    cachedLabel: '缓存命中 ' + formatHitRate(u.hitRate),
    hasCache: u.cached > 0,
  }
}
```

- [ ] **Step 4: 跑，确认通过**（不提交）

Run: `cd frontend && npm test`
Expected: PASS，且原 3 个测试文件全绿。按 `AGENTS.md §1` **不 commit**。

---

## Task 4: `chatStore.onAgentDone` 消费 usage（store 单测，TDD）

**Files:**
- Modify: `frontend/src/stores/chatStore.js::send` 内的 `onAgentDone` 回调（约 :1512-1517）
- Test: `frontend/src/stores/chatStore.lifecycle.test.js`（既有 `sendMessageStream` mock、`CONV_KEY` 常量已 import）

**Interfaces:** Consumes `AGENT_DONE` payload 的 `usage` 键（Task 1）。Produces assistant 消息上的 `message.usage`（Task 7 消费）。

- [ ] **Step 1: 写失败测试**（追加到既有 `agent_done` 用例 :314 之后）

```js
  it('agent_done 带 usage → 落到 assistant 消息（底栏才有数据）', async () => {
    const usage = { prompt_tokens: 1000, completion_tokens: 284, total_tokens: 1284,
                    cached_tokens: 630, reasoning_tokens: 0 }
    sendMessageStream.mockImplementation((msgs, cbs) => {
      cbs.onToken('你好')
      cbs.onAgentDone({ rounds: 1, usage })
      return { abort: vi.fn() }
    })
    const store = useChatStore()

    await store.send('提问')

    const stored = JSON.parse(localStorage.getItem(CONV_KEY))
    const conv = stored.find(c => c.messages.some(m => m.role === 'assistant'))
    expect(conv.messages.at(-1).usage).toEqual(usage)
    expect(conv.messages.at(-1).content).toBe('你好')   // 既有正文不能被冲掉
  })
```

- [ ] **Step 2: 跑，确认失败**

Run: `cd frontend && npm test -- src/stores/chatStore.lifecycle.test.js`
Expected: FAIL — `expected undefined to deeply equal {...}`。

- [ ] **Step 3: 最小实现**（`chatStore.js::onAgentDone`，**保留 `seq !== streamSeq` 护栏**）

```js
        onAgentDone: (data) => {
          if (seq !== streamSeq) return   // 旧流迟到的事件，忽略
          patchLastAssistant(conv, () => ({ ts: Date.now(), usage: data?.usage || null }))
          loading.value = false
          persist()
        },
```

- [ ] **Step 4: 跑，确认通过 + 回归**（不提交）

Run: `cd frontend && npm test`
Expected: PASS（含 :314「agent_done 后 loading=false 且正文已落盘」、:332「思考逐片拼回一块」等既有 10+ 例）。按 `AGENTS.md §1` **不 commit**。

> ⚠️ 已知边界（写进实现注释）：`onDone` 空回复 / `onError` 会 `renderAssistant(..., true)` **替换**占位气泡，那两条失败路径不带 usage → 底栏不渲染，属优雅降级，符合「失败消息不该假装统计成功」。

---

## Task 5: 设计 token + 成功态去绿（`style.css` / `MessageBubble.vue` / `QuizCard.vue`）

**Files:**
- Modify: `frontend/src/style.css`（`:root` 对话区 :147-155 之后）
- Modify: `frontend/src/components/MessageBubble.vue`（`.msg-action-btn.is-done`，约 :863）
- Modify: `frontend/src/components/QuizCard.vue`（:176、:192）

**Interfaces:** Produces `--turn-*` 6 个 CSS 变量，供 Task 6 / Task 7 消费。

- [ ] **Step 1: 在 `style.css` `::root` 对话区追加**（**全部是 `var()` 别名，`html.dark` 覆盖底层 token 后自动生效，无需第二份 dark 定义** —— ponytail）

```css
  /* 单条 AI 回复：过程账本 + 用量底栏。成功态用墨色，不再复用 success 绿；
     全部是 var() 别名，html.dark 覆盖底层 token 后自动跟随，勿再写一份 dark 覆盖。 */
  --turn-ink:     var(--color-text-primary);
  --turn-muted:   var(--color-text-tertiary);
  --turn-rule:    var(--color-border-default);
  --turn-wash:    var(--color-bg-hover);
  --turn-accent:  var(--color-primary);
  --turn-done:    var(--color-text-primary);
  --turn-error:   var(--color-danger);
```
> **不要**改 `--color-success: #10b981` / dark `#34d399` / `--color-green` 别名：图谱掌握度等仍在用。

- [ ] **Step 2: `MessageBubble.vue` 复制成功态去绿**

`.msg-action-btn.is-done` 由 `color: var(--color-success);` 改为 `color: var(--turn-done);`。复制成功后的对勾 SVG（:461-473）保持不动，只是从绿变墨色。

- [ ] **Step 3: `QuizCard.vue` 两行去绿**

`:176` `.qc.is-sent { ... border-left-color: var(--color-green, #22a06b); }` → `var(--turn-done, var(--color-text-primary))`；`:192` `.qc-sent { ... color: var(--color-green, #22a06b); }` → 同上。

- [ ] **Step 4: 确认 `prefers-reduced-motion` 覆盖**

`read` `frontend/src/style.css:413-419`。若既有 `@media (prefers-reduced-motion: reduce)` 只改 `transition/animation-duration` 而未覆盖 `@keyframes`，在 Task 6 新增 spinner 的局部样式里自带 `@media (prefers-reduced-motion: reduce) { .turn-spinner { animation: none; } }`（已写在 Task 6 代码块中），不依赖全局块。

- [ ] **Step 5: 验证**（不提交）

Run: `cd frontend && npm run build`
Expected: 构建成功（仅既有 chunk >500kB 警告）。按 `AGENTS.md §1` **不 commit**。

---

## Task 6: 工具/思考区重构为统一纵向账本（`MessageBubble.vue`）

**Files:**
- Modify: `frontend/src/components/MessageBubble.vue`（script :75-173 + template :262-389 + style :589-780）

**Interfaces:** Consumes Task 5 的 `--turn-*`；复用既有 `getToolDisplayName`（:104）、`extractToolBrief`（:115）、`formatDuration`（:142）、`toggleToolDetail`（:160）、`expandedToolIdx`（:81）、`showThinking`（:78）、`hasTools`/`hasThinking`（:76-77）。

- [ ] **Step 1: script 清理 + 新增**（先 `read` 当前文件确认行号）

删除**只服务折叠总览**的死代码：`toolStats` computed（:148-158）、`toolsOpen` ref（:80）、`toolsExpanded` computed（:165-169）、`toggleTools`（:171-173）。保留 `expandedToolIdx` / `toggleToolDetail` / `showThinking` / `hasTools` / `hasThinking`。

追加：
```js
import TurnFooter from './TurnFooter.vue'

const WEB_TOOLS = new Set(['mcp__websearch__web_search', 'fetch_webpage'])
function isWebTool(tool) { return WEB_TOOLS.has(tool?.tool) }

/** 底栏总耗时 = 各工具 duration_ms 之和（与既有 toolStats.totalDuration 同口径） */
const toolDurationMs = computed(() =>
  (props.message.tools || []).reduce((s, t) => s + (Number(t.result?.duration_ms) || 0), 0)
)
```

- [ ] **Step 2: template —— 把 `.tool-panel`（:263-376）与 `.thinking-panel`（:379-389）**整体替换**为统一 `.turn-process`**

```vue
        <!-- 过程账本：思考 / 每个工具 / 每次网页搜索 = 一条独立纵行，按发生顺序排下来 -->
        <div v-if="hasTools || hasThinking" class="turn-process">
          <button v-if="hasThinking" type="button" class="turn-row"
                  :aria-expanded="showThinking" @click="showThinking = !showThinking">
            <span class="turn-glyph" aria-hidden="true">·</span>
            <span class="turn-row-label">思考</span>
            <span class="turn-row-brief">{{ (message.thinking || []).join(' ').slice(0, 60) }}</span>
            <span class="turn-row-caret" :class="{ expanded: showThinking }" aria-hidden="true">▾</span>
          </button>
          <div v-if="hasThinking && showThinking" class="turn-thinking-body">
            <div v-for="(think, idx) in message.thinking" :key="idx" class="thinking-block">{{ think }}</div>
          </div>

          <div v-for="(tool, idx) in message.tools" :key="idx" class="turn-step"
               :class="{ 'is-running': tool.status === 'running', 'is-error': tool.status === 'error' }">
            <button type="button" class="turn-row"
                    :class="{ clickable: !!(tool.args_head || tool.result?.summary) }"
                    :disabled="!(tool.args_head || tool.result?.summary)"
                    :aria-expanded="(tool.args_head || tool.result?.summary) ? expandedToolIdx === idx : undefined"
                    @click="(tool.args_head || tool.result?.summary) && toggleToolDetail(idx)">
              <span v-if="tool.status === 'running'" class="turn-spinner" aria-hidden="true" />
              <span v-else-if="tool.status === 'error'" class="turn-glyph is-error" aria-hidden="true">✕</span>
              <span v-else class="turn-glyph is-done" aria-hidden="true">{{ isWebTool(tool) ? '◎' : '•' }}</span>
              <span class="turn-row-label">{{ getToolDisplayName(tool.tool) }}</span>
              <span v-if="extractToolBrief(tool)" class="turn-row-brief">{{ extractToolBrief(tool) }}</span>
              <span v-if="tool.status === 'error'" class="turn-row-meta is-error">失败</span>
              <span v-else-if="formatDuration(tool.result?.duration_ms)" class="turn-row-meta">
                {{ formatDuration(tool.result?.duration_ms) }}
              </span>
            </button>
            <div v-if="expandedToolIdx === idx && (tool.args_head || tool.result?.summary)" class="turn-detail">
              <div v-if="tool.args_head && tool.args_head !== '{}'" class="turn-detail-row">
                <span class="turn-detail-label">参数</span>
                <code class="turn-detail-code">{{ tool.args_head }}</code>
              </div>
              <div v-if="tool.result?.summary" class="turn-detail-row">
                <span class="turn-detail-label">{{ isWebTool(tool) ? '来源摘要' : '结果' }}</span>
                <span class="turn-detail-text">{{ tool.result.summary }}</span>
              </div>
            </div>
          </div>
        </div>
```
> 决策：**去掉折叠，默认全部展开**（用户明确要「排列下来」）；长列表（>8 步）折叠溢出属 YAGNI 延期项，本轮不做。网页搜索不靠颜色区分，靠 `◎` 字形 + `来源摘要` 标签 —— 保持「全页只有一个亮色」的纪律，且不需要任何新后端数据。

- [ ] **Step 3: 把 `.tool-*` / `.thinking-toggle` 旧样式（:589-780 区间）**替换**为账本样式**

```css
/* ─── 过程账本：一条 1px 竖线串起所有步骤，无卡片 / 无阴影 / 无绿色 ─── */
.turn-process {
  display: flex; flex-direction: column; gap: 2px;
  margin-bottom: var(--space-md, 12px);
  padding-left: var(--space-md, 12px);
  border-left: 1px solid var(--turn-rule);
}
.turn-step { display: flex; flex-direction: column; gap: 2px; }
.turn-row {
  display: flex; align-items: baseline; gap: var(--space-sm, 8px);
  width: 100%; min-width: 0; padding: 3px 0;
  appearance: none; border: 0; background: none; font: inherit;
  text-align: left; font-size: var(--font-size-xs, 12px); color: var(--turn-ink);
}
.turn-row.clickable { cursor: pointer; }
.turn-row.clickable:hover { background: var(--turn-wash); }
.turn-row:focus-visible { outline: 2px solid var(--turn-accent); outline-offset: 2px; }
.turn-row:disabled { cursor: default; opacity: 1; }
.turn-glyph { flex-shrink: 0; width: 12px; text-align: center; font-weight: 700; color: var(--turn-ink); }
.turn-glyph.is-error { color: var(--turn-error); }
.turn-row-label { flex-shrink: 0; font-weight: 500; }
.turn-row-brief {
  flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  color: var(--turn-muted);
}
.turn-row-meta { flex-shrink: 0; color: var(--turn-muted); font-variant-numeric: tabular-nums; }
.turn-row-meta.is-error { color: var(--turn-error); }
.turn-row-caret { flex-shrink: 0; color: var(--turn-muted); transition: transform .15s ease; }
.turn-row-caret.expanded { transform: rotate(180deg); }
.turn-spinner {
  flex-shrink: 0; width: 11px; height: 11px;
  border: 1.5px solid var(--turn-accent); border-top-color: transparent;
  border-radius: 50%; animation: turnSpin .6s linear infinite;
}
@keyframes turnSpin { to { transform: rotate(360deg); } }
.turn-thinking-body { padding: 2px 0 6px 20px; color: var(--turn-muted); font-size: var(--font-size-xs, 12px); }
.turn-detail {
  margin-left: 20px; padding: 6px 10px; border-left: 1px solid var(--turn-rule);
  display: flex; flex-direction: column; gap: 4px; font-size: 11.5px;
}
.turn-detail-row { display: flex; align-items: baseline; gap: 8px; min-width: 0; }
.turn-detail-label { flex-shrink: 0; color: var(--turn-muted); }
.turn-detail-code { font-family: var(--font-mono); color: var(--color-text-secondary); word-break: break-all; }
.turn-detail-text { color: var(--color-text-secondary); word-break: break-word; }
@media (prefers-reduced-motion: reduce) {
  .turn-spinner { animation: none; }
  .turn-row-caret { transition: none; }
}
```
> 旧的 `toolFadeIn` 逐行淡入动画**随 `.tool-step` 一起删除**（散落在每行的入场动画正是 frontend-design 要避免的通用默认）。`spin` keyframes 若已无其它引用一并删；若有别处引用则保留，删前先 `grep`。

- [ ] **Step 4: 验证**（不提交）

Run: `cd frontend && npm run build && npm test`
Expected: 构建成功、原 3 个测试文件全绿。按 `AGENTS.md §1` **不 commit**。

---

## Task 7: `<TurnFooter>` 共用组件 + 挂到每条 AI 消息最底部

**Files:**
- Create: `frontend/src/components/TurnFooter.vue`
- Modify: `frontend/src/components/MessageBubble.vue`（AI 分支末尾，`.msg-toolbar` 之后、`:496` `</template>` 之前）

**Interfaces:** Consumes Task 3 的 `formatTurnUsage`、Task 4 的 `message.usage`、Task 5 的 `--turn-*`、Task 6 的 `toolDurationMs`。

- [ ] **Step 1: 新建组件**

```vue
<script setup>
import { computed } from 'vue'
import { formatTurnUsage } from '../utils/turnUsage.js'

const props = defineProps({
  usage: { type: Object, default: null },
  toolCount: { type: Number, default: 0 },
  durationMs: { type: Number, default: 0 },
})

const model = computed(() => formatTurnUsage(props.usage))
const durationLabel = computed(() => {
  const n = Number(props.durationMs) || 0
  if (n <= 0) return ''
  return n >= 1000 ? `${(n / 1000).toFixed(1)}s` : `${Math.round(n)}ms`
})
</script>

<template>
  <!-- 所有 AI 消息共用的最底部一行；无用量（流式 / 失败 / 老消息）时整行不渲染 -->
  <div v-if="model" class="turn-footer">
    <span class="turn-metric">{{ model.tokensLabel }}</span>
    <span class="turn-sep" aria-hidden="true" />
    <span class="turn-metric" :class="{ 'is-cached': model.hasCache }">{{ model.cachedLabel }}</span>
    <template v-if="toolCount">
      <span class="turn-sep" aria-hidden="true" />
      <span class="turn-metric">{{ toolCount }} 次工具</span>
    </template>
    <template v-if="durationLabel">
      <span class="turn-sep" aria-hidden="true" />
      <span class="turn-metric">{{ durationLabel }}</span>
    </template>
  </div>
</template>

<style scoped>
.turn-footer {
  display: flex; align-items: baseline; flex-wrap: wrap; gap: var(--space-sm, 8px);
  margin-top: var(--space-md, 12px);
  padding-top: var(--space-sm, 8px);
  border-top: 1px solid var(--turn-rule);
}
.turn-metric {
  font-size: var(--font-size-xs, 12px); color: var(--turn-muted);
  font-variant-numeric: tabular-nums; white-space: nowrap;
}
/* 全页唯一亮色：只给「缓存命中」这一个数字 */
.turn-metric.is-cached { color: var(--turn-accent); }
.turn-sep { width: 1px; height: 10px; background: var(--turn-rule); align-self: center; }
</style>
```

- [ ] **Step 2: 挂载**（`MessageBubble.vue`，放在 `.msg-toolbar` 之后，成为 AI 分支**最后一个元素**）

```vue
        <!-- 所有对话通用：本轮用量恒定在消息最底部（既有悬停工具栏保持不动，ponytail） -->
        <TurnFooter
          :usage="message.usage"
          :tool-count="message.tools?.length || 0"
          :duration-ms="toolDurationMs"
        />
```
> 决策：**不合并**.msg-toolbar 进 TurnFooter —— 工具栏是 hover 显现、用量是常驻，合并会把用量也变成 hover 才可见，或反过来把工具栏常驻化，两种都改变既有行为。`TurnFooter` 无条件挂载即可：流式 / 失败 / 老消息没有 `usage` 时 `v-if="model"` 自动不渲染，无布局跳动。

- [ ] **Step 3: 构建验证**（不提交）

Run: `cd frontend && npm run build`
Expected: 成功。按 `AGENTS.md §1` **不 commit**。

---

## Task 8: 全量验证（`verification-before-completion`，证据先于断言）

**Files:** 无新增改动；只跑命令并记录输出。

- [ ] **Step 1: 后端全量**

Run: `backend/venv/Scripts/python.exe -m pytest backend/tests -q -m "not llm_api"`
Expected: 基线 1983 collected / 7 deselected；本计划新增 3 例（`test_agent_done_carries_usage` + `test_append_turn_persists_usage_on_assistant` + `test_append_turn_without_usage_keeps_message_clean`）→ **1986 collected / 7 deselected，全 passed + 1 skipped**。若报 `PermissionError: …Temp…pytest-of-<user>`，按 `AGENTS.md §0` 兜底改用 `backend/venv/Scripts/python.exe .dsh_local/pytestrun/run.py backend/tests -q -m "not llm_api" -p no:cacheprovider --basetemp=.dsh_local/pytestrun/tmp`。

- [ ] **Step 2: 前端单测 + 构建**

Run: `cd frontend && npm test`
Expected: 原 3 文件全绿 + `turnUsage.test.js` 9 例 + lifecycle 1 例 → **60 passed (4 files)**（实测基线 50，WIP 浮动时以「无 failed」为准）。
Run: `cd frontend && npm run build`
Expected: 成功（允许既有 chunk >500kB 与 `INEFFECTIVE_DYNAMIC_IMPORT` 提示）。

- [ ] **Step 3: 真机手工验收**（起服务：`.\start.ps1`，或 cwd=`backend` `venv\Scripts\python.exe -m uvicorn app.main:app --port 8000 --workers 1` + cwd=`frontend` `npm run dev`）

逐条打勾：
1. 发一条会触发工具 + 联网搜索的提问 → 每一步**逐行纵向排列**，网页搜索行是 `◎` 字形、点开显示「来源摘要」，不再折叠成「已完成」一行。
2. 完成态**没有绿色**（工具点、复制成功勾、题卡「已提交」均为墨色）；失败态才是红 `✕`。
3. 回复**最底部**出现 `1.3k tokens │ 缓存命中 63% │ N 次工具 │ X.Xs`，缓存命中数字为紫色，其余灰。
4. **刷新页面** → 底栏仍在（验证 Task 2 服务端持久化 + `adoptRemote`）。
5. 切 **dark 主题** → 竖线 / 文字 / 紫色均跟随主题，无不可读对比。
6. 窄窗口（~390px）→ 底栏 `flex-wrap` 不溢出、工具行 `brief` 省略号生效。
7. **Tab** 能聚焦到每个工具行与思考行，`:focus-visible` 有 2px 紫环。
8. 开系统「减少动态效果」→ spinner 不转、caret 无过渡。
9. 发一条不带工具的问题 → 底栏仍出现（usage 由 `AGENT_DONE` 必发），过程账本不渲染。
10. 断流 / 故意失败 → 失败气泡不带底栏（优雅降级，符合 Task 4 注释）。

- [ ] **Step 4: 检查并发 WIP 与 diff**（不提交）

Run: `git status --short` + `git diff --stat`
Expected: 只出现本计划 §2 列出的文件；**若看到别人的 WIP 混进来，不动它、不 `git add -A`**。按 `AGENTS.md §1` **不主动 commit**；真要提交时按文件族拆、路径限定 add。

---

## §3 Self-Review

**1. Spec coverage（用户 5 条需求 → 任务）**

| 用户需求（m00146） | 落地任务 |
|---|---|
| ① tok 计数 + 缓存命中放到**每条对话消息最下面** | Task 1（数据上 SSE）→ Task 4（落消息）→ Task 7（底栏组件，AI 分支最后元素） |
| ① 「所有对话里通用的部分」= 共用组件 | Task 7 单一 `<TurnFooter>` 被每条 AI 消息挂载；纯逻辑抽 Task 3 共用 |
| ② UI 不要难看 / 不模板化 / 不「雷霆」 | §0 设计系统 + Task 5/6/7；第二遍已对照 5 类通用默认逐条修订并写明原因 |
| ③ 工具调用和网页搜索**逐条纵向排列**，不要叠在一块 | Task 6 删除折叠总览、统一 `.turn-process` 竖线账本、`◎` 区分网页搜索 |
| ④ 成功后的**绿色不好看** | Task 5 新建 `--turn-done` 墨色 token，改 MessageBubble 复制态 + QuizCard；Task 6 把绿勾换成墨点 |
| ⑤ **先出结构化计划再讲** | 本文档 + 会话回复；**本轮不实现** |
| （隐含）刷新后不能消失 | Task 2 服务端持久化，Task 8 手工验收第 4 条 |

**2. Placeholder scan**：无 TBD / TODO /「适当处理」/「类似 Task N」/ 未定义类型。每个 code step 都有完整可粘贴代码；`read`/`grep` 指令给出了具体目标。

**3. Type consistency**：`usage` 从头到尾是同一个 5 键 dict（`TokenUsage.to_dict()`）→ `normalizeUsage` → `{total, cached, completion, hitRate}` → `formatTurnUsage` → `{tokensLabel, cachedLabel, hasCache}` → `TurnFooter` 的 `usage` prop；`toolCount`/`durationMs` 均为 Number；`append_turn(usage=)` 与 `_persist_turn` 调用一致。函数名 `isWebTool` / `toolDurationMs` / `toggleToolDetail` 在定义处与使用处逐字一致。

**4. 额外自查**：
- SSE 三处同步：本计划**零改动** events.py / `_format_agent_sse` / `api/index.js`，因为 `agent_done` 已在白名单且整 dict 透传 —— 但 Task 1 Step 5 仍跑 `test_event_bus.py` + `test_event_contract.py` 证明契约未破。
- 零迁移：`conversations.messages` 是 JSON blob；未碰 `agent_runs`/`llm_usage`/`agent_debug_logs` 表，未碰 `records.ensure_columns` 补列清单。
- 不引依赖：无 `@vue/test-utils`、无 webfont、无新 npm 包、无新端点。
- `frontend/` 冻结张力已在 Global Constraints 标注，按用户显式指令优先执行。

---

## §4 执行交接（本轮**不执行**——用户要求「先出计划，然后再给我说说」）

Plan complete and saved to `docs/superpowers/plans/2026-11-15-chat-turn-footer-and-process-ledger.md`. Two execution options（待用户拍板后启动，届时先调 `hindsight_capture_initiative` 记录该 initiative）:

1. **Subagent-Driven (recommended)** — 每个 Task 派一个全新子 agent，任务间由我 review，迭代快。REQUIRED SUB-SKILL: `superpowers:subagent-driven-development`。
2. **Inline Execution** — 在本会话按 `superpowers:executing-plans` 分批执行，带检查点。REQUIRED SUB-SKILL: `superpowers:executing-plans`。

**待用户确认的 3 个歧义**（不阻塞计划本身，计划已按「默认取值」写死、可直接执行）：
1. 截图是**正面参照**还是**反面参照**？默认：结构正面（逐行排列）、配色不照抄（沿用项目明暗主题）。
2. 「很雷霆」= 「很雷同」还是「很吓人」？默认：两者都覆盖（不去模板化 + 不用力过猛 / 不大红大绿）。
3. 「所有对话里通用的部分」= 一个共用底栏组件（默认），还是要把时间戳 / 复制按钮也并进同一行？默认：共用组件，既有悬停工具栏保持不动。
4. 底栏是否显示**金额（¥）**？默认：不显示 —— 主系统 `/agent/usage` 不含 `cost_total_yuan`，金额只在运维后台那套聚合里，引入它会造成第二套口径。
