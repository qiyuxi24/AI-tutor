"""
按用户限流（chat）+ 登录成功归还配额的回归测试。

背景（均已实测确认，不是猜测）：
  ① `chat` 限流曾按 `request.client.host` 计数 —— 校园网 / 公司 NAT 下多个用户共用
     同一个出口 IP，A 多说几句 B 就被 429。`user_id` 由 `Depends(get_current_user)`
     在进入函数体前解析好，所以限流检查时可以直接用。
  ② 登录成功也消耗 `login_rate_limiter` 配额 —— 正常切号 / 刷新重登 5 次就被锁 60 秒。
     `max_requests=5` 的本意是防撞库，不该罚正常用户；失败路径必须继续计数。

本文件锁死三件事：chat 的限流 key 是**用户**（同一 IP 下互不影响）、429 响应体/头格式
不变、`reset()` 让某个 key 的配额恢复且对不存在的 key 静默。
"""
import base64
import json
import sqlite3

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from fastapi.testclient import TestClient
from starlette.requests import Request

import app.api.v1.chat as chat_api
from app.core import transport_crypto as tc
from app.core.rate_limiter import RateLimiter

LOGIN_URL = "/api/v1/auth/login"
_OAEP = padding.OAEP(
    mgf=padding.MGF1(algorithm=hashes.SHA256()),
    algorithm=hashes.SHA256(),
    label=None,
)


def _request(ip: str = "10.1.2.3") -> Request:
    """最小 http scope：`_check_chat_rate_limit` 只读 `request.client`。"""
    return Request({
        "type": "http", "method": "POST", "path": "/api/v1/chat",
        "headers": [], "query_string": b"", "client": (ip, 1234),
    })


@pytest.fixture()
def chat_limiter(monkeypatch):
    """每个用例一个干净的 chat 限流器（否则跨用例污染）。"""
    limiter = RateLimiter(max_requests=2, window_seconds=60)
    monkeypatch.setattr(chat_api, "chat_rate_limiter", limiter)
    return limiter


# ---------------- chat：限流维度是用户，不是 IP ----------------

def test_same_ip_different_users_are_independent(chat_limiter):
    """同一 IP 下两个用户各自独立计数（NAT 场景的核心回归）。"""
    ip = "10.0.0.1"
    assert chat_api._check_chat_rate_limit(_request(ip), user_id=1) is None
    assert chat_api._check_chat_rate_limit(_request(ip), user_id=1) is None
    assert chat_api._check_chat_rate_limit(_request(ip), user_id=1) is not None  # 超限

    # B 与 A 同一个出口 IP，但配额完全独立
    assert chat_api._check_chat_rate_limit(_request(ip), user_id=2) is None
    assert chat_api._check_chat_rate_limit(_request(ip), user_id=2) is None


def test_same_user_exceeding_limit_returns_429(chat_limiter):
    """超限返回 429，且响应体 / Retry-After 格式不变（前端依赖）。"""
    assert chat_api._check_chat_rate_limit(_request(), user_id=42) is None
    assert chat_api._check_chat_rate_limit(_request(), user_id=42) is None

    resp = chat_api._check_chat_rate_limit(_request(), user_id=42)
    assert resp is not None
    assert resp.status_code == 429
    assert resp.headers["Retry-After"].isdigit()
    detail = json.loads(resp.body)["detail"]
    assert "请求过于频繁" in detail


def test_falls_back_to_ip_when_user_id_unavailable(chat_limiter):
    """拿不到 user_id 时退回 IP 计数（正常不会走，但不能炸）。"""
    ip = "1.2.3.4"
    assert chat_api._check_chat_rate_limit(_request(ip), user_id=None) is None
    assert chat_api._check_chat_rate_limit(_request(ip), user_id=None) is None
    assert chat_api._check_chat_rate_limit(_request(ip), user_id=None) is not None

    # 用户维度不受 IP 回退影响
    assert chat_api._check_chat_rate_limit(_request(ip), user_id=99) is None


def test_reset_restores_chat_quota(chat_limiter):
    """`reset()` 后该 key 配额恢复（同时锁死 key 命名约定 `u:<id>`）。"""
    for _ in range(2):
        assert chat_api._check_chat_rate_limit(_request(), user_id=7) is None
    assert chat_api._check_chat_rate_limit(_request(), user_id=7) is not None

    chat_limiter.reset("u:7")
    assert chat_api._check_chat_rate_limit(_request(), user_id=7) is None


# ---------------- RateLimiter.reset 语义 ----------------

def test_reset_unknown_key_is_silent():
    """key 不存在时静默返回，且不凭空建条目。"""
    rl = RateLimiter(max_requests=2, window_seconds=60)
    rl.reset("never-seen")
    assert "never-seen" not in rl.attempts


