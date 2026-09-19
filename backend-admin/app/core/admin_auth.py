"""运维管理员认证：bcrypt 密码哈希 + JWT（与主系统同一套算法）"""
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from app.core.config import settings
from app.core.db import get_db

security = HTTPBearer()

# 管理员角色：super_admin 为权限顶格（可管管理员），admin 只能管普通用户
SUPER_ADMIN = "super_admin"
ADMIN = "admin"


def hash_password(password: str) -> str:
    """bcrypt 哈希（截断到 72 字节，与主系统 backend/app/core/auth.py 一致）"""
    return bcrypt.hashpw(password.encode("utf-8")[:72], bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8")[:72], hashed_password.encode("utf-8")
        )
    except ValueError:
        # 库里存的不是合法 bcrypt 哈希
        return False


def create_access_token(admin_id: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_minutes)
    return jwt.encode(
        {"sub": admin_id, "exp": expire},
        settings.secret_key,
        algorithm=settings.algorithm,
    )


def decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
    except JWTError:
        return None


class CurrentAdmin:
    """当前登录的运维管理员"""

    def __init__(self, id: str, username: str, role: str):
        self.id = id
        self.username = username
        self.role = role


def get_current_admin(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    conn: sqlite3.Connection = Depends(get_db),
) -> CurrentAdmin:
    payload = decode_token(credentials.credentials)
    admin_id = payload.get("sub") if payload else None
    if not admin_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "无效的认证令牌")

    row = conn.execute(
        "SELECT id, username, role, is_active FROM admin_users WHERE id = ?", (admin_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "管理员不存在")
    if not row["is_active"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "账号已被禁用")

    return CurrentAdmin(row["id"], row["username"], row["role"])


def require_super_admin(
    admin: CurrentAdmin = Depends(get_current_admin),
) -> CurrentAdmin:
    """管理员账户管理是超管专属：普通 admin 只能管用户，管不了同行"""
    if admin.role != SUPER_ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "需要超级管理员权限")
    return admin
