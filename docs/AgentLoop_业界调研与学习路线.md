# Agent Loop 业界调研与学习路线

> 创建：2026-09-06
> 定位：为「对话 Agent 循环（Agent Loop）重构」提供**业界成熟做法的调研整理 + 一手资料清单**，学习时对照代码。结论与落地取舍仍以 `docs/AgentLoop_重构设计讨论.md`（决策载体）为准，本文档给出"业界参照物"。
> 用法：按「二、逐家拆解」对照源码/文档回看；实现 Batch1 前先读「三、对照表」与「四、落地增量建议」。
> 前置阅读：`docs/AgentLoop_重构设计讨论.md`（v0.1，现状诊断 + 路线 A/B/C + 里程碑）

---

## 〇、本文回答的三个问题

1. 业界"真 agent 循环"长什么样 —— 各成熟实现收敛出的**共识骨架**是什么（不是某一家专属设计）。
2. 我们 `docs/AgentLoop_重构设计讨论.md` §2 诊断的六个事实，业界各自怎么处理 —— 差距在哪。
3. Batch1（`run_agent_loop` 落地）应从业界**借鉴哪些具体点**、可去掉哪些重概念 —— 以及按什么顺序读哪些一手资料。

---

## 一、业界共识骨架：所有"真循环"都长一个样

把 OpenAI Agents SDK / Anthropic / LangGraph / smolagents / PydanticAI 的循环实现剥掉外壳后，**收敛到同一张图**：

```text
messages = [system, *history]          # 共享运行上下文（各家叫 run items / state / session）
for round in 0..max_turns:             # ← 循环边界是框架级硬约束
    resp = LLM(messages, tools=tools)  # 每次调用都带 tools —— 增广能力默认在场
    if resp 无 tool_calls:             # 自然终止：模型决定直接回答
        return final_text(resp)
    messages += assistant_snapshot(含 tool_calls)   # 协议顺序：assistant 先回填
    for tc in resp.tool_calls:                       # 一次可并发/串行多工具
        out = execute(tc)                            # 环境反馈 = ground truth
        messages += tool(tool_call_id=tc.id, out)    # 一一对应回填
        trace.append(round, tc, out_head)            # 观测随循环免费长出
# 兜底终止：达 max_turns
return 截断提示 / 最后一次结果
```

六个**共性设计点**（写进任何自研循环都不该省）：

| # | 共性点 | 各家对应物 | 省掉它的后果 |
|:-:|------|-----------|------------|
| 1 | 工具结果回填后**循环内继续请求**（不是只再问一次） | OpenAI SDK run loop；LangGraph 回边；smolagents step | 二次 tool_calls 被丢 = 现在 `call_llm`/`call_llm_tools` 的 P0 病 |
| 2 | **硬轮数上限**（max_turns / recursion_limit / max_iterations）+ 达限兜底文案 | 各家均有 | 无限循环烧额度 |
| 3 | 每轮 LLM 调用**都带 tools**（检索/工具默认在场） | Anthropic "augmented LLM" | "先答后做"：答案在没有检索能力下生成 |
| 4 | 中间产物**可观测**（trace/run items/stream events） | OpenAI Tracing；LangSmith；OTel GenAI | 无技术报告素材、难调试 |
| 5 | 工具异常**隔离不炸循环**（返回错误文本继续走） | smolagents monitor；各家 try/except | 一个工具失败整条消息失败 |
| 6 | 循环是**纯编排**，不含业务（检索/图谱/判分都在工具里） | Anthropic 分层思想 | 业务与循环耦合 → 不可测 |

> 注意：Anthropic 强调 **"augmented LLM（LLM+检索+工具+记忆）是默认积木，而不是可选项"** —— 即**每次**循环内 LLM 调用都应具备工具/检索能力。我们现状恰好相反：阶段1 流式刻意关掉 tools，等于"先让模型裸答，再偷偷补刀"。这是结构性差异，不是实现细节。

---

## 二、逐家拆解（内容整理 + 可借鉴点）

