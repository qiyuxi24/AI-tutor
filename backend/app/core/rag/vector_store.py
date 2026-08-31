"""
向量存储：基于 SQLite 存储嵌入向量，numpy 实现余弦相似度检索

设计决策：
- 复用项目已有的 SQLite 技术栈，零新增数据库依赖
- 向量以 JSON 数组字符串存 blob，维度固定（text-embedding-v4 为 1024 维）
- 检索时加载全部向量到内存做余弦相似度（知识图谱节点规模小，完全够用）
- 每个用户独立数据文件，与知识图谱的用户隔离策略一致
- 增删改走 SQL，保持与 knowledge_graph.py 一致的风格
"""

import json
import sqlite3
import numpy as np
from pathlib import Path
from typing import Optional


class VectorStore:
    """
    SQLite 向量存储
    :param data_dir: 数据目录（rag 数据库所在目录）
    """

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
                CREATE TABLE IF NOT EXISTS chunks (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    node_id    TEXT NOT NULL,
                    node_name  TEXT NOT NULL,
                    heading    TEXT DEFAULT '',
                    content    TEXT NOT NULL,
                    chunk_index INTEGER DEFAULT 0,
                    embedding  TEXT NOT NULL,
                    user_id    INTEGER NOT NULL,
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_chunks_user_node ON chunks (user_id, node_id)"
            )

    def close(self) -> None:
        self._conn.close()

    # ────────────────────────────────────────────
    #  写入
    # ────────────────────────────────────────────

    def upsert_chunk(self, user_id: int, node_id: str, node_name: str,
                     heading: str, content: str, chunk_index: int,
                     embedding: list[float]) -> None:
        """插入单个片段及其向量"""
        with self._conn:
            self._conn.execute("""
                INSERT INTO chunks (node_id, node_name, heading, content,
                                    chunk_index, embedding, user_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                node_id, node_name, heading, content,
                chunk_index, json.dumps(embedding, ensure_ascii=False), user_id,
            ))

    def delete_node_chunks(self, user_id: int, node_id: str) -> int:
        """删除某节点的所有片段（节点更新/删除时调用）"""
        with self._conn:
            cur = self._conn.execute(
                "DELETE FROM chunks WHERE user_id = ? AND node_id = ?",
                (user_id, node_id),
            )
            return cur.rowcount

    def clear_user(self, user_id: int) -> int:
        """清空某用户的全部索引（重建时调用）"""
        with self._conn:
            cur = self._conn.execute(
                "DELETE FROM chunks WHERE user_id = ?", (user_id,)
            )
            return cur.rowcount

    # ────────────────────────────────────────────
    #  检索
    # ────────────────────────────────────────────

    def search(self, user_id: int, query_embedding: list[float],
               top_k: int = 5) -> list[dict]:
        """
        余弦相似度检索，返回最相似的 top_k 个片段

        参数:
            user_id:         当前用户 ID
            query_embedding: 查询文本的嵌入向量
            top_k:           返回条数

        返回:
            [{node_id, node_name, heading, content, score, ...}, ...] 按相似度降序
        """
        rows = self._conn.execute(
            "SELECT id, node_id, node_name, heading, content, chunk_index, embedding "
            "FROM chunks WHERE user_id = ?",
            (user_id,),
        ).fetchall()

        if not rows:
            return []

        q = np.asarray(query_embedding, dtype=np.float32)
        q_norm = np.linalg.norm(q)
        if q_norm == 0:
            return []

        scored = []
        for row in rows:
            try:
                vec = np.asarray(json.loads(row["embedding"]), dtype=np.float32)
            except (json.JSONDecodeError, TypeError):
                continue
            norm = np.linalg.norm(vec)
            if norm == 0:
                continue
            score = float(np.dot(q, vec) / (q_norm * norm))
            scored.append({
                "node_id": row["node_id"],
                "node_name": row["node_name"],
                "heading": row["heading"],
                "content": row["content"],
                "score": round(score, 4),
            })

        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:top_k]

    def count(self, user_id: int) -> int:
        """返回某用户的片段总数"""
        row = self._conn.execute(
            "SELECT COUNT(*) AS c FROM chunks WHERE user_id = ?", (user_id,)
        ).fetchone()
        return row["c"] if row else 0
