"""
数据库迁移脚本：doc_chunks.embedding 列允许 NULL（B2.4 BM25-only 支持）

背景：知识库短文/题目支持"只写 whoosh 稀疏索引、不做 embedding"，
doc_chunks.embedding 列需从 NOT NULL 改为可空。SQLite 不支持直接改列约束，
采用标准"重建表"迁移：建允许 NULL 的新表 → 复制数据 → 换名 → 重建索引。

幂等：表不存在 / 无 embedding 列 / 已可空时直接跳过，可重复执行。

用法（在 backend/ 下执行）：
    venv/Scripts/python.exe scripts/migrate_embedding_nullable.py
    venv/Scripts/python.exe scripts/migrate_embedding_nullable.py --db <单个 rag.db 路径>
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

# KB 数据根：backend/data/kb/{user_id}/rag.db（与 KbManager 默认目录一致）
KB_DIR = Path(__file__).resolve().parents[1] / "data" / "kb"

# 重建后的表结构（仅 embedding 去掉 NOT NULL，其余与 DocVectorStore 建表一致）
_NEW_TABLE_SQL = """
    CREATE TABLE doc_chunks_new (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        node_id     INTEGER NOT NULL,
        doc_id      INTEGER NOT NULL,
        chunk_index INTEGER DEFAULT 0,
        content     TEXT NOT NULL,
        heading     TEXT DEFAULT '',
        embedding   TEXT,
        user_id     INTEGER NOT NULL,
        created_at  TEXT DEFAULT (datetime('now'))
    )
"""


def migrate_db(db_path: Path) -> bool:
    """迁移单个 rag.db 的 doc_chunks 表；返回是否真的执行了重建（幂等）。"""
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    try:
        has_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='doc_chunks'"
        ).fetchone()
        if not has_table:
            return False

        embedding_notnull = None
        for _cid, name, _type, notnull, _dflt, _pk in \
                conn.execute("PRAGMA table_info(doc_chunks)").fetchall():
            if name == "embedding":
                embedding_notnull = bool(notnull)
                break
        if embedding_notnull is None or not embedding_notnull:
            return False  # 无该列或已可空：无需迁移

        conn.execute("BEGIN IMMEDIATE")
        conn.execute(_NEW_TABLE_SQL)
        conn.execute("""
            INSERT INTO doc_chunks_new (id, node_id, doc_id, chunk_index,
                                        content, heading, embedding, user_id,
                                        created_at)
            SELECT id, node_id, doc_id, chunk_index, content, heading,
                   embedding, user_id, created_at FROM doc_chunks
        """)
        conn.execute("DROP TABLE doc_chunks")
        conn.execute("ALTER TABLE doc_chunks_new RENAME TO doc_chunks")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_doc_chunks_user_node "
                     "ON doc_chunks (user_id, node_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_doc_chunks_doc "
                     "ON doc_chunks (doc_id)")
        conn.execute("COMMIT")
        return True
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="doc_chunks.embedding 列迁移为可空（B2.4 BM25-only）")
    parser.add_argument("--db", type=Path, default=None,
                        help="只迁移指定 rag.db（默认扫描 KB 目录下全部用户库）")
    args = parser.parse_args()

    if args.db is not None:
        targets = [args.db]
    else:
        if not KB_DIR.is_dir():
            print(f"未找到 KB 数据目录: {KB_DIR}，跳过")
            return
        targets = sorted(KB_DIR.glob("*/rag.db"))

    migrated = skipped = 0
    for db in targets:
        if not db.is_file():
            continue
        if migrate_db(db):
            print(f"  已迁移: {db}")
            migrated += 1
        else:
            skipped += 1
    print(f"完成：迁移 {migrated} 个库，跳过 {skipped} 个"
          f"（表不存在/已可空，幂等可重复执行）")


if __name__ == "__main__":
    main()
