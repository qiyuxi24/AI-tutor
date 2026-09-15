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
"""
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

    def __init__(self, run_id: str, user_id: int | None = None):
        self.run_id = run_id
        self.user_id = user_id

    def emit(self, event_type: str, **data) -> None:
        """发布一条 run 内事件（自动带 run_id，精准路由到 user_id；无用户则静默）。"""
        if self.user_id is None:
            return
        publish(event_type, {"run_id": self.run_id, **data}, user_id=self.user_id)
