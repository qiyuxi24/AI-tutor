"""定向通知的持久化暂存（`pending_notices`）—— 「没订阅者时不丢」的唯一保证。

为什么需要（A2-3 / P1-4）：
  `event_bus.notify()` 只往**已存在的**订阅队列投递，没有订阅者就无处可投。
  而 `quiz_ready` 是**后台任务约 40s 后**才产生的 —— 用户完全可能在这段时间里
  关掉页面或切到别的端，事件投进空气，前端永远等不到那句"出题完成"
  （用户视角：AI 说"稍等片刻"，然后永远没有下文）。
  本节把通知**先落库**、能推则推；客户端重连时用 `take_pending()` 补拉。

落在哪：`agent_runs.db`（与 `agent_runs` / `llm_usage` 同库）—— 记录型库统一走
  `core/records.connect`，库路径与建表范式不各算一份（见 records.py docstring）。

取走语义：`take_pending()` **取走即标记已投递**。通知是"队列"不是"档案"：
  重复投递会让前端把同一张题卡渲染两遍（用户视角 = "怎么又出一遍"）。
  因此实时推送成功时也立刻标记（见 event_bus.notify），保证两种投递路径互斥。

⚠️ 删号必须一并清本节 —— `backend-admin/app/core/db.py::purge_user_storage`
   已按表名列出（该连接不开 FK 级联，漏一行就是永久脏数据）。
"""
import json
import sqlite3
import time
from pathlib import Path

from app.core import records

logger = None  # 惰性导入避免启动开销，见 _log()


# 与 agent/store.py 同库同文件（记录型库的唯一底座 = core/records.py）
_DB_FOLDER = "agent_runs"
_DB_FILE = "agent_runs.db"

# 保留期（天）：通知是"送到就完事"的短命数据，但也不能因为用户一周没登录就丢。
_RETENTION_DAYS = 7

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pending_notices (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL,
    kind         TEXT NOT NULL,
    payload      TEXT NOT NULL DEFAULT '{}',
    created_at   REAL NOT NULL,
    delivered_at REAL
);
CREATE INDEX IF NOT EXISTS idx_pending_notices_user
    ON pending_notices(user_id, delivered_at, id);
"""


def _log():
    global logger
    if logger is None:
        import logging
        logger = logging.getLogger("ai-tutor")
    return logger


def _connect(db_dir: Path | None = None) -> sqlite3.Connection:
    return records.connect(_DB_FOLDER, _DB_FILE, _SCHEMA, db_dir)


def record(user_id: int, kind: str, data: dict | None = None,
           *, db_dir: Path | None = None) -> int | None:
    """落库一条待投递通知，返回其 id（落库失败返回 None，绝不影响推送主流程）。

    参数:
        kind: 事件类型（`event_bus` 的常量，如 `quiz_ready`）
        data: 事件内容（**不含** `type`）—— 补拉时按 `{"type": kind, **payload}` 还原
    """
    try:
        conn = _connect(db_dir)
        try:
            with conn:
                cur = conn.execute(
                    "INSERT INTO pending_notices (user_id, kind, payload, created_at)"
                    " VALUES (?, ?, ?, ?)",
                    (user_id, kind,
                     json.dumps(data or {}, ensure_ascii=False), time.time()),
                )
            return cur.lastrowid
        finally:
            conn.close()
    except sqlite3.Error as e:
        _log().warning(f"待投递通知落库失败（user={user_id} kind={kind}）: {e}")
        return None


def mark_delivered(notice_id: int | None, *, db_dir: Path | None = None) -> None:
    """标记该通知已投递（实时推成功时调用）。失败仅记日志。"""
    if notice_id is None:
        return
    try:
        conn = _connect(db_dir)
        try:
            with conn:
                conn.execute("UPDATE pending_notices SET delivered_at = ? WHERE id = ?",
                             (time.time(), notice_id))
        finally:
            conn.close()
    except sqlite3.Error as e:
        _log().warning(f"标记通知已投递失败（id={notice_id}）: {e}")


def take_pending(user_id: int, since: float | None = None, *,
                 limit: int = 100, db_dir: Path | None = None) -> list[dict]:
    """取走该用户**尚未投递**的通知（按 id 升序，最多 limit 条），并标记为已投递。

    参数:
        since: 只取 created_at 严格大于该时间戳（epoch 秒）的通知；None = 不限
        limit: 单次上限（防补拉时一次吐出上千条把前端打挂）

    返回: [{id, type, ...payload}, ...]（`type` 由 kind 还原，前端可直接复用实时事件的
          处理分支）。
    """
    try:
        conn = _connect(db_dir)
        try:
            sql = ("SELECT id, kind, payload FROM pending_notices"
                   " WHERE user_id = ? AND delivered_at IS NULL")
            params: list = [user_id]
            if since is not None:
                sql += " AND created_at > ?"
                params.append(since)
            sql += " ORDER BY id LIMIT ?"
            params.append(limit)
            rows = list(conn.execute(sql, params))

            out: list[dict] = []
            for r in rows:
                try:
                    payload = json.loads(r["payload"] or "{}")
                except json.JSONDecodeError:
                    payload = {}
                out.append({"type": r["kind"], **payload})

            # 取走即标记：与实时推送互斥，避免前端重复渲染同一张题卡
            if rows:
                with conn:
                    conn.executemany(
                        "UPDATE pending_notices SET delivered_at = ? WHERE id = ?",
                        [(time.time(), r["id"]) for r in rows],
                    )
            return out
        finally:
            conn.close()
    except sqlite3.Error as e:
        _log().warning(f"补拉待投递通知失败（user={user_id}）: {e}")
        return []


def prune(days: float | None = None, *, db_dir: Path | None = None) -> dict:
    """删除超过保留期的通知。返回值形状对齐 `main.py::_prune_once` 的 `_JOBS` 契约。"""
    conn = _connect(db_dir)
    try:
        deleted = records.prune_older_than(
            conn, "pending_notices",
            _RETENTION_DAYS if days is None else days,
            ts_col="created_at",
        )
        return {"deleted": deleted, "stripped": 0}
    finally:
        conn.close()
