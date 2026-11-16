"""GraphAnalyzer 的两处 call_llm 必须关思考（全离线，LLM 全程 mock）。

为什么：`call_llm(thinking=True)` 是默认值，而 M3 的思考与正文**共享** max_tokens
预算（见 `core/llm/thinking.py`）。这两处都只回短结构化 JSON，开思考会让预算先被
推理吃掉 → 正文空串或被硬截断 → JSON 解析必失败。同类修复的先例是 `kg_taxonomy`
（`test_llm_call_disables_thinking`）与 `quiz/generator`、`quiz/grader`。

两处**分开断言**：只测一处等于没测到另一半。
"""
import asyncio

import app.core.graph_analyzer as ga


class _FakeKg:
    """GraphAnalyzer 只需要 .nodes / .edges 构造注入上下文（空图即走最短路径）。"""

    nodes = []
    edges = []


def _patch_llm(monkeypatch, reply):
    """替换 call_llm 并返回记录器：captured = {"system": ..., "messages": ..., **kwargs}"""
    captured = {}

    async def fake(system_prompt, messages, **kw):
        captured["system"] = system_prompt
        captured["messages"] = messages
        captured.update(kw)
        return reply

    monkeypatch.setattr(ga, "call_llm", fake)
    return captured


def test_analyze_conversation_disables_thinking(monkeypatch):
    """analyze_conversation 只回 {"suggestions":[...]} → 必须 thinking=False"""
    captured = _patch_llm(monkeypatch, '{"suggestions": [{"action": "none"}]}')
    analyzer = ga.GraphAnalyzer(_FakeKg())

    out = asyncio.run(analyzer.analyze_conversation("什么是队列", "队列是先进先出"))

    assert captured.get("thinking") is False, (
        "analyze_conversation 的 call_llm 未关思考：思考与正文共享 max_tokens，"
        "短 JSON 输出会变成空正文/硬截断"
    )
    assert captured.get("kind") == "graph_analyze"
    # 预算与关思考一起定档：prompt 规则 7 要求每节点完整 Markdown，正文本身就近 2000
    assert captured.get("max_tokens") == ga.GRAPH_ANALYZE_MAX_TOKENS
    # 解析链路照常工作（关思考不该影响返回值形状）
    assert out == {"suggestions": []}


def test_decompose_question_disables_thinking(monkeypatch):
    """decompose_question 只回骨架 JSON（id/name/tags）→ 必须 thinking=False"""
    captured = _patch_llm(
        monkeypatch,
        '{"target": {"id": "queue", "name": "队列"},'
        ' "nodes": [{"id": "array", "name": "数组"}],'
        ' "edges": [{"from": "array", "to": "queue", "relation": "prerequisite"}]}',
    )
    analyzer = ga.GraphAnalyzer(_FakeKg())

    out = asyncio.run(analyzer.decompose_question("怎么学队列"))

    assert captured.get("thinking") is False, (
        "decompose_question 的 call_llm 未关思考：思考与正文共享 max_tokens，"
        "短 JSON 输出会变成空正文/硬截断"
    )
    assert captured.get("kind") == "graph_decompose"
    assert out["target"]["id"] == "queue"
    assert len(out["edges"]) == 1
