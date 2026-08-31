"""
知识库目录树存储：管理用户上传的文件/文件夹（多级树结构）

数据模型（资源管理器式多级树，非固定三层）：
- nodes 表：目录树的节点（文件夹 + 文件）
    id, parent_id (NULL=根), name, type ('folder'/'file'), user_id
- documents 表：已解析的文件元数据
    node_id, file_type, extract_text, status, file_size, created_at

设计：
- 每个用户独立数据文件（kb.db），与知识图谱/RAG 用户隔离一致
- parent_id 递归表示目录层级，支持无限嵌套（递归深度由调用方限制）
- 删除文件夹时递归删除其下所有子节点及关联文档
"""

import json
import sqlite3
from pathlib import Path
from typing import Optional


class KbStore:
    """知识库目录树存储（SQLite 后端）"""

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "kb.db"
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._create_tables()

    def _create_tables(self) -> None:
        with self._conn:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS nodes (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    parent_id INTEGER,          -- NULL = 根目录下
                    name      TEXT NOT NULL,
                    type      TEXT NOT NULL,    -- 'folder' / 'file'
                    user_id   INTEGER NOT NULL,
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS documents (
                    node_id      INTEGER PRIMARY KEY,   -- 对应 nodes.id (文件)
                    file_type    TEXT NOT NULL,          -- .pdf / .docx / .md ...
                    extract_text TEXT NOT NULL,          -- 解析出的纯文本
                    file_size    INTEGER DEFAULT 0,
                    status       TEXT DEFAULT 'parsed',  -- 'pending' / 'parsed' / 'failed'
                    created_at   TEXT DEFAULT (datetime('now'))
                )
            """)
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_nodes_parent ON nodes (parent_id, user_id)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_nodes_user ON nodes (user_id)"
            )

    def close(self) -> None:
        self._conn.close()

    # ────────────────────────────────────────────
    #  目录操作
    # ────────────────────────────────────────────

    def create_folder(self, user_id: int, name: str, parent_id: Optional[int] = None) -> int:
        """创建文件夹，返回新节点 ID"""
        if parent_id is not None:
            parent = self.get_node(parent_id)
            if parent is None or parent["user_id"] != user_id:
                raise ValueError("父目录不存在")
            if parent["type"] != "folder":
                raise ValueError("父节点不是文件夹")
        with self._conn:
            cur = self._conn.execute(
                "INSERT INTO nodes (parent_id, name, type, user_id) VALUES (?, ?, 'folder', ?)",
                (parent_id, name, user_id),
            )
            return cur.lastrowid

    def add_file(self, user_id: int, name: str, parent_id: Optional[int],
                 file_type: str, extract_text: str, file_size: int = 0) -> int:
        """添加一个已解析的文件节点，返回节点 ID"""
        if parent_id is not None:
            parent = self.get_node(parent_id)
            if parent is None or parent["user_id"] != user_id:
                raise ValueError("父目录不存在")
            if parent["type"] != "folder":
                raise ValueError("父节点不是文件夹")
        with self._conn:
            cur = self._conn.execute(
                "INSERT INTO nodes (parent_id, name, type, user_id) VALUES (?, ?, 'file', ?)",
                (parent_id, name, user_id),
            )
            node_id = cur.lastrowid
            self._conn.execute("""
                INSERT INTO documents (node_id, file_type, extract_text, file_size, status)
                VALUES (?, ?, ?, ?, 'parsed')
            """, (node_id, file_type, extract_text, file_size))
            return node_id

    def get_node(self, node_id: int) -> Optional[dict]:
        """根据 ID 查找节点"""
        row = self._conn.execute(
            "SELECT * FROM nodes WHERE id = ?", (node_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_node_path(self, user_id: int, node_id: int) -> str:
        """
        获取某节点在目录树中的完整路径（从根到该节点），如 '/数据结构/第2章/栈.md'。

        实现：从 node_id 向上逐级查父节点拼名称。深度一般很小（2~5 层），
        用局部缓存避免同一用户同批命中节点的重复查询。

        返回:
            路径字符串；节点不存在或不属于该用户时返回空字符串。
        """
        parts: list[str] = []
        seen: set[int] = set()  # 防环保护
        current = node_id
        while current is not None and current not in seen:
            row = self._conn.execute(
                "SELECT id, parent_id, name, user_id FROM nodes WHERE id = ?",
                (current,),
            ).fetchone()
            if row is None:
                return ""  # 节点不存在，视为无效路径
            if row["user_id"] != user_id:
                return ""
            parts.append(row["name"])
            seen.add(current)
            current = row["parent_id"]

        if not parts:
            return ""
        return "/" + "/".join(reversed(parts))

    def get_children(self, user_id: int, parent_id: Optional[int]) -> list[dict]:
        """获取某目录下的直接子节点"""
        if parent_id is None:
            rows = self._conn.execute(
                "SELECT * FROM nodes WHERE user_id = ? AND parent_id IS NULL ORDER BY "
                "CASE WHEN type='folder' THEN 0 ELSE 1 END, name",
                (user_id,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM nodes WHERE user_id = ? AND parent_id = ? ORDER BY "
                "CASE WHEN type='folder' THEN 0 ELSE 1 END, name",
                (user_id, parent_id),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_document(self, node_id: int) -> Optional[dict]:
        """获取文件的解析文档"""
        row = self._conn.execute(
            "SELECT * FROM documents WHERE node_id = ?", (node_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_document_text(self, node_id: int) -> str:
        """获取文件解析出的文本（用于向量化）"""
        doc = self.get_document(node_id)
        return doc["extract_text"] if doc else ""

    def update_document_status(self, node_id: int, status: str) -> None:
        """更新文档解析状态"""
        with self._conn:
            self._conn.execute(
                "UPDATE documents SET status = ? WHERE node_id = ?", (status, node_id)
            )

    def collect_descendant_files(self, user_id: int, node_id: int,
                                 max_depth: Optional[int] = None) -> list[int]:
        """
        收集某节点下所有文件节点 ID（递归）。

        参数:
            node_id:   起始节点（文件夹或文件）
            max_depth: 最大递归深度（None=不限）。文件夹自身深度为 0，
                       子文件夹深度 +1。用于限制递归范围。

        返回:
            文件节点 ID 列表。如果起始节点本身是文件，返回 [node_id]。
        """
        node = self.get_node(node_id)
        if node is None or node["user_id"] != user_id:
            return []
        if node["type"] == "file":
            return [node_id]

        result: list[int] = []
        self._dfs_collect(user_id, node_id, 0, max_depth, result)
        return result

    def _dfs_collect(self, user_id: int, parent_id: int, depth: int,
                     max_depth: Optional[int], result: list[int]) -> None:
        """DFS 递归收集文件夹下所有文件"""
        for child in self.get_children(user_id, parent_id):
            if child["type"] == "file":
                result.append(child["id"])
            else:  # folder
                if max_depth is not None and depth + 1 > max_depth:
                    continue
                self._dfs_collect(user_id, child["id"], depth + 1, max_depth, result)

    def delete_node_recursive(self, user_id: int, node_id: int) -> list[int]:
        """
        删除节点及其所有子节点（递归），返回被删除的节点 ID 列表。
        同时清理关联的 documents 记录。
        """
        node = self.get_node(node_id)
        if node is None or node["user_id"] != user_id:
            raise ValueError("节点不存在")

        to_delete: list[int] = []
        self._collect_ids(user_id, node_id, to_delete)

        with self._conn:
            # 先删 documents（外键），再删 nodes
            for nid in to_delete:
                self._conn.execute("DELETE FROM documents WHERE node_id = ?", (nid,))
            for nid in to_delete:
                self._conn.execute("DELETE FROM nodes WHERE id = ?", (nid,))
        return to_delete

    def _collect_ids(self, user_id: int, node_id: int, result: list[int]) -> None:
        """递归收集节点及其所有子节点 ID"""
        result.append(node_id)
        for child in self.get_children(user_id, node_id):
            self._collect_ids(user_id, child["id"], result)

    def build_tree(self, user_id: int, parent_id: Optional[int] = None) -> list[dict]:
        """
        构建目录树（嵌套结构），供前端渲染。

        返回:
            [{id, name, type, children: [...], is_leaf}]
        """
        children = self.get_children(user_id, parent_id)
        tree = []
        for child in children:
            item = {
                "id": child["id"],
                "name": child["name"],
                "type": child["type"],
            }
            if child["type"] == "folder":
                item["children"] = self.build_tree(user_id, child["id"])
                item["is_leaf"] = not item["children"]
            else:
                item["is_leaf"] = True
                # 附带文件元信息
                doc = self.get_document(child["id"])
                item["file_type"] = doc["file_type"] if doc else ""
                item["status"] = doc["status"] if doc else ""
            tree.append(item)
        return tree
