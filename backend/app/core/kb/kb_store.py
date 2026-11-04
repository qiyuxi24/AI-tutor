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
                    asset_key    TEXT DEFAULT '',        -- 讲义截图目录键（data/kb/{uid}/slides/{key}）
                    created_at   TEXT DEFAULT (datetime('now'))
                )
            """)
            # 老库补列：CREATE TABLE IF NOT EXISTS 不会给已存在的表加字段
            cols = {row["name"] for row in
                    self._conn.execute("PRAGMA table_info(documents)").fetchall()}
            if "asset_key" not in cols:
                self._conn.execute(
                    "ALTER TABLE documents ADD COLUMN asset_key TEXT DEFAULT ''")
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_nodes_parent ON nodes (parent_id, user_id)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_nodes_user ON nodes (user_id)"
            )
            self._create_import_tables()

    def _create_import_tables(self) -> None:
        """视频导入队列（持久化：进程重启后按 status 续跑）。

        放在**每用户的 kb.db** 里而不是全局库：天然按 user_id 隔离，删号由
        `data/kb/{uid}` 整目录覆盖，不需要再维护一份删号清单。
        一 task = 一次提交（可含 N 条链接 × 展开后的 M 集），一 item = 一集。
        """
        with self._conn:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS import_tasks (
                    id                TEXT PRIMARY KEY,
                    user_id           INTEGER NOT NULL,
                    title             TEXT DEFAULT '',
                    status            TEXT DEFAULT 'pending',  -- pending/running/paused/done/failed/canceled
                    transcript_source TEXT DEFAULT 'auto',
                    asr_model         TEXT DEFAULT '',
                    slides            INTEGER DEFAULT 0,
                    parent_id         INTEGER,
                    total             INTEGER DEFAULT 0,
                    done              INTEGER DEFAULT 0,
                    failed            INTEGER DEFAULT 0,
                    error             TEXT DEFAULT '',
                    created_at        TEXT DEFAULT (datetime('now')),
                    updated_at        TEXT DEFAULT (datetime('now')),
                    finished_at       TEXT
                )
            """)
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS import_items (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id    TEXT NOT NULL,
                    url        TEXT NOT NULL,
                    title      TEXT DEFAULT '',
                    status     TEXT DEFAULT 'pending',  -- pending/running/done/failed/canceled
                    stage      TEXT DEFAULT '',         -- 当前步骤（video_import.STAGE_*）
                    node_id    INTEGER,
                    chars      INTEGER DEFAULT 0,
                    source     TEXT DEFAULT '',
                    error      TEXT DEFAULT '',
                    position   INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT (datetime('now')),
                    started_at TEXT,
                    finished_at TEXT
                )
            """)
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_import_items_task "
                "ON import_items (task_id, status, position)"
            )
            # 老库补列（同 documents.asset_key 的道理：CREATE TABLE IF NOT EXISTS 不加字段）。
            # stage / started_at 是"刷新后还能看到在第几步、还要多久"的唯一依据 ——
            # 进度事件本身是瞬态的（`import_queue._progress`），不落库就刷新即失忆。
            cols = {row["name"] for row in
                    self._conn.execute("PRAGMA table_info(import_items)").fetchall()}
            for column, ddl in (
                ("stage", "TEXT DEFAULT ''"),
                ("started_at", "TEXT"),
            ):
                if column not in cols:
                    self._conn.execute(
                        f"ALTER TABLE import_items ADD COLUMN {column} {ddl}")

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
                 file_type: str, extract_text: str, file_size: int = 0,
                 asset_key: str = "") -> int:
        """添加一个已解析的文件节点，返回节点 ID。

        `asset_key` 只给讲义用（截图目录键）；普通上传留空。
        """
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
                INSERT INTO documents (node_id, file_type, extract_text, file_size, status, asset_key)
                VALUES (?, ?, ?, ?, 'parsed', ?)
            """, (node_id, file_type, extract_text, file_size, asset_key or ""))
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

    # ────────────────────────────────────────────
    #  视频导入队列（持久化，见 _create_import_tables）
    # ────────────────────────────────────────────

    def create_import_task(self, user_id: int, items: list[tuple[str, str]], *,
                           transcript_source: str = "auto", asr_model: str = "",
                           slides: bool = False, parent_id: Optional[int] = None,
                           title: str = "") -> str:
        """建任务 + 全部待办分集（一次提交 = 一个任务）。返回 task_id。"""
        import uuid

        task_id = uuid.uuid4().hex[:12]
        with self._conn:
            self._conn.execute(
                "INSERT INTO import_tasks (id, user_id, title, transcript_source,"
                " asr_model, slides, parent_id, total, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))",
                (task_id, user_id, (title or "")[:200], transcript_source, asr_model or "",
                 int(bool(slides)), parent_id, len(items)),
            )
            for position, (url, item_title) in enumerate(items):
                self._conn.execute(
                    "INSERT INTO import_items (task_id, url, title, status, position)"
                    " VALUES (?, ?, ?, 'pending', ?)",
                    (task_id, url, (item_title or "")[:200], position),
                )
        return task_id

    def get_import_task(self, task_id: str) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT * FROM import_tasks WHERE id = ?", (task_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_import_tasks(self, user_id: int, limit: int = 20) -> list[dict]:
        """最近的任务 + 各自的分集（前端队列面板用）。

        分集带上 `duration_secs`（本集实际用时）：队列行要显示"这集跑了多久 / 还要多久"，
        而 `created_at` 是入队时间（整批相同），只有 `started_at → finished_at` 才是耗时。
        """
        rows = self._conn.execute(
            "SELECT * FROM import_tasks WHERE user_id = ?"
            " ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        tasks = []
        for row in rows:
            task = dict(row)
            task["items"] = [dict(r) for r in self._conn.execute(
                "SELECT *, CASE WHEN started_at IS NOT NULL AND finished_at IS NOT NULL"
                " THEN CAST((julianday(finished_at) - julianday(started_at)) * 86400 AS INTEGER)"
                " END AS duration_secs"
                " FROM import_items WHERE task_id = ? ORDER BY position, id",
                (task["id"],),
            ).fetchall()]
            tasks.append(task)
        return tasks

    def import_task_progress(self, task_id: str) -> dict:
        """任务级耗时与剩余估计（只做算术，不猜）。

        `eta_secs` 用**本任务已完成分集的平均耗时 × 待办数**；一集都没跑完时返回 0，
        前端显示"估算中"——比编一个数字诚实，也避免用户按假 ETA 安排时间。
        """
        task = self.get_import_task(task_id) or {}
        avg = self._conn.execute(
            "SELECT AVG((julianday(finished_at) - julianday(started_at)) * 86400.0) AS secs"
            " FROM import_items WHERE task_id = ?"
            " AND started_at IS NOT NULL AND finished_at IS NOT NULL", (task_id,),
        ).fetchone()["secs"] or 0.0
        left = self._conn.execute(
            "SELECT COUNT(*) AS n FROM import_items WHERE task_id = ?"
            " AND status IN ('pending', 'running')", (task_id,),
        ).fetchone()["n"]
        running = self._conn.execute(
            "SELECT (julianday('now') - julianday(started_at)) * 86400.0 AS secs"
            " FROM import_items WHERE task_id = ? AND status = 'running'"
            " AND started_at IS NOT NULL LIMIT 1", (task_id,),
        ).fetchone()
        started = self._conn.execute(
            "SELECT MIN(started_at) AS t FROM import_items WHERE task_id = ?"
            " AND started_at IS NOT NULL", (task_id,),
        ).fetchone()["t"]
        # 起点：第一集真正开始的时间；一集都没开始 → 建任务时间
        begin = started or task.get("created_at") or ""
        end = task.get("finished_at")
        elapsed = self._conn.execute(
            "SELECT CAST((julianday(COALESCE(?, 'now')) - julianday(?)) * 86400 AS INTEGER) AS secs",
            (end, begin),
        ).fetchone()["secs"] or 0
        return {
            "elapsed_secs": max(0, int(elapsed)),
            "running_secs": round(float(running["secs"] or 0)) if running else 0,
            "avg_item_secs": round(avg),
            "eta_secs": round(avg * left) if avg else 0,
        }


    def set_import_task_status(self, task_id: str, status: str,
                              error: Optional[str] = None) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE import_tasks SET status = ?, error = COALESCE(?, error),"
                " updated_at = datetime('now'),"
                " finished_at = CASE WHEN ? IN ('done', 'failed', 'canceled')"
                " THEN datetime('now') ELSE finished_at END"
                " WHERE id = ?",
                (status, error, status, task_id),
            )

    def next_open_task(self, user_id: int) -> Optional[dict]:
        """下一条还有待办分集的任务（pending/running）。暂停/取消的不会被取走。"""
        row = self._conn.execute(
            "SELECT * FROM import_tasks WHERE user_id = ? AND status IN ('pending','running')"
            " AND EXISTS (SELECT 1 FROM import_items WHERE task_id = import_tasks.id"
            "             AND status = 'pending')"
            " ORDER BY created_at, id LIMIT 1",
            (user_id,),
        ).fetchone()
        return dict(row) if row else None

    def has_open_tasks(self, user_id: int) -> bool:
        return self.next_open_task(user_id) is not None

    def next_pending_item(self, task_id: str) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT * FROM import_items WHERE task_id = ? AND status = 'pending'"
            " ORDER BY position, id LIMIT 1",
            (task_id,),
        ).fetchone()
        return dict(row) if row else None

    def set_import_item_status(self, item_id: int, status: str, *,
                               node_id: Optional[int] = None, chars: int = 0,
                               source: str = "", error: str = "") -> None:
        """改分集状态，并顺带维护计时：running 记 started_at，退回 pending 清掉它。

        `started_at` 是队列"这集跑了多久 / 还要多久"的唯一数据来源；
        重试（pending）必须清空，否则同一集重跑会把上一次的时长算进去。
        """
        with self._conn:
            self._conn.execute(
                "UPDATE import_items SET status = ?,"
                " node_id = COALESCE(?, node_id), chars = ?,"
                " source = CASE WHEN ? = '' THEN source ELSE ? END,"
                " error = ?,"
                " stage = CASE WHEN ? = 'pending' THEN '' ELSE stage END,"
                " started_at = CASE"
                "   WHEN ? = 'running' AND started_at IS NULL THEN datetime('now')"
                "   WHEN ? = 'pending' THEN NULL"
                "   ELSE started_at END,"
                " finished_at = CASE WHEN ? IN ('done','failed','canceled')"
                " THEN datetime('now') ELSE finished_at END"
                " WHERE id = ?",
                (status, node_id, chars, source, source, error,
                 status, status, status, status, item_id),
            )

    def set_import_item_stage(self, item_id: int, stage: str) -> None:
        """只记当前步骤（进度落库）。

        事件本身仍是瞬态的（前端拿来实时刷新），但阶段必须留在库里：
        刷新/换设备后 `running` 这一行要能回答"它卡在哪一步"，而不是只剩一个状态词。
        """
        with self._conn:
            self._conn.execute(
                "UPDATE import_items SET stage = ? WHERE id = ?", (stage[:20], item_id))

    def refresh_import_task(self, task_id: str) -> dict:
        """重算计数；没有待办分集时收尾（done / failed）。返回任务行。"""
        task = self.get_import_task(task_id) or {}
        counts = {row["status"]: row["n"] for row in self._conn.execute(
            "SELECT status, COUNT(*) AS n FROM import_items WHERE task_id = ?"
            " GROUP BY status", (task_id,),
        ).fetchall()}
        done = counts.get("done", 0)
        failed = counts.get("failed", 0)
        left = counts.get("pending", 0) + counts.get("running", 0)
        with self._conn:
            self._conn.execute(
                "UPDATE import_tasks SET done = ?, failed = ?, updated_at = datetime('now')"
                " WHERE id = ?", (done, failed, task_id),
            )
        if left == 0 and task.get("status") not in ("paused", "canceled"):
            # 一集都没成功 = 失败；部分成功仍记 done，失败明细留在分集里。
            # 全被用户取消（没有成功也没有失败）→ canceled，别报成"全部分集导入失败"
            if done:
                self.set_import_task_status(task_id, "done")
            elif failed:
                self.set_import_task_status(task_id, "failed", error="全部分集导入失败")
            else:
                self.set_import_task_status(task_id, "canceled")
        return self.get_import_task(task_id) or {}

    def cancel_import_task(self, task_id: str) -> None:
        """取消：待办分集标记 canceled；**在跑的那集照常跑完**（半张讲义比多等更糟）。

        worker 在集与集之间看到任务已 canceled 就停，不会再把后续 pending 取走。
        """
        with self._conn:
            self._conn.execute(
                "UPDATE import_items SET status = 'canceled', finished_at = datetime('now')"
                " WHERE task_id = ? AND status = 'pending'", (task_id,),
            )
        self.set_import_task_status(task_id, "canceled")

    def retry_failed_items(self, task_id: str) -> int:
        """失败的分集退回 pending（重试）；返回被重置的条数。"""
        with self._conn:
            cur = self._conn.execute(
                "UPDATE import_items SET status = 'pending', error = '', stage = '',"
                " started_at = NULL, finished_at = NULL"
                " WHERE task_id = ? AND status = 'failed'", (task_id,),
            )
            if cur.rowcount:
                self._reopen_import_task(task_id)
            return cur.rowcount

    def retry_import_item(self, task_id: str, item_id: int) -> bool:
        """单集重试（只认 failed 的分集）；返回是否真的重置了。"""
        with self._conn:
            cur = self._conn.execute(
                "UPDATE import_items SET status = 'pending', error = '', stage = '',"
                " started_at = NULL, finished_at = NULL"
                " WHERE id = ? AND task_id = ? AND status = 'failed'",
                (item_id, task_id),
            )
            if cur.rowcount:
                self._reopen_import_task(task_id)
            return bool(cur.rowcount)

    def cancel_import_item(self, task_id: str, item_id: int) -> bool:
        """取消某一集待办（只认 pending：正在跑的那集跑完为止，见 import_queue 的说明）。"""
        with self._conn:
            cur = self._conn.execute(
                "UPDATE import_items SET status = 'canceled', stage = '',"
                " finished_at = datetime('now')"
                " WHERE id = ? AND task_id = ? AND status = 'pending'",
                (item_id, task_id),
            )
            return bool(cur.rowcount)

    def _reopen_import_task(self, task_id: str) -> None:
        """把已收尾的任务翻回 pending，让 worker 重新取走（重试的唯一入口）。"""
        self._conn.execute(
            "UPDATE import_tasks SET status = 'pending', finished_at = NULL,"
            " updated_at = datetime('now') WHERE id = ?", (task_id,),
        )

    def reorder_import_item(self, task_id: str, item_id: int, delta: int) -> None:
        """把某个待办分集上移/下移一格（delta = -1 / +1）。"""
        rows = self._conn.execute(
            "SELECT id, position FROM import_items WHERE task_id = ? AND status = 'pending'"
            " ORDER BY position, id", (task_id,),
        ).fetchall()
        ids = [row["id"] for row in rows]
        if item_id not in ids:
            return
        idx = ids.index(item_id)
        target = idx + delta
        if target < 0 or target >= len(ids):
            return
        with self._conn:
            for position, nid in enumerate(ids):
                new_pos = position
                if position == idx:
                    new_pos = target
                elif position == target:
                    new_pos = idx
                self._conn.execute(
                    "UPDATE import_items SET position = ? WHERE id = ?", (new_pos, nid))
