"""运维后台登录接口"""
import sqlite3
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.core.admin_auth import (
    CurrentAdmin,
    create_access_token,
    get_current_admin,
    hash_password,
    verify_password,
)
from app.core.db import AuditAction, add_audit_log, get_db

router = APIRouter()


class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str = Field(min_length=6, max_length=50)


@router.post("/login")
def login(body: LoginRequest, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    row = conn.execute(
        "SELECT id, username, password_hash, role, is_active FROM admin_users WHERE username = ?",
        (body.username,),
    ).fetchone()

    if not row or not verify_password(body.password, row["password_hash"]):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户名或密码错误")
    if not row["is_active"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "账号已被禁用")

    conn.execute(
        "UPDATE admin_users SET last_login_at = ? WHERE id = ?",
        (datetime.now(timezone.utc).isoformat(), row["id"]),
    )
    add_audit_log(
        conn,
        row["id"],
        row["username"],
        AuditAction.LOGIN,
        ip_address=request.client.host if request.client else None,
    )
    conn.commit()

    return {
        "access_token": create_access_token(row["id"]),
        "token_type": "bearer",
        "admin": {"id": row["id"], "username": row["username"], "role": row["role"]},
    }


@router.get("/me")
def get_me(admin: CurrentAdmin = Depends(get_current_admin)):
    return {"id": admin.id, "username": admin.username, "role": admin.role}


@router.post("/me/password")
def change_my_password(
    body: ChangePasswordRequest,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    admin: CurrentAdmin = Depends(get_current_admin),
):
    """自助改密：必须验旧密码（超管帮别人改密走 /admins/{id}/reset-password）"""
    row = conn.execute(
        "SELECT password_hash FROM admin_users WHERE id = ?", (admin.id,)
    ).fetchone()
    if not row or not verify_password(body.old_password, row["password_hash"]):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "原密码错误")

    conn.execute(
        "UPDATE admin_users SET password_hash = ? WHERE id = ?",
        (hash_password(body.new_password), admin.id),
    )
    add_audit_log(
        conn,
        admin.id,
        admin.username,
        AuditAction.CHANGE_PASSWORD,
        ip_address=request.client.host if request.client else None,
    )
    conn.commit()
    return {"message": "密码已修改，请重新登录"}
