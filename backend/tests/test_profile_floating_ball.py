"""悬浮球偏好能不能真正存进画像（`PATCH/GET /api/v1/profile` 契约回归）。

背景（本文件存在的理由）：`ProfilePreferences` 是 Pydantic 模型，默认 `extra='ignore'`。
前端把 `preferences.floating_ball` 一起 PATCH 上来时，只要模型里没声明这个字段，
它就在**进业务层之前被静默丢掉** —— 请求 200、前端乐观更新也显示成功，
只有刷新/换设备才露馅（表现为"设置里开了悬浮球，刷新就没了"）。
所以这里断言的不是"接口 200"，而是"值真的读得回来"。

全离线：画像目录被 monkeypatch 到 tmp_path，不碰真实 `data/profiles/`。
"""
import pytest
from fastapi.testclient import TestClient

import app.core.profile.store as store_mod
from app.core.auth import get_current_user

URL = "/api/v1/profile"
USER_ID = 5
BALL = {
    "enabled": True,
    "default_open": False,
    "size": "large",
    "corner": "bottom-left",
    "offset": {"x": 30, "y": 40},
}


@pytest.fixture
def client(monkeypatch, tmp_path):
    """TestClient + 画像目录指向 tmp_path + 固定登录用户。"""
    monkeypatch.setattr(store_mod, "default_data_dir", lambda: tmp_path)

    from app.main import app

    app.dependency_overrides[get_current_user] = lambda: USER_ID
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_floating_ball_roundtrip(client):
    """写入 → 读回：不被 Pydantic 丢掉，且真的落盘（不是只在响应里回显）"""
    r = client.patch(URL, json={"data": {"preferences": {"floating_ball": BALL}}})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["preferences"]["floating_ball"] == BALL
    # 另起一次 GET：确认来自存储，而不是请求上下文里的回显
    assert client.get(URL).json()["data"]["preferences"]["floating_ball"] == BALL


def test_floating_ball_patch_keeps_other_prefs(client):
    """只提交悬浮球时，已存的其它偏好（usage_mode）不被清掉"""
    client.patch(URL, json={"data": {"preferences": {"usage_mode": "commercial"}}})
    client.patch(URL, json={"data": {"preferences": {"floating_ball": BALL}}})

    prefs = client.get(URL).json()["data"]["preferences"]
    assert prefs["floating_ball"] == BALL
    assert prefs["usage_mode"] == "commercial"


def test_never_set_floating_ball_is_none(client):
    """没配过的用户读到 None（前端据此回落到自己的默认档，不需要后端补默认值）"""
    assert client.get(URL).json()["data"]["preferences"]["floating_ball"] is None
