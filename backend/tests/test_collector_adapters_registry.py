"""B1.2 adapter 注册表：注册/按名路由/覆盖/注销 + 内置适配器（全部离线）"""
import asyncio

import pytest

from app.core.collector.adapters import get_adapter, get_registry
from app.core.collector.adapters.base import BaseAdapter, AdapterRegistry
from app.core.collector.types import CollectCandidate


def _run(coro):
    return asyncio.run(coro)


class FakeAdapter(BaseAdapter):
    name = "fake"
    license_level = "L0"

    async def search(self, query):
        return []

    async def fetch(self, candidate):
        return b""


class FakeL1Adapter(BaseAdapter):
    name = "fake2"
    license_level = "L1"

    async def search(self, query):
        return []


def test_register_and_get_by_name():
    reg = AdapterRegistry()
    a = FakeAdapter()
    reg.register(a)
    assert reg.get("fake") is a
    assert reg.names() == ["fake"]
    assert reg.all() == [a]


def test_register_overwrites_same_name():
    reg = AdapterRegistry()
    reg.register(FakeAdapter())
    a2 = FakeAdapter()
    reg.register(a2)  # 同名后注册者覆盖
    assert reg.get("fake") is a2
    assert len(reg.all()) == 1


def test_register_rejects_non_adapter():
    with pytest.raises(TypeError):
        AdapterRegistry().register("wikipedia")  # type: ignore[arg-type]


def test_unregister():
    reg = AdapterRegistry()
    a = FakeAdapter()
    reg.register(a)
    assert reg.unregister("fake") is a
    assert reg.get("fake") is None
    assert reg.unregister("nope") is None


def test_builtin_adapters_registered():
    reg = get_registry()
    assert "wikipedia" in reg.names()
    assert "wikibooks" in reg.names()
    assert get_adapter("wikipedia") is reg.get("wikipedia")
    assert {a.name for a in reg.all()} == {"wikipedia", "wikibooks"}


def test_builtin_license_level_l0():
    assert get_adapter("wikipedia").license_level == "L0"
    assert get_adapter("wikibooks").license_level == "L0"


def test_abstract_adapter_raises():
    with pytest.raises(NotImplementedError):
        _run(BaseAdapter().search("x"))


def test_lazy_http_no_network_on_instantiate():
    """仅注册/实例化不建 HTTP 客户端（导入期零副作用）"""
    reg = AdapterRegistry()
    reg.register(FakeAdapter())
    assert reg.get("fake")._http is None


def test_candidate_type_roundtrip():
    """BaseAdapter 与 B1.1 CollectCandidate 类型天然打通"""
    c = CollectCandidate(title="t", source_url="https://x/u",
                         license_level="L0", meta={"adapter": "fake"})
    assert c.meta["adapter"] == "fake"
    assert isinstance(FakeAdapter(), BaseAdapter)
