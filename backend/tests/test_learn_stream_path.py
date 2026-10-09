"""`process_message_stream`（前端对话真正走的那条路）的回归测试。

为什么单独一个文件（2026-10-10）
-------------------------------
非流式 `process_message` 有一堆用例，**流式这条 `AsyncGenerator` 却零覆盖**，而它恰是
前端 `/chat/stream` 走的路 —— 2026-10-10 00:25 这里出现过 `learn_target` 改名后的悬空引用
（`NameError: learn_target`），异常被函数体的大 `try/except` 吞掉、只回一帧 `{"error": ...}`，
**全量套件 1561 passed 全绿也抓不到**：用户看到的就是"对话没反应"（本仓库最难排查的一类）。

本文件把流式路径的外围依赖全部换成离线替身（假 `run_agent_loop` / 假提示词 / tmp_path 上的
真实 `KnowledgeGraph`），只锁三件事：

  1. 没有学习上下文时不抛异常：跑得完、产出事件、**没有任何 error 帧**；
  2. `learn_kind == "normal"` 时学习会话真的被绑上（`learning.turn_of(kg)` 有落点）；
  3. 出题兜底 `ensure_section_quiz_scheduled` 在流式路径**确实被调用**（且拿到 loop 的结果对象）。

外加一条开场轮的硬保证：`no_tools=True` 必须传到 `run_agent_loop`（2026-09-30 真机教训：
提示词里写"禁止调工具"无效，只能靠这一层硬关）。

全离线：不碰 LLM / 网络 / 开发库（`KnowledgeGraph` 被替换成 tmp_path 实例的薄代理）。
注：pytest-asyncio 未安装，异步用例统一 `asyncio.run`。
"""
import asyncio
import json
from types import SimpleNamespace

import pytest

from app.core.agent.events import TEXT_DELTA
from app.core.knowledge_graph import LEARN_MARK_UNDERSTOOD, KnowledgeGraph
from app.core.learning import session as learn
from app.services import chat_service

USER_ID = 33
RUN_ID = "run-stream-1"


@pytest.fixture
def kg(tmp_path):
    g = KnowledgeGraph(user_id=USER_ID, data_dir=tmp_path / "kg")
    with g._conn:
        g._conn.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (?, 's', 'x')",
            (USER_ID,))
    g.create_node_with_content({"id": "n1", "name": "二重积分", "subject": "高等数学"}, "正文")
    g.create_node_with_content({"id": "n2", "name": "老节点无小节", "subject": "高等数学"}, "正文")
    g.create_section("n1", "定义与几何意义", content="定义" * 60)
    g.create_section("n1", "计算例题", content="例题" * 60)
    yield g
    g.close()


class _KeepAliveKG:
    """真实 `KnowledgeGraph` 的薄代理 —— 只拦 `close()`。

    生产里 `process_message_stream` 在 `finally` 里 `kg.close()`（每请求一个实例）；
    测试要在这之后继续断言"会话绑到哪个 kg 上了"，所以把 close 变成 no-op，其余属性转发。
    """

    def __init__(self, real):
        self._real = real

    def __getattr__(self, name):
        return getattr(self._real, name)

    def close(self):
        pass


def _install(monkeypatch, kg, *, events=(), text="你好", rounds=None):
    """装好流式路径的全部替身；返回捕获字典（ensure / broadcast / analyze / loop / result）。"""
    cap = {"ensure": [], "broadcast": [], "analyze": [], "loop": [], "kg": None, "result": None}
    proxy = _KeepAliveKG(kg)
    cap["kg"] = proxy

    async def fake_prompt(*args, **kwargs):
        return "SYS-PROMPT", "你好"

    async def fake_grade(*args, **kwargs):
        return ""                      # 判分是增强项：这里只保证不碰真题库

    async def fake_analyze(*args, **kwargs):
        cap["analyze"].append(args)
        await asyncio.sleep(0)

    async def fake_loop(prompt, messages, *, kg=None, user_id=None, event_queue=None,
                        event_sink=None, db_dir=None, no_tools=False):
        cap["loop"].append({"no_tools": no_tools, "user_id": user_id})
        if event_queue is not None:
            for evt in events:
                event_queue.put_nowait(evt)
        await asyncio.sleep(0)
        result = SimpleNamespace(text=text, rounds=list(rounds or []))
        cap["result"] = result
        return result

    def spy_ensure(k, result, **kwargs):
        cap["ensure"].append((k, result))
        return False                   # 只观"是否调用"，不真的排期出题

    monkeypatch.setattr(chat_service, "KnowledgeGraph", lambda **kwargs: proxy)
    monkeypatch.setattr(chat_service, "run_agent_loop", fake_loop)
    monkeypatch.setattr(chat_service, "_build_system_prompt", fake_prompt)
    monkeypatch.setattr(chat_service, "trim_history_to_budget", lambda prompt, msgs: (msgs, None))
    monkeypatch.setattr(chat_service, "auto_grade_pending", fake_grade)
    monkeypatch.setattr(chat_service, "_analyze_and_apply", fake_analyze)
    monkeypatch.setattr(chat_service, "broadcast", lambda *a, **k: cap["broadcast"].append(a))
    monkeypatch.setattr(chat_service, "ensure_section_quiz_scheduled", spy_ensure)
    return cap


def _run_stream(messages, *, current_node=""):
    """跑完整条 SSE 流，返回原始帧列表。"""
    async def _go():
        frames = []
        async for frame in chat_service.process_message_stream(
                messages=messages, user_id=USER_ID, current_node=current_node):
            frames.append(frame)
        await asyncio.sleep(0.05)      # 让 fire-and-forget 的图谱分析跑完，不留下悬挂任务
        return frames

    return asyncio.run(_go())


