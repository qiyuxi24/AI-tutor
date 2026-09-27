"""Agent 运行 → 前端的消息发射中间件（公开层）。

位置关系：
  - event_bus.py    = 进程内队列基础设施（发布/订阅 + 事件类型常量，任何模块可用）
  - events.py       = 本文件：agent 一次运行的语义封装，是 loop 对 event_bus 的
                      唯一「分发/管理」入口，loop 不再直调 event_bus。
  - 后续分发/管理策略（多端推送、持久化重放、切换底层实现）都落在此层，
    替换/扩展实现只需保证 emit(event_type, **data) 鸭子接口。

语义保证：
  - emit 自动为事件注入 run_id（SSE 回源契约，见 AGENTS.md §3.4 —— 除
    graph_updated/error 外，agent 事件均带 run_id）
  - user_id 为空 → 静默丢弃，不发布（对齐 run_agent_loop「user_id=None 不推事件」
    的文档语义，避免离线/测试运行把事件广播给订阅者）
  - 传入 queue → 直投该队列，**不进 event_bus 的 per-user 队列**

为什么要有 queue（2026-09-26 故障根因）：
  event_bus 的 per-user 队列是**一条队列**，而它有两个消费者 —— 对话流
  (`chat_service._consume_agent_events`) 与常驻长连接 (`/knowledge/events` →
  `event_bus.subscribe(user_id=...)`)。两者各自 `await q.get()`，事件被谁取到
  是随机的：长连接进对话页就常驻，实测把 `text_delta`（正文）整段抢走并丢弃
  （它只认 graph_updated/quiz_ready）→ 对话侧拿到空流 → 前端 `onDone('')`
  删掉 AI 气泡 → 用户侧"对话没有反应"，而后端 agent_runs 里回复是完整的。
  对话事件改为走**本请求私有队列**后，两条通道互不干扰：per-user 队列留给
  长连接该收的 quiz_ready/graph_updated，对话流独占自己的队列。
"""
import asyncio

from app.core.event_bus import (
    AGENT_START, AGENT_DONE,
    TOOL_START, TOOL_RESULT,
    THINKING, TEXT_DELTA,
    publish,
)

__all__ = [
    "AgentEventEmitter",
    "AGENT_START", "AGENT_DONE", "TOOL_START", "TOOL_RESULT", "THINKING", "TEXT_DELTA",
]


class AgentEventEmitter:
    """绑定一次 agent run（run_id + user_id）的事件发射器。"""

    def __init__(self, run_id: str, user_id: int | None = None,
                 queue: "asyncio.Queue | None" = None):
        self.run_id = run_id
        self.user_id = user_id
        self.queue = queue

    def emit(self, event_type: str, **data) -> None:
        """发布一条 run 内事件（自动带 run_id；有私有队列则直投，否则走 event_bus）。

        `queue` 直投用 `put_nowait`：agent loop 全链路在同一事件循环上 emit
        （工具执行虽在 to_thread，但 emit 发生在 await 返回后的 loop 协程里），
        而 `publish` 的 `call_soon_threadsafe` 会把事件推迟一轮 —— 私有队列无需那层。
        """
        if self.user_id is None:
            return
        if self.queue is not None:
            self.queue.put_nowait({"type": event_type, "run_id": self.run_id, **data})
            return
        publish(event_type, {"run_id": self.run_id, **data}, user_id=self.user_id)
