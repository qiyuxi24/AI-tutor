"""运维后台数据访问层：纯 sqlite3，复用主系统数据库文件"""
import shutil
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.core.config import settings

# 主系统 users.role 的合法取值（建号/改角色共用，见 backend/scripts/migrate_add_users_role_status.py）
USER_ROLES = ("user", "admin")


def get_db():
    """FastAPI 依赖：每请求一个连接，请求结束自动关闭"""
    conn = sqlite3.connect(str(settings.db_path))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


class AuditAction:
    """审计日志操作类型"""

    LOGIN = "login"
    RESET_PASSWORD = "reset_password"
    DISABLE_USER = "disable_user"
    ENABLE_USER = "enable_user"
    UPDATE_ROLE = "update_role"
    # 账户增删（用户 / 运维管理员）
    CREATE_USER = "create_user"
    DELETE_USER = "delete_user"
    CREATE_ADMIN = "create_admin"
    DELETE_ADMIN = "delete_admin"
    DISABLE_ADMIN = "disable_admin"
    ENABLE_ADMIN = "enable_admin"
    CHANGE_PASSWORD = "change_password"

    ALL = (
        LOGIN,
        RESET_PASSWORD,
        DISABLE_USER,
        ENABLE_USER,
        UPDATE_ROLE,
        CREATE_USER,
        DELETE_USER,
        CREATE_ADMIN,
        DELETE_ADMIN,
        DISABLE_ADMIN,
        ENABLE_ADMIN,
        CHANGE_PASSWORD,
    )


