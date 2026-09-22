"""用户管理接口（读写主系统 users 表）"""
import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from app.core.admin_auth import CurrentAdmin, get_current_admin, hash_password
from app.core.db import (
    AuditAction,
    add_audit_log,
    delete_user_rows,
    get_db,
    purge_user_storage,
    user_data_summary,
)

router = APIRouter()

_COLUMNS = "id, username, role, status, created_at, last_login_at"

# 主系统 users.role 的合法取值
# 见 backend/scripts/migrate_add_users_role_status.py
Role = Literal["user", "admin"]


class UserItem(BaseModel):
    id: int
    username: str
    role: str
    status: str
    created_at: str | None = None
    last_login_at: str | None = None


class CreateUserRequest(BaseModel):
    """建号字段与主系统 /auth/register 对齐（用户名 3-20、密码 6-50）"""

    username: str = Field(min_length=3, max_length=20)
    password: str = Field(min_length=6, max_length=50)
    role: Role = "user"


class ResetPasswordRequest(BaseModel):
    new_password: str = Field(min_length=6, max_length=50)


class UpdateRoleRequest(BaseModel):
    role: Role


class BatchDeleteUsersRequest(BaseModel):
    """批量删号：一次最多 100 个（防误点全表 / 打满事务）"""

    user_ids: list[int] = Field(min_length=1, max_length=100)


def _get_user(conn: sqlite3.Connection, user_id: int) -> sqlite3.Row:
    row = conn.execute(f"SELECT {_COLUMNS} FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "用户不存在")
    return row


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.get("/users")
def list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str | None = None,
    status_filter: str | None = None,
    conn: sqlite3.Connection = Depends(get_db),
    admin: CurrentAdmin = Depends(get_current_admin),
):
    where: list[str] = []
    params: list = []
    if search:
        where.append("username LIKE ?")
        params.append(f"%{search}%")
    if status_filter:
        where.append("status = ?")
        params.append(status_filter)
    where_sql = " AND ".join(where) if where else "1=1"

    total = conn.execute(f"SELECT COUNT(*) FROM users WHERE {where_sql}", params).fetchone()[0]
    rows = conn.execute(
        f"SELECT {_COLUMNS} FROM users WHERE {where_sql} ORDER BY id DESC LIMIT ? OFFSET ?",
        params + [page_size, (page - 1) * page_size],
    ).fetchall()

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "users": [UserItem(**dict(r)) for r in rows],
    }


@router.post("/users", status_code=status.HTTP_201_CREATED, response_model=UserItem)
def create_user(
    body: CreateUserRequest,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    admin: CurrentAdmin = Depends(get_current_admin),
):
    """管理员代建用户账号（主系统注册接口需要自助注册，这里用于批量开号）"""
    username = body.username.strip()
    exists = conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone()
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, "用户名已存在")

    cursor = conn.execute(
        "INSERT INTO users (username, password_hash, role, status) VALUES (?, ?, ?, 'active')",
        (username, hash_password(body.password), body.role),
    )
    add_audit_log(
        conn,
        admin.id,
        admin.username,
        AuditAction.CREATE_USER,
        target_user_id=cursor.lastrowid,
        target_username=username,
        ip_address=_client_ip(request),
        details=f"角色={body.role}",
    )
    conn.commit()
    return UserItem(**dict(_get_user(conn, cursor.lastrowid)))


@router.get("/users/{user_id}", response_model=UserItem)
def get_user(
    user_id: int,
    conn: sqlite3.Connection = Depends(get_db),
    admin: CurrentAdmin = Depends(get_current_admin),
):
    return UserItem(**dict(_get_user(conn, user_id)))


@router.get("/users/{user_id}/stats")
def get_user_stats(
    user_id: int,
    conn: sqlite3.Connection = Depends(get_db),
    admin: CurrentAdmin = Depends(get_current_admin),
):
    """查看该用户在各存储里的数据量（图谱 / 对话 / 知识库）"""
    user = _get_user(conn, user_id)
    return {"user": UserItem(**dict(user)), "data": user_data_summary(user_id)}


def _set_status(
    conn: sqlite3.Connection,
    user_id: int,
    new_status: str,
    action: str,
    request: Request,
    admin: CurrentAdmin,
) -> dict:
    """禁用/启用的共用实现：改状态 + 记审计，同一事务提交"""
    user = _get_user(conn, user_id)
    conn.execute("UPDATE users SET status = ? WHERE id = ?", (new_status, user_id))
    add_audit_log(
        conn,
        admin.id,
        admin.username,
        action,
        target_user_id=user_id,
        target_username=user["username"],
        ip_address=_client_ip(request),
    )
    conn.commit()
    return {"message": "用户已禁用" if new_status == "disabled" else "用户已启用"}


