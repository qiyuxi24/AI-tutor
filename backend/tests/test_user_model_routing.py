"""
用户级模型路由单元测试（全离线，不碰真实网络）。

覆盖：
- 用户配置了自定义模型 → chat_create 走用户的候选链，不用全局 .env 档
- 用户候选链内部同样「主失败 → 静默降级到备用」，并把实际生效档位回写
- 用户没有任何可用模型 → 回落全局 LLM_CANDIDATES（旧行为不变）
- 同一 (base_url, key) 只构造一个 AsyncOpenAI 客户端
"""
import asyncio
from types import SimpleNamespace

import httpx
import pytest
from openai import APIStatusError

from app.core.llm import fallback as llm_client
from app.core.llm.user_models import ModelStore


def _api_err(status: int) -> APIStatusError:
    req = httpx.Request("POST", "http://test")
    return APIStatusError(
        f"HTTP {status}", response=httpx.Response(status, request=req), body=None
    )


class _FakeCompletions:
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


@pytest.fixture
def store(tmp_path):
    return ModelStore(1, data_dir=tmp_path)


@pytest.fixture(autouse=True)
def _clean_client_cache(monkeypatch):
    """客户端缓存是模块级全局：不清会让上个用例的假客户端串到下一个用例。"""
    monkeypatch.setattr(llm_client, "_CLIENT_CACHE", {})


def _wire(monkeypatch, store, client_by_base: dict):
    """把 fallback 的「取用户配置」与「建客户端」两处接成测试替身。"""
    monkeypatch.setattr(llm_client, "_model_store", lambda uid: store)
    built = []

    def fake_build(base_url, api_key):
        built.append((base_url, api_key))
        return client_by_base[base_url]

    monkeypatch.setattr(llm_client, "_build_client", fake_build)
    return built


def test_user_candidates_used_when_configured(monkeypatch, store):
    """用户配了模型 → 用用户的地址与 Key，不碰全局候选。"""
    a = store.add(name="A", base_url="https://a/v1", api_key="ka", model="ma")
    store.mark_test(a["id"], ok=True)
    store.set_active(a["id"])
    built = _wire(monkeypatch, store, {"https://a/v1": _fake_client(["user-ok"])})
    # 全局档故意是坏客户端：一旦被用上就会 500
    monkeypatch.setattr(llm_client, "LLM_CANDIDATES", [(_fake_client([_api_err(500)]), "global")])

    resp = asyncio.run(llm_client.chat_create(messages=[], user_id=1))

    assert resp == "user-ok"
    assert built == [("https://a/v1", "ka")]
    assert store.snapshot()["effective_id"] == a["id"]


def test_fallback_within_user_chain_records_effective(monkeypatch, store):
    """用户主模型 403 → 静默降级到用户备用模型，生效档位指向备用。"""
    a = store.add(name="A", base_url="https://a/v1", api_key="ka", model="ma")
    b = store.add(name="B", base_url="https://b/v1", api_key="kb", model="mb")
    store.mark_test(a["id"], ok=True)
    store.mark_test(b["id"], ok=True)
    store.set_active(a["id"])
    _wire(monkeypatch, store, {
        "https://a/v1": _fake_client([_api_err(403)]),
        "https://b/v1": _fake_client(["backup-ok"]),
    })

    resp = asyncio.run(llm_client.chat_create(messages=[], user_id=1))

    assert resp == "backup-ok"
    assert store.snapshot()["effective_id"] == b["id"]


def test_no_user_model_falls_back_to_global(monkeypatch, store):
    """用户没配任何模型 → 全局 .env 档照旧（单候选失败即抛，不引入新行为）。"""
    _wire(monkeypatch, store, {})
    monkeypatch.setattr(llm_client, "LLM_CANDIDATES", [(_fake_client([_api_err(403)]), "global")])

    with pytest.raises(RuntimeError):
        asyncio.run(llm_client.chat_create(messages=[], user_id=1))


def test_client_is_reused_per_base_and_key(monkeypatch, store):
    """同一地址+Key 复用客户端（避免每次调用新建连接池）。"""
    a = store.add(name="A", base_url="https://a/v1", api_key="ka", model="ma")
    store.mark_test(a["id"], ok=True)
    store.set_active(a["id"])
    built = _wire(monkeypatch, store, {"https://a/v1": _fake_client(["ok", "ok"])})
    monkeypatch.setattr(llm_client, "_CLIENT_CACHE", {})

    asyncio.run(llm_client.chat_create(messages=[], user_id=1))
    asyncio.run(llm_client.chat_create(messages=[], user_id=1))

    assert built == [("https://a/v1", "ka")]


def test_store_failure_does_not_break_call(monkeypatch):
    """用户配置读盘异常 → 静默回落全局档，绝不让对话挂掉。"""
    def boom(uid):
        raise OSError("disk on fire")

    monkeypatch.setattr(llm_client, "_model_store", boom)
    monkeypatch.setattr(llm_client, "LLM_CANDIDATES", [(_fake_client(["global-ok"]), "global")])

    assert asyncio.run(llm_client.chat_create(messages=[], user_id=1)) == "global-ok"
