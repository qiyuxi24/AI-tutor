# `core/agent` —— Agent 运行内核

> 建立：2026-09-15（原先是 `core/` 下 5 个平铺文件，归并成本包；同日 `token_estimator.py` 作为第 6 个成员加入。2026-09-19 拆出 `loop_guard.py`、新增 `debug_log.py`，**现为 8 个模块**）
> 这个文件夹回答一个问题：**一条学生消息进来后，到最终回答落库之间发生了什么、谁负责哪一段。**

---

## 1. 为什么归成一个包

这 8 个职责是**互相咬合**的：消息怎么写由 context 管，什么时候裁由 guard 管，发出去前估多少由 estimator 管，什么时候再来一轮由 loop 管，发出去的通知由 events 管，跑完存哪由 store 管。原先平铺在 `core/` 下时：

- 想搞清"一次 run 里消息是怎么攒起来的"，要在 5 个文件之间来回跳；
- `core/` 下同时躺着 RAG、知识图谱、quiz、collector……**分不出哪些是"一次对话运行"的内务**；
- 新加一个 run 级能力（比如预算记账、工具结果清理）没有天然落点。

归并后：**一个包 = 一次 run 的全部内务**。外部只 import 本包门面，不需要知道内部拆成了几个文件。

## 2. 八个模块（职责 + 它垄断了什么）

| 文件 | 原名 | 职责 | **垄断的不变量**（别在别处做第二遍） |
|---|---|---|---|
| `loop.py` | `agent_loop.py` | 主循环：LLM ↔ 工具 多轮串联（纯编排，不碰图谱/RAG 内部） | 唯一决定"要不要再来一轮工具"的地方；护栏：**轮数** `max_rounds=5`、**单工具超时** 60s，安全边界问 `loop_guard` |
| `loop_guard.py` | （2026-09-19 拆出） | 工具调用安全边界：`LoopGuard` + 终止原因 `STOP_*` + 拒绝文案 | **只判定不执行**；边界：**调用总次数** 12、**run 墙钟** 180s、**同参数重复** ≤2 次、**连续失败熔断** 3 次 |
| `debug_log.py` | （2026-09-19 新） | 调试日志：控制台 + SQLite 双写（`agent_debug_logs`），跨 run 按 run_id 回看 | 排查"为什么输出无用数据"的第一站；记录范围见下节，**不记**学生正文与工具全文 |
| `context.py` | `agent_context.py` | run 内 API 消息序列的持有与写入 + **Tool Result Clearing**（较早工具批次正文换占位符） | **唯一往 `messages` 里 append 的地方**：assistant 带 `tool_calls` 快照、`reasoning_details` 随轮保留、tool 回填按 `tool_call_id` 配对 |
| `guard.py` | `context_guard.py` | 发送前预算守卫：按预算线裁最旧历史 + 插省略说明 | **唯一裁历史的地方**（入口一次性；loop 内刻意不裁） |
| `estimator.py` | `token_estimator.py` | 发送前 token 预估：prompt 计数 + completion 预估（历史中位数 / 关键词规则）+ 成本换算 | **唯一做"发送前预估"的地方**；结果进 `AgentRunResult.token_estimate` → `agent_runs.token_estimate` 字段，与真值 `token_usage` 同表可对照 |
| `events.py` | `agent_events.py` | 事件发射：run_id 自动注入 + per-user 路由 | loop 对 `event_bus` 的**唯一入口**（loop 不再直调 bus） |
| `store.py` | `agent_run_store.py` | `agent_runs` 表落库 / 查询 / 清理 / 统计 | **`agent_runs` 的唯一写入方**（只有 loop 调 `save_run`） |

门面（`__init__.py`）只导出外部真正要用的名字：

```python
from app.core.agent import run_agent_loop, AgentRunResult      # loop
from app.core.agent import AgentContext, assistant_snapshot    # context
from app.core.agent import trim_history_to_budget              # guard
from app.core.agent import estimate_token_consumption, TokenEstimate   # estimator
from app.core.agent import AgentEventEmitter                   # events
from app.core.agent import store                               # store（子模块，按需 import）
```

## 3. 一次 run 的时序（含预算介入点）

