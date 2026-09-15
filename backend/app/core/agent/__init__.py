"""Agent 运行内核 —— 一次 run 从上下文组装到落库的完整内务。

这个包回答一个问题：**一条学生消息进来后，到最终回答落库之间发生了什么、谁负责哪一段。**
逐模块的"为什么这么分"见本目录 README.md；预算口径见 `docs/上下文工程_预算框架.md`。

子模块（2026-09-15 自 `core/` 平铺归并，原名 → 现名）：
    loop.py      ← core/agent_loop.py       主循环：LLM ↔ 工具 多轮串联（纯编排）
    context.py   ← core/agent_context.py    run 内 API 消息序列的唯一持有/写入点
                                            （含 S8 Tool Result Clearing）
    guard.py     ← core/context_guard.py    发送前守卫：历史超预算时分层压缩
                                            （锚点 + 最近 N 轮 + 规则要点）
    estimator.py ← core/token_estimator.py  发送前 token 预估（prompt/completion/成本）
    events.py    ← core/agent_events.py     事件发射：run_id 注入 + per-user 路由
    store.py     ← core/agent_run_store.py  agent_runs 落库（运行记录唯一事实源）

调用链（谁调谁）：
    chat_service.process_message_stream()
      ├─ guard.trim_history_to_budget(prompt, messages)     ← 发送前分层压缩一次
      └─ loop.run_agent_loop(prompt, messages, kg, user_id)
             ├─ context.AgentContext                     每条消息的唯一写入点（协议约束单点）
             │   └─ clear_old_tool_results()              每轮发送前：较早工具批次正文换占位符
             ├─ estimator.estimate_token_consumption     发送前预估（Layer 1，带 user_id 走历史）
             ├─ events.AgentEventEmitter                 逐事件推 SSE（带 run_id）
             └─ store.save_run                           结束（正常 or 异常）自动落库，
                                                         预估一并写入 token_estimate 字段

边界（什么**不**在这个包里）：
- `core/token_counter.py` 是通用计量工具（quiz / API / 探针脚本共用），留在 core/；
  本包的 guard（预算比较）与 estimator（消耗预估）都建立在它之上；
- 运行记录的**查询**侧（列表/详情/删除/统计）由 `api/v1/agent_runs.py` 直连 `store`；
- 提示词组装（`chat_service`）、工具注册表（`core/agent_tools/`）、事件基础设施
  （`core/event_bus.py`）各自独立，本包只消费它们。
"""
from app.core.agent.context import AgentContext, assistant_snapshot
from app.core.agent.estimator import TokenEstimate, estimate_token_consumption
from app.core.agent.events import AgentEventEmitter
from app.core.agent.guard import trim_history_to_budget
from app.core.agent.loop import AgentRunResult, run_agent_loop

__all__ = [
    "run_agent_loop",
    "AgentRunResult",
    "AgentContext",
    "assistant_snapshot",
    "trim_history_to_budget",
    "AgentEventEmitter",
    "estimate_token_consumption",
    "TokenEstimate",
]
