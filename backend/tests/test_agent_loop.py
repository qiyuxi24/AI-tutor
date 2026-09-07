"""
Agent Loop 单元测试（全部离线，mock _chat_once 与 execute_kg_tool）。

覆盖（对齐 docs/AgentLoop_重构设计讨论.md §8）：
- 三种终止：无工具自然结束 / 达 max_rounds 强制收尾 / 工具异常（超时）隔离后继续
- 协议顺序：assistant(含 tool_calls) 快照 → tool 一一对应回填
- 二次 tool_calls 不丢（两轮以上工具链）
- 工具超时护栏触发
- trace 结构完整 + save_trace 落盘
"""
import asyncio
import copy
import json
import time
from types import SimpleNamespace

from app.core import agent_loop
from app.core.agent_loop import run_agent_loop, save_trace


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
    monkeypatch.setattr(agent_loop, "execute_kg_tool", fn or (lambda tc, kg: f"已执行 {tc.function.name}"))


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
    monkeypatch.setattr(agent_loop, "execute_kg_tool", lambda tc, kg: time.sleep(5))

    result = asyncio.run(run_agent_loop("sys", [{"role": "user", "content": "x"}], kg=object(),
                                        tool_timeout_secs=0.2))

    assert result.text == "网页抓取超时了，我基于已有知识回答。"
    r0 = result.rounds[0]
    assert r0["ok"] is False
    assert "超时" in r0["result_head"]
    # 超时错误文案已作为 tool 消息回填，协议仍完整
    assert received[1]["messages"][-1]["role"] == "tool"
    assert "超时" in received[1]["messages"][-1]["content"]


def test_save_trace_writes_and_empty_skips(tmp_path, monkeypatch):
    """有工具动作的 run 落盘 JSONL；无工具动作不落盘。"""
    received = []
    _install_fake_chat(monkeypatch, [
        _resp(_msg(tool_calls=[_tc("add_edge", {"from": "a", "to": "b", "relation": "related"}, tc_id="c1")])),
        _resp(_msg(content="已添加关联。")),
    ], received)
    _install_fake_execute(monkeypatch)

    result = asyncio.run(run_agent_loop("sys", [{"role": "user", "content": "连边"}], kg=object()))
    path = save_trace(result, user_id=7, trace_dir=tmp_path)
    assert path is not None and path.exists()

    run = json.loads(path.read_text(encoding="utf-8"))
    assert run["user_id"] == 7
    assert run["total_llm_calls"] == 2
    assert run["context_tokens"] == 240
    assert len(run["rounds"]) == 1
    assert run["rounds"][0]["tool"] == "add_edge"
    assert run["final_text_head"] == "已添加关联。"

    # 无工具动作：不落盘
    _install_fake_chat(monkeypatch, [_resp(_msg(content="纯回答"))], received)
    plain = asyncio.run(run_agent_loop("sys", [{"role": "user", "content": "hi"}], kg=object()))
    assert save_trace(plain, user_id=7, trace_dir=tmp_path) is None


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