### 1. OpenAI Agents SDK（Python，官方）
- 链接：
  - 官方文档：https://openai.github.io/openai-agents-python/
  - Streaming 事件：https://openai.github.io/openai-agents-python/streaming/
  - Function calling 指南：https://developers.openai.com/api/docs/guides/function-calling
  - 源码拆解（中文，约 800 行核心循环 `Runner._run_impl`）：https://cloud.tencent.com/developer/article/2697330 ｜ https://zhuanlan.zhihu.com/p/2048511634314995691
- 内容整理：
  - 三原语：**Agent**（instructions+tools 的 LLM）、**Handoff**（agent 作为工具互相委托）、**Guardrail**（输入/输出护栏）。我们暂只需 Agent 这一层。
  - `Runner` 驱动 run loop：`LLM → 有 tool_calls/handoff? → 执行/移交 → 回填 → 再 LLM`；无 tool_calls 得 final_output 自然结束。
  - 终止：自然结束 / `max_turns`（超限抛 `MaxTurnsExceeded`）/ guardrail fail-fast / 工具异常中断。
  - 循环内中间步骤保存为 **run items**（含 usage/token），支持结果检查与 resume；**tracing 内置**（trace→span→事件）。
  - streaming 以**事件流**暴露内部状态（token / tool 调用 / 结果 / 终止），UI 实时展示 agent 过程 —— 正是设计讨论路线 B 想要的形态。
- 可借鉴点（对应本项目）：
  - **一个 `run_agent_loop` 统一入口 + 旧函数薄壳委托** 与 SDK "Runner 托管循环、业务方只描述 agent" 同构。
  - **run items = trace 的前身**：我们不需要 SDK 的完整 run 存储，但 trace 结构可对齐"round + tool + args + result + duration"。
  - **max_turns 语义要精确**：一个 turn = 一次 LLM 调用 + 其触发的全部工具执行。我们 while 循环每轮一次 LLM 往返，`max_rounds` 即 turn 数，默认 5 合理。

### 2. Anthropic《Building Effective Agents》+ Claude Code（官方工程博客）
- 链接：https://www.anthropic.com/engineering/building-effective-agents （2024-12-19）
- 内容整理：
  - **Workflows vs Agents 的边界**：workflow=预定义代码路径；agent=LLM 动态决定步骤。教育导学要分清哪些是 workflow（出题、判分）、哪些是 agent（对话导学）。
  - Agent 循环定义："LLM 基于当前上下文规划 → 调工具 → **把工具结果视为 ground truth** → 评估进展 → 继续或暂停请示 → 直到完成或达停止条件"。**"把工具结果当真实状态、不自说自话"** 是对我们"先答后做"最直接的批评。
  - 三原则：**保持简单**（先 LLM API 直接实现，框架起步工具，必须懂底层）；**优先透明**（显式展示规划和思考）；**ACI 工具设计**（像打磨 HCI 一样打磨工具接口，示例/边界/防错；同类型工具勿多）。原文："在 SWE-bench 编码 Agent 里，优化工具的时间比优化总 prompt 还多。"
  - 强调 **eval 闭环**：先简单模式 + 反馈验证，再上多步 agent；复杂度要用评估证明值得才加。
- 可借鉴点：
  - **"每次调用都带检索/工具" 是我们要迁移的方向**（Batch3 合并流式与工具）。
  - **透明优先 → trace +（Batch3）SSE thinking/tool 事件** 与 Claude Code 前端观感一致。
  - **工具即 ACI**：Batch1 不动 `KG_TOOLS`，但文档给我们的警告是——8 个工具已偏多，描述质量（含边界、反例）比数量重要，符合我们知识图谱注释风格。

### 3. LangGraph（官方，借鉴语义不引框架）
- 链接：官方文档 https://langchain-ai.github.io/langgraph/ ；prebuilt ReAct agent：`create_react_agent`（文档 how-tos 内）；中文解读若干（搜索 "LangGraph 最大迭代次数 收敛条件"）
- 内容整理：
  - 把 agent 建模为**有向图**：节点=LLM/工具/函数，**循环=图的回边**。每次状态更新后走回 LLM 节点即"再思考一轮"。
  - **recursion_limit**（默认 25 步）= 我们 max_rounds 的同义概念；达限可截断或转人工（HITL interrupt）。
  - 核心卖点是 **共享 State（schemas 强类型状态对象）**、checkpointer（断点续跑）、interrupt（暂停等人工）。
  - 收敛条件两层：最大迭代（硬）+ 收敛判据（软，如工具不再返回新信息就停）——后者是高级优化，我们 YAGNI。
