"""
MiniMax-M3 思考内容适配测试（全离线）。

覆盖（对齐 TODO.md P1「MiniMax 推理内容适配」，2026-09-07）：
- 请求统一携带 `reasoning_split=True`（extra_body），让 content 保持纯净正文
- 兜底剥离函数 `_strip_think_tags` 能删掉 `…` 思考块
- 多轮工具调用快照保留 reasoning_details（官方思维链连续最佳实践）
- 自然终止文本仍会剥离 thinking 兜底
"""
import asyncio
import copy
import json
from types import SimpleNamespace

from app.core import agent_loop, llm_client


# ─── 构造响应 helper（与 test_agent_loop 对齐）───

def _tc(name: str, args: dict, tc_id: str = "call_1") -> SimpleNamespace:
    return SimpleNamespace(
        id=tc_id,
        function=SimpleNamespace(name=name, arguments=json.dumps(args, ensure_ascii=False)),
    )


def _msg(content=None, tool_calls=None, reasoning_details=None) -> SimpleNamespace:
    return SimpleNamespace(content=content, tool_calls=tool_calls,
                           reasoning_details=reasoning_details)


def _resp(msg, prompt_tokens=120) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=msg)],
        usage=SimpleNamespace(prompt_tokens=prompt_tokens),
    )


def _install_fake_chat(monkeypatch, responses: list) -> list:
    queue = list(responses)
    received = []

    async def fake_chat(api_messages, *, temperature, tools=None, max_tokens=2000):
        received.append({"messages": copy.deepcopy(api_messages), "tools": tools})
        item = queue.pop(0)
        if item is None:
            raise RuntimeError("[E-LLM-001] 模拟异常")
        return item

    monkeypatch.setattr(agent_loop, "_chat_once", fake_chat)
    monkeypatch.setattr(agent_loop, "execute_kg_tool", lambda tc, kg: f"已执行 {tc.function.name}")
    return received


# ─── 用例 ─────────────────────────────────────────────────

def test_extra_body_reasoning_split_passed(monkeypatch):
    """所有 LLM 调用透传 reasoning_split=True（含 chat_once）。"""
    captured = {}

    async def fake_with_retry(fn, **kwargs):
        captured.update(kwargs)
        return _resp(_msg(content="ok"))

    monkeypatch.setattr(agent_loop, "_with_retry", fake_with_retry)
    asyncio.run(agent_loop._chat_once([{"role": "user", "content": "hi"}], temperature=0.3))
    assert captured.get("extra_body") == {"reasoning_split": True}


def test_strip_think_tags_removes_block():
    """剥离函数删掉 MiniMax `…` 思考块，保留正文。"""
    txt = "\u03e9我需要先分析用户意图，判断该调用哪个工具\n然后构造参数。\u03e9已为你添加汉诺塔节点。"
    assert llm_client._strip_think_tags(txt) == "已为你添加汉诺塔节点。"


def test_strip_think_tags_noop_when_clean():
    """正文不含思考标签时剥离函数为无操作。"""
    assert llm_client._strip_think_tags("正常的教学正文") == "正常的教学正文"
    assert llm_client._strip_think_tags(None) is None
    assert llm_client._strip_think_tags("") == ""


def test_assistant_snapshot_keeps_reasoning_details():
    """多轮工具调用快照保留 reasoning_details（思维链连续）。"""
    msg = _msg(
        content="\n",
        tool_calls=[_tc("add_knowledge_node", {"id": "hanoi", "name": "汉诺塔", "content": "..."})],
        reasoning_details=[{"type": "reasoning.text", "text": "用户想建汉诺塔节点"}],
    )
    snap = agent_loop._assistant_snapshot(msg)
    assert snap["reasoning_details"] == [{"type": "reasoning.text", "text": "用户想建汉诺塔节点"}]
    assert snap["tool_calls"][0]["function"]["name"] == "add_knowledge_node"


def test_assistant_snapshot_without_reasoning():
    """reasoning_split 未返回思考时快照不冗余。"""
    msg = _msg(content="", tool_calls=[_tc("update_mastery", {"node_id": "a", "mastery": 10})])
    snap = agent_loop._assistant_snapshot(msg)
    assert "reasoning_details" not in snap


def test_natural_finish_strips_think_tags(monkeypatch):
    """自然结束时 content 残留 thinking → 结果剥离为纯正文。"""
    _install_fake_chat(monkeypatch, [
        _resp(_msg(content="\u03e9先想一下你掌握了什么。\u03e9好的，接下来教你汉诺塔的递归算法。"))
    ])
    result = asyncio.run(agent_loop.run_agent_loop("sys", [{"role": "user", "content": "学汉诺塔"}], kg=None))
    assert result.text == "好的，接下来教你汉诺塔的递归算法。"


def test_multi_tool_chain_fills_thinking(monkeypatch):
    """多轮工具链：第二轮请求的 assistant 快照携带上轮 reasoning_details。"""
    rd = [{"type": "reasoning.text", "text": "需要先检索知识库依据"}]
    received = _install_fake_chat(monkeypatch, [
        _resp(_msg(tool_calls=[_tc("rag_search", {"query": "汉诺塔"}, tc_id="c1")], reasoning_details=rd)),
        _resp(_msg(content="已检索依据并完成回答。")),
    ])
    asyncio.run(agent_loop.run_agent_loop("sys", [{"role": "user", "content": "综合检索回答"}], kg=object()))
    last = received[1]["messages"]
    assert last[-2]["role"] == "assistant"
    assert last[-2]["reasoning_details"] == rd