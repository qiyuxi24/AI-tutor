"""服务商方言表 + 按档注入 extra_body（2026-11-16，全离线）。

根因回归：`chat_create` 曾把 `extra_body={"reasoning_split": True}` 无条件发给**所有**模型
（MiniMax-M3 专有字段）。换 deepseek/GLM 后严格网关 400、宽容网关静默忽略后者更贵 ——
不报错，但思考永远拿不到。本文件钉住三件事：
1. 未知 provider **一个字段都不发**（不是"发个默认值试试"）；
2. 候选链里**每一档各自**按自己的方言注入（主备可能属于不同服务商）；
3. 思考字段按网关实际下发的那个读取/回填（reasoning_details vs reasoning_content）。
"""
import asyncio
from types import SimpleNamespace

import pytest

from app.core.agent.context import assistant_snapshot
from app.core.llm import fallback as fallback_mod
from app.core.llm import provider as prov
from app.core.llm import stream as stream_mod


# ─── 方言表解析 ──────────────────────────────────────────────

def test_prefix_resolves_known_providers():
    assert prov.provider_for("MiniMax-M3").key == "minimax"
    assert prov.provider_for("deepseek-reasoner").key == "deepseek"
    assert prov.provider_for("glm-4.6").key == "glm"
    assert prov.provider_for("qwen-plus").key == "qwen"
    assert prov.provider_for("gpt-4o").key == "openai"


def test_unknown_model_falls_back_to_zero_config():
    """未知模型名 → 兜底档，且**不发任何字段**（关键：不是发默认值）。"""
    p = prov.provider_for("my-private-model")
    assert p.key == prov.DEFAULT_KEY
    assert prov.extra_body_for(p, thinking=True) == {}
    assert prov.extra_body_for(p, thinking=False) == {}


def test_explicit_provider_wins_over_name():
    """显式声明优先：自建网关叫 MiniMax-M3 但选了通用档 → 仍按通用档（零字段）。"""
    p = prov.provider_for("MiniMax-M3", "openai-compat")
    assert p.key == prov.DEFAULT_KEY
    assert prov.extra_body_for(p, thinking=True) == {}


def test_explicit_unknown_provider_degrades_to_auto():
    """配置漂移（写了不认识的 provider 键）不该让对话瘫痪 → 退回按模型名判定。"""
    assert prov.provider_for("deepseek-chat", "typo-provider").key == "deepseek"


def test_minimax_dialect():
    p = prov.PROVIDERS["minimax"]
    assert prov.extra_body_for(p, thinking=True) == {"reasoning_split": True}
    body = prov.extra_body_for(p, thinking=False)
    assert body == {"reasoning_split": True, "thinking": {"type": "disabled"}}


def test_deepseek_dialect_sends_no_reasoning_split():
    """DeepSeek 不认 reasoning_split —— 发过去就是给严格网关一个 400。"""
    p = prov.PROVIDERS["deepseek"]
    assert prov.extra_body_for(p, thinking=True) == {}
    assert prov.extra_body_for(p, thinking=False) == {"thinking": {"type": "disabled"}}


def test_is_known_accepts_empty_and_compat_key():
    assert prov.is_known("")                    # 空 = 自动判定
    assert prov.is_known("deepseek")
    assert prov.is_known(prov.DEFAULT_KEY)
    assert not prov.is_known("openrouter")


def test_presets_lists_all_providers_plus_compat():
    keys = [p["key"] for p in prov.presets()]
    assert keys[-1] == prov.DEFAULT_KEY          # 逃生口放末尾
    assert set(prov.PROVIDERS) <= set(keys)
    assert all("base_url" in p and "models" in p for p in prov.presets())


def test_calibration_comes_from_dialect_table():
    """token 校准系数只有一份真值（原先在 token_counter 里另抄了一张表）。"""
    from app.core.token_counter import _get_calibration_factor
    assert _get_calibration_factor("MiniMax-M3") == prov.PROVIDERS["minimax"].calibration
    assert _get_calibration_factor("deepseek-v3") == prov.PROVIDERS["deepseek"].calibration
    assert _get_calibration_factor("glm-4.6") == 1.0      # 原先表里压根没有 glm
    assert _get_calibration_factor("unknown") == 1.0


# ─── 按档注入（候选链里主备可以是不同服务商）─────────────────

class _Rec:
    def __init__(self, outcomes):
        self._q = list(outcomes)
        self.kwargs: list[dict] = []

    async def create(self, **kwargs):
        self.kwargs.append(kwargs)
        item = self._q.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _client(outcomes):
    rec = _Rec(outcomes)
    return SimpleNamespace(chat=SimpleNamespace(completions=rec)), rec


