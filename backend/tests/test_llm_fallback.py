"""
模型回退链单元测试（全离线，不碰真实网络）。

覆盖（对齐 llm.fallback「模型回退链」设计，2026-09-08）：
- 主模型配额耗尽(403) → 自动静默降级到备用模型并返回其响应
- 未配置备用（单候选）→ 主模型失败照旧抛 RuntimeError（旧行为不变）
- 请求侧错误(400) → 不降级直接抛（换模型也没用）
"""
import asyncio
from types import SimpleNamespace

import httpx
import pytest
from openai import APIStatusError

from app.core.llm import fallback as llm_client


def _api_err(status: int) -> APIStatusError:
    req = httpx.Request("POST", "http://test")
    return APIStatusError(
        f"HTTP {status}", response=httpx.Response(status, request=req), body=None
    )


class _FakeCompletions:
    """模拟 client.chat.completions：按序弹出结果（Exception 直接抛出）。"""

    def __init__(self, outcomes: list):
        self._q = list(outcomes)
        self.calls = 0

    async def create(self, **kwargs):
        self.calls += 1
        item = self._q.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _fake_client(outcomes: list) -> SimpleNamespace:
    return SimpleNamespace(
        chat=SimpleNamespace(completions=_FakeCompletions(outcomes))
    )


def test_fallback_to_backup_when_primary_quota_exhausted(monkeypatch):
    """主模型 403 配额耗尽 → 降级到备用模型，返回备用响应；主模型只打一次。"""
    primary = _fake_client([_api_err(403)])
    backup = _fake_client(["backup-ok"])
    monkeypatch.setattr(llm_client, "LLM_CANDIDATES", [
        (primary, "primary-model"), (backup, "backup-model"),
    ])

    resp = asyncio.run(llm_client.chat_create(messages=[]))

    assert resp == "backup-ok"
    assert primary.chat.completions.calls == 1
    assert backup.chat.completions.calls == 1


def test_no_fallback_when_single_candidate(monkeypatch):
    """未配置备用（单候选）→ 403 照旧抛 RuntimeError，保持旧行为。"""
    primary = _fake_client([_api_err(403)])
    monkeypatch.setattr(llm_client, "LLM_CANDIDATES", [(primary, "primary-model")])

    with pytest.raises(RuntimeError):
        asyncio.run(llm_client.chat_create(messages=[]))


def test_request_side_error_does_not_fallback(monkeypatch):
    """400（请求本身错误）→ 不降级直接抛，备用模型不被调用。"""
    primary = _fake_client([_api_err(400)])
    backup = _fake_client(["backup-ok"])
    monkeypatch.setattr(llm_client, "LLM_CANDIDATES", [
        (primary, "primary-model"), (backup, "backup-model"),
    ])

    with pytest.raises(RuntimeError):
        asyncio.run(llm_client.chat_create(messages=[]))
    assert backup.chat.completions.calls == 0
