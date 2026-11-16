"""
auth token 解析的回归测试。

背景：曾经 `get_current_user_from_token` 在函数开头无条件调用 `log_error(AUTH_TOKEN_INVALID)`，
导致**每次成功鉴权**都往控制台打一条 E-AUTH-005 警告（日志噪音，且会误导排查方向）。
本文件锁死：成功路径静默，失败路径才告警。
"""
import logging
import sqlite3
import time

import pytest
from fastapi import HTTPException
from jose import jwt

from app.core import auth
from app.core.error_codes import ErrorCode

_LOGGER = "ai-tutor"
_INVALID_CODE = ErrorCode.code_only(ErrorCode.AUTH_TOKEN_INVALID)


@pytest.fixture(autouse=True)
def _isolate_users_db(tmp_path, monkeypatch):
    """把 auth 层的 users 库指向 tmp，并建出本文件用到的 uid=42。

    背景（2026-11-16）：`get_current_user_from_token` 现在会查一次 `users.status`
    （禁用账号的旧 token 必须立刻失效，见 `test_auth_disabled_user.py`）。本文件的
    用例只关心"验签层"的语义，用的是随手写的 uid=42 —— 那个用户在真实库里并不存在，
    于是会被判成 `[E-AUTH-003] 用户不存在`。这里给它建一行，让用例只测它想测的东西。
    """
    db = tmp_path / "knowledge" / "knowledge.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL,"
        " password_hash TEXT NOT NULL, created_at TEXT DEFAULT (datetime('now')),"
        " status TEXT DEFAULT 'active', role TEXT DEFAULT 'user', last_login_at TEXT)"
    )
    conn.execute(
        "INSERT INTO users (id, username, password_hash, status) VALUES (42, 'testuser42', 'x', 'active')"
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(auth, "_users_db_path", lambda: db)


def _make_token(sub: str = "42", exp_delta: int = 600) -> str:
    now = int(time.time())
    return jwt.encode(
        {"sub": sub, "exp": now + exp_delta, "iat": now},
        auth.SECRET_KEY,
        algorithm=auth.ALGORITHM,
    )


def test_valid_token_returns_user_id_without_warning(caplog):
    """合法 token：返回 user_id，且不产生 E-AUTH-005 警告"""
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        user_id = auth.get_current_user_from_token(_make_token("42"))

    assert user_id == 42
    assert _INVALID_CODE not in caplog.text


@pytest.mark.parametrize(
    "token",
    [
        "not-a-jwt",
        jwt.encode({"no_sub": 1, "exp": int(time.time()) + 600}, auth.SECRET_KEY, algorithm=auth.ALGORITHM),
    ],
    ids=["malformed", "missing_sub"],
)
def test_invalid_token_warns_and_raises_401(caplog, token):
    """非法 / 缺 sub 的 token：抛 401 且记录 E-AUTH-005"""
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        with pytest.raises(HTTPException) as exc_info:
            auth.get_current_user_from_token(token)

    assert exc_info.value.status_code == 401
    assert _INVALID_CODE in caplog.text


def test_expired_token_warns_and_raises_401(caplog):
    """过期 token：抛 401 且记录 E-AUTH-005"""
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        with pytest.raises(HTTPException) as exc_info:
            auth.get_current_user_from_token(_make_token("42", exp_delta=-10))

    assert exc_info.value.status_code == 401
    assert _INVALID_CODE in caplog.text
