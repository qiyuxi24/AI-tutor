# 对话 Agent 循环（Agent Loop）重构 — 设计讨论

> 状态：**讨论中（v0.1 初稿）**，文档随讨论更新，结论进入「12. 决策记录」
> 创建：2026-09-05
> 关联：COMPETITION.md（AIC 技术纵深叙事）、TODO.md、docs/教育资料采集模块_设计讨论.md（结构范式）
> 一句话定位：把 TutorAgent 的"两阶段固定脚本"升级为**可观测、有边界、单循环驱动**的真 Agent 循环

---

## 1. 目标与非目标

### 目标
- 让"学生一问 → AI 一答"变成标准 agent 语义：**思考 → 检索/行动 → 观察 → 再回答**，全程可观测、有终止边界
- 消除"先答后做"：让工具（图谱操作、RAG 检索、网页查询、画像记录）在**可见回复产生之前**就有机会被执行
- 单条用户消息的 LLM 调用从"2~3 次割裂调用"收敛为"1 个循环内串联"，每轮都共享同一份运行上下文
- 为比赛产出**可导出的 agent 运行 trace**（回合数 / 工具 / 耗时 / context 消耗），支撑 AIC 技术报告量化

### 非目标（本期不做）
- **不引入任何 agent 框架**（LangGraph / LangChain / PydanticAI 等）——用最少代码实现标准循环语义
- 不做多 agent 协作 / 规划器（planner）/ 反思（reflection）等进阶模式
- 不改知识图谱存储、不改 RAG pipeline / kb 内部、不改错误码体系（全部只做复用）
- 不做"教学回合规划"（这轮该测掌握还是讲解）——那是更高一层的教育策略问题，见 §10，避免混进本重构

---

## 2. 现状诊断（代码级事实，2026-09-05 实测）

### 2.1 调用链全貌（`/chat/stream` 主流路径）

```
用户消息 POST /api/v1/chat/stream            backend/app/api/v1/chat.py
 ├─ 阶段1（可见） process_message_stream()    backend/app/services/chat_service.py:407
 │    └─ call_llm_stream() 【不带 tools】     backend/app/core/llm_client.py:680
 │         = 纯文本流式回复（RAG 只是预注入，AI 无检索工具可用）
 └─ 流式结束后 BackgroundTasks
      └─ process_background_tools()           backend/app/services/chat_service.py:452
           ├─ call_llm_tools() 【带 tools】   backend/app/core/llm_client.py:726
           │    = LLM → 执行工具 → LLM 收尾（最多 1 轮工具）
           └─ _analyze_and_apply()            后端独立调 GraphAnalyzer
                = 再发一次 LLM 判断改不改图谱  backend/app/core/graph_analyzer.py:200
```

### 2.2 六个核心事实

1. **不是循环，是固定两轮脚本。** `call_llm` / `call_llm_tools` 都是"LLM → 工具 → LLM 再生成一次"就结束。若第二次 LLM 响应又带 `tool_calls`，会被**静默丢弃**（只取 `.content`）。
2. **先答后做，结构性撕裂。** 阶段1 刻意不带 tools（见 `chat_service.py` 头注释"AI 专注于教学引导"），意味着用户看到的答案是**没有检索/没有确认图谱事实能力**下生成的；后台偷偷执行完工具后再发一个 `graph_update` 事件。前端 ChatView 里"已完成的图谱动作"和"AI 刚说的话"是两个世界。
3. **单条消息 2~3 次割裂 LLM 调用。** 流式文本（temp 0.7）+ 后台工具判断（temp 0.3）+ GraphAnalyzer（再一遍图谱上下文），三者各自独立组装 prompt、各自看一遍全量图谱。成本、延迟、额度三重浪费（DASHSCOPE 额度本就紧张）。
4. **上下文管理为零。** `messages` 原样全传不裁剪；图谱上下文**全量注入**——`_build_system_prompt` 里 `detailed=True` 对每个节点取 200 字摘要拼进 system prompt（`chat_service.py:144-153`），节点一多 prompt 每轮膨胀。这是"越聊越蠢/越聊越慢"的直接嫌疑。
5. **零观测。** 全程没有任何"思考 / 工具步骤 / 检索命中"的轨迹输出。AIC 要技术纵深 + 量化报告时，现在连一次运行 trace 都导不出。
6. **实现层小毛病。** `fetch_webpage` 用同步 `httpx.Client`（`llm_client.py:550`）在 async 上下文里阻塞；`rag_search` 靠线程池 hack（`_run_async`，`llm_client.py:365`）；图谱工具无超时护栏（网页抓取有 10s，KG 操作没有）。

