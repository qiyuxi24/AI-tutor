"""
用户模型管理 API 测试（全离线）。

隔离手段：
- 模型配置目录指向 tmp_path（monkeypatch 本模块的 `_store` 工厂）
- 连通性探测替换成假的 `_probe_model`（绝不打真实网络）
- 登录用户靠 dependency_overrides 切换
"""
import pytest
from fastapi.testclient import TestClient

from app.api.v1 import llm_models as llm_api
from app.core.auth import get_current_user
from app.core.llm.user_models import ModelStore

URL = "/api/v1/llm/models"
USER = 3


async def _ok_probe(base_url, api_key, model):
    return True, ""


async def _fail_probe(base_url, api_key, model):
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
