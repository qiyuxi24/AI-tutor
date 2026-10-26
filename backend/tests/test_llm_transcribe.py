"""
语音转写出口单元测试（全离线：桩掉候选链，不碰真实网络与额度）。

覆盖（对齐 `llm/transcribe.py` 的模块说明）：
- 未配置 ASR 模型名 → ValueError（**不猜默认模型**：对话模型发到
  /audio/transcriptions 必然 400，猜一个只会让用户看到更难懂的报错）
- 空音频 / 超体积在**本地先拒**，不产生任何网络调用
- 请求里的 model 必须是 ASR 模型名，而不是候选链里的对话模型名
- 401 认证失败 → 静默降级到下一档；400 请求侧错误 → 不降级（换档也没用）
- 返回正文两端去空白
"""
import asyncio
from types import SimpleNamespace

import httpx
import pytest
from openai import APIStatusError

from app.core.llm import transcribe as transcribe_mod


def _api_err(status: int) -> APIStatusError:
    req = httpx.Request("POST", "http://test")
    return APIStatusError(
        f"HTTP {status}", response=httpx.Response(status, request=req), body=None
    )


class _FakeTranscriptions:
    """模拟 client.audio.transcriptions：按序弹出结果（Exception 直接抛出）。"""

    def __init__(self, outcomes: list):
        self._q = list(outcomes)
        self.calls = 0
        self.last: dict = {}

    async def create(self, **kwargs):
        self.calls += 1
        self.last = kwargs
        item = self._q.pop(0)
        if isinstance(item, Exception):
            raise item
        return SimpleNamespace(text=item)


def _cand(outcomes: list):
    """返回 (候选链元素, 可断言的假 transport)"""
    t = _FakeTranscriptions(outcomes)
    return (SimpleNamespace(audio=SimpleNamespace(transcriptions=t)), "chat-model", None), t


@pytest.fixture()
def asr_model(monkeypatch):
    monkeypatch.setattr(transcribe_mod.settings, "asr_model", "whisper-1")


# ── 参数与体积：本地先拒 ──

def test_missing_model_name_raises(monkeypatch):
    monkeypatch.setattr(transcribe_mod.settings, "asr_model", "")
    with pytest.raises(ValueError, match="ASR_MODEL"):
        asyncio.run(transcribe_mod.transcribe_audio(b"abc", "a.mp3"))


def test_empty_audio_raises(asr_model):
    with pytest.raises(ValueError, match="为空"):
        asyncio.run(transcribe_mod.transcribe_audio(b"", "a.mp3"))


def test_oversize_audio_rejected_before_any_network_call(asr_model, monkeypatch):
    monkeypatch.setattr(transcribe_mod, "MAX_AUDIO_BYTES", 8)
    touched = []
    monkeypatch.setattr(transcribe_mod, "resolve_candidates",
                        lambda uid: touched.append(1) or [])
    with pytest.raises(ValueError, match="音频过大"):
        asyncio.run(transcribe_mod.transcribe_audio(b"123456789", "a.mp3"))
    assert not touched, "体积校验必须发生在取候选/发请求之前"


# ── 模型名与返回 ──

def test_uses_asr_model_not_chat_model(asr_model, monkeypatch):
    cand, t = _cand(["  转写正文  "])
    monkeypatch.setattr(transcribe_mod, "resolve_candidates", lambda uid: [cand])

    text = asyncio.run(transcribe_mod.transcribe_audio(b"audio", "a.mp3", user_id=1))

    assert text == "转写正文"
    assert t.last["model"] == "whisper-1", "必须是 ASR 模型名，不是候选链里的对话模型"
    assert t.last["file"] == ("a.mp3", b"audio")


def test_explicit_model_overrides_settings(asr_model, monkeypatch):
    cand, t = _cand(["ok"])
    monkeypatch.setattr(transcribe_mod, "resolve_candidates", lambda uid: [cand])

    asyncio.run(transcribe_mod.transcribe_audio(b"a", "a.mp3", model="qwen3-asr-flash"))

    assert t.last["model"] == "qwen3-asr-flash"


def test_language_only_sent_when_given(asr_model, monkeypatch):
    cand, t = _cand(["ok"])
    monkeypatch.setattr(transcribe_mod, "resolve_candidates", lambda uid: [cand])

    asyncio.run(transcribe_mod.transcribe_audio(b"a", "a.mp3"))
    assert "language" not in t.last, "没传语言就不要带这个字段（部分服务端不认）"

    cand2, t2 = _cand(["ok"])
    monkeypatch.setattr(transcribe_mod, "resolve_candidates", lambda uid: [cand2])
    asyncio.run(transcribe_mod.transcribe_audio(b"a", "a.mp3", language="zh"))
    assert t2.last["language"] == "zh"


# ── 降级语义（与对话出口一致）──

def test_auth_error_falls_back_to_next_candidate(asr_model, monkeypatch):
    bad, bad_t = _cand([_api_err(401)])
    good, good_t = _cand(["备用转写成功"])
    monkeypatch.setattr(transcribe_mod, "resolve_candidates", lambda uid: [bad, good])

    assert asyncio.run(transcribe_mod.transcribe_audio(b"a", "a.mp3")) == "备用转写成功"
    assert bad_t.calls == 1 and good_t.calls == 1


def test_request_side_error_does_not_fallback(asr_model, monkeypatch):
    bad, _bad_t = _cand([_api_err(400)])
    good, good_t = _cand(["不该到这里"])
    monkeypatch.setattr(transcribe_mod, "resolve_candidates", lambda uid: [bad, good])

    with pytest.raises(RuntimeError):
        asyncio.run(transcribe_mod.transcribe_audio(b"a", "a.mp3"))
    assert good_t.calls == 0, "400 是请求本身的问题，换档也没用"
