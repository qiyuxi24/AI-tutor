"""core/llm/call.call_llm：思考开关、空回复重试、截断告警"""
import asyncio
from types import SimpleNamespace

from app.core.llm import call as call_mod
from app.core.llm.thinking import extra_body


def _resp(content: str, finish: str = "stop"):
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    message = SimpleNamespace(content=content)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish)], usage=usage)


def _patch(monkeypatch, responses):
    """把 chat_create 换成按序返回给定响应的假实现，返回收到的 kwargs 列表"""
    seen = []

    async def fake_chat_create(**kwargs):
        seen.append(kwargs)
        return responses[min(len(seen) - 1, len(responses) - 1)]

    monkeypatch.setattr(call_mod, "chat_create", fake_chat_create)
    return seen


def test_thinking_flag_maps_to_extra_body():
    assert "thinking" not in extra_body(True)          # 默认自适应思考
    assert extra_body(True)["reasoning_split"] is True
    assert extra_body(False)["thinking"] == {"type": "disabled"}
    assert extra_body(False)["reasoning_split"] is True  # 响应格式与思考开关独立


def test_call_llm_passes_thinking_off(monkeypatch):
    seen = _patch(monkeypatch, [_resp("正文")])
    asyncio.run(call_mod.call_llm("sys", [{"role": "user", "content": "hi"}],
                                  thinking=False))
    assert seen[0]["extra_body"]["thinking"] == {"type": "disabled"}


def test_call_llm_retries_once_when_empty(monkeypatch):
    """空回复（思考吃满输出预算）重发一次通常就有正文"""
    seen = _patch(monkeypatch, [_resp("", finish="length"), _resp("正文")])
    text = asyncio.run(call_mod.call_llm("sys", [{"role": "user", "content": "hi"}]))
    assert text == "正文"
    assert len(seen) == 2


def test_call_llm_raises_when_always_empty(monkeypatch):
    """重试仍为空 → E-LLM-006（调用方据此跳过该块，但失败已可见）"""
    seen = _patch(monkeypatch, [_resp("", finish="length")])
    try:
        asyncio.run(call_mod.call_llm("sys", [{"role": "user", "content": "hi"}]))
        raise AssertionError("应抛 RuntimeError")
    except RuntimeError as e:
        assert "E-LLM-006" in str(e)
    assert len(seen) == call_mod.EMPTY_RESPONSE_RETRIES + 1


def test_call_llm_returns_text_on_length_truncation(monkeypatch):
    """截断只告警不重试（重发同样会撞上限，由调用方决定是否调大 max_tokens）"""
    seen = _patch(monkeypatch, [_resp('{"nodes":[', finish="length")])
    text = asyncio.run(call_mod.call_llm("sys", [{"role": "user", "content": "hi"}]))
    assert text == '{"nodes":['
    assert seen[0]["max_tokens"] == 2000
