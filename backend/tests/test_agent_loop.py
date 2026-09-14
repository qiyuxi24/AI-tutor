"""
Agent Loop 单元测试（全部离线，mock _chat_once 与 execute_kg_tool_async）。

覆盖（对齐 docs/AgentLoop_重构设计讨论.md §8）：
- 三种终止：无工具自然结束 / 达 max_rounds 强制收尾 / 工具异常（超时）隔离后继续
- 协议顺序：assistant(含 tool_calls) 快照 → tool 一一对应回填
- 二次 tool_calls 不丢（两轮以上工具链）
- 工具超时护栏触发
- trace 结构完整 + 传 user_id 自动落库 agent_runs（证据级）
"""
import asyncio
import copy
import inspect
import json
import time
from types import SimpleNamespace

from app.core import agent_loop, agent_run_store as run_store
from app.core.agent_events import (
    AgentEventEmitter,
    AGENT_START, AGENT_DONE,
    TOOL_START, TOOL_RESULT,
    THINKING, TEXT_DELTA,
)
from app.core.agent_loop import run_agent_loop


# ─── 构造假响应 ──────────────────────────────────────────────

def _tc(name: str, args: dict, tc_id: str = "call_1") -> SimpleNamespace:
    return SimpleNamespace(
        id=tc_id,
        function=SimpleNamespace(name=name, arguments=json.dumps(args, ensure_ascii=False)),
    )


def _msg(content=None, tool_calls=None) -> SimpleNamespace:
    return SimpleNamespace(content=content, tool_calls=tool_calls)


def _resp(msg, prompt_tokens=120) -> SimpleNamespace:
    # usage 在 response 层（与真实 OpenAI 兼容网关一致）
    return SimpleNamespace(
        choices=[SimpleNamespace(message=msg)],
        usage=SimpleNamespace(prompt_tokens=prompt_tokens),
    )


def _install_fake_chat(monkeypatch, responses: list, received: list):
    """把 agent_loop._chat_once 换成按序返回 responses 的 fake，记录每次请求的 messages 与 tools。"""
    queue = list(responses)

    async def fake_chat(api_messages, *, temperature, tools=None, max_tokens=2000):
        received.append({
            "messages": copy.deepcopy(api_messages),
            "tools": copy.deepcopy(tools),
        })
        return queue.pop(0)

    monkeypatch.setattr(agent_loop, "_chat_once", fake_chat)


def _install_fake_execute(monkeypatch, fn=None):
    """
    桩掉工具分发入口。

    2026-09-14：agent_loop 改用 `execute_kg_tool_async`（支持协程 handler），
    这里改为桩异步版本 —— 仍接受同步 fn，自动包成协程以兼容旧用例写法。
    """
    if fn is None:
        async def _default(tc, kg):
            return f"已执行 {tc.function.name}"
        fn = _default

    async def _async_dispatch(tc, kg):
        result = fn(tc, kg)
        if inspect.isawaitable(result):
            result = await result
        return result

    monkeypatch.setattr(agent_loop, "execute_kg_tool_async", _async_dispatch)


# ─── 用例 ───────────────────────────────────────────────────

def test_natural_finish_without_tools(monkeypatch):
    """无工具请求 → 一次 LLM 调用自然结束，trace 空。"""
    received = []
    _install_fake_chat(monkeypatch, [_resp(_msg(content="你好，想学什么？"))], received)
    _install_fake_execute(monkeypatch)

    result = asyncio.run(run_agent_loop("sys", [{"role": "user", "content": "hi"}], kg=None))

    assert result.text == "你好，想学什么？"
    assert result.rounds == []
    assert result.total_llm_calls == 1
    assert result.context_tokens == 120
    assert len(received) == 1


