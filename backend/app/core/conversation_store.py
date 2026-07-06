"""
对话持久化存储模块：基于 SQLite 管理用户对话历史

设计决策：
- 独立数据库 data/conversations/conversations.db，不与 knowledge.db 混合
- messages 字段以 JSON 字符串存储（SQLite 不支持数组类型）
- 每个用户一个表行，upsert 模式（同一对话 ID 覆盖更新）
- 前端双写：localStorage 为主 + 后端同步；如果前端 localStorage 为空则从后端拉取
"""

import json
import sqlite3
import time
from pathlib import Path
from typing import Optional


class ConversationStore:
    """
    对话存储管理类（SQLite 后端，独立数据库）
    :param user_id: 当前登录用户 ID，所有查询/写入都按此隔离
    """

    def __init__(self, user_id: int, data_dir: Optional[Path] = None):
        if data_dir is None:
            data_dir = Path(__file__).parent.parent.parent.parent / "data" / "conversations"
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "conversations.db"
        self.user_id = user_id

        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._create_table()

    def _create_table(self) -> None:
        """创建 conversations 表（如果不存在）"""
        with self._conn:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id          TEXT NOT NULL,
                    user_id     INTEGER NOT NULL,
                    title       TEXT NOT NULL DEFAULT '新对话',
                    messages    TEXT NOT NULL DEFAULT '[]',
                    created_at  REAL NOT NULL,
                    updated_at  REAL NOT NULL,
                    PRIMARY KEY (id, user_id)
                )
            """)

    def close(self) -> None:
        """关闭数据库连接"""
        self._conn.close()

    # ════════════════════════════════════════════
    #  CRUD 操作
    # ════════════════════════════════════════════

    def list_conversations(self) -> list[dict]:
        """
        获取当前用户的所有对话列表（不含 messages 内容，减少传输量）
        :return: [{id, title, created_at, updated_at, message_count}, ...]
        """
        rows = self._conn.execute(
            "SELECT id, title, messages, created_at, updated_at "
            "FROM conversations WHERE user_id = ? "
            "ORDER BY updated_at DESC",
            (self.user_id,),
        ).fetchall()
        return [
            {
                "id": r["id"],
                "title": r["title"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
                "message_count": len(json.loads(r["messages"] or "[]")),
            }
            for r in rows
        ]

    def get_conversation(self, conv_id: str) -> Optional[dict]:
        """
        获取单个对话的完整数据（含 messages）
        :param conv_id: 对话 ID
        :return: 完整对话对象，不存在则返回 None
        """
        row = self._conn.execute(
            "SELECT id, title, messages, created_at, updated_at "
            "FROM conversations WHERE id = ? AND user_id = ?",
            (conv_id, self.user_id),
        ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "title": row["title"],
            "messages": json.loads(row["messages"] or "[]"),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def save_conversation(self, conv: dict) -> None:
        """
        保存/更新对话（upsert：相同 id+user_id 则覆盖）
        :param conv: 对话对象 {id, title, messages, created_at}
                      messages 为 list[dict] 格式
        """
        now = conv.get("updated_at", conv.get("created_at", time.time()))
        with self._conn:
            self._conn.execute(
                """INSERT INTO conversations (id, user_id, title, messages, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id, user_id) DO UPDATE SET
                       title = excluded.title,
                       messages = excluded.messages,
                       updated_at = excluded.updated_at""",
                (
                    conv["id"],
                    self.user_id,
                    conv.get("title", "新对话"),
                    json.dumps(conv.get("messages", []), ensure_ascii=False),
                    conv.get("created_at", now),
                    now,
                ),
            )

    def delete_conversation(self, conv_id: str) -> bool:
        """
        删除对话
        :param conv_id: 对话 ID
        :return: 是否成功删除（对话不存在返回 False）
        """
        with self._conn:
            cursor = self._conn.execute(
                "DELETE FROM conversations WHERE id = ? AND user_id = ?",
                (conv_id, self.user_id),
            )
            return cursor.rowcount > 0

    def sync_from_client(self, conversations: list[dict]) -> dict:
        """
        全量同步：前端传来的对话列表与后端合并
        策略：后端已有的对话（同 id）保留，前端新增的写入后端，
              后端有但前端没有的也返回给前端（多设备同步场景）

        :param conversations: 前端当前所有对话 [{id, title, messages, createdAt}, ...]
        :return: {conversations: [...合并后的完整列表...]}
        """
        # 1. 获取后端所有对话
        backend_rows = self._conn.execute(
            "SELECT id, title, messages, created_at, updated_at "
            "FROM conversations WHERE user_id = ?",
            (self.user_id,),
        ).fetchall()
        backend_map = {
            r["id"]: {
                "id": r["id"],
                "title": r["title"],
                "messages": json.loads(r["messages"] or "[]"),
                "createdAt": r["created_at"],
                "updatedAt": r["updated_at"],
            }
            for r in backend_rows
        }

        # 2. 前端传来的对话写入后端
        for conv in conversations:
            conv_id = conv.get("id")
            if not conv_id:
                continue
            self.save_conversation({
                "id": conv_id,
                "title": conv.get("title", "新对话"),
                "messages": conv.get("messages", []),
                "created_at": conv.get("createdAt", conv.get("created_at", 0)),
            })
            # 更新 backend_map
            backend_map[conv_id] = {
                "id": conv_id,
                "title": conv.get("title", "新对话"),
                "messages": conv.get("messages", []),
                "createdAt": conv.get("createdAt", conv.get("created_at", 0)),
                "updatedAt": conv.get("updatedAt", conv.get("updated_at", 0)),
            }

        # 3. 返回合并后的完整列表（按更新时间倒序）
        merged = sorted(
            backend_map.values(),
            key=lambda c: c.get("updatedAt", c.get("createdAt", 0)),
            reverse=True,
        )
        return {"conversations": merged}