### 2.3 要公允的一面（重构时不能破坏的资产）
- 错误码体系 `E-LLM-*` / `E-CHAT-*` 完整，LLM 重试（指数退避+抖动）已实现
- `execute_kg_tool` 已按 `kg` 直操作（无 HTTP 自调用），工具返回友好错误文案
- `fetch_webpage` SSRF 防护（内网/回环/私有 IP 全挡）+ 超时 + 截断
- RAG pipeline：多源隔离 + 单源超时降级 + 异常隔离，绝不抛错
- SSE 流式客户端契约清晰（`frontend/src/api/index.js:sendMessageStream`），改动可向后兼容
- `graph_middleware.slice_graph(kg, subject, board)` 纯函数切片已存在 → **可复用于"按需注入图谱上下文"**

---

## 3. 问题分级清单

| 级别 | 问题 | 现状代码位置 |
|:---:|------|------|
| P0 | 无真循环：二次 `tool_calls` 被丢、无 max_rounds、无终止护栏 | `llm_client.py:649-670, 773-806` |
| P0 | 先答后做：可见文本与工具行动脱节 | `chat_service.py:407-449` + `api/v1/chat.py:44-100` |
| P0 | 图谱全量注入 system prompt，随图谱规模膨胀 | `chat_service.py:114-159` |
| P1 | 单消息 2~3 次割裂 LLM 调用 | `process_background_tools` + GraphAnalyzer |
| P1 | 无步骤 trace / 观测，拿不出技术报告素材 | 全链路 |
| P1 | messages 无裁剪 | `llm_client._build_api_messages` |
| P2 | 工具无超时护栏、同步阻塞调用 | `llm_client.py:525-580, 365-383` |
| P2 | GraphAnalyzer 与 function calling 职责重叠（两套"改图谱"入口） | `graph_analyzer.py` vs `KG_TOOLS` |

---

## 4. 设计原则（沿项目惯例）

1. **去耦合**：Agent 循环 = 纯编排层，不碰知识图谱 / RAG / 错误码内部实现
2. **复用已有**：工具执行逻辑（`execute_kg_tool`）、RAG pipeline、错误码、SSE 事件总线、`graph_middleware.slice_graph` 全复用
3. **测试**：每个行为（循环退出条件、消息回填、护栏）都有单测，mock LLM（httpx MockTransport 有先例），全套旧测试 0 回归
4. **YAGNI 不引框架**：标准 while 循环 20~40 行能解决，不上 LangGraph
5. **先文档后代码**：本文档讨论定稿后，按「9. 里程碑」进入实现

---

## 5. 目标架构：三条路线（推荐 A 先行）

### 路线 A：把假循环升级为真循环 `run_agent_loop()`（必做，改动集中在 `llm_client.py`）

统一两个旧函数（`call_llm` 的工具分支 / `call_llm_tools`）为一个标准循环：

```python
async def run_agent_loop(system_prompt, messages, kg, *, max_rounds=5,
                         tool_timeout_secs=60) -> AgentRunResult:
    api_messages = _build_api_messages(system_prompt, messages)
    trace = []                                   # ← trace 从这里开始长出来
    for _ in range(max_rounds + 1):
        resp = await _with_retry(client.chat.completions.create,
                                 model=MODEL_NAME, messages=api_messages,
                                 temperature=0.3, tools=KG_TOOLS)
        msg = resp.choices[0].message
        if not msg.tool_calls:                   # 自然终止：无工具请求
            return AgentRunResult(text=msg.content or "", trace=trace)
        # 回填 assistant（含 tool_calls），协议顺序不能错
        api_messages.append({"role": "assistant", "content": msg.content or "",
                             "tool_calls": [serialize(tc) for tc in msg.tool_calls]})
        for tc in msg.tool_calls:
            result = await _execute_tool_with_timeout(tc, kg, tool_timeout_secs)
            api_messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
            trace.append({"round": _, "tool": tc.function.name, "result_head": result[:200]})
    return AgentRunResult(text="本轮已达工具轮数上限……", trace=trace)  # 兜底终止
```

**关键语义（比现状强的点）**：
- 循环内每轮都可能再调工具 → 二次 `tool_calls` 不再被丢
- `max_rounds` 硬上限 + 工具级超时 → 不会再无限循环 / 卡死
- 单次入口就产出 `trace` → 观测能力白送
- 旧的 `call_llm_tools` 保留一层薄壳委托（`call_llm` 的旧 `/chat` 路径兼容），老测试不破

**本路线默认解决的问题**：P0-①、P2-⑥、P1 的一半（调用次数收敛需配合 C 或至少让 GraphAnalyzer 从"每轮都跑"改为"仅当本轮有图谱工具动作时才跑"）。