def add_audit_log(
    conn: sqlite3.Connection,
    admin_id: str,
    admin_username: str,
    action: str,
    target_user_id: Optional[int] = None,
    target_username: Optional[str] = None,
    ip_address: Optional[str] = None,
    details: Optional[str] = None,
) -> None:
    """写一条审计日志。

    刻意不 commit：调用方把「业务更新 + 审计日志」放在同一事务里一次提交，
    避免只改了用户状态却没留下操作记录。
    """
    conn.execute(
        """INSERT INTO admin_audit_logs
           (id, admin_id, admin_username, action, target_user_id, target_username,
            ip_address, details, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            str(uuid.uuid4()),
            admin_id,
            admin_username,
            action,
            None if target_user_id is None else str(target_user_id),
            target_username,
            ip_address,
            details,
            datetime.now(timezone.utc).isoformat(),
        ),
    )


# ════════════════════════════════════════════
#  用户数据量统计 / 删除（跨主系统多个 sqlite 文件）
# ════════════════════════════════════════════

def _count(db_file: Path | str, sql: str, params: tuple = ()) -> int:
    """在指定 sqlite 文件里跑一次 COUNT。

    主系统的库按模块分散在不同文件、且是「用到才创建」的：缺文件、缺表、缺列
    都按 0 处理，绝不让"某个存储还没初始化"把运维页面整个打挂。
    """
    path = Path(db_file)
    if not path.exists():
        return 0
    try:
        conn = sqlite3.connect(str(path))
        try:
            return conn.execute(sql, params).fetchone()[0] or 0
        finally:
            conn.close()
    except sqlite3.Error:
        return 0


def _user_dir(kind: str, user_id: int) -> Path:
    """主系统按用户分目录的存储根：backend/data/{kb|rag}/{user_id}"""
    return Path(settings.backend_data_dir) / kind / str(user_id)


def _agent_runs_db() -> Path:
    """Agent 运行记录库（backend/data/agent_runs，与 kb/quiz 同数据根）"""
    return Path(settings.backend_data_dir) / "agent_runs" / "agent_runs.db"


def user_data_summary(user_id: int) -> dict:
    """汇总某用户在各存储里的数据量（图谱 / 对话 / 知识库 / 运行记录）"""
    kb_dir = _user_dir("kb", user_id)
    return {
        "nodes": _count(
            settings.db_path, "SELECT COUNT(*) FROM nodes WHERE user_id = ?", (user_id,)
        ),
        # 掌握度四档里的 MASTERED 阈值 70，见 core/knowledge_graph.mastery_bucket
        "mastered_nodes": _count(
            settings.db_path,
            "SELECT COUNT(*) FROM nodes WHERE user_id = ? AND mastery >= 70",
            (user_id,),
        ),
        "edges": _count(
            settings.db_path, "SELECT COUNT(*) FROM edges WHERE user_id = ?", (user_id,)
        ),
        # 主题层级（KG-T1）：themes=主题条目数，theme_assignments=节点↔主题归属数
        "themes": _count(
            settings.db_path, "SELECT COUNT(*) FROM themes WHERE user_id = ?", (user_id,)
        ),
        "theme_assignments": _count(
            settings.db_path, "SELECT COUNT(*) FROM node_themes WHERE user_id = ?", (user_id,)
        ),
        "conversations": _count(
            settings.conversations_db,
            "SELECT COUNT(*) FROM conversations WHERE user_id = ?",
            (user_id,),
        ),
        "kb_documents": _count(
            kb_dir / "kb.db",
            "SELECT COUNT(*) FROM documents d JOIN nodes n ON n.id = d.node_id "
            "WHERE n.user_id = ?",
            (user_id,),
        ),
        "kb_chunks": _count(
            kb_dir / "rag.db", "SELECT COUNT(*) FROM doc_chunks WHERE user_id = ?", (user_id,)
        ),
        "agent_runs": _count(
            _agent_runs_db(), "SELECT COUNT(*) FROM agent_runs WHERE user_id = ?", (user_id,)
        ),
    }


# 删号时要清空的 knowledge.db 表，顺序 = 「先叶子后主表」。
# 见 delete_user_rows 的 docstring：本连接未开 FK 级联，顺序只能自己保证。
# 参数化 SQL 里统一用 ? 占位，users 走 id、其余走 user_id（调用处传 user_id 即可）。
_USER_ROW_DELETES: tuple[tuple[str, str], ...] = (
    ("node_themes", "DELETE FROM node_themes WHERE user_id = ?"),
    ("node_aliases", "DELETE FROM node_aliases WHERE user_id = ?"),
    ("mastery_events", "DELETE FROM mastery_events WHERE user_id = ?"),
    ("themes", "DELETE FROM themes WHERE user_id = ?"),
    ("edges", "DELETE FROM edges WHERE user_id = ?"),
    ("nodes", "DELETE FROM nodes WHERE user_id = ?"),
    ("users", "DELETE FROM users WHERE id = ?"),
)


def delete_user_rows(conn: sqlite3.Connection, user_id: int) -> None:
    """删掉 users 行 + 该用户在 knowledge.db 的全部数据（图谱 / 主题 / 别名 / 掌握度事件）。

    刻意不 commit：调用方把「删号 + 审计日志」放在同一事务里一次提交，与
    _set_status 等写操作保持同一约定。

    为什么必须逐表显式删：本连接来自 get_db()，**没有开 PRAGMA foreign_keys**，
    ON DELETE CASCADE 不生效 —— 删 users/nodes 不会顺带清掉 node_aliases /
    mastery_events / themes / node_themes，漏一行就是永久脏数据（还会与新号撞
    nodes.id 全局主键）。顺序按「先叶子后主表」：node_themes → node_aliases →
    mastery_events → themes → edges → nodes → users。

    老库可能还没有 themes/node_themes/node_aliases/mastery_events（建表时间晚于老库），
    先探 sqlite_master，缺表直接跳过 —— 与 _count 的「缺表按 0 处理」同口径，
    绝不让"某张新表还没建"把删号整个打挂。
    """
    existing = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    for table, sql in _USER_ROW_DELETES:
        if table in existing:
            conn.execute(sql, (user_id,))


def purge_user_storage(user_id: int) -> None:
    """清掉该用户遗留的磁盘数据（对话行、知识库/向量目录、题库目录、节点 MD 目录）。

    必须排在事务提交之后调用：这些写入与文件删除**无法参与事务回滚**，顺序反了
    会出现「连接回滚了、文件却已删掉」的半残状态。

    节点正文 MD 按用户分目录（`data/knowledge/nodes/{user_id}/`，见
    KnowledgeGraph.nodes_dir），整目录删即可；nodes.file_path 只是装饰性相对串，
    不能拿去 unlink。
    """
    conv = Path(settings.conversations_db)
    if conv.exists():
        try:
            with sqlite3.connect(str(conv)) as c:
                c.execute("DELETE FROM conversations WHERE user_id = ?", (user_id,))
        except sqlite3.Error:
            pass  # 对话库不可写不该阻断删号（残留数据也不会再被访问）

    runs_db = _agent_runs_db()
    if runs_db.exists():
        try:
            with sqlite3.connect(str(runs_db)) as c:
                c.execute("DELETE FROM agent_runs WHERE user_id = ?", (user_id,))
        except sqlite3.Error:
            pass

    roots = (
        Path(settings.db_path).parent / "nodes" / str(user_id),
        _user_dir("kb", user_id),
        _user_dir("rag", user_id),
        # 题库（`core/quiz/quiz_store.py::_QUIZ_DIR` = backend/data/quiz/<uid>）：
        # 与 kb/rag 同数据根，删号必须一并清 —— 漏了会残留该用户的题目与判分记录。
        _user_dir("quiz", user_id),
    )
    for path in roots:
        shutil.rmtree(path, ignore_errors=True)


def init_db() -> None:
    """建表 + 首次创建超级管理员，启动时调用一次"""
    conn = sqlite3.connect(str(settings.db_path))
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS admin_users (
                id            TEXT PRIMARY KEY,
                username      TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role          TEXT NOT NULL DEFAULT 'admin',
                is_active     INTEGER NOT NULL DEFAULT 1,
                created_at    TEXT,
                last_login_at TEXT
            );
            CREATE TABLE IF NOT EXISTS admin_audit_logs (
                id              TEXT PRIMARY KEY,
                admin_id        TEXT NOT NULL,
                admin_username  TEXT NOT NULL,
                action          TEXT NOT NULL,
                target_user_id  TEXT,
                target_username TEXT,
                ip_address      TEXT,
                details         TEXT,
                created_at      TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_admin_audit_logs_created_at
                ON admin_audit_logs (created_at DESC);
            """
        )

        existing = conn.execute(
            "SELECT 1 FROM admin_users WHERE username = 'admin'"
        ).fetchone()
        if not existing:
            if not settings.default_admin_password:
                print("[初始化] 未设置 default_admin_password，跳过超级管理员创建")
            else:
                # 函数内导入：admin_auth 依赖本模块，模块级导入会成环
                from app.core.admin_auth import hash_password

                conn.execute(
                    "INSERT INTO admin_users (id, username, password_hash, role, created_at) "
                    "VALUES (?, 'admin', ?, 'super_admin', ?)",
                    (
                        str(uuid.uuid4()),
                        hash_password(settings.default_admin_password),
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                print("[初始化] 已创建超级管理员: admin")

        conn.commit()
    finally:
        conn.close()
