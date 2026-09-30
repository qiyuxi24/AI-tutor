"""
修改密码 API 测试（全离线；RSA 密钥与用户库都隔离到 tmp_path）。

关键回归：**旧密码错误必须返回 400（不是 401）** —— 前端 axios 拦截器把 401 一律
当成"会话过期"，会清 token 并静默登录体验账户，用户输错一次旧密码就会被换号。
"""
import base64
import sqlite3

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from fastapi.testclient import TestClient

from app.core import transport_crypto as tc

URL = "/api/v1/auth/change-password"
_OAEP = padding.OAEP(
    mgf=padding.MGF1(algorithm=hashes.SHA256()),
    algorithm=hashes.SHA256(),
    label=None,
)


def _encrypt(public_pem: str, password: str) -> str:
    """模拟浏览器端：PEM 公钥 → RSA-OAEP 加密 → base64"""
    pub = serialization.load_pem_public_key(public_pem.encode())
    return base64.b64encode(pub.encrypt(password.encode("utf-8"), _OAEP)).decode("ascii")


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(tc, "KEY_PATH", tmp_path / "keys" / "rsa_private.pem")
    tc.reset_key_cache()
    try:
        yield _make_client(tmp_path, monkeypatch)
    finally:
        tc.reset_key_cache()


def _make_client(tmp_path, monkeypatch) -> TestClient:
    from app.main import app
    import app.api.v1.auth as auth_api

    db = tmp_path / "users.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE users ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "username TEXT UNIQUE NOT NULL, "
        "password_hash TEXT NOT NULL, "
        "created_at TEXT DEFAULT (datetime('now')), "
        "status TEXT DEFAULT 'active', "
        "role TEXT DEFAULT 'user', "
        "last_login_at TEXT)"
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(auth_api, "DB_PATH", str(db))
    monkeypatch.setattr(auth_api.login_rate_limiter, "is_allowed", lambda ip: True)
    return TestClient(app)


def _register(api: TestClient, username="alice01", password="hunter22"):
    """注册并返回 (token, 公钥 PEM)"""
    pem = api.get("/api/v1/auth/public-key").json()["public_key"]
    r = api.post("/api/v1/auth/register",
                 json={"username": username, "password": _encrypt(pem, password)})
    assert r.status_code == 201, r.text
    return r.json()["token"], pem


def _login(api: TestClient, pem: str, password: str, username="alice01"):
    return api.post("/api/v1/auth/login",
                    json={"username": username, "password": _encrypt(pem, password)})


def _change(api, token, pem, old, new):
    return api.post(URL, headers={"Authorization": f"Bearer {token}"},
                    json={"old_password": _encrypt(pem, old),
                          "new_password": _encrypt(pem, new)})


def test_change_password_then_new_password_works(api):
    token, pem = _register(api)
    r = _change(api, token, pem, "hunter22", "brand-new-pw")
    assert r.status_code == 200, r.text

    assert _login(api, pem, "brand-new-pw").status_code == 200
    assert _login(api, pem, "hunter22").status_code == 401   # 旧密码已失效


def test_wrong_old_password_returns_400_not_401(api):
    token, pem = _register(api)
    r = _change(api, token, pem, "wrong-old-pw", "brand-new-pw")
    assert r.status_code == 400, "401 会被前端当成会话过期并静默换号"
    assert "E-AUTH-008" in r.json()["detail"]
    # 密码没被改掉
    assert _login(api, pem, "hunter22").status_code == 200


def test_short_new_password_rejected(api):
    """新密码长度校验在解密后进行（密文长度恒等，前端校验可绕过）"""
    token, pem = _register(api)
    assert _change(api, token, pem, "hunter22", "12345").status_code == 422


def test_requires_login(api):
    _register(api)
    pem = api.get("/api/v1/auth/public-key").json()["public_key"]
    r = api.post(URL, json={"old_password": _encrypt(pem, "hunter22"),
                            "new_password": _encrypt(pem, "brand-new-pw")})
    assert r.status_code == 401