```
chat_service.process_message_stream(messages, mode, user_id)
 │
 ① _build_system_prompt()                       ← 不在本包（chat_service）
 │    静态指令 + 图谱结构注入 + 画像 + RAG 检索块 + 工具说明
 │
 ② guard.trim_history_to_budget(prompt, messages)      ★ 预算介入点 1
 │    「静态段 + 历史」> B − 输出预留 → 从头部丢最旧 + 插省略说明
 │
 ③ loop.run_agent_loop(prompt, messages, kg=..., user_id=...)
 │    run_id = uuid4().hex                      ← 事件与落库共用这一个 id
 │    ctx = AgentContext(prompt, messages)      ← context：消息序列在此累积
 │    estimator.estimate_token_consumption()    ← 发送前预估（带 user_id 走历史中位数）
 │    guard = _LoopGuard(…)                安全边界建账
 │    ┌── 每轮 ─────────────────────────────────────────┐
 │    │ 入口先查硬预算（墙钟/次数）→ 耗尽即收尾          │
 │    │ msg = _chat_once(ctx.messages)     真打 LLM     │
 │    │ ctx.append_assistant_turn(msg)     快照（含思考）│
 │    │ for tc in msg.tool_calls:                       │
 │    │     guard.deny_reason(tc)? → 拒绝文案回填        │
 │    │     execute(tc, kg)  → ctx.append_tool_result() │
 │    │     guard.note_result(ok) → 连续失败达阈值即熔断 │
 │    │ 无 tool_calls → 自然结束 / 达上限 → 强制收尾      │
 │    └─────────────────────────────────────────────────┘
 │    _build_result → loop_stop 证据 + run 级日志 + AGENT_DONE(stop_reason)
 │    store.save_run(evidence, token_usage, token_estimate, …)   ← 正常 or 异常都落库
 │
 ④ back in chat_service：消费 EventBus 队列 → 转 SSE 给前端
```

**事件 ↔ 记录靠同一个 `run_id` 打通**：前端拿到 SSE 里的 `run_id`，就能用 `GET /agent/runs/{run_id}` 回源完整证据（thinking 全文、完整工具参数与返回）。

## 4. 预算/压缩的介入点（全项目就这几处）

| 介入点 | 位置 | 做什么 |
|---|---|---|
| ① 组装时（固定段） | `chat_service._build_system_prompt` | 固定段（S2–S7）越 **40%×B 告警**、越 **45%×B 强制重建图谱注入**（`guard` 裁不动字符串，必须在组装侧）；S6/S7 按源**独立截断** + query 双端放置 |
| ② 发送前（入口一次） | `guard.trim_history_to_budget` | **历史（S8）分层压缩**：`[省略说明+规则要点] + [首条 user 锚点] + [最近 N 轮完整]` |
| ③ loop 内每轮（幂等） | `context.AgentContext.clear_old_tool_results` | **较早工具批次**的正文换占位符（保留最近 K 批 = 当前推理链；`tool_call_id` 一个字不动） |
| ④ 图谱注入（组装时） | `graph_analyzer.build_graph_context` | 图谱结构段（S4）的硬上限 + 降级阶梯（省摘要 → 限量节点 + 焦点邻域优先） |

**loop 内不做历史裁剪**：每轮回填的 tool 结果属"必要思维链"，整条丢掉会让模型看到断裂的推理。③ 只清**较早批次**的正文（工具可重放），最近 K 批原样。

**③ 为什么不在 `guard`**（2026-09-15 实测修正，别再搬回去）：guard 处理的是跨请求历史，而 `models/schemas.ChatMessage` 只有 `role` / `content` —— **历史里永远没有 tool 消息**，在 guard 里清理是死代码。另一条独立理由：`llm/messages.build_api_messages` 只透传 `role` / `content`，会丢掉 `tool_call_id`，谁在那里改 tool 消息都会造出非法序列（真机 400：`tool result's tool id() not found`）。

**预估不介入裁剪决策**：`estimator` 只产出"这一次大概花多少"（prompt/completion/成本），不改变任何行为；裁不裁仍由 `guard` 的精确计数与预算线决定。预估值随 run 落库，是事后校准 Phase 2 历史中位数、以及回看 S1/S8 配额的依据（预估 vs 真值同表）。

> 各段的配额、让位顺序、触发阶梯见 **`docs/上下文工程/上下文工程_预算框架.md`**（配额 SSOT）。

## 5. 不装什么（边界）

| 不在这里 | 在哪 | 为什么 |
|---|---|---|
| `token_counter` | `core/token_counter.py` | 通用计量工具（quiz / API / 探针脚本共用），不是 run 专属；本包的 `guard` 与 `estimator` 都建立在它之上 |
| 提示词组装 | `services/chat_service.py` | 依赖画像、图谱、RAG pipeline、工具注册表，是本包的上游 |
| 工具注册表与执行 | `core/agent_tools/` | 另有 README；本包只通过 `KG_TOOLS` / `execute_kg_tool_async` 消费 |
| 事件基础设施 | `core/event_bus.py` | 进程内队列，任何模块可用；本包只是它的一个语义封装 |
| 运行记录的查询侧 | `api/v1/agent_runs.py` | 直连 `store` 做列表/详情/删除/统计，不经 loop |

