"""禁用账号后已签发 token 必须立即失效 —— `get_current_user_from_token` 的状态校验。

背景（实测确认的权限绕过，不是推测）：
    改之前 `get_current_user_from_token` 只做 JWT 解码 + 取 `sub`，**完全不查库**。
    于是运维后台把某人 `users.status` 改成 `disabled` 之后，对方只要还握着未过期的
    token，就能继续调全部 API（对话 / 建图 / 出题 …）直到 token 自然过期。
    登录端点（`api/v1/auth.py::login`）有状态检查，但它只挡"下一次登录"。

本文件锁死四件事：
    1. `active` 通过（回归：别把正常人挡了）
    2. `status` 为 NULL / 空串通过（老库兼容 —— 那三列是 `ensure_user_columns` 补的，
       历史行本来就是 NULL，"猜默认值"会误伤存量用户）
    3. `disabled` → 403（**token 签发在禁用之前**也照样拒，这才是本 bug 的形态）
    4. 用户行不存在 → 401

隔离：不碰真实 `data/knowledge/knowledge.db`。`tests/conftest.py` **没有**对
knowledge.db 做 autouse 隔离（它只管 usage / rag / conversations / notices / LLM），
所以这里显式把 `auth._users_db_path` 指到 `tmp_path` 的库 —— 读的是同一个取径函数，
只是换掉根路径，与 `test_change_password.py` 把 `auth_api.DB_PATH` 指向 tmp 库同一手法。
"""
import sqlite3

import pytest
from fastapi import HTTPException

from app.core import auth
from app.core.error_codes import ErrorCode

# `users` 表列集合：与 `core/kg/schema.py::_create_tables` 的建表语句逐字一致。
# 注意 `status` **不带 NOT NULL** —— 老库补列后历史行就是 NULL，这是要覆盖的输入。
_SCHEMA = """
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    status TEXT DEFAULT 'active',
    role TEXT DEFAULT 'user',
    last_login_at TEXT
)
"""

MISSING_USER_ID = 4321


@pytest.fixture
def users_db(tmp_path, monkeypatch):
    """把 auth 层的 users 库指向 tmp；返回 (db_path, 建行函数)。"""
    db = tmp_path / "knowledge" / "knowledge.db"
    db.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db))
    conn.executescript(_SCHEMA)
    conn.commit()
    conn.close()

    monkeypatch.setattr(auth, "_users_db_path", lambda: db)

    def add_user(user_id: int, status="active", username: str | None = None) -> int:
        conn = sqlite3.connect(str(db))
        conn.execute(
            "INSERT INTO users (id, username, password_hash, status) VALUES (?, ?, 'x', ?)",
            (user_id, username or f"u{user_id}", status),
        )
        conn.commit()
        conn.close()
        return user_id

    return db, add_user


def _token(user_id: int) -> str:
    """真签一张 token（走唯一的签发实现，不手搓 payload）。"""
    return auth.create_access_token(user_id)


# ── 1. 正常用户不被误伤 ──────────────────────────────────────────

def test_active_user_passes(users_db):
    _, add_user = users_db
    uid = add_user(101, status="active")

    assert auth.get_current_user_from_token(_token(uid)) == uid


def test_active_user_passes_through_depends_wrapper(users_db):
    """`get_current_user`（路由依赖）与裸 token 入口必须同一条判定。"""
    _, add_user = users_db
    uid = add_user(102, status="active")

    assert auth.get_current_user(_token(uid)) == uid


# ── 2. 老库兼容：status 为 NULL / 空串 ──────────────────────────

@pytest.mark.parametrize("status", [None, ""], ids=["null", "empty"])
def test_legacy_null_or_empty_status_passes(users_db, status):
    """`status` 三列是后补的，老行可以是 NULL —— 必须当正常处理，不能拒掉。"""
    _, add_user = users_db
    uid = add_user(103, status=status)

    assert auth.get_current_user_from_token(_token(uid)) == uid