def _payloads(frames):
    out = []
    for frame in frames:
        body = frame[len("data: "):].strip()
        out.append({"type": "done"} if body == "[DONE]" else json.loads(body))
    return out


# ── 1. 没有学习上下文也要跑完（NameError 那一类故障的守卫）────────────

@pytest.mark.parametrize("current_node", ["", "n2"])
def test_stream_path_without_learn_context_emits_events_and_no_error(
        monkeypatch, kg, current_node):
    """`current_node` 为空（普通对话）或指向**没有小节的老节点** → 学习机制整体不适用。

    此时流式路径必须照常跑完：事件照发、`[DONE]` 收尾、**一帧 error 都没有**。
    改名前那次的 `NameError` 就是被吞成 error 帧的 —— 所以这里断言的是"没有 error 帧"，
    而不是"函数没抛异常"（异常本来就被吞掉，抛不抛在调用方看不出来）。
    """
    events = [
        {"type": "thinking", "run_id": RUN_ID, "text": "想一下"},
        {"type": TEXT_DELTA, "run_id": RUN_ID, "text": "你好"},
    ]
    cap = _install(monkeypatch, kg, events=events, text="你好")

    payloads = _payloads(_run_stream(
        messages=[{"role": "user", "content": "你好"}], current_node=current_node))

    assert [p["type"] for p in payloads if p.get("type") == "error"] == [], \
        f"流式路径吐了错误帧（前端表现为「对话没反应」）：{payloads}"
    assert payloads[-1] == {"type": "done"}
    assert "".join(p.get("token", "") for p in payloads) == "你好"
    assert any(p.get("type") == "final" and p.get("text") == "你好" for p in payloads), \
        "收尾兜底帧（final）必须在 [DONE] 之前下发"
    assert len(cap["ensure"]) == 1, "出题兜底在流式路径也必须被调用一次"
    assert learn.turn_of(cap["kg"]) is None or not learn.turn_of(cap["kg"]).active, \
        "不适用学习模式时不该绑上任何小节"


# ── 2. 学习模式后续轮：会话必须被绑上（B1 的唯一归属对象）────────────

def test_stream_path_binds_learn_session_on_normal_turn(monkeypatch, kg):
    """`learn_kind == "normal"` → `_stash_learn_target` 把"本轮该处理的节"绑进学习会话。

    会话里已有 assistant 消息 = 后续轮；起点 = 顺序上第一个未通过的节（这里 s01 已懂 →
    起点仍是 s01，本轮该出题验证）。绑定是工具层硬校验（B2 越级记账）与出题兜底的前提。
    """
    kg.set_section_learn("n1", "s01", mark=LEARN_MARK_UNDERSTOOD)
    cap = _install(monkeypatch, kg)

    payloads = _payloads(_run_stream([
        {"role": "user", "content": "开始学习"},
        {"role": "assistant", "content": "我们先看第一节"},
    ], current_node="n1"))

    assert [p for p in payloads if p.get("type") == "error"] == [], payloads
    turn = learn.turn_of(cap["kg"])
    assert turn is not None and turn.active, "normal 轮没有绑上学习会话"
    assert (turn.node_id, turn.section_id) == ("n1", "s01")
    assert turn.needs_quiz is True                       # 「已懂」→ 本轮该出题验证
    assert turn.action == learn.ACTION_QUIZ
    assert cap["loop"][0]["no_tools"] is False
    # 落库（跨请求可读回的地基）：落库失败不阻断对话，所以这里骗过了就是真骗过了
    assert learn.load(kg, "n1")["section_id"] == "s01"


def test_stream_path_opener_turn_binds_nothing_and_disables_tools(monkeypatch, kg):
    """开场轮（会话里还没有 assistant 消息）：禁工具 + 不绑目标。

    提示词里写"本轮禁止调用任何工具"实测无效（2026-09-30 真机），`no_tools` 是唯一硬保证；
    这条断言放在流式路径上，因为前端「去学习」走的正是它。
    """
    cap = _install(monkeypatch, kg, text="本节有三小节，我们从第一节开始。")

    payloads = _payloads(_run_stream(
        [{"role": "user", "content": "开始学习「二重积分」"}], current_node="n1"))

    assert [p for p in payloads if p.get("type") == "error"] == [], payloads
    assert cap["loop"][0]["no_tools"] is True
    turn = learn.turn_of(cap["kg"])
    assert turn is not None                      # bind_consent 会挂一个空 turn（同意判定独立）
    assert not turn.active                       # 但没有任何小节落点


# ── 3. 出题兜底必须在流式路径被调用（拿到 loop 的结果对象）──────────

def test_stream_path_calls_quiz_fallback_with_loop_result(monkeypatch, kg):
    """兜底入口拿到的必须是 `run_agent_loop` 的结果对象（它靠 `result.rounds` 判"模型出没出题"）。

    若流式路径漏调（或把别的对象传进去），模型的"口头承诺"就没人接住 —— 学生干等题。
    """
    cap = _install(monkeypatch, kg, text="我出了一道题", rounds=[{"tool": "quiz_generate"}])

    _run_stream([
        {"role": "user", "content": "好，出题吧"},
        {"role": "assistant", "content": "来一道"},
    ], current_node="n1")

    assert len(cap["ensure"]) == 1
    called_kg, called_result = cap["ensure"][0]
    assert called_result is cap["result"], "兜底必须拿到 agent loop 的结果对象（读 result.rounds）"
    assert called_result.rounds == [{"tool": "quiz_generate"}]
    assert called_kg is cap["kg"]
    assert len(cap["broadcast"]) == 1, "图谱刷新广播在流式路径照常发出（graph_updated）"