## 6. 改代码时的坑

1. **别在 loop 里直接 `messages.append({...})`** —— 一律走 `AgentContext.append_*`。协议细节（`tool_calls` 快照、`reasoning_details` 回填、tool id 配对）全靠这一处收敛。
2. **`reasoning_details` 必须随轮保留** —— MiniMax 的 Interleaved Thinking 要求多轮工具调用时回填思考块，丢了会掉进"模型不思考"的退化。
3. **传了 `user_id` 就自动落库**：调用方**不要**再自己 `save_run` / `save_trace`（历史上两套记录并存过）。
4. **token 真值优先用 `AgentRunResult.token_usage`**（API `usage` 提取），`context_tokens` 是旧字段，仅向后兼容。
5. **`user_id=None` 是纯测试模式**：不推事件、不落库 —— 写测试时用它，别 mock 整个 event_bus。
6. **每个 `KnowledgeGraph` 实例用毕 `close()`**，别跨协程共享（`chat_service` 已经踩过 use-after-close）。
7. **事件类型是白名单**：`chat_service._format_agent_sse` 是 if/elif 无 else，新增事件类型必须同步加透传，否则会被静默丢弃。
8. **改 `store.py` 的表结构要同步** `prune()` 的分层保留策略（≤30 天全量 / >30 天摘 evidence / >180 天整行删）与 `api/v1/agent_runs.py` 的字段假设。**新增列必须写进 `store._COLUMN_MIGRATIONS`**（由 `core/records.py::ensure_columns` 就地补）——`CREATE TABLE IF NOT EXISTS` **不会**给已存在的表补字段（`token_estimate` 就是这么加的）。库路径 / 连接 / 建表 / 过期清理也统一走 `core/records.py`，别再自己写一份 `_connect`。
9. **预估 ≠ 真值**：`token_estimate`（estimator 估的）与 `token_usage`（API usage 真值）不是一回事，业务判断一律用真值（见坑 4），预估值只用于预算参考与事后校准。
10. **改 `guard.py` 分层规则前先看 `tests/test_history_layering.py` 的四条不变量**：首条 user（任务锚点）永不丢；保留的近端消息**逐字未改**（分层只产出"原文 or 要点行"，不篡改）；裁剪后仍以 user 开头（分层会产生**连续 user** 消息，真机已验证 API 接受）；总 token ≤ 裁剪线。
11. **改 `context.clear_old_tool_results` 前先确认配对不破**：只改 `tool` 消息的 `content`，`tool_call_id` 与 assistant `tool_calls` 的 id 列表必须逐字保留 —— 破了服务端报 400 `tool result's tool id() not found`。接线在 `loop._loop_core` 每轮 `_chat_once` 之前，`tests/test_agent_loop.py::test_old_tool_results_cleared_across_rounds` 锁住"真的被调用"（防止再次出现"实现了但匹配不到数据"的空转）。
12. **工具调用被安全边界拦下 ≠ 静默丢弃**：`_execute_tool` 必须回填一条**写明原因**的拒绝文案（`_DENY_MESSAGES`），且仍然 append tool 消息保证 `tool_call_id` 配对 —— 静默跳过会让模型以为工具没执行，进而反复重试同一个调用（这正是"循环输出无用数据"的成因）。
13. **`stop_reason` 三出同值**：`AgentRunResult.stop_reason` / `AGENT_DONE` 事件 / evidence 的 `loop_stop` 步骤必须一致，所以新增终止路径一律走 `_build_result` 这个唯一出口（并发事项：`_persist_run` 的 error 分支是唯一例外，它对 `loop_core` 抛异常的情况手写 `loop_stop`）。

### 6.1 工具调用安全边界（`loop._LoopGuard`，2026-09-19）

`max_rounds` 只限制"最多问模型几次"，挡不住四类退化，所以逐条设了上限（默认值可经 `run_agent_loop` 参数覆盖）：

| 边界 | 默认值 | 挡住的退化 | 触发后 |
|---|---|---|---|
| `max_tool_calls` | 12 | 单轮并行返回 N 个 `tool_calls` → 几轮跑出几十次调用，学生端长时间无输出 | 提前收尾，不再发起新的 LLM 轮 |
| `max_total_secs` | 180 | LLM 或工具整体变慢，无任何墙钟兜底 | 同上 |
| `max_identical_calls` | 2 | 同一 `(工具, 参数)` 反复重试 —— **Tool Result Clearing 会诱发**（早期结果换成"需要时请重新调用"占位符，模型就去重取同一份） | 拒绝该调用，回填"再调也一样"的文案 |
| `max_consecutive_fails` | 3 | 工具连续失败却一路换参重试，五轮烧完只剩固定兜底文案 | 熔断 + 提前收尾 |