# ── 3. 禁用：包括"先签发、后禁用"这个真实形态 ──────────────────

def test_disabled_user_is_rejected_with_403(users_db):
    _, add_user = users_db
    uid = add_user(104, status="disabled")

    with pytest.raises(HTTPException) as exc_info:
        auth.get_current_user_from_token(_token(uid))

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "账号已被禁用"   # 与 login 端点逐字一致


def test_token_issued_before_disable_stops_working(users_db):
    """本 bug 的原貌：token 在禁用**之前**签发，禁用后必须立刻失效。

    写成"先禁用再签 token"会漏掉真正的绕过路径（禁用不会追溯已签发的 token）。
    """
    db, add_user = users_db
    uid = add_user(105, status="active")
    token = _token(uid)                      # 禁用前签发
    assert auth.get_current_user_from_token(token) == uid

    conn = sqlite3.connect(str(db))          # 运维后台改状态
    conn.execute("UPDATE users SET status = 'disabled' WHERE id = ?", (uid,))
    conn.commit()
    conn.close()

    with pytest.raises(HTTPException) as exc_info:
        auth.get_current_user_from_token(token)
    assert exc_info.value.status_code == 403


def test_disabled_user_rejected_through_depends_wrapper(users_db):
    """路由依赖走的是同一个出口 —— 不能只堵裸 token 入口。"""
    _, add_user = users_db
    uid = add_user(106, status="disabled")

    with pytest.raises(HTTPException) as exc_info:
        auth.get_current_user(_token(uid))
    assert exc_info.value.status_code == 403


# ── 4. 用户行不存在（删号 / 伪造 sub）────────────────────────────

def test_missing_user_row_is_rejected_with_401(users_db):
    with pytest.raises(HTTPException) as exc_info:
        auth.get_current_user_from_token(_token(MISSING_USER_ID))

    assert exc_info.value.status_code == 401
    assert ErrorCode.code_only(ErrorCode.AUTH_USER_NOT_FOUND) in exc_info.value.detail


def test_missing_user_401_is_distinguishable_from_disabled_403(users_db):
    """两种拒绝的语义不同（重新登录 vs 找运维），不能被合并成同一条。"""
    _, add_user = users_db
    disabled_id = add_user(107, status="disabled")

    with pytest.raises(HTTPException) as disabled:
        auth.get_current_user_from_token(_token(disabled_id))
    with pytest.raises(HTTPException) as missing:
        auth.get_current_user_from_token(_token(MISSING_USER_ID))

    assert (disabled.value.status_code, missing.value.status_code) == (403, 401)
    assert ErrorCode.code_only(ErrorCode.AUTH_TOKEN_INVALID) not in missing.value.detail


# ── 5. 连接必须归还（否则每次请求泄漏一个文件句柄）──────────────

def test_connection_is_closed_after_check(users_db):
    """Windows 上句柄未关就删不掉文件 —— 用 unlink 反证 `closing()` 真的生效。

    认证依赖跑在每个请求上，漏关连接 = 句柄慢性泄漏（长驻进程最终 errno 24）。
    """
    db, add_user = users_db
    uid = add_user(108, status="active")

    assert auth.get_current_user_from_token(_token(uid)) == uid
    db.unlink()          # 句柄泄漏时这里会抛 PermissionError
    assert not db.exists()


# ── 6. token 本身的失败路径不受影响（回归）──────────────────────

def test_invalid_token_still_401_without_touching_db(users_db):
    """验签失败要在查库**之前**返回，不能因为库状态把 401 变成 500。"""
    db, _ = users_db
    db.unlink()          # 库都不在了：若先查库会抛 OperationalError

    with pytest.raises(HTTPException) as exc_info:
        auth.get_current_user_from_token("not-a-jwt")

    assert exc_info.value.status_code == 401
    assert ErrorCode.code_only(ErrorCode.AUTH_TOKEN_INVALID) in exc_info.value.detail
