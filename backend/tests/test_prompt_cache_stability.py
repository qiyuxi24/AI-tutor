"""前缀链不变量回归（P0-① / P0-②，2026-09-28）。

背景（实测机理，见 docs/上下文工程/上下文工程_Prompt缓存命中率_调研与优化方案.md §1.5）：
    MiniMax 的缓存命中 = "存在一条曾**完整发过**的请求，它是本轮请求的逐字节前缀"。
    ⇒ 任何"改写已发过的消息"（如清工具结果）都是**剪链**，代价不是被改那几条的 token，
      而是**其后全部前缀按全价（4.20 元/M）重算**（命中价只要 0.84 元/M）。
    本文件把两条不变量锁成回归：① 同一 run 内历史只增不改（前缀链）；
    ② 强制收尾保留 `tools`（工具定义排在最前，丢了必 0 命中）。

为什么不用 `@pytest.mark.asyncio`：本项目**未装 pytest-asyncio**，异步一律
`asyncio.run(...)` 包在同步函数里（与 tests/test_llm_usage.py 同款）。
"""
import asyncio
import copy
import json
from types import SimpleNamespace

from app.core.agent import loop as agent_loop
from app.core.agent.context import TOOL_RESULT_CLEARED
from app.core.agent.loop import run_agent_loop


def _tc(name: str, args: dict, tc_id: str = "call_1") -> SimpleNamespace:
    return SimpleNamespace(
        id=tc_id,
        function=SimpleNamespace(name=name, arguments=json.dumps(args, ensure_ascii=False)),
    )


def _msg(content=None, tool_calls=None) -> SimpleNamespace:
    return SimpleNamespace(content=content, tool_calls=tool_calls)


def _resp(msg, prompt_tokens=120) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=msg)],
        usage=SimpleNamespace(prompt_tokens=prompt_tokens),
    )


def _install(monkeypatch, responses: list, received: list, *, tool_result: str):
    """桩掉 `_chat_once`（记录每次请求的 messages/tools/tool_choice）与工具分发。"""
    queue = list(responses)

    async def fake_chat(api_messages, *, temperature, tools=None,
                        tool_choice=None, max_tokens=2000, user_id=None):
        received.append({
            "messages": copy.deepcopy(api_messages),
            "tools": copy.deepcopy(tools),
            "tool_choice": tool_choice,
        })
        return queue.pop(0)

    async def fake_execute(tc, kg):
        return tool_result

    monkeypatch.setattr(agent_loop, "_chat_once", fake_chat)
    monkeypatch.setattr(agent_loop, "execute_kg_tool_async", fake_execute)


def _tool_msgs(req: dict) -> list[dict]:
    return [m for m in req["messages"] if m.get("role") == "tool"]


# ─── (a) 默认不清理：内容很大但未越预算 → 已发过的 tool 正文逐字节不变 ───

def test_tool_results_untouched_when_under_budget(monkeypatch):
    """多轮工具、正文很大但**未越预算**时，任何一次请求里的 tool 正文都不得被改写。"""
    monkeypatch.setattr(agent_loop, "AGENT_CLEAR_TOOL_RESULTS_TOKENS", 10 ** 9)  # 永不越线
    big = "检索到的知识片段正文。" * 300
    received = []
    _install(monkeypatch, [
        _resp(_msg(tool_calls=[_tc("rag_search", {"query": "q1"}, tc_id="c1")])),
        _resp(_msg(tool_calls=[_tc("rag_search", {"query": "q2"}, tc_id="c2")])),
        _resp(_msg(tool_calls=[_tc("rag_search", {"query": "q3"}, tc_id="c3")])),
        _resp(_msg(content="讲完了。")),
    ], received, tool_result=big)

    result = asyncio.run(run_agent_loop("sys", [{"role": "user", "content": "x"}], kg=object()))

    assert result.text == "讲完了。"
    for req in received:
        assert all(t["content"] == big for t in _tool_msgs(req)), "未越预算却改写了历史 = 剪链"
    assert len(_tool_msgs(received[-1])) == 3


# ─── (b) 前缀链不变量：本轮请求以上一轮请求为逐字节前缀 ───

def test_request_is_byte_prefix_of_next_request(monkeypatch):
    """同一 run 内第 N+1 次请求的 messages 必须以前 N 次请求的 messages 为逐字节前缀。"""
    monkeypatch.setattr(agent_loop, "AGENT_CLEAR_TOOL_RESULTS_TOKENS", 10 ** 9)
    big = "工具结果正文。" * 300
    received = []
    _install(monkeypatch, [
        _resp(_msg(tool_calls=[_tc("rag_search", {"query": "q1"}, tc_id="c1")])),
        _resp(_msg(tool_calls=[_tc("rag_search", {"query": "q2"}, tc_id="c2")])),
        _resp(_msg(content="收尾。")),
    ], received, tool_result=big)

    asyncio.run(run_agent_loop("sys", [{"role": "user", "content": "x"}], kg=object()))

    assert len(received) == 3
    for prev, nxt in zip(received, received[1:]):
        n = len(prev["messages"])
        assert nxt["messages"][:n] == prev["messages"], "本轮请求破坏了上一轮的缓存前缀链"
    # 且只追加：消息数单调不减（前缀 = 上一轮整条被复用）
    lengths = [len(r["messages"]) for r in received]
    assert lengths == sorted(lengths)


