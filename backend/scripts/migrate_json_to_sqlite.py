"""
JSON → SQLite 数据迁移脚本

功能：
1. 读取 data/knowledge/index.json
2. 将 nodes 和 edges 插入 SQLite（data/knowledge/knowledge.db）
3. 输出迁移日志
4. 保留原 index.json 作为备份（index.json.bak 已由规范化脚本生成）

用法：
    python backend/scripts/migrate_json_to_sqlite.py
"""

import json
import sqlite3
import sys
from pathlib import Path


def main():
    project_root = Path(__file__).resolve().parent.parent.parent
    index_path = project_root / "data" / "knowledge" / "index.json"
    db_path = project_root / "data" / "knowledge" / "knowledge.db"

    # 1. 读取 JSON
    if not index_path.exists():
        print(f"❌ 错误：找不到 {index_path}")
        print("   请先运行 normalize_index.py 确保数据规范")
        sys.exit(1)

    with open(index_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    nodes = data.get("nodes", [])
    edges = data.get("edges", [])
    print(f"📊 读取 JSON：{len(nodes)} 个节点，{len(edges)} 条边")

    # 2. 删除旧数据库（如果存在）
    if db_path.exists():
        db_path.unlink()
        print(f"🗑️  已删除旧数据库：{db_path.name}")

    # 3. 创建数据库并建表
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    conn.executescript("""
        CREATE TABLE nodes (
            id              TEXT PRIMARY KEY,
            name            TEXT NOT NULL,
            file_path       TEXT NOT NULL,
            tags            TEXT DEFAULT '[]',
            summary         TEXT DEFAULT '',
            mastery         INTEGER DEFAULT 0,
            difficulty      INTEGER DEFAULT 3,
            estimated_minutes INTEGER DEFAULT 15,
            added_by        TEXT DEFAULT 'human',
            created_at      TEXT,
            confidence      REAL
        );

        CREATE TABLE edges (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            from_node   TEXT NOT NULL,
            to_node     TEXT NOT NULL,
            relation    TEXT NOT NULL,
            label       TEXT DEFAULT '',
            added_by    TEXT DEFAULT 'human',
            confidence  REAL,
            FOREIGN KEY (from_node) REFERENCES nodes(id) ON DELETE CASCADE,
            FOREIGN KEY (to_node)   REFERENCES nodes(id) ON DELETE CASCADE
        );
    """)
    print("✅ 数据库表创建完成")

    # 4. 迁移节点
    node_count = 0
    for node in nodes:
        try:
            tags_json = json.dumps(node.get("tags", []), ensure_ascii=False)
            # JSON 中字段名是 "file"，数据库中是 "file_path"
            file_path = node.get("file", f"nodes/{node['id']}.md")

            conn.execute("""
                INSERT INTO nodes (id, name, file_path, tags, summary, mastery,
                                   difficulty, estimated_minutes, added_by, created_at, confidence)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                node["id"],
                node.get("name", ""),
                file_path,
                tags_json,
                node.get("summary", ""),
                node.get("mastery", 0),
                node.get("difficulty", 3),
                node.get("estimated_minutes", 15),
                node.get("added_by", "human"),
                node.get("created_at", ""),
                node.get("confidence"),
            ))
            node_count += 1
            print(f"  ✅ 节点：{node['id']} ({node.get('name', '')})")
        except Exception as e:
            print(f"  ❌ 节点 {node.get('id', '?')} 插入失败：{e}")

    # 5. 迁移边
    edge_count = 0
    for edge in edges:
        try:
            conn.execute("""
                INSERT INTO edges (from_node, to_node, relation, label, added_by, confidence)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                edge["from"],
                edge["to"],
                edge.get("relation", "related"),
                edge.get("label", ""),
                edge.get("added_by", "human"),
                edge.get("confidence"),
            ))
            edge_count += 1
            print(f"  ✅ 边：{edge['from']} → {edge['to']} ({edge.get('relation', '')})")
        except Exception as e:
            print(f"  ❌ 边 {edge.get('from', '?')}→{edge.get('to', '?')} 插入失败：{e}")

    conn.commit()

    # 6. 验证
    db_nodes = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
    db_edges = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]

    print(f"\n{'='*50}")
    print(f"迁移完成：")
    print(f"  节点：{node_count} 个（数据库 {db_nodes} 个）")
    print(f"  边：  {edge_count} 条（数据库 {db_edges} 条）")
    print(f"  数据库：{db_path}")
    print(f"  备份：  {index_path.with_suffix('.json.bak')}")
    print(f"{'='*50}")

    if node_count != db_nodes or edge_count != db_edges:
        print("⚠️  警告：迁移数量不一致，请检查！")
        sys.exit(1)

    conn.close()
    print("\n🎉 迁移成功！")


if __name__ == "__main__":
    main()