- 可借鉴点：
  - **循环边界 = 图回边 + recursion_limit** 的视角帮我们想清"谁在驱动循环"：自研 while 更简单，不需要图运行时。
  - **收敛判据**不引入，但 trace 里保留 result_head 可让将来判断"工具是否产生新信息"成为可能（留给以后）。
  - 图/状态抽象、checkpointer、HITL 均**不引**（非目标，见设计讨论 §1）。

### 4. HuggingFace smolagents（最小 code-first agent）
- 链接：https://github.com/huggingface/smolagents ；文档 https://huggingface.co/docs/smolagents
- 内容整理：
  - CodeAgent：**LLM 直接写 Python 代码调工具**（而非 JSON tool_calls），循环里多步规划、模型自决下一步。
  - 关键机制 **Monitors**：token 用量上限、代码执行沙箱、非法 import / 网络拦截 —— 是"护栏内化到循环执行器"的例子。
  - `max_iterations` 硬上限 + 工具级异常 → 返回错误继续。
- 可借鉴点：
  - **monitor 思想 → 我们的工具超时护栏 + （可选）单次循环 token 配额**。Votek 已有同款工程化先例（见 #7），直接搬。
  - 我们**不用 code-first**（JSON tool_calls 对 qwen 兼容网关更稳、更可审计），此点不借鉴。

### 5. PydanticAI（类型安全 + 校验）
- 链接：https://ai.pydantic.dev/ （agents / run、events 文档）
- 内容整理：run loop 与 OpenAI SDK 类似，但主打 **Pydantic 结构化输出 + 依赖注入 + 内置 retry + 事件模型**；最终输出可定义 schema 强校验，天然好测。
- 可借鉴点：
  - **"输出 schema = 护栏"**：设计讨论 §6.3 提到的 `response_format: json_schema` 根治 GraphAnalyzer JSON 脆弱性，与 PydanticAI 同思想（路线 C 做时用，Batch1 不动）。
  - run_stream 的 **structured events** 是路线 B SSE 事件设计的好模板。

### 6. ReAct（学术源头）
- 链接：论文 *ReAct: Synergizing Reasoning and Acting in Language Models*（Yao et al., 2022）：https://arxiv.org/abs/2210.03629
- 内容整理：思想 = 让模型 **reason（thought）与 act（action/工具）交错**，把环境反馈（observation）喂回下一轮。所有 agent 框架都是它的工程化。
- 可借鉴点：**trace 的一条记录就是 ReAct 的 observation**。我们 trace schema 可显式留 `thought/action/observation` 语义（不必逐字对齐，但命名与报告叙事一致，AIC 报告好引用 ReAct）。

### 7. 自家 Votek `agent_loop`（Rust，本项目团队已有工程经验）
- 链接：`agent-desktop/src-tauri/src/agent_loop/`（mod.rs/types.rs/core.rs/tools.rs/retry.rs/emit.rs …，2026-07 拆分，编译零警告）
- 内容整理（直接可复用的决策，比外部参考更贴我们）：
  - `tool_timeout_secs = 60`：循环内每个工具执行包超时（`execute_with_timeout`），KG 本地操作瞬时、网络工具走已有超时，护栏兜底慢工具。
  - `max_consecutive_tool_rounds = 15`：**连续工具轮次计数器**超限后 → 注入系统提示（"请直接给出最终回答"）+ **空工具列表**再调 LLM，强制模型输出文本；仍失败则兜底取最后一个工具结果作为回复。
  - 单工具异常隔离：工具结果（含错误文案）作为 `tool` 消息回填，循环继续，不炸整轮。
  - L2 验证 / 日志脱敏等与 LLM 输出可信度相关机制在 retry.rs / log.rs。
  - 体会：**Votek 用纯 Rust 手写 while 就实现了标准循环 + 护栏 + trace，20~40 行核心 + 若干辅助**——正是设计讨论 §4.4"不引框架，标准 while 能解决"的实证。我们 Python 侧照抄这套边界参数与兜底策略即可，比照搬 LangGraph 便宜得多。

