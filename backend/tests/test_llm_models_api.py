"""
用户模型管理 API 测试（全离线）。

隔离手段：
- 模型配置目录指向 tmp_path（monkeypatch 本模块的 `_store` 工厂）
- 连通性探测替换成假的 `_probe_model`（绝不打真实网络）
- 登录用户靠 dependency_overrides 切换
"""
import pytest
from fastapi.testclient import TestClient
from types import SimpleNamespace

from app.api.v1 import llm_models as llm_api
from app.core.auth import get_current_user
from app.core.llm.user_models import ModelStore

URL = "/api/v1/llm/models"
USER = 3


async def _ok_probe(base_url, api_key, model, provider=""):
    return True, ""


async def _fail_probe(base_url, api_key, model, provider=""):
    return False, "HTTP 401 Unauthorized"


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    """TestClient + 配置落 tmp_path + 可切换登录用户。"""
    state = {"uid": USER}
    monkeypatch.setattr(llm_api, "_store", lambda uid: ModelStore(uid, data_dir=tmp_path))
    # SSRF 校验会解析 DNS（全离线环境必然失败）→ 默认放行；拒绝语义由专门用例覆盖
    monkeypatch.setattr(llm_api, "is_blocked_url", lambda url: None)

    from app.main import app
    app.dependency_overrides[get_current_user] = lambda: state["uid"]
    try:
        yield TestClient(app), state
    finally:
        app.dependency_overrides.clear()


def _payload(**kw):
    return {"name": "校内网关", "base_url": "https://gw/v1",
            "api_key": "sk-abcdefgh1234", "model": "dau", **kw}


def test_list_empty_returns_system_default(ctx):
    client, _ = ctx
    body = client.get(URL).json()
    assert body["models"] == []
    assert body["active_id"] is None
    assert body["effective_id"] is None
    # 未配置自有模型的用户走 .env 系统默认档，界面要能显示它是哪个模型
    assert body["system_default"]["model"]


def test_create_then_list_returns_masked_key(ctx):
    client, _ = ctx
    created = client.post(URL, json=_payload())
    assert created.status_code == 200, created.text
    m = created.json()
    assert m["api_key_masked"] == "sk-a****1234"
    assert "api_key" not in m            # 明文绝不出库
    assert m["status"] == "unverified"
    assert [x["id"] for x in client.get(URL).json()["models"]] == [m["id"]]


def test_create_validates_input(ctx):
    client, _ = ctx
    assert client.post(URL, json=_payload(base_url="not-a-url")).status_code == 422
    assert client.post(URL, json={"name": "x"}).status_code == 422


def test_create_rejects_loopback_base_url(ctx, monkeypatch):
    """base_url 指向回环/内网 → 400（SSRF 防线）。

    用真实 `is_blocked_url`（`ctx` 默认放行是为离线 DNS）；`127.0.0.1` 命中 host 层
    快速拦截、无需 DNS。否则登录用户可借 base_url 让服务器探测内网 / 云元数据。
    """
    from app.core.net_guard import is_blocked_url as real_is_blocked

    monkeypatch.setattr(llm_api, "is_blocked_url", real_is_blocked)
    client, _ = ctx
    r = client.post(URL, json=_payload(base_url="http://127.0.0.1:8080/v1"))
    assert r.status_code == 400
    assert "不被允许" in r.json()["detail"]


def test_probe_marks_available_then_unavailable(ctx, monkeypatch):
    client, _ = ctx
    mid = client.post(URL, json=_payload()).json()["id"]

    monkeypatch.setattr(llm_api, "_probe_model", _fail_probe)
    failed = client.post(f"{URL}/{mid}/test").json()
    assert failed["status"] == "unavailable"
    assert "401" in failed["last_error"]

    monkeypatch.setattr(llm_api, "_probe_model", _ok_probe)
    ok = client.post(f"{URL}/{mid}/test").json()
    assert ok["status"] == "available"
    assert ok["last_error"] == ""


def test_patch_access_fields_resets_to_unverified(ctx, monkeypatch):
    client, _ = ctx
    mid = client.post(URL, json=_payload()).json()["id"]
    monkeypatch.setattr(llm_api, "_probe_model", _ok_probe)
    client.post(f"{URL}/{mid}/test")

    updated = client.patch(f"{URL}/{mid}", json={"model": "dau2"}).json()
    assert updated["model"] == "dau2"
    assert updated["status"] == "unverified"      # 改了地址/模型 → 旧结论失效


