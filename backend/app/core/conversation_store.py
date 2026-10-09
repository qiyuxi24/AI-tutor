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
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from app.core import storage


class ConversationStore:
    """
    对话存储管理类（SQLite 后端，独立数据库）
    :param user_id: 当前登录用户 ID，所有查询/写入都按此隔离
    """

    def __init__(self, user_id: int, data_dir: Optional[Path] = None):
        if data_dir is None:
            data_dir = storage.conversations_dir()
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "conversations.db"
        self.user_id = user_id

        # timeout/busy_timeout：多端同时写同一库时，等写锁而不是立刻抛
        # "database is locked"（Python sqlite3 默认 5s，这里显式调大并写进口径）
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False, timeout=10.0)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=10000")
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
    #  写事务 / 内部查询
    # ════════════════════════════════════════════

    @contextmanager
    def _write_txn(self):
        """写事务：**BEGIN IMMEDIATE 立即取写锁**，保证"读-比较-写"期间无他人插入。

        为什么不用 `with self._conn:`（deferred）：deferred 事务在首次写入时才升级
        写锁，WAL 下若两连接都先读到同一快照，后写者会拿到 SQLITE_BUSY_SNAPSHOT。
        IMMEDIATE 让第二个连接在 BEGIN 处就等锁（busy_timeout 内串行），
        这正是"多端并发写 = 串行"的落点。
        """
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()

    def _all_conversations(self) -> list[dict]:
        """读取当前用户全部对话（含 messages），按 updated_at 倒序。"""
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
                "messages": json.loads(r["messages"] or "[]"),
                "createdAt": r["created_at"],
                "updatedAt": r["updated_at"],
            }
            for r in rows
        ]

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

    def save_conversation(self, conv: dict) -> float:
        """
        保存/更新对话（upsert：相同 id+user_id 则覆盖）

        :param conv: 对话对象 {id, title, messages, created_at, updated_at?}
                      messages 为 list[dict] 格式
        :return: 实际落库的 updated_at（供全量同步回填排序键 —— 默认值只在**这一处**定义，
                 避免调用方各写一份、写出 0 或 created_at）

        多端防覆盖（2026-10-07）：
        - 客户端**显式**给出 updated_at（>0）= "这份快照的修改时刻"。库里更新的版本
          （updated_at 更大）**拒绝被它覆盖**，原样返回库里版本 —— 否则在另一端登录的
          旧设备全量上传时，会把新消息整段盖回去（静默数据丢失）。
        - 未给出 updated_at（老前端 / 单条保存不传）→ 维持原有语义：服务端当前时间。

        时间戳语义（2026-09-26 修）：
        - 缺省或为 0（api/v1/conversations.py 传的就是 0）→ 服务端当前时间。
          **不得退化成 created_at**：那会让"刚聊过的老对话"排序键仍是创建时刻、在列表里沉底。
        - messages 内容没变 → 不推进 updated_at：全量同步每次 persist 都会重传所有对话，
          无条件刷新会把它们刷成同一时刻，排序同样失效。
        """
        now = time.time()
        incoming_updated = float(conv.get("updated_at") or 0)

        with self._write_txn():
            current = self._conn.execute(
                "SELECT updated_at FROM conversations WHERE id = ? AND user_id = ?",
                (conv["id"], self.user_id),
            ).fetchone()

            # 多端防覆盖：显式给了 updated_at，但没库里的新 → 拒绝写入
            if (current is not None and incoming_updated > 0
                    and float(current["updated_at"] or 0) > incoming_updated):
                return float(current["updated_at"] or 0)

            row = self._conn.execute(
                """INSERT INTO conversations (id, user_id, title, messages, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id, user_id) DO UPDATE SET
                       title = excluded.title,
                       messages = excluded.messages,
                       updated_at = CASE WHEN conversations.messages = excluded.messages
                                         THEN conversations.updated_at
                                         ELSE excluded.updated_at END
                   RETURNING updated_at""",
                (
                    conv["id"],
                    self.user_id,
                    conv.get("title", "新对话"),
                    json.dumps(conv.get("messages", []), ensure_ascii=False),
                    conv.get("created_at") or now,
                    conv.get("updated_at") or now,
                ),
            ).fetchone()
        return row["updated_at"] if row else now

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
        全量同步：前端传来的对话列表与后端合并。
        策略（2026-10-07 改）：
        - 前端传来的逐条写入，防覆盖规则由 `save_conversation` 统一把关（旧快照写不进）；
        - 返回值 = **库里的真实状态**（而不是前端传的那份），所以"你这份被拒了"这件事
          调用方看得见 —— 前端据此刷新本地，而不是继续拿着旧快照。

        :param conversations: 前端当前所有对话 [{id, title, messages, createdAt, updatedAt}, ...]
                              updatedAt = 本地最后修改时刻（缺失则不参与防覆盖，向后兼容）
        :return: {conversations: [...库里全部对话，按 updatedAt 倒序...]}
        """
        for conv in conversations:
            conv_id = conv.get("id")
            if not conv_id:
                continue
            self.save_conversation({
                "id": conv_id,
                "title": conv.get("title", "新对话"),
                "messages": conv.get("messages", []),
                "created_at": conv.get("createdAt") or conv.get("created_at") or 0,
                "updated_at": conv.get("updatedAt") or conv.get("updated_at") or 0,
            })
        return {"conversations": self._all_conversations()}
