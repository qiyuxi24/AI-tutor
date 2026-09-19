"""审计日志查询接口"""
import sqlite3

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.core.admin_auth import CurrentAdmin, get_current_admin
from app.core.db import get_db

router = APIRouter()


class AuditLogItem(BaseModel):
    id: str
    admin_username: str
    action: str
    target_user_id: str | None = None
    target_username: str | None = None
    ip_address: str | None = None
    details: str | None = None
    created_at: str


@router.get("/audit-logs")
def list_audit_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    action: str | None = None,
    conn: sqlite3.Connection = Depends(get_db),
    admin: CurrentAdmin = Depends(get_current_admin),
):
    where: list[str] = []
    params: list = []
    if action:
        where.append("action = ?")
        params.append(action)
    where_sql = " AND ".join(where) if where else "1=1"

    total = conn.execute(
        f"SELECT COUNT(*) FROM admin_audit_logs WHERE {where_sql}", params
    ).fetchone()[0]
    rows = conn.execute(
        "SELECT id, admin_username, action, target_user_id, target_username, "
        "ip_address, details, created_at FROM admin_audit_logs "
        f"WHERE {where_sql} ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
        params + [page_size, (page - 1) * page_size],
    ).fetchall()

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "logs": [AuditLogItem(**dict(r)) for r in rows],
    }
