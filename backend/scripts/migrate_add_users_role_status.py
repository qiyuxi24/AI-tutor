"""
数据库迁移：给 users 表添加角色和状态字段
- role: 角色 (admin/user)
- status: 状态 (active/disabled)
- last_login_at: 最后登录时间

运行：cd backend && venv/Scripts/python.exe scripts/migrate_add_users_role_status.py
"""
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
DB_PATH = PROJECT_ROOT / "data" / "knowledge" / "knowledge.db"


def migrate():
    print(f"数据库: {DB_PATH}")
    if not DB_PATH.exists():
        print("错误：数据库不存在")
        return

    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    try:
        # 检查现有字段
        cur.execute("PRAGMA table_info(users)")
        cols = {c[1] for c in cur.fetchall()}

        # 添加 role 字段
        if "role" not in cols:
            print("添加 role 字段...")
            cur.execute("ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'user'")

        # 添加 status 字段
        if "status" not in cols:
            print("添加 status 字段...")
            cur.execute("ALTER TABLE users ADD COLUMN status TEXT DEFAULT 'active'")

        # 添加 last_login_at 字段
        if "last_login_at" not in cols:
            print("添加 last_login_at 字段...")
            cur.execute("ALTER TABLE users ADD COLUMN last_login_at TEXT")

        # 把现有 admin 设为 admin 角色
        cur.execute("UPDATE users SET role = 'admin' WHERE username = 'admin'")

        conn.commit()
        print("迁移完成!")

        # 验证
        cur.execute("SELECT id, username, role, status FROM users")
        print("\n用户列表:")
        for row in cur.fetchall():
            print(f"  {row}")
    except Exception as e:
        conn.rollback()
        print(f"失败: {e}")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    migrate()