def test_single_tool_round_protocol_order(monkeypatch):
    """一轮工具：先回填 assistant(含 tool_calls)，再 tool 一一对应，最后文本。"""
    received = []
    _install_fake_chat(monkeypatch, [
        _resp(_msg(tool_calls=[_tc("update_mastery", {"node_id": "hanoi", "mastery": 50})])),
        _resp(_msg(content="已将「汉诺塔」掌握度更新为 50。")),
    ], received)
    _install_fake_execute(monkeypatch)

    result = asyncio.run(run_agent_loop("sys", [{"role": "user", "content": "我会汉诺塔了"}], kg=object()))

    assert result.total_llm_calls == 2
    assert len(result.rounds) == 1
    r0 = result.rounds[0]
    assert r0["tool"] == "update_mastery"
    assert r0["ok"] is True
    assert r0["args_head"] == '{"node_id": "hanoi", "mastery": 50}'
    assert r0["result_head"].startswith("已执行")

    # 协议顺序：第二轮请求末尾应是 tool 消息，其前是 assistant 快照（含 tool_calls）
    second = received[1]["messages"]
    assert second[-1]["role"] == "tool"
    assert second[-1]["tool_call_id"] == "call_1"
    assert second[-2]["role"] == "assistant"
    assert second[-2]["tool_calls"][0]["function"]["name"] == "update_mastery"


def test_second_tool_calls_not_dropped(monkeypatch):
    """连续两轮工具链（二次 tool_calls 不丢）→ 三轮 LLM、历史逐轮累积。"""
    received = []
    _install_fake_chat(monkeypatch, [
        _resp(_msg(tool_calls=[_tc("rag_search", {"query": "汉诺塔"}, tc_id="c1")])),
        _resp(_msg(tool_calls=[_tc("add_knowledge_node", {"id": "hanoi", "name": "汉诺塔", "content": "..."}, tc_id="c2")])),
        _resp(_msg(content="已建节点并检索到依据。")),
    ], received)
    _install_fake_execute(monkeypatch)

    result = asyncio.run(run_agent_loop("sys", [{"role": "user", "content": "建汉诺塔"}], kg=object()))

    assert result.total_llm_calls == 3
    assert [r["tool"] for r in result.rounds] == ["rag_search", "add_knowledge_node"]
    # 第三轮请求应携带前两轮完整工具往返：... assistant1(tool_calls) + tool1
    third = received[2]["messages"]
    assert len(third) == len(received[0]["messages"]) + 4  # +assistant+tool ×2
    assert third[-1]["role"] == "tool"
    assert third[-1]["tool_call_id"] == "c2"
    assert third[-2]["role"] == "assistant"
    assert third[-2]["tool_calls"][0]["function"]["name"] == "add_knowledge_node"


def test_max_rounds_force_finish(monkeypatch):
    """达 max_rounds 仍请求工具 → 不再执行新工具，注入停止提示 + 空 tools 强制文本收尾。"""
    received = []
    _install_fake_chat(monkeypatch, [
        _resp(_msg(tool_calls=[_tc("rag_search", {"query": "q1"}, tc_id="c1")])),
        _resp(_msg(tool_calls=[_tc("rag_search", {"query": "q2"}, tc_id="c2")])),
        _resp(_msg(content="综合已有信息给出最终回答。")),
    ], received)
    _install_fake_execute(monkeypatch)

    result = asyncio.run(run_agent_loop("sys", [{"role": "user", "content": "x"}], kg=object(), max_rounds=1))

    assert result.text == "综合已有信息给出最终回答。"
    assert result.total_llm_calls == 3
    # 第 1 轮（round 0 < max_rounds）执行了工具；第 2 轮（round 1 == max_rounds）不执行
    assert len(result.rounds) == 1
    # 强制收尾那次请求不带 tools，且追加停止调用指令（以 user 消息规避多 system 风险）
    assert received[2]["tools"] is None
    assert received[2]["messages"][-1]["role"] == "user"
    assert "停止调用工具" in received[2]["messages"][-1]["content"]