# ─── (c) 越线才清理：阈值调小 → 清理发生，且配对不变 ───

def test_clearing_triggers_only_over_threshold(monkeypatch):
    """阈值调小后清理发生；tool_call_id / assistant tool_calls 配对必须原样（否则服务端 400）。"""
    monkeypatch.setattr(agent_loop, "AGENT_CLEAR_TOOL_RESULTS_TOKENS", 1)  # 小阈值 → 每轮越线
    big = "检索到的知识片段正文。" * 200
    received = []
    _install(monkeypatch, [
        _resp(_msg(tool_calls=[_tc("rag_search", {"query": "q1"}, tc_id="c1")])),
        _resp(_msg(tool_calls=[_tc("rag_search", {"query": "q2"}, tc_id="c2")])),
        _resp(_msg(tool_calls=[_tc("rag_search", {"query": "q3"}, tc_id="c3")])),
        _resp(_msg(content="好了，讲完了。")),
    ], received, tool_result=big)

    result = asyncio.run(run_agent_loop("sys", [{"role": "user", "content": "x"}], kg=object()))

    assert result.text == "好了，讲完了。"
    msgs = received[3]["messages"]
    tools = [m for m in msgs if m.get("role") == "tool"]
    assert len(tools) == 3
    assert tools[0]["content"] == TOOL_RESULT_CLEARED       # 最早批次被清
    assert tools[1]["content"] == big and tools[2]["content"] == big
    assert [m["tool_call_id"] for m in tools] == ["c1", "c2", "c3"]   # 配对不动
    assistants = [m for m in msgs if m.get("tool_calls")]
    assert [[tc["id"] for tc in m["tool_calls"]] for m in assistants] == [["c1"], ["c2"], ["c3"]]


# ─── (d) 强制收尾保留 tools + tool_choice="none"（含降级）───

def test_force_finish_keeps_tools_and_sets_tool_choice_none(monkeypatch):
    """强制收尾保留 tools（工具定义在最前，丢了必 0 命中）且 tool_choice="none"。"""
    received = []
    _install(monkeypatch, [
        _resp(_msg(tool_calls=[_tc("rag_search", {"query": "q1"}, tc_id="c1")])),
        _resp(_msg(tool_calls=[_tc("rag_search", {"query": "q2"}, tc_id="c2")])),  # 达上限仍要工具
        _resp(_msg(content="最终总结。")),
    ], received, tool_result="工具结果。")

    result = asyncio.run(run_agent_loop("sys", [{"role": "user", "content": "x"}],
                                        kg=object(), max_rounds=1))

    assert result.text == "最终总结。"
    finish = received[-1]
    assert finish["tools"] == received[0]["tools"]     # 与轮内 tools 逐字节一致
    assert finish["tool_choice"] == "none"
    assert finish["messages"][-1]["role"] == "user"    # 追加的停止调用指令


def test_force_finish_downgrades_when_tool_choice_rejected(monkeypatch):
    """网关拒绝 `tool_choice="none"`（典型 400）→ 降级为不带 tools 收尾，仍拿到最终文本。"""
    received = []
    queue = [
        _resp(_msg(tool_calls=[_tc("rag_search", {"query": "q1"}, tc_id="c1")])),
        _resp(_msg(tool_calls=[_tc("rag_search", {"query": "q2"}, tc_id="c2")])),
        _resp(_msg(content="降级后仍给出的最终文本。")),
    ]

    async def fake_chat(api_messages, *, temperature, tools=None,
                        tool_choice=None, max_tokens=2000, user_id=None):
        received.append({"tools": tools, "tool_choice": tool_choice})
        if tool_choice == "none":       # 模拟网关不支持该参数
            raise RuntimeError("[E-LLM-400] tool_choice not supported")
        return queue.pop(0)

    async def fake_execute(tc, kg):
        return "工具结果。"

    monkeypatch.setattr(agent_loop, "_chat_once", fake_chat)
    monkeypatch.setattr(agent_loop, "execute_kg_tool_async", fake_execute)

    result = asyncio.run(run_agent_loop("sys", [{"role": "user", "content": "x"}],
                                        kg=object(), max_rounds=1))

    assert result.text == "降级后仍给出的最终文本。"
    finish_calls = received[2:]           # 前 2 次是轮内调用
    assert finish_calls[0]["tool_choice"] == "none" and finish_calls[0]["tools"] is not None
    assert finish_calls[1]["tool_choice"] is None and finish_calls[1]["tools"] is None
    # 轮内 2 次 + 收尾尝试 2 次（降级重试也如实计数）
    assert result.total_llm_calls == 4