终止原因取值：`natural` / `max_rounds` / `time_budget` / `call_budget` / `fail_circuit` / `error`。
**排查路径**：调试日志（`debug_log.recent(run_id=…)`，见下节）→ `agent_runs` 的 `loop_stop` 证据（同 `stop_reason`）→ SSE `agent_done` 事件。

### 6.2 调试日志（`debug_log.py`，2026-09-19）

控制台 + SQLite 双写，库在 `backend/app/data/agent_debug/debug_log.db`（表 `agent_debug_logs`，
与 `agent_runs` 同数据根；保留 `_RETENTION_DAYS=14` 天，启动 + 每日 GC 各清一次，见 `main.py`）。

**记录什么**（`scope.event` 清单，**改代码加事件时同步这段**）：

| scope | event（何时记） | 关键 data |
|---|---|---|
| `loop` | `run_start` / `run_end`（一次 run 的首尾） | run_end 带 `stop_reason` / rounds / tool_calls / llm_calls / elapsed |
| `loop` | `round_start` / `round_end`（每轮工具批次） | `tools`（工具名清单）/ `failed` / `duration_ms` |
| `loop` | `budget_exhausted`（硬预算耗尽，提前收尾）、`force_finish`、`force_finish_failed`、`empty_final_text`、`run_error` | `reason` / `error` / `tool_calls` |
| `tool` | `tool_result`（每次工具执行完） | `tool` / `ok` / `duration_ms` / `result_head`（前 120 字） |
| `tool` | `tool_denied`（被安全边界拦下） | `tool` / `reason` |
| `llm` | `llm_call`（每次 LLM 返回） | `duration_ms` / `prompt_tokens` / `completion_tokens` / `tool_calls` / `has_text` |
| `context` | `tool_results_cleared`（S8 清理） | `cleared` / `keep_batches` |
| `bg` | `quiz_bg_start` / `_end` / `_timeout` / `_error`（后台出题子任务） | `node_id` / `ok` / `reason` / `elapsed` |

**不记录什么**（边界，测试锁死）：学生消息正文、系统提示词全文、工具完整返回、密钥。
文本一律截断（message 2K / data 8K / 单值 500），落库失败只 warning 不抛。

**怎么查**（开发期直接跑，不必开界面）：
```bash
cd backend && venv/Scripts/python.exe -c "from app.core.agent import debug_log as d; \
[print(r['ts'], r['scope'], r['event'], r['message'], r['data']) for r in d.recent(limit=50)]"
```
按 run_id 串起一次运行：`d.recent(run_id='<run_id>')`（run_id 可从 SSE `agent_start` 事件拿）。
生产想关掉：`AGENT_DEBUG_LOG=0`（整体）/ `AGENT_DEBUG_LOG_DB=0`（只关落库）。

## 7. 自测

```bash
# 本包相关（离线，零真实 API）
backend/venv/Scripts/python.exe -m pytest backend/tests/test_agent_loop.py \
    backend/tests/test_context_guard.py backend/tests/test_agent_run_store.py \
    backend/tests/test_token_estimator.py backend/tests/test_minimax_thinking.py -q

# 全量离线
backend/venv/Scripts/python.exe -m pytest backend/tests -q -m "not llm_api"
```

真实 API 的用例在 `tests/test_agent_loop_real_api.py`（打 `llm_api` 标记，需付费 key，不随默认套件跑）。

## 8. 相关文档

| 文档 | 管什么 |
|---|---|
| `docs/上下文工程/上下文工程_预算框架.md` | **配额 SSOT**：九段配额、让位顺序、触发阶梯 |
| `docs/上下文工程/上下文工程_调研与差距审计.md` | 现状审计 + 业界/学术调研 + 实施记录 |
| `docs/AgentLoop/AgentLoop_重构设计讨论.md` | loop 的设计决策（路线 A、护栏、事件/记录整合） |
| `docs/AgentLoop/AgentLoop_业界调研与学习路线.md` | 业界 Agent 模式调研 |
| `AGENTS.md` §3 | 本包的内部契约与已知耦合（对外索引） |
| `backend/app/core/agent_tools/README.md` | 上游：工具注册表与执行 |