def test_force_finish_empty_text_fallback(monkeypatch):
    """强制收尾若模型仍返回空文本 → 兜底提示，不抛异常。"""
    _install_fake_chat(monkeypatch, [
        _resp(_msg(tool_calls=[_tc("update_mastery", {"node_id": "a", "mastery": 10}, tc_id="c1")])),
        _resp(_msg(content=None), prompt_tokens=5),
    ], [])
    _install_fake_execute(monkeypatch)

    result = asyncio.run(run_agent_loop("sys", [{"role": "user", "content": "x"}], kg=object(), max_rounds=0))

    assert "已达上限" in result.text


def test_tool_timeout_guardrail(monkeypatch):
    """单工具超时护栏：超时以错误文案回填，循环继续不炸。"""
    received = []
    _install_fake_chat(monkeypatch, [
        _resp(_msg(tool_calls=[_tc("fetch_webpage", {"url": "https://example.com"}, tc_id="slow")])),
        _resp(_msg(content="网页抓取超时了，我基于已有知识回答。")),
    ], received)
    async def _slow_dispatch(tc, kg):
        await asyncio.sleep(5)   # 模拟慢工具（新分发是异步的，同步 sleep 会阻塞事件循环）
    monkeypatch.setattr(agent_loop, "execute_kg_tool_async", _slow_dispatch)

    result = asyncio.run(run_agent_loop("sys", [{"role": "user", "content": "x"}], kg=object(),
                                        tool_timeout_secs=0.2))

    assert result.text == "网页抓取超时了，我基于已有知识回答。"
    r0 = result.rounds[0]
    assert r0["ok"] is False
    assert "超时" in r0["result_head"]
    # 超时错误文案已作为 tool 消息回填，协议仍完整
    assert received[1]["messages"][-1]["role"] == "tool"
    assert "超时" in received[1]["messages"][-1]["content"]


def test_run_persists_to_agent_runs_evidence(tmp_path, monkeypatch):
    """传 user_id 的 run 自动写入 agent_runs 表（证据级：thinking 全文/完整 args/完整结果）；
    纯文本 run 同样落库（status=ok，evidence 仅 final_text）。"""
    received = []
    _install_fake_chat(monkeypatch, [
        _resp(_msg(tool_calls=[_tc("add_edge", {"from": "a", "to": "b", "relation": "related"}, tc_id="c1")])),
        _resp(_msg(content="已添加关联。")),
    ], received)
    _install_fake_execute(monkeypatch)

    result = asyncio.run(run_agent_loop(
        "sys", [{"role": "user", "content": "连边"}], kg=object(), user_id=7, db_dir=tmp_path,
    ))
    assert result.total_llm_calls == 2
    assert len(result.evidence) == 3  # tool_call + tool_result + final_text

    runs = run_store.list_runs(7, db_dir=tmp_path)
    assert len(runs) == 1
    assert runs[0]["status"] == "ok"
    assert runs[0]["total_llm_calls"] == 2
    assert runs[0]["context_tokens"] == 240
    assert runs[0]["final_text"] == "已添加关联。"

    run = run_store.get_run(7, runs[0]["run_id"], db_dir=tmp_path)
    kinds = [s["kind"] for s in run["evidence"]]
    assert kinds == ["tool_call", "tool_result", "final_text"]
    # 证据级：工具参数与结果完整保留（不截断）
    tc_step = run["evidence"][0]
    assert json.loads(tc_step["arguments"]) == {"from": "a", "to": "b", "relation": "related"}
    assert tc_step["tool_call_id"] == "c1"
    assert run["evidence"][1]["content"] == "已执行 add_edge"
    # 工具参数出现在 evidence 而不仅是截断的 rounds
    assert result.rounds[0]["args_head"] == '{"from": "a", "to": "b", "relation": "related"}'

    # 纯文本 run：同样落库，无工具证据
    _install_fake_chat(monkeypatch, [_resp(_msg(content="纯回答"))], received)
    asyncio.run(run_agent_loop(
        "sys", [{"role": "user", "content": "hi"}], kg=object(), user_id=7, db_dir=tmp_path,
    ))
    runs = run_store.list_runs(7, db_dir=tmp_path)
    assert len(runs) == 2
    plain = run_store.get_run(7, runs[0]["run_id"], db_dir=tmp_path)
    assert plain["status"] == "ok"
    assert [s["kind"] for s in plain["evidence"]] == ["final_text"]


