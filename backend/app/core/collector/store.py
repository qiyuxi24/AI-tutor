"""
采集模块存储（CollectorStore，B1.1）

按用户单文件 SQLite（{data_dir}/collector.db，仿 quiz_store）。注意：真正的用户隔离
由上层 CollectorStoreManager 的「每用户子目录」保证；本类接口对齐 B1.1 预写测试
（tests/test_collector_store.py），资源不重复带 user_id 条件。

两张表：
- tasks：    采集任务（cursor/processed_count 承载断点续传，决策 #21）
- resources：候选/资源明细（source_url UNIQUE 幂等 + content_hash 防重复入库，决策 #19）
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# 资源状态（manager 也复用这套）
RESOURCE_PENDING = "pending"
RESOURCE_INDEXED = "indexed"     # 已入库（区别于原始 'done'，见 B1.1 测试）
RESOURCE_DUPLICATE = "duplicate"  # 内容 hash 命中，未重复入库（决策 #19）
RESOURCE_FAILED = "failed"

# 任务「未结束」状态（list_active/resume 扫描用）
TASK_ACTIVE = ("pending", "processing")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class CollectorStore:
    """采集记录存储（SQLite 后端）"""

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "collector.db"
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._create_tables()

    def _create_tables(self) -> None:
        with self._conn:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id         INTEGER NOT NULL DEFAULT 0,
                    subject         TEXT DEFAULT '',
                    source          TEXT DEFAULT '',        -- 适配器名/来源标记
                    status          TEXT DEFAULT 'pending',
                    cursor          TEXT DEFAULT '',        -- 断点续传游标（决策 #21）
                    processed_count INTEGER DEFAULT 0,
                    total_count     INTEGER DEFAULT 0,
                    error           TEXT DEFAULT '',
                    created_at      TEXT DEFAULT (datetime('now')),
                    finished_at     TEXT
                )
            """)
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS resources (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id         INTEGER,
                    title           TEXT DEFAULT '',
                    source_url      TEXT NOT NULL,
                    source          TEXT DEFAULT '',        -- 适配器名
                    license_level   TEXT DEFAULT 'L0',
                    subject         TEXT DEFAULT '',
                    mode            TEXT DEFAULT 'personal',-- 采集用途（决策 §3.3）
                    status          TEXT DEFAULT 'pending',
                    content_hash    TEXT,                   -- 入库内容 sha256
                    file_node_id    INTEGER,                -- 入库后 kb nodes.id
                    quiz_id         INTEGER,                -- 预留：关联题目（Batch3）
                    times_referenced INTEGER DEFAULT 0,     -- RAG 命中计数（Batch3）
                    error           TEXT DEFAULT '',
                    fetched_at      TEXT,
                    created_at      TEXT DEFAULT (datetime('now')),
                    UNIQUE (source_url)
                )
            """)
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_resources_task "
                "ON resources (task_id, status)"
            )

    def close(self) -> None:
        self._conn.close()

    # ────────────────────────────────────────────
    #  资源（候选）
    # ────────────────────────────────────────────

    def add_resource(self, source_url: str, *, title: str = "",
                     source: str = "", license_level: str = "L0",
                     subject: str = "", mode: str = "personal",
                     content_hash: Optional[str] = None,
                     task_id: Optional[int] = None) -> int:
        """新增候选；同 source_url 幂等（返回既有行 id，不重复插入）"""
        with self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO resources "
                "(task_id, title, source_url, source, license_level, subject,"
                " mode, status, content_hash) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
                (task_id, (title or "")[:500], source_url, source or "",
                 license_level or "L0", subject, mode, content_hash),
            )
            row = self._conn.execute(
                "SELECT id FROM resources WHERE source_url = ?",
                (source_url,),
            ).fetchone()
        return row["id"]

    def reassign_task(self, rid: int, task_id: int) -> None:
        """把历史残留资源（failed/旧任务 pending）收养到新任务重跑"""
        with self._conn:
            self._conn.execute(
                "UPDATE resources SET task_id = ?, status = 'pending', error = ''"
                " WHERE id = ?",
                (task_id, rid),
            )

    def get_resource(self, rid: int) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT * FROM resources WHERE id = ?", (rid,),
        ).fetchone()
        return dict(row) if row else None

    def get_by_url(self, source_url: str) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT * FROM resources WHERE source_url = ?", (source_url,),
        ).fetchone()
        return dict(row) if row else None

    def list_resources(self, *, task_id: Optional[int] = None,
                       subject: Optional[str] = None,
                       mode: Optional[str] = None,
                       status: Optional[str] = None) -> list[dict]:
        """多条件过滤（可组合，全空 = 全部）"""
        sql = "SELECT * FROM resources WHERE 1=1"
        args: list = []
        for key, val in (("task_id", task_id), ("subject", subject),
                         ("mode", mode), ("status", status)):
            if val is not None:
                sql += f" AND {key} = ?"
                args.append(val)
        rows = self._conn.execute(sql + " ORDER BY id", args).fetchall()
        return [dict(r) for r in rows]

    def update_resource_status(self, rid: int, status: str, *,
                               content_hash: Optional[str] = None,
                               file_node_id: Optional[int] = None,
                               error: Optional[str] = None) -> None:
        """更新候选处理结果（含 fetched_at 完成时间）"""
        with self._conn:
            self._conn.execute(
                "UPDATE resources SET status = ?, content_hash = COALESCE(?, content_hash),"
                " file_node_id = COALESCE(?, file_node_id), error = COALESCE(?, error),"
                " fetched_at = ? WHERE id = ?",
                (status, content_hash, file_node_id, error, _now(), rid),
            )

    def delete_resource(self, rid: int) -> bool:
        with self._conn:
            cur = self._conn.execute(
                "DELETE FROM resources WHERE id = ?", (rid,),
            )
        return cur.rowcount > 0

    def has_content_hash(self, content_hash: str) -> bool:
        """内容去重：是否已有入库（非 pending）资源含该 hash（决策 #19）"""
        row = self._conn.execute(
            "SELECT 1 FROM resources WHERE content_hash = ? AND status != 'pending'"
            " LIMIT 1",
            (content_hash,),
        ).fetchone()
        return row is not None

    def next_pending(self, task_id: int, after_cursor: str = "") -> Optional[dict]:
        """取任务里 cursor 之后下一个待处理候选（断点续传，字典序推进）"""
        row = self._conn.execute(
            "SELECT * FROM resources WHERE task_id = ? AND status = 'pending'"
            " AND source_url > ? ORDER BY source_url LIMIT 1",
            (task_id, after_cursor),
        ).fetchone()
        return dict(row) if row else None

    def count_processed(self, task_id: int) -> int:
        """任务内已处理（indexed/duplicate/failed）条数"""
        row = self._conn.execute(
            "SELECT COUNT(*) AS c FROM resources WHERE task_id = ?"
            " AND status IN ('indexed', 'duplicate', 'failed')",
            (task_id,),
        ).fetchone()
        return row["c"] if row else 0

    # ────────────────────────────────────────────
    #  任务
    # ────────────────────────────────────────────

    def create_task(self, user_id: int, subject: str,
                    source: str = "") -> int:
        with self._conn:
            cur = self._conn.execute(
                "INSERT INTO tasks (user_id, subject, source, status) "
                "VALUES (?, ?, ?, 'pending')",
                (user_id, subject, source),
            )
        return cur.lastrowid

    def get_task(self, task_id: int) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,),
        ).fetchone()
        return dict(row) if row else None

    def list_tasks(self, *, user_id: Optional[int] = None,
                   subject: Optional[str] = None,
                   limit: int = 50) -> list[dict]:
        sql = "SELECT * FROM tasks WHERE 1=1"
        args: list = []
        if user_id is not None:
            sql += " AND user_id = ?"
            args.append(user_id)
        if subject is not None:
            sql += " AND subject = ?"
            args.append(subject)
        rows = self._conn.execute(sql + " ORDER BY id DESC LIMIT ?",
                                  [*args, limit]).fetchall()
        return [dict(r) for r in rows]

    def list_active(self, user_id: int) -> list[dict]:
        """未完成任务（pending/processing），供重启续跑扫描"""
        marks = ",".join("?" for _ in TASK_ACTIVE)
        rows = self._conn.execute(
            f"SELECT * FROM tasks WHERE user_id = ? AND status IN ({marks})",
            (user_id, *TASK_ACTIVE),
        ).fetchall()
        return [dict(r) for r in rows]

    def update_task(self, task_id: int, *, status: Optional[str] = None,
                    cursor: Optional[str] = None,
                    processed_count: Optional[int] = None,
                    total_count: Optional[int] = None,
                    error: Optional[str] = None,
                    finished: bool = False,
                    finished_at: Optional[str] = None) -> None:
        """更新任务；finished=True 表示收尾成功（status='finished' + 时间戳）"""
        fields, values = [], []
        if finished:
            fields += ["status = 'finished'", "finished_at = ?"]
            values.append(_now())
        for key, val in (("status", status), ("cursor", cursor),
                         ("processed_count", processed_count),
                         ("total_count", total_count), ("error", error)):
            if val is not None:
                fields.append(f"{key} = ?")
                values.append(val)
        if finished_at is not None:
            fields.append("finished_at = ?")
            values.append(finished_at)
        if not fields:
            return
        values.append(task_id)
        with self._conn:
            self._conn.execute(
                f"UPDATE tasks SET {', '.join(fields)} WHERE id = ?", values,
            )


class CollectorStoreManager:
    """按用户管理 CollectorStore（每用户一个 data_dir/{uid}/collector.db）"""

    def __init__(self, data_dir: Optional[Path] = None):
        base = Path(__file__).parent.parent.parent.parent / "data" / "collector"
        self.data_dir = Path(data_dir) if data_dir else base
        self._stores: dict[int, CollectorStore] = {}

    def _get_store(self, user_id: int) -> CollectorStore:
        if user_id not in self._stores:
            self._stores[user_id] = CollectorStore(self.data_dir / str(user_id))
        return self._stores[user_id]
