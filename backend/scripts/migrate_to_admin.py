"""
迁移脚本：将历史知识图谱数据迁移到默认 admin 账户

功能：
1. 确保 admin 账户存在于 data/knowledge/knowledge.db 中
2. 将所有 user_id != 1 的 nodes/edges 数据改为 user_id = 1
3. 迁移 MD 文件到 nodes/1/ 目录下
"""
import sqlite3
import shutil
from pathlib import Path

# 路径配置（与 KnowledgeGraph 默认 data_dir 一致）
DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "knowledge"
DB_PATH = DATA_DIR / "knowledge.db"
NODES_DIR = DATA_DIR / "nodes"  # 旧 MD 文件目录
TARGET_USER_ID = 1  # admin 用户 ID
SOURCE_USER_ID = 5  # 历史数据所属用户 ID

print(f"数据库路径: {DB_PATH}")
print(f"节点目录: {NODES_DIR}")
print()

if not DB_PATH.exists():
    print("❌ 数据库不存在，无需迁移")
    exit(1)

conn = sqlite3.connect(str(DB_PATH))
conn.row_factory = sqlite3.Row
conn.execute("PRAGMA foreign_keys=ON")

# ════════════════════════════════════════════
# 1. 检查 admin 账户
# ════════════════════════════════════════════
admin = conn.execute("SELECT id, username FROM users WHERE id = ?", (TARGET_USER_ID,)).fetchone()
if not admin:
    # admin 不在 id=1 位置，尝试查找
    admin = conn.execute("SELECT id, username FROM users WHERE username = 'admin'").fetchone()
    if admin:
        print(f"admin 账户在 id={admin['id']}，将使用此 ID 作为目标")
        TARGET_USER_ID = admin["id"]
    else:
        print("❌ admin 账户不存在，请先启动后端自动创建")
        conn.close()
        exit(1)
else:
    print(f"✓ admin 账户已存在 (id={TARGET_USER_ID})")

# ════════════════════════════════════════════
# 2. 查找需要迁移的节点
# ════════════════════════════════════════════
# 找出所有不属于 admin 的节点
migrate_nodes = conn.execute(
    "SELECT id, name, file_path, user_id FROM nodes WHERE user_id != ? OR user_id IS NULL",
    (TARGET_USER_ID,)
).fetchall()

if not migrate_nodes:
    print("✓ 没有需要迁移的节点数据")
else:
    print(f"\n需要迁移的节点: {len(migrate_nodes)} 个")
    for n in migrate_nodes:
        print(f"  - {n['id']} ({n['name']}) user_id={n['user_id']}")

    # 检查是否有节点 ID 冲突（admin 已有同名节点）
    admin_node_ids = set(
        r["id"] for r in conn.execute(
            "SELECT id FROM nodes WHERE user_id = ?", (TARGET_USER_ID,)
        ).fetchall()
    )
    conflicts = [n for n in migrate_nodes if n["id"] in admin_node_ids]
    if conflicts:
        print(f"\n⚠ 以下节点 ID 在 admin 账户中已存在，将被跳过：")
        for n in conflicts:
            print(f"  - {n['id']}")
        migrate_nodes = [n for n in migrate_nodes if n["id"] not in admin_node_ids]

    if migrate_nodes:
        # 更新 nodes 表的 user_id
        node_ids = [n["id"] for n in migrate_nodes]
        placeholders = ",".join("?" * len(node_ids))
        conn.execute(
            f"UPDATE nodes SET user_id = ? WHERE id IN ({placeholders})",
            [TARGET_USER_ID] + node_ids
        )
        conn.commit()
        print(f"\n✓ 已迁移 {len(migrate_nodes)} 个节点到 admin 账户")

# ════════════════════════════════════════════
# 3. 迁移边数据
# ════════════════════════════════════════════
migrate_edges = conn.execute(
    "SELECT id, from_node, to_node, relation, user_id FROM edges WHERE user_id != ? OR user_id IS NULL",
    (TARGET_USER_ID,)
).fetchall()

if not migrate_edges:
    print("✓ 没有需要迁移的边数据")
else:
    print(f"\n需要迁移的边: {len(migrate_edges)} 条")
    for e in migrate_edges:
        print(f"  - {e['from_node']} --[{e['relation']}]--> {e['to_node']} (user_id={e['user_id']})")

    edge_ids = [e["id"] for e in migrate_edges]
    placeholders = ",".join("?" * len(edge_ids))
    conn.execute(
        f"UPDATE edges SET user_id = ? WHERE id IN ({placeholders})",
        [TARGET_USER_ID] + edge_ids
    )
    conn.commit()
    print(f"\n✓ 已迁移 {len(migrate_edges)} 条边到 admin 账户")

# ════════════════════════════════════════════
# 4. 迁移 MD 文件
# ════════════════════════════════════════════
target_nodes_dir = NODES_DIR / str(TARGET_USER_ID)
target_nodes_dir.mkdir(parents=True, exist_ok=True)

# 从旧位置（nodes/ 根目录 或 nodes/5/）复制到 nodes/1/
md_migrated = 0
for old_subdir in [NODES_DIR, NODES_DIR / str(SOURCE_USER_ID)]:
    if old_subdir.is_dir():
        for md_file in old_subdir.glob("*.md"):
            target_path = target_nodes_dir / md_file.name
            if not target_path.exists():
                shutil.copy2(md_file, target_path)
                print(f"  ✓ 复制 MD: {md_file.name}")
                md_migrated += 1

if md_migrated > 0:
    print(f"\n✓ 已迁移 {md_migrated} 个 MD 文件到 nodes/{TARGET_USER_ID}/")

# ════════════════════════════════════════════
# 5. 最终统计
# ════════════════════════════════════════════
print("\n" + "=" * 50)
print("迁移完成！最终数据统计：")
print("-" * 50)
node_count = conn.execute(
    "SELECT COUNT(*) FROM nodes WHERE user_id = ?", (TARGET_USER_ID,)
).fetchone()[0]
edge_count = conn.execute(
    "SELECT COUNT(*) FROM edges WHERE user_id = ?", (TARGET_USER_ID,)
).fetchone()[0]
print(f"  admin 账户节点数: {node_count}")
print(f"  admin 账户边数:   {edge_count}")
print(f"  MD 文件目录:      {target_nodes_dir}")
print("=" * 50)

conn.close()
