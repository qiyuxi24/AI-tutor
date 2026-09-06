"""
文档向量存储：存储上传文件的分块向量，支持按文档/目录范围检索

与图谱向量存储（rag/vector_store.py）分开：
- 图谱检索走 rag_manager（node_id 维度）
- 上传文件检索走本模块（doc_id 维度）
- 二者数据隔离，避免混淆

表结构：
- doc_chunks: id, doc_id, node_id(文件夹节点), chunk_index, content,
              heading, embedding, user_id
  其中 node_id 是目录树中文件节点的 id（即 KbStore 的 nodes.id）
  embedding 允许 NULL：短文/题目可只进 whoosh 稀疏索引（BM25-only），
  此时向量检索跳过该行、混合检索仍经 BM25 命中（B2.4）。
"""

import json
import sqlite3
import numpy as np
from pathlib import Path


class DocVectorStore:
    """上传文档的向量存储（SQLite + numpy 余弦相似度）"""

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "rag.db"
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._create_tables()

    def _create_tables(self) -> None:
        with self._conn:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS doc_chunks (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    node_id    INTEGER NOT NULL,       -- KbStore nodes.id (文件节点)
                    doc_id     INTEGER NOT NULL,       -- 文档标识（同文件所有分块共享）
                    chunk_index INTEGER DEFAULT 0,
                    content    TEXT NOT NULL,
                    heading    TEXT DEFAULT '',
                    embedding  TEXT,              -- 可空：BM25-only 块置 NULL
                    user_id    INTEGER NOT NULL,
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_doc_chunks_user_node "
                "ON doc_chunks (user_id, node_id)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_doc_chunks_doc ON doc_chunks (doc_id)"
            )

    def close(self) -> None:
        self._conn.close()

    # ────────────────────────────────────────────
    #  写入
    # ────────────────────────────────────────────

    def upsert_chunk(self, user_id: int, node_id: int, doc_id: int,
                     chunk_index: int, content: str, heading: str,
                     embedding: list[float] | None) -> int:
        """
        插入片段；embedding 为 None 时写入 NULL（BM25-only，不进向量检索）。

        返回:
            新插入片段的 id（doc_chunks.id，即混合检索的统一主键 chunk_id）
        """
        emb_json = None if embedding is None \
            else json.dumps(embedding, ensure_ascii=False)
        with self._conn:
            cur = self._conn.execute("""
                INSERT INTO doc_chunks (node_id, doc_id, chunk_index, content,
                                        heading, embedding, user_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (node_id, doc_id, chunk_index, content, heading,
                  emb_json, user_id))
            return cur.lastrowid

    def delete_node_chunks(self, user_id: int, node_id: int) -> int:
        """删除某文件节点的全部分块"""
        with self._conn:
            cur = self._conn.execute(
                "DELETE FROM doc_chunks WHERE user_id = ? AND node_id = ?",
                (user_id, node_id),
            )
            return cur.rowcount

    def delete_docs_chunks(self, user_id: int, node_ids: list[int]) -> int:
        """删除一批文件节点（含其子节点）的全部分块"""
        if not node_ids:
            return 0
        placeholders = ",".join("?" * len(node_ids))
        with self._conn:
            cur = self._conn.execute(
                f"DELETE FROM doc_chunks WHERE user_id = ? AND node_id IN ({placeholders})",
                [user_id] + node_ids,
            )
            return cur.rowcount

    def clear_user(self, user_id: int) -> int:
        with self._conn:
            cur = self._conn.execute(
                "DELETE FROM doc_chunks WHERE user_id = ?", (user_id,)
            )
            return cur.rowcount

    # ────────────────────────────────────────────
    #  检索
    # ────────────────────────────────────────────

    def search(self, user_id: int, query_embedding: list[float],
               node_ids: list[int] | None = None, top_k: int = 5) -> list[dict]:
        """
        余弦相似度检索。

        参数:
            user_id:         用户 ID
            query_embedding: 查询向量
            node_ids:        限定检索范围的文件节点 ID 列表（None=检索该用户全部上传文档）
            top_k:           返回条数

        返回:
            [{node_id, content, score, chunk_index}, ...] 按相似度降序
        """
        if node_ids:
            placeholders = ",".join("?" * len(node_ids))
            sql = f"SELECT * FROM doc_chunks WHERE user_id = ? AND node_id IN ({placeholders})"
            params: list = [user_id] + node_ids
        else:
            sql = "SELECT * FROM doc_chunks WHERE user_id = ?"
            params = [user_id]

        rows = self._conn.execute(sql, params).fetchall()
        if not rows:
            return []

        q = np.asarray(query_embedding, dtype=np.float32)
        q_norm = np.linalg.norm(q)
        if q_norm == 0:
            return []

        scored = []
        for row in rows:
            if not row["embedding"]:
                continue  # embedding 为 NULL（BM25-only 块）：向量检索跳过
            try:
                vec = np.asarray(json.loads(row["embedding"]), dtype=np.float32)
            except (json.JSONDecodeError, TypeError):
                continue
            norm = np.linalg.norm(vec)
            if norm == 0:
                continue
            score = float(np.dot(q, vec) / (q_norm * norm))
            scored.append({
                "chunk_id": row["id"],           # doc_chunks.id，混合检索统一主键
                "node_id": row["node_id"],
                "chunk_index": row["chunk_index"],
                "content": row["content"],
                "heading": row["heading"],
                "score": round(score, 4),
            })

        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:top_k]

    def get_node_chunks(self, user_id: int, node_id: int) -> list[dict]:
        """
        获取某文件节点（doc_id）的全部片段，按 chunk_index 升序排列。

        用于父级扩展：命中某个子 chunk 后，据此取该文件相邻的片段做上下文拼接。

        返回:
            [{chunk_id, chunk_index, content, heading}, ...]
            空列表表示该文件无分块或不存在。
        """
        rows = self._conn.execute(
            "SELECT id, chunk_index, content, heading FROM doc_chunks "
            "WHERE user_id = ? AND node_id = ? ORDER BY chunk_index ASC",
            (user_id, node_id),
        ).fetchall()
        return [
            {
                "chunk_id": r["id"],
                "chunk_index": r["chunk_index"],
                "content": r["content"],
                "heading": r["heading"],
            }
            for r in rows
        ]

    def count(self, user_id: int) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS c FROM doc_chunks WHERE user_id = ?", (user_id,)
        ).fetchone()
        return row["c"] if row else 0
