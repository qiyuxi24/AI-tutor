"""
auth token 解析的回归测试。

背景：曾经 `get_current_user_from_token` 在函数开头无条件调用 `log_error(AUTH_TOKEN_INVALID)`，
导致**每次成功鉴权**都往控制台打一条 E-AUTH-005 警告（日志噪音，且会误导排查方向）。
本文件锁死：成功路径静默，失败路径才告警。
"""
import logging
import time

import pytest
from fastapi import HTTPException
from jose import jwt

from app.core import auth
from app.core.error_codes import ErrorCode

_LOGGER = "ai-tutor"
_INVALID_CODE = ErrorCode.code_only(ErrorCode.AUTH_TOKEN_INVALID)


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
