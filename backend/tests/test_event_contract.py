"""Agent 事件通道契约（重构 Phase A2）—— 让「静默回落」不可能再回来。

为什么要单独一个文件（P1-4；与 2026-09-26「对话没反应」故障同源）：
  `core/agent/events.py` 原先在 `queue=None` 时**静默回落**到
  `event_bus.publish(user_id=...)`。也就是说"请求内事件必须走本请求私有队列"
  这条不变量，唯一保证是**调用点记得传参** —— 注释不是契约。

  一旦漏传：thinking / text_delta / tool_* 就灌进 per-user 广播队列，被常驻长连接
  `/knowledge/events` 抢走并丢弃 → 对话侧拿到空流 → 前端 `onDone('')` 删掉 AI 气泡
  → 用户侧"对话没有反应"，而后端 `agent_runs` 里回复是完整的（最难排查的一类）。

本文件把那条注释升级成**会报错的契约**：
  1. `user_id` 有值 = 本次 run 会推事件 → 必须**显式**给出投递目标（`queue` 或 `sink`），
     否则构造即抛 `ValueError` —— 漏传立刻炸，而不是静默串台；
  2. "本路径不推事件"是**显式选择**（`NullSink`），并且**可计数**
     —— 这就是 A2-2 要的"写出事件数"观测口径；
  3. `user_id` 为空（离线 / 单测 / 脚本）→ 保持"不推、不报错"。

断言风格说明：本文件用**结构性断言**而非 spy —— run 事件不该经 event_bus，这件事
最强也最简单的证明是「events 模块里根本没有那条路径」（`test_run_events_have_no_event_bus_path`）。
spy `event_bus.publish` 反而会漏判：若有人写回 `from app.core.event_bus import publish`
（模块加载时绑定函数对象），事后 patch `event_bus.publish` 拦不住它。

落点：`core/agent/events.py`（契约本体）+ `core/agent/loop.py::run_agent_loop`（入口守约）。
`event_bus.publish` 拆 `broadcast` / `notify`（A2-1b）与 `pending_notices` 落库补拉（A2-3）
均已于 2026-10-09 落盘 —— 通知侧的行为断言分别在 `tests/test_event_bus.py`
与 `tests/test_event_notices.py`，本文件只守「run 事件不经 event_bus」这条。
"""
import asyncio

import pytest

from app.core.agent import events as agent_events
from app.core.agent.events import AgentEventEmitter, NullSink
from app.core.agent.loop import run_agent_loop
from app.core.event_bus import TEXT_DELTA, THINKING


# ── 契约 1：会推事件的 run 必须显式给出投递目标 ──


def test_emitter_with_user_id_and_no_target_raises():
    """漏传 queue/sink 且 user_id 有值 → 构造即抛（不静默回落 event_bus）。"""
    with pytest.raises(ValueError, match="queue|sink"):
        AgentEventEmitter("r1", user_id=1)


def test_emitter_with_user_id_and_no_target_raises_positionally():
    """位置参数写法（loop.py 用的就是这种）同样要抛。"""
    with pytest.raises(ValueError):
        AgentEventEmitter("r1", 1)


def test_emitter_without_user_id_is_allowed_and_silent():
    """user_id 为空（离线 / 单测）→ 不推、不报错（保留既有语义）。"""
    em = AgentEventEmitter("r1", user_id=None)
    em.emit(TEXT_DELTA, text="不该有人收到")   # 不抛
    assert em.queue is None


def test_emitter_rejects_both_queue_and_sink():
    """queue 与 sink 同时给 = 投递目标有两个家 → 拒绝（单一归属）。"""
    with pytest.raises(ValueError):
        AgentEventEmitter("r1", user_id=1, queue=asyncio.Queue(), sink=NullSink())


# ── 契约 2：显式 NullSink —— 「本次 run 不推事件」且可计数 ──


def test_null_sink_counts_events():
    """NullSink = 显式不推；事件被计数（A2-2 的"写出事件数"口径）。"""
    sink = NullSink()
    em = AgentEventEmitter("r1", user_id=1, sink=sink)
    em.emit(THINKING, text="嗯")
    em.emit(TEXT_DELTA, text="正文")

    assert sink.count == 2


def test_null_sink_count_is_per_instance():
    """计数归属单个 run，不跨 run 串账。"""
    s1, s2 = NullSink(), NullSink()
    AgentEventEmitter("r1", user_id=1, sink=s1).emit(TEXT_DELTA, text="a")
    assert (s1.count, s2.count) == (1, 0)


# ── 契约 3：有投递目标时，事件带 run_id 且只进该目标 ──


def test_queue_target_gets_event_with_run_id():
    """传 queue → 事件直投该队列，带 run_id（SSE 回源契约）。"""
    q: asyncio.Queue = asyncio.Queue()
    AgentEventEmitter("r1", user_id=1, queue=q).emit(TEXT_DELTA, text="正文")

    assert q.get_nowait() == {"type": TEXT_DELTA, "run_id": "r1", "text": "正文"}


def test_emitter_queue_property_exposes_only_the_queue_target():
    """`emitter.queue` 仍可读（兼容既有调用方），但它是 sink 的**派生视图**，不是第二个家。"""
    q: asyncio.Queue = asyncio.Queue()
    assert AgentEventEmitter("r1", user_id=1, queue=q).queue is q
    assert AgentEventEmitter("r1", user_id=1, sink=NullSink()).queue is None


def test_run_events_have_no_event_bus_path():
    """结构性守卫：events 模块里**不存在** publish 符号 → 回落路径物理上不可能回来。

    （比 spy 更强：spy 拦不住"模块加载时 `from ... import publish` 绑定函数对象"
    这种写法，`hasattr` 能。）
    """
    assert not hasattr(agent_events, "publish")


# ── 契约 4：run_agent_loop 这一层也守约（漏传在入口就炸，不进 LLM）──


def test_run_agent_loop_with_user_id_and_no_event_target_raises(tmp_path):
    """`run_agent_loop(user_id=X)` 不给 event_queue/event_sink → 立刻 ValueError。

    端到端守卫：若 loop.py 的
    `AgentEventEmitter(run_id, user_id, queue=event_queue, sink=event_sink)`
    被改回宽松写法，或有人给 events.py 加回 `queue=None` 回落分支，本用例变红。
    抛点在构造 emitter 处（早于任何 LLM 调用），所以本用例零网络。
    """
    with pytest.raises(ValueError):
        asyncio.run(run_agent_loop(
            "sys", [{"role": "user", "content": "x"}],
            kg=object(), user_id=7, db_dir=tmp_path,
        ))


# `event_sink=NullSink()` 让 run 正常跑完 + 计数可用这条，放在
# `tests/test_agent_loop.py::test_run_accepts_explicit_null_sink`
# —— 那里已有 mock LLM 的 helper，避免在本文件重抄一份假响应构造。