def test_each_candidate_gets_its_own_dialect(monkeypatch):
    """主档 MiniMax 带 reasoning_split；降级到 DeepSeek 档时**不带**。"""
    primary, p_rec = _client([RuntimeError("boom")])
    backup, b_rec = _client(["ok"])
    monkeypatch.setattr(fallback_mod, "LLM_CANDIDATES", [
        (primary, "MiniMax-M3"), (backup, "deepseek-chat"),
    ])
    # 让"不可降级"的错误变成可降级：RuntimeError 不触发降级，这里显式放行
    monkeypatch.setattr(fallback_mod, "should_fallback", lambda e: True)

    asyncio.run(fallback_mod.chat_create(messages=[]))

    assert p_rec.kwargs[0]["extra_body"] == {"reasoning_split": True}
    assert "extra_body" not in b_rec.kwargs[0] or b_rec.kwargs[0]["extra_body"] == {}


def test_thinking_false_only_disables_for_supporting_provider(monkeypatch):
    """批量抽取（thinking=False）在 MiniMax 档发 disabled；通用档什么都不发。"""
    mm, mm_rec = _client(["ok"])
    generic, g_rec = _client(["ok"])
    monkeypatch.setattr(fallback_mod, "LLM_CANDIDATES", [(mm, "MiniMax-M3")])
    asyncio.run(fallback_mod.chat_create(messages=[], thinking=False))
    assert mm_rec.kwargs[0]["extra_body"] == {
        "reasoning_split": True, "thinking": {"type": "disabled"}}

    monkeypatch.setattr(fallback_mod, "LLM_CANDIDATES", [(generic, "some-gateway-model")])
    asyncio.run(fallback_mod.chat_create(messages=[], thinking=False))
    assert g_rec.kwargs[0]["extra_body"] == {}


def test_caller_extra_body_is_respected(monkeypatch):
    """逃生口：调用方自己给了 extra_body → 不再注入（测试与特殊网关用）。"""
    c, rec = _client(["ok"])
    monkeypatch.setattr(fallback_mod, "LLM_CANDIDATES", [(c, "MiniMax-M3")])
    asyncio.run(fallback_mod.chat_create(messages=[], extra_body={"x": 1}))
    assert rec.kwargs[0]["extra_body"] == {"x": 1}


# ─── 思考字段：按实际下发的那个读写 ───────────────────────────

def _msg(**kw):
    base = {"content": "", "tool_calls": None,
            "reasoning_details": None, "reasoning_content": None}
    base.update(kw)
    return SimpleNamespace(**base)


def test_snapshot_keeps_reasoning_content_for_deepseek():
    """DeepSeek 思考模式多轮**必须**回填 reasoning_content，否则 400。"""
    snap = assistant_snapshot(_msg(tool_calls=[
        SimpleNamespace(id="c1", function=SimpleNamespace(name="t", arguments="{}"))],
        reasoning_content="先查图谱"))
    assert snap["reasoning_content"] == "先查图谱"
    assert "reasoning_details" not in snap


def test_snapshot_keeps_both_when_gateway_sends_both():
    """网关同时给了两个字段就都带上 —— 它给过的它必然认得。"""
    snap = assistant_snapshot(_msg(
        tool_calls=[SimpleNamespace(id="c1", function=SimpleNamespace(name="t", arguments="{}"))],
        reasoning_details=[{"type": "reasoning.text", "text": "A"}],
        reasoning_content="A"))
    assert snap["reasoning_details"] == [{"type": "reasoning.text", "text": "A"}]
    assert snap["reasoning_content"] == "A"


def test_snapshot_omits_reasoning_for_plain_models():
    snap = assistant_snapshot(_msg(
        tool_calls=[SimpleNamespace(id="c1", function=SimpleNamespace(name="t", arguments="{}"))]))
    assert "reasoning_details" not in snap and "reasoning_content" not in snap


def test_reasoning_texts_reads_whichever_field_exists():
    assert prov.reasoning_texts(_msg(reasoning_details=[{"text": "甲"}])) == ["甲"]
    assert prov.reasoning_texts(_msg(reasoning_content="乙")) == ["乙"]
    assert prov.reasoning_texts(_msg()) == []


def test_stream_accumulates_reasoning_content(monkeypatch):
    """DeepSeek/GLM 流式思考是 reasoning_content 字符串分片 —— 原先这里只收
    reasoning_details，换 provider 后思考面板全空、且 assistant 快照缺字段 → 400。"""
    async def fake_create(**kwargs):
        async def gen():
            for piece in ("先", "想一下"):
                yield SimpleNamespace(
                    model="deepseek-reasoner", usage=None, choices=[SimpleNamespace(
                        finish_reason=None,
                        delta=SimpleNamespace(content=None, tool_calls=None,
                                              reasoning_content=piece))])
            yield SimpleNamespace(
                model="deepseek-reasoner", usage=None, choices=[SimpleNamespace(
                    finish_reason="stop",
                    delta=SimpleNamespace(content="答案", tool_calls=None))])
        return gen()

    monkeypatch.setattr(stream_mod, "chat_create", fake_create)
    pieces = []
    resp = asyncio.run(stream_mod.chat_create_stream(
        messages=[], on_reasoning=pieces.append))

    assert pieces == ["先", "想一下"]                       # 逐片回调（前端思考面板）
    assert resp.choices[0].message.reasoning_content == "先想一下"
    assert resp.choices[0].message.content == "答案"