def test_patch_can_disable(ctx):
    client, _ = ctx
    mid = client.post(URL, json=_payload()).json()["id"]
    assert client.patch(f"{URL}/{mid}", json={"status": "disabled"}).json()["status"] == "disabled"


def test_activate_sets_active_id(ctx):
    client, _ = ctx
    mid = client.post(URL, json=_payload()).json()["id"]
    assert client.post(f"{URL}/{mid}/activate").status_code == 200
    assert client.get(URL).json()["active_id"] == mid


def test_delete_active_falls_back_to_system_default(ctx):
    client, _ = ctx
    mid = client.post(URL, json=_payload()).json()["id"]
    client.post(f"{URL}/{mid}/activate")

    assert client.delete(f"{URL}/{mid}").json()["ok"] is True
    body = client.get(URL).json()
    assert body["models"] == [] and body["active_id"] is None


def test_unknown_model_returns_404(ctx):
    client, _ = ctx
    assert client.post(f"{URL}/nope/activate").status_code == 404
    assert client.post(f"{URL}/nope/test").status_code == 404
    assert client.patch(f"{URL}/nope", json={"name": "x"}).status_code == 404
    assert client.delete(f"{URL}/nope").status_code == 404


def test_models_are_isolated_per_user(ctx):
    client, state = ctx
    client.post(URL, json=_payload())
    state["uid"] = USER + 1
    assert client.get(URL).json()["models"] == []


# ─── 方言（2026-11-16）：对话框切换模型 / 设置页预设 ─────────────

def test_list_exposes_dialect_presets(ctx):
    """设置页「选服务商 → 自动填地址」的数据源在后端方言表，前端不另存一份。"""
    client, _ = ctx
    body = client.get(URL).json()
    keys = [p["key"] for p in body["presets"]]
    assert "deepseek" in keys and "glm" in keys
    assert keys[-1] == "openai-compat"            # 逃生口放末尾
    assert body["system_default"]["provider"]     # 系统默认档也标明方言


def test_deactivate_falls_back_to_system_default(ctx):
    """对话框里选「系统默认」= 取消激活，不是选了个叫"默认"的模型。"""
    client, _ = ctx
    mid = client.post(URL, json=_payload()).json()["id"]
    client.post(f"{URL}/{mid}/activate")
    assert client.get(URL).json()["active_id"] == mid

    assert client.post(f"{URL}/deactivate").json()["active_id"] is None
    body = client.get(URL).json()
    assert body["active_id"] is None and body["effective_id"] is None
    assert [m["id"] for m in body["models"]] == [mid]   # 模型本身还在


def test_provider_field_round_trips(ctx, monkeypatch):
    """provider 随模型存下来，并在改参数后退回未验证（与其它接入参数同口径）。"""
    client, _ = ctx
    created = client.post(URL, json=_payload(provider="deepseek")).json()
    assert created["provider"] == "deepseek"

    monkeypatch.setattr(llm_api, "_probe_model", _ok_probe)
    client.post(f"{URL}/{created['id']}/test")
    updated = client.patch(f"{URL}/{created['id']}", json={"provider": "glm"}).json()
    assert updated["provider"] == "glm"
    assert updated["status"] == "unverified"


def test_unknown_provider_rejected(ctx):
    """写错方言键要当场报错（否则会静默按模型名猜→发错字段）。"""
    client, _ = ctx
    assert client.post(URL, json=_payload(provider="openrouter")).status_code == 400
    mid = client.post(URL, json=_payload()).json()["id"]
    assert client.patch(f"{URL}/{mid}", json={"provider": "nope"}).status_code == 400


def test_probe_uses_dialect_tokens(monkeypatch):
    """探测按方言表取 max_tokens：DeepSeek reasoner 拒绝 1，通用档才用 1。

    原先硬编码 `max_tokens=1` → 好模型被误判 unavailable（"能用的模型显示不可用"）。
    """
    import asyncio

    from app.core.llm import provider as prov

    seen = {}

    class _FakeCompletions:
        async def create(self, **kwargs):
            seen.update(kwargs)

    monkeypatch.setattr(llm_api, "AsyncOpenAI", lambda **kw: SimpleNamespace(
        chat=SimpleNamespace(completions=_FakeCompletions())))

    asyncio.run(llm_api._probe_model("https://x/v1", "k", "deepseek-reasoner"))
    assert seen["max_tokens"] == prov.PROVIDERS["deepseek"].probe_tokens
    assert "extra_body" in seen and seen["extra_body"] == {}   # 该方言不发 reasoning_split

    asyncio.run(llm_api._probe_model("https://x/v1", "k", "some-gateway-model"))
    assert seen["max_tokens"] == 1