### 路线 B：两阶段合并为一条可见的 agent 轨迹（演示叙事价值最高，改动前后端）

**方向**：不再"阶段1 纯文本先答 + 阶段2 后台补刀"，而是让 agent 循环的每一轮都实时可见：

- SSE 事件类型扩展（向后兼容，老字段不变）：
  | 事件 | 含义 | 载荷示例 |
  |------|------|---------|
  | `text_delta` | 可见文本增量 | `{delta:"..."}`（兼容旧 `token`） |
  | `thinking` | AI 正在思考/规划 | `{text:"..."}` |
  | `tool_start` | 开始执行某工具 | `{tool:"rag_search", args:{...}}` |
  | `tool_result` | 工具执行完毕 | `{tool:"rag_search", summary:"命中2条…"}` |
  | `graph_update` | 图谱已变更（沿用现有） | `{}` |
  | `error` / `done` | 沿用现有 | — |
- 前端 ChatView 增加"agent 活动"行内展示（工具调用以轻量 chip / 展开条显示，参照 Votek 已重构的 Codex/Claude Code 无边框风格）

**这里必须先验证一个事实**：qwen-plus 是否支持 `stream=True` 时流式下发 `tool_calls` delta。支持 → 可以"边流式输出边执行工具"（延迟最优）；不支持 → 退化为"先跑循环（静默/半静默），跑完统一流式输出最终文本"（即用 `tool_result` 事件在前端补一条活动记录，仍然比现状强）。

### 路线 C：GraphAnalyzer 并入单循环（可选，最后做）

图谱更新/画像更新**本来就是工具**（`update_mastery` / `add_knowledge_node` / `update_user_profile` 已在 `KG_TOOLS`）。GraphAnalyzer 是第二套实现同一职责（独立 LLM 调用、独立 JSON 解析、双入口审批逻辑）。并入后：
- agent 在循环内自己决定要不要建节点 / 记笔记 / 提掌握度 → 砍掉一次独立 LLM 调用
- 风险：现在"低置信建议 → 待审核列表"的流程要迁移，需要新的行为验证，**故放最后且可砍**

### 附带工程项（与 A 同批做，成本低收益大）

**① 图谱上下文按需注入（替代全量注入）**
- 现状：`_build_graph_context(kg, detailed=True)` 全量
- 改法：注入时只取与"本轮话题"相关的子图。两级方案：
  - L1（零成本）：沿用对话所在 `subject`/`board`（`slice_graph` 已实现），只注入该学科/板块
  - L2（更好）：用最后一条用户消息过一遍图谱 RAG 粗筛（复用 `graph` source，top_k≈8），只把命中的节点（及其一跳邻居）注入
- 效果：prompt 体积从 O(全部节点) → O(相关节点)，缓解"越聊越蠢"

**② messages 裁剪**
- 简单窗口化：保 system + 最近 N 轮 + 首条（含用户目标），超长丢中段（先不上摘要化，YAGNI）

---

## 6. 关键技术细节

### 6.1 循环契约（必须遵守的协议顺序）
`assistant`(含 tool_calls) → 对**每个** tool_call 一条 `tool` 消息（`tool_call_id` 一一对应）→ 再次请求。顺序错 / 缺 id / 多轮不把 assistant 快照回填，OpenAI 兼容网关会 400。

### 6.2 trace 结构（供评测脚本 + AIC 报告）
```json
{
  "run_id": "…", "user_id": 1, "mode": "adaptive",
  "created_at": "…", "total_llm_calls": 3,
  "rounds": [
    {"round": 0, "tool": "rag_search", "args_head": "{...}", "ok": true,
     "duration_ms": 820, "result_head": "命中2条…"}
  ],
  "context_tokens": 4500,
  "final_text_head": "…"
}
```
写入 `backend/data/traces/{user_id}/{run_id}.jsonl`（trace 目录与 kb/quiz 同数据根，可脚本后处理）。

### 6.3 结构化输出的机会
GraphAnalyzer 并入（路线 C）后，其"输出必须是合法 JSON"的脆弱性（现在靠 `_parse_json_response` 三策略兜底）可换 OpenAI `response_format: {type:"json_schema"}` 根治。**未做 C 前不动**。

### 6.4 工具超时护栏
参考 Votek `agent_loop` 经验（`tool_timeout_secs` 默认 60s + 单工具异常隔离）：循环内每个工具执行包 `asyncio.wait_for`；KG 本地操作基本瞬时，RAG/网页走已有超时，护栏兜底防未来新增慢工具。