@router.post("/users/{user_id}/disable")
def disable_user(
    user_id: int,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    admin: CurrentAdmin = Depends(get_current_admin),
):
    return _set_status(conn, user_id, "disabled", AuditAction.DISABLE_USER, request, admin)


@router.post("/users/{user_id}/enable")
def enable_user(
    user_id: int,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    admin: CurrentAdmin = Depends(get_current_admin),
):
    return _set_status(conn, user_id, "active", AuditAction.ENABLE_USER, request, admin)


@router.post("/users/{user_id}/reset-password")
def reset_password(
    user_id: int,
    body: ResetPasswordRequest,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    admin: CurrentAdmin = Depends(get_current_admin),
):
    user = _get_user(conn, user_id)
    conn.execute(
        "UPDATE users SET password_hash = ? WHERE id = ?",
        (hash_password(body.new_password), user_id),
    )
    add_audit_log(
        conn,
        admin.id,
        admin.username,
        AuditAction.RESET_PASSWORD,
        target_user_id=user_id,
        target_username=user["username"],
        ip_address=_client_ip(request),
        details="密码已重置",
    )
    conn.commit()
    return {"message": "密码已重置"}


@router.delete("/users/{user_id}")
def delete_user(
    user_id: int,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    admin: CurrentAdmin = Depends(get_current_admin),
):
    """彻底删除用户及其全部数据（图谱/对话/知识库）。

    防误删只靠前端一次确认弹窗 + 管理员身份校验，不再要求重填用户名（填名字太费事）。
    删除量与账号信息一并返回，便于留痕。
    """
    user = _get_user(conn, user_id)
    summary = user_data_summary(user_id)
    delete_user_rows(conn, user_id)
    add_audit_log(
        conn,
        admin.id,
        admin.username,
        AuditAction.DELETE_USER,
        target_user_id=user_id,
        target_username=user["username"],
        ip_address=_client_ip(request),
        details="；".join(f"{k}={v}" for k, v in summary.items()),
    )
    conn.commit()
    purge_user_storage(user_id)
    return {"message": f"用户 {user['username']} 已删除", "deleted": summary}


@router.post("/users/batch-delete")
def batch_delete_users(
    body: BatchDeleteUsersRequest,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    admin: CurrentAdmin = Depends(get_current_admin),
):
    """批量彻底删除用户及其全部数据（图谱/对话/知识库/运行记录）。

    与单删同一套防误删口径：前端一次确认弹窗 + 管理员身份校验，不额外要求填确认词。
    事务边界与单删一致：行删除 + 审计日志同事务提交，磁盘文件（对话与 agent_runs 行、
    节点/知识库/向量目录）在提交后逐个清理 —— 文件操作不可回滚，顺序反了会留半残数据。
    查不到的 id 不报错、只回填在 not_found 里，方便页面提示"部分已不存在"。
    """
    ids = list(dict.fromkeys(body.user_ids))  # 去重，保持前端选择顺序
    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT {_COLUMNS} FROM users WHERE id IN ({placeholders})", ids
    ).fetchall()
    if not rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "选中的用户都不存在")

    ip = _client_ip(request)
    deleted: list[dict] = []
    for row in rows:
        summary = user_data_summary(row["id"])
        delete_user_rows(conn, row["id"])
        add_audit_log(
            conn,
            admin.id,
            admin.username,
            AuditAction.DELETE_USER,
            target_user_id=row["id"],
            target_username=row["username"],
            ip_address=ip,
            details=f"批量删除(共{len(rows)}人)；"
            + "；".join(f"{k}={v}" for k, v in summary.items()),
        )
        deleted.append({"id": row["id"], "username": row["username"], "data": summary})

    conn.commit()
    for item in deleted:
        purge_user_storage(item["id"])

    found = {item["id"] for item in deleted}
    return {
        "message": f"已删除 {len(deleted)} 个用户",
        "deleted": deleted,
        "not_found": [i for i in ids if i not in found],
    }


@router.post("/users/{user_id}/role")
def update_role(
    user_id: int,
    body: UpdateRoleRequest,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    admin: CurrentAdmin = Depends(get_current_admin),
):
    user = _get_user(conn, user_id)
    conn.execute("UPDATE users SET role = ? WHERE id = ?", (body.role, user_id))
    add_audit_log(
        conn,
        admin.id,
        admin.username,
        AuditAction.UPDATE_ROLE,
        target_user_id=user_id,
        target_username=user["username"],
        ip_address=_client_ip(request),
        details=f"角色 {user['role']} → {body.role}",
    )
    conn.commit()
    return {"message": "角色已更新"}
