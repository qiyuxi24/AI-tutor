"""运维管理员账户管理（仅超级管理员可操作）。

保护规则（防止把自己或整个后台锁死）：
- 不能对自己禁用/降级/删除；
- 任何操作后都必须至少留下一个「启用状态的超级管理员」。
"""
import sqlite3
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from app.core.admin_auth import (
    SUPER_ADMIN,
    CurrentAdmin,
    hash_password,
    require_super_admin,
)
from app.core.db import AuditAction, add_audit_log, get_db

router = APIRouter()

_COLUMNS = "id, username, role, is_active, created_at, last_login_at"

AdminRole = Literal["admin", "super_admin"]


class AdminItem(BaseModel):
    id: str
    username: str
    role: str
    is_active: bool
    created_at: str | None = None
    last_login_at: str | None = None


class CreateAdminRequest(BaseModel):
    username: str = Field(min_length=3, max_length=20)
    password: str = Field(min_length=6, max_length=50)
    role: AdminRole = "admin"


class UpdateAdminRoleRequest(BaseModel):
    role: AdminRole


class ResetAdminPasswordRequest(BaseModel):
    new_password: str = Field(min_length=6, max_length=50)


def _get_admin(conn: sqlite3.Connection, admin_id: str) -> sqlite3.Row:
    row = conn.execute(f"SELECT {_COLUMNS} FROM admin_users WHERE id = ?", (admin_id,)).fetchone()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "管理员不存在")
    return row


def _guard_not_self(me: CurrentAdmin, admin_id: str) -> None:
    if me.id == admin_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "不能对自己执行该操作")


def _guard_not_last_super(conn: sqlite3.Connection, row: sqlite3.Row) -> None:
    """目标是否已是最后一个启用的超管 —— 是则拦住（禁用/降级/删除都会导致后台失管）"""
    if row["role"] != SUPER_ADMIN or not row["is_active"]:
        return
    others = conn.execute(
        "SELECT COUNT(*) FROM admin_users WHERE role = ? AND is_active = 1 AND id != ?",
        (SUPER_ADMIN, row["id"]),
    ).fetchone()[0]
    if not others:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "系统必须保留至少一个启用状态的超级管理员"
        )


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.get("/admins")
def list_admins(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str | None = None,
    conn: sqlite3.Connection = Depends(get_db),
    admin: CurrentAdmin = Depends(require_super_admin),
):
    where, params = "1=1", []
    if search:
        where, params = "username LIKE ?", [f"%{search}%"]

    total = conn.execute(
        f"SELECT COUNT(*) FROM admin_users WHERE {where}", params
    ).fetchone()[0]
    rows = conn.execute(
        f"SELECT {_COLUMNS} FROM admin_users WHERE {where} "
        "ORDER BY created_at DESC, username LIMIT ? OFFSET ?",
        params + [page_size, (page - 1) * page_size],
    ).fetchall()

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "admins": [AdminItem(**dict(r)) for r in rows],
    }


@router.post("/admins", status_code=status.HTTP_201_CREATED, response_model=AdminItem)
def create_admin(
    body: CreateAdminRequest,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    admin: CurrentAdmin = Depends(require_super_admin),
):
    username = body.username.strip()
    if conn.execute("SELECT 1 FROM admin_users WHERE username = ?", (username,)).fetchone():
        raise HTTPException(status.HTTP_409_CONFLICT, "管理员用户名已存在")

    new_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO admin_users (id, username, password_hash, role, is_active, created_at) "
        "VALUES (?, ?, ?, ?, 1, datetime('now'))",
        (new_id, username, hash_password(body.password), body.role),
    )
    add_audit_log(
        conn,
        admin.id,
        admin.username,
        AuditAction.CREATE_ADMIN,
        target_user_id=new_id,
        target_username=username,
        ip_address=_client_ip(request),
        details=f"角色={body.role}",
    )
    conn.commit()
    return AdminItem(**dict(_get_admin(conn, new_id)))


@router.post("/admins/{admin_id}/role")
def update_admin_role(
    admin_id: str,
    body: UpdateAdminRoleRequest,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    admin: CurrentAdmin = Depends(require_super_admin),
):
    target = _get_admin(conn, admin_id)
    _guard_not_self(admin, admin_id)
    _guard_not_last_super(conn, target)

    conn.execute("UPDATE admin_users SET role = ? WHERE id = ?", (body.role, admin_id))
    add_audit_log(
        conn,
        admin.id,
        admin.username,
        AuditAction.UPDATE_ROLE,
        target_user_id=admin_id,
        target_username=target["username"],
        ip_address=_client_ip(request),
        details=f"角色 {target['role']} → {body.role}",
    )
    conn.commit()
    return {"message": "角色已更新"}


@router.post("/admins/{admin_id}/reset-password")
def reset_admin_password(
    admin_id: str,
    body: ResetAdminPasswordRequest,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    admin: CurrentAdmin = Depends(require_super_admin),
):
    target = _get_admin(conn, admin_id)
    conn.execute(
        "UPDATE admin_users SET password_hash = ? WHERE id = ?",
        (hash_password(body.new_password), admin_id),
    )
    add_audit_log(
        conn,
        admin.id,
        admin.username,
        AuditAction.RESET_PASSWORD,
        target_user_id=admin_id,
        target_username=target["username"],
        ip_address=_client_ip(request),
        details="管理员密码已重置",
    )
    conn.commit()
    return {"message": "密码已重置"}


def _set_admin_active(
    conn: sqlite3.Connection,
    admin_id: str,
    is_active: bool,
    action: str,
    request: Request,
    admin: CurrentAdmin,
) -> dict:
    target = _get_admin(conn, admin_id)
    _guard_not_self(admin, admin_id)
    _guard_not_last_super(conn, target)

    conn.execute("UPDATE admin_users SET is_active = ? WHERE id = ?", (int(is_active), admin_id))
    add_audit_log(
        conn,
        admin.id,
        admin.username,
        action,
        target_user_id=admin_id,
        target_username=target["username"],
        ip_address=_client_ip(request),
    )
    conn.commit()
    return {"message": "管理员已启用" if is_active else "管理员已禁用"}


@router.post("/admins/{admin_id}/disable")
def disable_admin(
    admin_id: str,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    admin: CurrentAdmin = Depends(require_super_admin),
):
    return _set_admin_active(
        conn, admin_id, False, AuditAction.DISABLE_ADMIN, request, admin
    )


@router.post("/admins/{admin_id}/enable")
def enable_admin(
    admin_id: str,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    admin: CurrentAdmin = Depends(require_super_admin),
):
    return _set_admin_active(
        conn, admin_id, True, AuditAction.ENABLE_ADMIN, request, admin
    )


@router.delete("/admins/{admin_id}")
def delete_admin(
    admin_id: str,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    admin: CurrentAdmin = Depends(require_super_admin),
):
    target = _get_admin(conn, admin_id)
    _guard_not_self(admin, admin_id)
    _guard_not_last_super(conn, target)

    conn.execute("DELETE FROM admin_users WHERE id = ?", (admin_id,))
    add_audit_log(
        conn,
        admin.id,
        admin.username,
        AuditAction.DELETE_ADMIN,
        target_user_id=admin_id,
        target_username=target["username"],
        ip_address=_client_ip(request),
        details=f"角色={target['role']}",
    )
    conn.commit()
    return {"message": f"管理员 {target['username']} 已删除"}