---

## 7. 接口边界：不动什么（防止重构失控）

| 资产 | 策略 |
|------|------|
| `execute_kg_tool` 工具执行体 | 原样复用，只外包超时 |
| `KG_TOOLS` 工具 schema | 原样（后续加工具另议） |
| `rag_pipeline` / `kb_manager` / `graph_middleware` | 只读调用 |
| 错误码 `E-LLM-*` 等 | 沿用 |
| SSE 事件总线 `publish` | 沿用；`graph_update` 事件语义不变 |
| 前端 `sendMessageStream` | 兼容（新增事件字段，旧字段保留） |
| `/chat`（legacy 一次性） | 用薄壳委托新循环，行为尽量不变 |

---

## 8. 测试落点

- **单元（mock LLM，离线）**：
  - `run_agent_loop` 三种终止：无工具自然结束 / 工具执行到 `max_rounds` 达上限兜底 / 工具异常隔离后继续
  - 协议顺序：assistant(tool_calls) → tool 一一对应回填
  - 二次 tool_calls 不丢（两轮以上工具链）
  - 工具超时护栏触发
  - trace 结构完整
- **图谱注入**：按 subject/board 切片注入后 prompt 不含无关学科节点（复用 `slice_graph` 测试思路）
- **messages 裁剪**：超长历史保头保尾
- **回归**：现有全库测试 0 回归（重点 `chat` / `llm` / `knowledge` 相关）
- **评测（AIC 素材）**：`backend/scripts/eval_agent_loop.py` —— 脚本化跑 N 条场景，输出回合数/工具成功率/耗时/context 均值的 JSON，作为技术报告量化证据（对齐现有 `scripts/eval_rag.py` 范式）

---

## 9. 里程碑（拟，待讨论确认后落 TODO 文档）

| 批次 | 内容 | 工作量估 | 产出物 |
|:---:|------|:---:|------|
| Batch1 | 路线 A：`run_agent_loop` + 薄壳委托 + 单元测试 + 全库回归 | ~1-2 天 | 真循环落地，trace 可用 |
| Batch2 | 附带工程：图谱按需注入（先 L1 后 L2）+ messages 裁剪 | ~1 天 | context 收敛 |
| Batch3 | 路线 B：SSE 事件扩展 + 前端活动展示 + qwen 流式 tools 能力验证 | ~2 天 | 演示可见 agent 过程 |
| Batch4 | `eval_agent_loop.py` 评测脚本 | ~1 天 | AIC 量化素材 |
| Batch5 | 路线 C（可选）：GraphAnalyzer 并入 + 结构化输出 | ~2 天 | 调用数收敛到 1 循环 |

> 注意 10/15（AIC 与 AI+教育双截止）。若时间紧：**Batch1+Batch4 是硬核**（技术叙事 + 量化证据），Batch3 是 demo 观感加分，Batch5 可砍。

---

## 10. 更高一层的教育策略（本期明确不做，只记录方向）

Agent 循环解决的是"**怎么把一次任务跑对**"，不解决"**这一轮该教还是该练、答对了该推进还是该巩固**"——后者是教育策略层。参考此前调研的 DeepTutor mastery policy / Hermes 错题四步闭环。为避免两层耦合导致重构失焦，本重构**不触碰**掌握度判定逻辑，保持"掌握度=工具 update_mastery 的单一入口"。

---

## 11. 待决问题（需用户拍板）

1. **范围与节奏**：只做 Batch1，还是 Batch1+3（要 demo 可见轨迹）？时间约束下 Batch4 是否同步排？
2. **阶段1是否带工具**：教育场景"先检索依据再回答"更好，但会让首 token 延迟变大。能否接受"循环跑完才开始流式文本"？还是维持"先答后做 + 后台轨迹事件"过渡方案？
3. **qwen-plus 流式 tool_calls 支持度**：是否支持 `stream=True` 下发 tool_calls？需一次真网验证（依赖额度恢复）。
4. **max_rounds 默认值**：5 还是 3？
5. **GraphAnalyzer（路线 C）**：并还是暂留？若暂留，是否改为"仅当本轮执行过图谱工具才跳过其重复分析"这种低成本中间态？
6. **前端展示程度**：是否复刻 Votek 那种 thinking/tool 行内展示？还是仅工具 chip？
7. **messages 裁剪**：先窗口化（简单）还是直接上摘要化（重）？
8. **trace 存储位置**：`backend/data/traces/` 是否 OK？是否要上"可开关"（调试期全开、生产默认开但可关）？

---

## 12. 决策记录

（讨论后按序追加，风格参照采集模块文档的「决策 #N」）

---