def test_reset_only_affects_target_key():
    rl = RateLimiter(max_requests=1, window_seconds=60)
    assert rl.is_allowed("a") is True
    assert rl.is_allowed("b") is True
    assert rl.get_retry_after("a") > 0
    rl.reset("a")
    assert rl.get_retry_after("a") == 0      # 计数已清
    assert rl.is_allowed("a") is True
    assert rl.is_allowed("b") is False       # 别人的配额没被一起清掉


# ---------------- 端点层：两个调用点都按用户记账 ----------------

@pytest.fixture()
def chat_endpoints(monkeypatch):
    """真实 `/chat` 与 `/chat/stream` 路由 + 打桩的对话服务（不碰 LLM）。"""
    from fastapi import FastAPI, Request as FastAPIRequest

    from app.core.auth import get_current_user

    limiter = RateLimiter(max_requests=2, window_seconds=60)
    monkeypatch.setattr(chat_api, "chat_rate_limiter", limiter)

    async def fake_process_message(**kwargs):
        return "ok", None

    async def fake_process_message_stream(**kwargs):
        yield "data: ok\n\n"

    monkeypatch.setattr(chat_api, "process_message", fake_process_message)
    monkeypatch.setattr(chat_api, "process_message_stream", fake_process_message_stream)

    app = FastAPI()
    app.include_router(chat_api.router, prefix="/api/v1")

    def _user_from_header(request: FastAPIRequest) -> int:
        return int(request.headers.get("x-test-user", "1"))

    app.dependency_overrides[get_current_user] = _user_from_header
    return TestClient(app)


def test_both_endpoints_share_per_user_quota(chat_endpoints):
    """`/chat` 用掉的配额同样约束 `/chat/stream`（两个调用点都传了 user_id）。"""
    body = {"messages": [{"role": "user", "content": "hi"}]}
    alice = {"x-test-user": "1"}
    bob = {"x-test-user": "2"}

    assert chat_endpoints.post("/api/v1/chat", json=body, headers=alice).status_code == 200
    assert chat_endpoints.post("/api/v1/chat/stream", json=body, headers=alice).status_code == 200

    resp = chat_endpoints.post("/api/v1/chat", json=body, headers=alice)
    assert resp.status_code == 429
    assert resp.headers["Retry-After"].isdigit()
    assert "请求过于频繁" in resp.json()["detail"]

    # 流式端点也是按用户，所以 alice 同样被拦
    stream_resp = chat_endpoints.post("/api/v1/chat/stream", json=body, headers=alice)
    assert stream_resp.status_code == 429

    # 同一出口 IP（testclient）的 bob 不受影响
    assert chat_endpoints.post("/api/v1/chat/stream", json=body, headers=bob).status_code == 200


# ---------------- 登录：成功不消耗配额，失败继续计数 ----------------

def _encrypt(public_pem: str, password: str) -> str:
    """模拟浏览器端：PEM 公钥 → RSA-OAEP 加密 → base64。"""
    pub = serialization.load_pem_public_key(public_pem.encode())
    return base64.b64encode(pub.encrypt(password.encode("utf-8"), _OAEP)).decode("ascii")


@pytest.fixture()
def login_api(tmp_path, monkeypatch):
    """真实登录端点 + 隔离的用户库 / RSA 密钥 / 限流器。"""
    from app.core.auth import get_password_hash

    monkeypatch.setattr(tc, "KEY_PATH", tmp_path / "keys" / "rsa_private.pem")
    tc.reset_key_cache()

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
    conn.execute(
        "INSERT INTO users (username, password_hash) VALUES (?, ?)",
        ("alice01", get_password_hash("hunter22")),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(auth_api, "DB_PATH", str(db))
    # max_requests=2：成功登录若占配额，第 3 次就必然 429
    limiter = RateLimiter(max_requests=2, window_seconds=60)
    monkeypatch.setattr(auth_api, "login_rate_limiter", limiter)

    try:
        yield TestClient(app), limiter
    finally:
        tc.reset_key_cache()


def test_successful_logins_do_not_consume_quota(login_api):
    """连续 4 次成功登录都不该被锁（max_requests=2）。"""
    client, limiter = login_api
    pem = client.get("/api/v1/auth/public-key").json()["public_key"]
    body = {"username": "alice01", "password": _encrypt(pem, "hunter22")}

    for i in range(4):
        resp = client.post(LOGIN_URL, json=body)
        assert resp.status_code == 200, f"第 {i + 1} 次成功登录被限流: {resp.text}"

    assert "testclient" not in limiter.attempts, "成功登录后该 IP 的计数应被清空"


def test_failed_logins_still_consume_quota(login_api):
    """失败必须继续计数 —— 那才是防撞库的本体。"""
    client, _ = login_api
    pem = client.get("/api/v1/auth/public-key").json()["public_key"]
    body = {"username": "alice01", "password": _encrypt(pem, "wrong-pw")}

    assert client.post(LOGIN_URL, json=body).status_code == 401
    assert client.post(LOGIN_URL, json=body).status_code == 401
    resp = client.post(LOGIN_URL, json=body)
    assert resp.status_code == 429, resp.text
    assert "Retry-After" in resp.headers
