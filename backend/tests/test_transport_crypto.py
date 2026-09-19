"""
传输加密模块测试（真实 RSA，零 mock 密码学）。

覆盖：
- 单元：密钥生成/持久化/复用、公钥 PEM 导出、加解密往返、篡改密文与非法 base64
- API：GET /auth/public-key 下发 PEM；register/login 用 RSA 加密密码走通全流程；
  错误密文 400 (E-AUTH-007)；解密后短密码 422

隔离手段：
- RSA 密钥指向 tmp_path（monkeypatch KEY_PATH + 重置缓存）
- users 库指向 tmp sqlite（monkeypatch auth.DB_PATH）
- 速率限制器 is_allowed 恒真（TestClient 同 IP 连续登录会触发 5/min）
"""
import base64
import sqlite3

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from fastapi.testclient import TestClient

from app.core import transport_crypto as tc

_OAEP = padding.OAEP(
    mgf=padding.MGF1(algorithm=hashes.SHA256()),
    algorithm=hashes.SHA256(),
    label=None,
)


def _encrypt(public_pem: str, password: str) -> str:
    """模拟浏览器端：加载 PEM 公钥 → RSA-OAEP 加密 → base64"""
    pub = serialization.load_pem_public_key(public_pem.encode())
    ct = pub.encrypt(password.encode("utf-8"), _OAEP)
    return base64.b64encode(ct).decode("ascii")


# ══════════════════ 单元：密钥生命周期 ══════════════════


def test_generate_key_and_persist(tmp_path):
    key_path = tmp_path / "keys" / "rsa_private.pem"
    key1 = tc.load_or_create_key(key_path)
    assert key1.key_size == 2048
    assert key_path.exists()
    # 二次加载复用同一把密钥（不重新生成）
    key2 = tc.load_or_create_key(key_path)
    assert key1.public_key().public_numbers() == key2.public_key().public_numbers()


def test_public_key_pem_format(tmp_path):
    key = tc.load_or_create_key(tmp_path / "k.pem")
    pem = tc.public_key_pem(key)
    assert pem.startswith("-----BEGIN PUBLIC KEY-----")
    assert pem.endswith("-----END PUBLIC KEY-----\n")


# ══════════════════ 单元：加解密 ══════════════════


def test_decrypt_roundtrip(tmp_path):
    key = tc.load_or_create_key(tmp_path / "k.pem")
    cipher_b64 = _encrypt(tc.public_key_pem(key), "hunter22-pass")
    assert tc.decrypt_with_key(key, cipher_b64) == "hunter22-pass"


def test_decrypt_tampered_ciphertext(tmp_path):
    key = tc.load_or_create_key(tmp_path / "k.pem")
    raw = bytearray(base64.b64decode(_encrypt(tc.public_key_pem(key), "pw123456")))
    raw[0] ^= 0xFF  # 篡改首字节
    with pytest.raises(ValueError):
        tc.decrypt_with_key(key, base64.b64encode(bytes(raw)).decode())


def test_decrypt_invalid_base64(tmp_path):
    key = tc.load_or_create_key(tmp_path / "k.pem")
    with pytest.raises(ValueError):
        tc.decrypt_with_key(key, "!!not-base64!!")


# ══════════════════ API 层 ══════════════════


@pytest.fixture
def api(tmp_path, monkeypatch):
    """TestClient + 密钥/用户库/速率限制三重隔离"""
    monkeypatch.setattr(tc, "KEY_PATH", tmp_path / "keys" / "rsa_private.pem")
    tc.reset_key_cache()
    yield _make_client(tmp_path, monkeypatch)
    tc.reset_key_cache()  # 恢复真实路径的密钥缓存


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


def test_public_key_endpoint(api):
    r = api.get("/api/v1/auth/public-key")
    assert r.status_code == 200
    assert r.json()["public_key"].startswith("-----BEGIN PUBLIC KEY-----")


def test_register_with_encrypted_password(api):
    pem = api.get("/api/v1/auth/public-key").json()["public_key"]
    r = api.post(
        "/api/v1/auth/register",
        json={"username": "alice01", "password": _encrypt(pem, "hunter22")},
    )
    assert r.status_code == 201
    assert r.json()["token"]


def test_login_with_encrypted_password(api):
    pem = api.get("/api/v1/auth/public-key").json()["public_key"]
    api.post(
        "/api/v1/auth/register",
        json={"username": "alice01", "password": _encrypt(pem, "hunter22")},
    )
    r = api.post(
        "/api/v1/auth/login",
        json={"username": "alice01", "password": _encrypt(pem, "hunter22")},
    )
    assert r.status_code == 200
    assert r.json()["token"]


def test_login_wrong_password_still_401(api):
    pem = api.get("/api/v1/auth/public-key").json()["public_key"]
    api.post(
        "/api/v1/auth/register",
        json={"username": "alice01", "password": _encrypt(pem, "hunter22")},
    )
    r = api.post(
        "/api/v1/auth/login",
        json={"username": "alice01", "password": _encrypt(pem, "wrong-pw1")},
    )
    assert r.status_code == 401
    assert "E-AUTH-001" in r.json()["detail"]


def test_login_garbage_ciphertext_rejected(api):
    r = api.post(
        "/api/v1/auth/login",
        json={"username": "alice01", "password": "!!not-ciphertext!!"},
    )
    assert r.status_code == 400
    assert "E-AUTH-007" in r.json()["detail"]


def test_register_short_password_after_decrypt(api):
    """长度校验发生在解密之后：解密出的明文过短应 422"""
    pem = api.get("/api/v1/auth/public-key").json()["public_key"]
    r = api.post(
        "/api/v1/auth/register",
        json={"username": "alice01", "password": _encrypt(pem, "abc")},
    )
    assert r.status_code == 422
