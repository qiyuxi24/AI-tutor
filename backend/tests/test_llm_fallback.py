"""
模型回退链单元测试（全离线，不碰真实网络）。

覆盖（对齐 llm.fallback「模型回退链」设计，2026-09-08）：
- 主模型配额耗尽(403) → 自动静默降级到备用模型并返回其响应
- 未配置备用（单候选）→ 主模型失败照旧抛 RuntimeError（旧行为不变）
- 请求侧错误(400) → 不降级直接抛（换模型也没用）
"""
import ast
import asyncio
import inspect
from types import SimpleNamespace

import httpx
import pytest
from openai import APIStatusError

from app.core.config import Settings
from app.core.llm import clients as llm_clients
from app.core.llm import fallback as llm_client


def test_clients_only_reads_existing_settings_fields():
    """clients.py **代码里**引用的 settings 字段必须真实存在（曾误写 fallback_base_url：
    该行被 `fallback_model_name` 的短路保护掩盖，一旦配上 FALLBACK_MODEL_NAME
    就会 AttributeError → 整个应用导入即崩）。

    ⚠️ 必须用 AST 只看**代码**（2026-09-30 合并 origin/main 后修正）：
    旧版用正则扫整份源码，会把**注释里**提到的历史错名 `settings.fallback_base_url`
    也算成"引用" → 注释解释这个坑、测试却因此变红（两边各自都对，合起来冲突）。
    注释不是引用，不该参与断言。
    """
    tree = ast.parse(inspect.getsource(llm_clients))
    used = {node.attr for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name) and node.value.id == "settings"}
    assert used, "一个 settings.* 引用都没扫到 —— AST 抽取逻辑坏了，先修测试"
    for field_name in sorted(used):
        assert hasattr(Settings, field_name), f"Settings 不存在字段 {field_name}"


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
