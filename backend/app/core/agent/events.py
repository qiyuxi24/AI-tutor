"""Agent 运行 → 前端的消息发射中间件（公开层）。

位置关系：
  - event_bus.py    = 进程内队列基础设施（发布/订阅 + 事件类型常量，任何模块可用）
  - events.py       = 本文件：agent 一次运行的语义封装，是 loop 对「事件投递」的
                      唯一入口。**run 事件不经 event_bus** —— 见下 A2 契约。
  - 后续分发/管理策略（持久化重放、切换底层实现）都落在此层，
    替换/扩展实现只需保证 `sink.emit(event)` 鸭子接口。

A2 契约（2026-10-08 —— 把注释升级成**会报错的约束**）：
  `user_id` 有值 = 本次 run 会往前端推事件 → **必须显式给出投递目标**：
    · `queue=<asyncio.Queue>` —— 本请求私有队列（对话流走这条）；
    · `sink=NullSink()`       —— 显式声明"本路径不推事件"（非流式/脚本/单测）。
  两者都不给 → `__init__` 直接抛 ValueError（漏传立刻炸，不进 LLM）。

  为什么必须报错（2026-09-26「对话没有反应」故障根因）：
  per-user 队列是**一条队列两个消费者** —— 对话流（`chat_service._consume_agent_events`）
  与常驻长连接（`/knowledge/events` → `event_bus.subscribe(user_id=...)`）。两者各自
  `await q.get()`，事件被谁取到是随机的。旧实现在 `queue=None` 时**静默回落**到
  `event_bus.publish(user_id=...)`，于是"请求内事件必须走私有队列"这条不变量，唯一
  保证是**调用点记得传参** —— 而注释不是契约。漏传时长连接把 `text_delta`（正文）
  整段抢走并丢弃（它只认 graph_updated/quiz_ready）→ 对话侧拿到空流 → 前端
  `onDone('')` 删掉 AI 气泡 → 用户侧"对话没有反应"，而后端 `agent_runs` 里回复是完整的。
  现在漏传会**立刻报错**，不再存在"看起来正常、其实串台"的中间态。

  · `user_id` 为空（离线 / 单测 / 无用户上下文）→ 保持"不推、不报错"的既有语义。
  · "不推事件"也必须**显式**：原先 `chat_service.process_message` 靠"传一个没人消费的
    Queue"来表达，队列无人读取；现在用 `NullSink()`，并且**可计数**（`count` = 本次 run
    写出的事件数），这就是「回复到底送到前端没有」观测缺口的后端一半。
"""
import asyncio

from app.core.event_bus import (
    AGENT_START, AGENT_DONE,
    TOOL_START, TOOL_RESULT,
    THINKING, TEXT_DELTA,
)

__all__ = [
    "AgentEventEmitter", "EventSink", "QueueSink", "NullSink",
    "AGENT_START", "AGENT_DONE", "TOOL_START", "TOOL_RESULT", "THINKING", "TEXT_DELTA",
]


class EventSink:
    """事件投递目标（鸭子接口：只需 `emit(event: dict)`）。"""

    def emit(self, event: dict) -> None:  # pragma: no cover - 接口声明
        raise NotImplementedError


class QueueSink(EventSink):
    """直投一个 asyncio.Queue —— 对话流（`chat_service`）的本请求私有队列。

    用 `put_nowait`：agent loop 全链路在同一事件循环上 emit（工具执行虽在 to_thread，
    但 emit 发生在 await 返回后的 loop 协程里），私有队列无需 event_bus 的
    `broadcast`/`notify` 那层 `call_soon_threadsafe` 的推迟一轮。
    """

    __slots__ = ("queue",)

    def __init__(self, queue: "asyncio.Queue") -> None:
        self.queue = queue

    def emit(self, event: dict) -> None:
        self.queue.put_nowait(event)


class NullSink(EventSink):
    """显式声明"本次 run 不向前端推事件"（非流式路径 / 脚本 / 单测）。

    **只计数**：`count` 即本次 run 写出的事件数。绝不回落 event_bus ——
    回落正是 2026-09-26 故障的成因（见模块 docstring）。
    """

    __slots__ = ("count",)

    def __init__(self) -> None:
        self.count = 0

    def emit(self, event: dict) -> None:
        self.count += 1


class AgentEventEmitter:
    """绑定一次 agent run（run_id + user_id）的事件发射器。"""

    def __init__(self, run_id: str, user_id: int | None = None, *,
                 queue: "asyncio.Queue | None" = None,
                 sink: "EventSink | None" = None):
        if queue is not None and sink is not None:
            raise ValueError(
                "事件投递目标只能有一个 —— queue 与 sink 不能同时给"
                "（target 有两个家 = 又一处「谁说了算」不清）"
            )
        if user_id is not None and queue is None and sink is None:
            raise ValueError(
                "run 事件必须显式给出投递目标：queue=<本请求私有队列>（对话流）"
                "或 sink=NullSink()（本路径不推事件）。"
                "静默回落 event_bus 会让常驻长连接抢走 text_delta → 前端拿到空流 "
                "→ 表现为「对话没有反应」（见 events.py 模块 docstring）。"
            )
        self.run_id = run_id
        self.user_id = user_id
        # 投递目标的**唯一**落点；`queue` 属性只是它的派生视图
        self._sink: EventSink | None = (
            sink if sink is not None
            else QueueSink(queue) if queue is not None
            else None
        )

    @property
    def queue(self) -> "asyncio.Queue | None":
        """投递目标是队列时返回该队列，否则 None（兼容既有调用方的读法）。"""
        return self._sink.queue if isinstance(self._sink, QueueSink) else None

    @property
    def events_written(self) -> "int | None":
        """本次 run 写出的事件数（A2-2 观测口径）—— 单一口径 = `NullSink.count`。

        队列目标返回 None：队列事件是否**送达**只有消费方知道，本层不重复记账
        （重复记账 = 又一个"谁说了算"不清）。调用方据此落 agent_runs.events_written。
        """
        return self._sink.count if isinstance(self._sink, NullSink) else None

    def emit(self, event_type: str, **data) -> None:
        """发布一条 run 内事件（自动带 run_id，直投本 run 的投递目标）。

        `user_id` 为空时静默不推 —— 对齐 `run_agent_loop`「user_id=None 不推事件」
        的文档语义，避免离线/测试运行把事件广播给订阅者。
        """
        if self.user_id is None:
            return
        self._sink.emit({"type": event_type, "run_id": self.run_id, **data})
