"""
数据库迁移脚本：添加用户系统支持
- 创建 users 表
- 给 nodes 和 edges 表添加 user_id 列
- 创建默认管理员用户（admin / admin123）
- 将已有数据分配给默认管理员用户

运行方式：python backend/scripts/migrate_add_users.py
"""
import sqlite3
import os
from pathlib import Path

# 数据库路径（相对于项目根目录）
PROJECT_ROOT = Path(__file__).parent.parent.parent
DB_PATH = PROJECT_ROOT / "data" / "knowledge" / "knowledge.db"


def migrate():
    """执行迁移"""
    print(f"数据库路径: {DB_PATH}")
    if not DB_PATH.exists():
        print("错误：数据库文件不存在，请先初始化知识图谱。")
        return

    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()

    try:
        # 1. 创建 users 表
        print("创建 users 表...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)

        # 2. 检查 nodes 表是否有 user_id 列
        cursor.execute("PRAGMA table_info(nodes)")
        columns = [col[1] for col in cursor.fetchall()]

        if "user_id" not in columns:
            print("给 nodes 表添加 user_id 列...")
            cursor.execute("ALTER TABLE nodes ADD COLUMN user_id INTEGER REFERENCES users(id)")

        # 3. 检查 edges 表是否有 user_id 列
        cursor.execute("PRAGMA table_info(edges)")
        columns = [col[1] for col in cursor.fetchall()]

        if "user_id" not in columns:
            print("给 edges 表添加 user_id 列...")
            cursor.execute("ALTER TABLE edges ADD COLUMN user_id INTEGER REFERENCES users(id)")

        # 4. 创建默认管理员用户
        from passlib.context import CryptContext
        pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
        admin_password_hash = pwd_context.hash("admin123")

        cursor.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (1, ?, ?)",
            ("admin", admin_password_hash)
        )
        conn.commit()

        # 获取管理员用户 ID
        cursor.execute("SELECT id FROM users WHERE username = 'admin'")
        admin_id = cursor.fetchone()[0]
        print(f"管理员用户 ID: {admin_id} (admin / admin123)")

        # 5. 将已有数据分配给默认管理员
        # nodes 表中 user_id 为 NULL 的行
        cursor.execute("SELECT COUNT(*) FROM nodes WHERE user_id IS NULL")
        null_nodes = cursor.fetchone()[0]
        if null_nodes > 0:
            print(f"将 {null_nodes} 个节点的 user_id 设为 {admin_id}...")
            cursor.execute("UPDATE nodes SET user_id = ? WHERE user_id IS NULL", (admin_id,))

        # edges 表中 user_id 为 NULL 的行
        cursor.execute("SELECT COUNT(*) FROM edges WHERE user_id IS NULL")
        null_edges = cursor.fetchone()[0]
        if null_edges > 0:
            print(f"将 {null_edges} 条边的 user_id 设为 {admin_id}...")
            cursor.execute("UPDATE edges SET user_id = ? WHERE user_id IS NULL", (admin_id,))

        conn.commit()
        print("\n迁移完成！")

        # 6. 打印当前状态
        cursor.execute("SELECT COUNT(*) FROM users")
        user_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM nodes")
        node_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM edges")
        edge_count = cursor.fetchone()[0]
        print(f"\n当前数据统计:")
        print(f"  用户数: {user_count}")
        print(f"  节点数: {node_count}")
        print(f"  边数:   {edge_count}")

    except Exception as e:
        conn.rollback()
        print(f"迁移失败: {e}")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    migrate()