---

## 三、对照表：六个事实 ↔ 业界做法 ↔ Batch1 采用

| 事实（设计讨论 §2） | 业界成熟做法 | Batch1 采用 |
|------|------------|------------|
| ① 固定两轮脚本、二次 tool_calls 被丢 | 所有框架循环内回填 assistant+tool 继续请求（共识骨架点 #1） | `run_agent_loop`：循环到自然终止或 max_rounds |
| ② 先答后做（阶段1 裸答） | Anthropic："工具结果是 ground truth，不能自说自话" | Batch1 阶段仅把旧工具通道（`call_llm_tools`）与 `/chat` legacy 切到新循环，行为兼容；**合并可见回答留 Batch3**（需 qwen 流式 tools 验证） |
| ③ 单消息 2~3 次割裂 LLM 调用 | 1 个 loop 串联、共享同一份 messages | Batch1 后台工具 + 文本收尾收敛为 1 个循环；GraphAnalyzer 重复分析 → 中间态"仅当本轮有图谱工具动作才跑"，并入（路线 C）留 Batch5 |
| ④ 图谱全量注入 system prompt | 各家均强调上下文管理；工具/记忆扩展上下文 | Batch2：L1 subject/board 切片注入 + L2 图谱 RAG 命中注入；messages 窗口裁剪 |
| ⑤ 零观测、无 trace | OpenAI run items/tracing；OTel GenAI；LangSmith | trace 结构随循环长出（对齐 §6.2 schema），写入 `data/traces/`；Batch4 出量化报告 |
| ⑥ 同步阻塞调用、工具无超时 | smolagents monitor；Votek timeout/连续轮护栏 | `execute_with_timeout`（参考 Votek `tool_timeout_secs`）；`fetch_webpage` 改 `httpx.AsyncClient`（顺带，低风险） |

**一句话**：业界没有比设计讨论路线 A 更复杂的"真循环"——我们的差距不是缺框架，而是**把两段割裂脚本改成一段带护栏的 while** + **让观测长出来**。方向 v0.1 文档正确，下述增量建议是对它的校准。

---

## 四、对 Batch1 落地建议的增量（在 v0.1 之上，据业界修正）

1. **达 max_rounds 的兜底文本策略升级**（对齐 Votek #7，优于 v0.1 §5 的固定文案）：
   - v0.1：直接返回"已达工具轮数上限……"。
   - 业界/自家更优：超限后**注入一条系统提示（要求直接给最终回答）+ 清空 tools** 再调一次 LLM；仍失败才兜底返回最后工具结果摘要。这样用户看到的还是自然收尾而非报错文案。
2. **temperature 简化**：循环内统一 `temperature=0.3`（工具调用确定性 + 教育文本确定性都好），不再区分"后台 0.3 / 文本 0.7"。保留争议空间，实现时可一键改。
3. **trace 记录每轮 usage**：OpenAI SDK 自动记录 token/usage；我们 trace 的 `context_tokens` 应从各轮 response.usage 累加，而不是估算——AIC 报告要"context 消耗"就得是真值。
4. **max_rounds 默认 5 确认**：一个 round = 一次 LLM 调用 + 其工具批。教育导学场景绝大多数 1~2 round 收敛，5 是护栏而非常态。（如需可再压到 3。）
5. **协议顺序是硬契约**：assistant(含 tool_calls 快照) → 每 tool_call 一条 `tool` 消息 → 再请求。qwen 兼容网关对乱序/缺 id 返回 400 —— 单测要锁住（见下）。
6. **不借鉴项（YAGNI）**：guardrail 框架、handoff/多 agent、checkpointer、图运行时、收敛判据、code-first 工具、run items 持久化（trace 够用）。

## 五、测试与评测借鉴

- 各家共识：**先简单模式 + eval 闭环再上复杂度**（Anthropic）；PydanticAI/OpenAI SDK 都对测试友好（可注入 fake LLM）。
- 我们已有资产：全库 211 passed 零网络；collector 系列用 `httpx.MockTransport` 离线 mock（`tests/test_collector_http.py` 等）。Batch1 单元测试 mock `client.chat.completions.create`（AsyncMock 或 MockTransport）即可覆盖三种终止 + 协议顺序 + 超时护栏 + 二次 tool_calls 不丢 + trace 结构。
- 评测脚本（Batch4）：对齐 `scripts/eval_rag.py` 范式，产出回合数/工具成功率/耗时/context_tokens 均值 JSON → AIC 技术报告量化素材。