def test_natural_finish_empty_content_fallback(monkeypatch):
    """自然终止但模型返回空 content → 兜底引导文案，不返回空白。"""
    _install_fake_chat(monkeypatch, [_resp(_msg(content=None))], [])
    _install_fake_execute(monkeypatch)

    result = asyncio.run(run_agent_loop("sys", [{"role": "user", "content": "hi"}], kg=None))

    assert "换个说法再问我一次" in result.text


def test_force_finish_llm_error_fallback(monkeypatch):
    """达上限强制收尾时 LLM 调用也失败 → 退化固定文案，不抛异常。"""
    responses = [
        _resp(_msg(tool_calls=[_tc("rag_search", {"query": "q1"}, tc_id="c1")])),
        None,  # 第二次（强制收尾请求）抛异常
    ]
    queue = list(responses)

    async def fake_chat(api_messages, *, temperature, tools=None, max_tokens=2000):
        item = queue.pop(0)
        if item is None:
            raise RuntimeError("[E-LLM-001] 模拟超时")
        return item

    monkeypatch.setattr(agent_loop, "_chat_once", fake_chat)
    _install_fake_execute(monkeypatch)

    result = asyncio.run(run_agent_loop("sys", [{"role": "user", "content": "x"}], kg=object(), max_rounds=0))

    assert "已达上限" in result.text
    assert result.total_llm_calls == 2


# ─── 消息发射中间件（app/core/agent_events.py）───

class _CollectEmitter:
    """注入用哑发射器：只收集事件序列，不触 event_bus。"""

    def __init__(self):
        self.events = []

    def emit(self, event_type, **data):
        self.events.append((event_type, data))


def test_agent_emitter_injects_run_id_and_routes(monkeypatch):
    """AgentEventEmitter.emit 自动注入 run_id 并精准路由 user_id。"""
    calls = []
    monkeypatch.setattr("app.core.agent_events.publish",
                        lambda t, d, user_id: calls.append((t, d, user_id)))
    AgentEventEmitter("run-abc", user_id=9).emit(TEXT_DELTA, text="hi")
    AgentEventEmitter("run-abc", user_id=9).emit(AGENT_DONE, rounds=1, total_llm_calls=2)
    assert calls == [
        (TEXT_DELTA, {"run_id": "run-abc", "text": "hi"}, 9),
        (AGENT_DONE, {"run_id": "run-abc", "rounds": 1, "total_llm_calls": 2}, 9),
    ]


def test_agent_emitter_silent_without_user(monkeypatch):
    """无 user 的 run 静默不发布（不污染全局广播/订阅者）。"""
    calls = []
    monkeypatch.setattr("app.core.agent_events.publish", lambda *a, **k: calls.append(a))
    AgentEventEmitter("run-abc").emit(THINKING, text="x")
    AgentEventEmitter("run-abc").emit(TEXT_DELTA, text="y")
    assert calls == []


def test_loop_events_flow_through_emitter(monkeypatch):
    """注入 emitter：一轮工具的 run 事件顺序 = start → tool_start/result → text_delta → done。"""
    received = []
    _install_fake_chat(monkeypatch, [
        _resp(_msg(tool_calls=[_tc("update_mastery", {"node_id": "a", "mastery": 1}, tc_id="c1")])),
        _resp(_msg(content="已更新。")),
    ], received)
    _install_fake_execute(monkeypatch)
    fake = _CollectEmitter()

    asyncio.run(run_agent_loop("sys", [{"role": "user", "content": "x"}],
                               kg=object(), emitter=fake))

    kinds = [t for t, _ in fake.events]
    assert kinds == [AGENT_START, TOOL_START, TOOL_RESULT, TEXT_DELTA, AGENT_DONE]
    assert fake.events[0][1]["max_rounds"] == 5
    assert fake.events[-1][1]["rounds"] == 1
    assert fake.events[-1][1]["total_llm_calls"] == 2