## 六、学习清单（一手资料，按阅读顺序）

### 必读（1~2 小时）
| 资料 | 链接 | 与本项目的关系 |
|------|------|--------------|
| Anthropic《Building Effective Agents》 | https://www.anthropic.com/engineering/building-effective-agents | agent loop 定义 + 增广 LLM 默认在场 + 透明性 → 直接支撑路线 A/B 取舍 |
| OpenAI Agents SDK 官方文档 | https://openai.github.io/openai-agents-python/ | run loop / max_turns / tracing 参照；实现前看一遍即可 |
| ReAct 论文 | https://arxiv.org/abs/2210.03629 | trace 语义（thought/action/observation）出处，报告叙事引用 |
| 自家 Votek `agent_loop/`（Rust） | `agent-desktop/src-tauri/src/agent_loop/core.rs` 等 | 团队自己的标准循环 + 护栏实证，边界参数直接抄 |

### 选读（按需深入）
| 资料 | 链接 | 可捞什么 |
|------|------|---------|
| OpenAI SDK 源码拆解（中文） | https://cloud.tencent.com/developer/article/2697330 ｜ https://zhuanlan.zhihu.com/p/2048511634314995691 | 约 800 行核心循环 `Runner._run_impl`，看别人怎么拆循环/异常/追踪 |
| smolagents（HF） | https://github.com/huggingface/smolagents ｜ https://huggingface.co/docs/smolagents | monitors（token/沙箱护栏）思想 → 我们工具超时护栏 |
| PydanticAI | https://ai.pydantic.dev/ | 结构化输出即护栏；run_stream 事件模型（路线 B 模板） |
| LangGraph 文档 | https://langchain-ai.github.io/langgraph/ | recursion_limit / 回边视角；明确"不引框架只借语义" |
| OpenAI Function calling 指南 | https://developers.openai.com/api/docs/guides/function-calling | 协议顺序/工具描述最佳实践，`KG_TOOLS` 描述质量审计用 |
| Infinite-loop 熔断 / max_iterations 讨论 | 站坑网 AI-agent 教程（搜索 "Agent 停止条件"） | 达限处理的工程细节旁证 |

### 延伸（观测性 / 报告素材期再读）
| 资料 | 链接 | 说明 |
|------|------|------|
| OpenTelemetry GenAI 语义约定 | https://opentelemetry.io/docs/specs/semconv/gen-ai/ | trace 字段命名对齐参考（我们不引 OTel，但 schema 语义可借鉴） |
| LangSmith / Agent 可观测性 | https://www.langchain.com/langsmith/observability | 商业平台做法，报告"观测设计"素材 |
| LLM Observability 生产实战（OTel GenAI 中文） | https://www.oh-bug.com/zh/posts/llm-observability-opentelemetry-genai-production/ | 中文一手，落地 OTel 前读 |

---

## 七、结论一句话

> 业界真循环 ≈ **带 max_turns 硬边界的 while + 每次调用都带 tools + 工具结果回填续跑 + trace 随循环长出**，无一需要框架；设计讨论路线 A 方向正确，落地时按「四」三点增量修正（自然收尾兜底、usage 真值、temperature 统一）即可，**先把 Batch1 做了，Batch3 再谈合并可见回答**。

## 附：与设计讨论文档的关系

- 本文档只提供"业界参照 + 学习清单"，不替代 `docs/AgentLoop_重构设计讨论.md` 的决策职能。
- 用户拍板范围与节奏后，把决策写入设计讨论文档「12. 决策记录」，再落 TODO.md 按批次实施。
- ⚠️ 前置阻塞不变：Batch3 需真网验证 qwen-plus 是否支持 `stream=True` 流式下发 tool_calls（DASHSCOPE 403 未恢复前无法验证）；Batch1/Batch2/Batch4 全离线可测，不依赖真 key。
