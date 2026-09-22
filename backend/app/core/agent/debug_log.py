"""Agent 调试日志 —— 结构化日志的唯一出口（控制台 + SQLite 双写，供日后开发回看）。

为什么单独一个模块（2026-09-19）：
  排查"agent 循环输出无用数据"这类问题时，需要**跨 run 的时间序列**，而既有记录各管一段：
    - `agent_runs.evidence`：证据完整，但 run 结束才落库，且要翻 JSON 大字段；
    - EventBus / SSE：实时，但内存易失，断线即无。
  本模块补的是"**跨 run 可查、按 run_id 串起来的调试流水**"：一行一条、带 scope/event/耗时，
  `recent()` 直接查，跟着 run_id 能完整回看一次运行。

记录范围（明确边界 —— 不记录的东西和记录的一样重要）：
  ✅ 记录：run 生命周期（起止 + 终止原因 + 轮次/工具数/LLM 次数/耗时）、每轮请求的工具清单、
     工具成败与耗时（结果只记 head）、工具被安全边界拦下的原因、LLM 调用的 token 与耗时、
     上下文治理动作（清理/压缩条数）、后台子任务成败、异常。
  ❌ 不记录：学生消息正文、系统提示词全文、工具完整返回、密钥/凭证。
     所有文本字段按 `_MSG_LIMIT` / `_DATA_LIMIT` 截断（落库失败不影响主流程）。

使用：
    from app.core.agent.debug_log import RunLogger
    rlog = RunLogger(run_id, user_id=user_id, db_dir=db_dir)
    rlog.log("loop", "run_start", "agent run 开始", max_rounds=5)
    rlog.log("tool", "tool_result", "工具完成", tool="rag_search", ok=True, duration_ms=12)

    # 事后回看（脚本 / 调试）
    python -c "from app.core.agent import debug_log as d; print(d.recent(run_id='...'))"

scope 取值（约定，新增时同步本段）：
    loop     主循环：run 起止、每轮进出、终止
    tool     工具执行：调用/结果/被拦下
    llm      LLM 往返：调用与 token/耗时
    context  上下文治理：历史裁剪、工具结果清理
    bg       后台子任务（后台出题等）
"""

import json
import logging
import os
import sqlite3
import time
from pathlib import Path

from app.core import records

logger = logging.getLogger("ai-tutor.debug")

# 库落点由 core/records.py 统一解析（= backend/app/data/agent_debug）。
# 独立成库（而不是与 agent_runs 同库）：开发流水写量大、且能由 AGENT_DEBUG_LOG=0
# 整体关闭，分开做写放大隔离，也避免它被清理策略绑死。
_DB_FOLDER = "agent_debug"
_DB_FILE = "debug_log.db"

# 总开关：环境变量 AGENT_DEBUG_LOG=0 可整体关闭（生产排查完可关，避免每 run 多行写放大）
_ENABLED = os.getenv("AGENT_DEBUG_LOG", "1").lower() not in ("0", "false", "no")
_DB_ENABLED = os.getenv("AGENT_DEBUG_LOG_DB", "1").lower() not in ("0", "false", "no")

# 截断护栏（调试流水要能长期留，不像 evidence 那样存全文）
_MSG_LIMIT = 2_000
_DATA_LIMIT = 8_000
_VALUE_LIMIT = 500   # data 里单个字符串值的上限：先按值截断，保证整体仍是合法 JSON

# 保留窗口（天）：调试日志比 agent_runs（30/180）短得多，够回溯最近的开发周期即可
_RETENTION_DAYS = 14

_LEVELS = {"DEBUG": logging.DEBUG, "INFO": logging.INFO,
           "WARNING": logging.WARNING, "ERROR": logging.ERROR}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_debug_logs (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       REAL NOT NULL,
    run_id   TEXT NOT NULL DEFAULT '',
    user_id  INTEGER,
    scope    TEXT NOT NULL,
    event    TEXT NOT NULL,
    level    TEXT NOT NULL DEFAULT 'INFO',
    message  TEXT NOT NULL DEFAULT '',
    data     TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_debug_logs_run  ON agent_debug_logs(run_id, id);
CREATE INDEX IF NOT EXISTS idx_debug_logs_time ON agent_debug_logs(ts DESC);
CREATE INDEX IF NOT EXISTS idx_debug_logs_user ON agent_debug_logs(user_id, ts DESC);
"""


def _connect(db_dir: Path | None) -> sqlite3.Connection:
    return records.connect(_DB_FOLDER, _DB_FILE, _SCHEMA, db_dir)


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "…(截断)"


def _dump(data: dict) -> str:
    """序列化调试数据：先按值截断（保 JSON 合法），再兜底整体截断。"""
    trimmed = {k: (_clip(v, _VALUE_LIMIT) if isinstance(v, str) else v)
               for k, v in data.items()}
    try:
        return _clip(json.dumps(trimmed, ensure_ascii=False, default=str), _DATA_LIMIT)
    except (TypeError, ValueError):
        return "{}"


def log(scope: str, event: str, message: str = "", *, run_id: str = "",
        user_id: int | None = None, level: str = "INFO", db_dir=None,
        **data) -> None:
    """写一行调试日志：控制台（logging）+ 落库。落库失败只记一条 warning，绝不打断主流程。"""
    if not _ENABLED:
        return
    tag = run_id[:8] if run_id else "-"
    plain = f"[run {tag}] {scope}.{event} {message}"
    if data:
        plain += f" | {_dump(data)}"
    logger.log(_LEVELS.get(level, logging.INFO), plain)

    if not _DB_ENABLED:
        return
    try:
        conn = _connect(db_dir)
        try:
            with conn:
                conn.execute(
                    """INSERT INTO agent_debug_logs
                       (ts, run_id, user_id, scope, event, level, message, data)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (time.time(), run_id, user_id, scope, event, level,
                     _clip(message, _MSG_LIMIT), _dump(data)),
                )
        finally:
            conn.close()
    except sqlite3.Error as e:  # 日志不能把业务打挂
        logger.warning(f"调试日志落库失败: {e}")


class RunLogger:
    """绑定 run_id / user_id / db_dir 的日志句柄 —— 一次 run 建一个，避免每个调用点重复传参。"""

    def __init__(self, run_id: str = "", user_id: int | None = None, db_dir=None):
        self.run_id = run_id
        self.user_id = user_id
        self.db_dir = db_dir

    @property
    def tag(self) -> str:
        return self.run_id[:8] if self.run_id else "-"

    def log(self, scope: str, event: str, message: str = "", *, level: str = "INFO",
            **data) -> None:
        log(scope, event, message, run_id=self.run_id, user_id=self.user_id,
            level=level, db_dir=self.db_dir, **data)


# ═══════════════════ 读取 / 治理 ═══════════════════

def recent(*, run_id: str | None = None, user_id: int | None = None,
           scope: str | None = None, level: str | None = None,
           limit: int = 100, db_dir=None) -> list[dict]:
    """按条件查调试流水（默认最近 100 条，新的在后）。开发期可直接当查询接口用。"""
    limit = max(1, min(int(limit), 1000))
    where, args = [], []
    if run_id:
        where.append("run_id = ?")
        args.append(run_id)
    if user_id is not None:
        where.append("user_id = ?")
        args.append(user_id)
    if scope:
        where.append("scope = ?")
        args.append(scope)
    if level:
        where.append("level = ?")
        args.append(level)
    sql = ("SELECT * FROM agent_debug_logs"
           + (f" WHERE {' AND '.join(where)}" if where else "")
           + " ORDER BY id DESC LIMIT ?")
    conn = _connect(db_dir)
    try:
        rows = conn.execute(sql, (*args, limit)).fetchall()
        out = []
        for r in reversed(rows):  # 新的在后，读起来像流水
            d = dict(r)
            try:
                d["data"] = json.loads(d["data"] or "{}")
            except json.JSONDecodeError:
                d["data"] = {}
            out.append(d)
        return out
    finally:
        conn.close()


def count(*, run_id: str | None = None, db_dir=None) -> int:
    """调试日志条数（测试/运维用）。"""
    sql = "SELECT COUNT(*) FROM agent_debug_logs"
    args: tuple = ()
    if run_id:
        sql += " WHERE run_id = ?"
        args = (run_id,)
    conn = _connect(db_dir)
    try:
        return conn.execute(sql, args).fetchone()[0]
    finally:
        conn.close()


def prune(days: int = _RETENTION_DAYS, db_dir=None) -> int:
    """删掉超过保留窗口的调试日志，返回删除条数（由启动/每日 GC 触发）。"""
    conn = _connect(db_dir)
    try:
        return records.prune_older_than(conn, "agent_debug_logs", days)
    finally:
        conn.close()


def clear(db_dir=None) -> None:
    """清空调试日志（测试/本地排障后清场用）。"""
    conn = _connect(db_dir)
    try:
        with conn:
            conn.execute("DELETE FROM agent_debug_logs")
    finally:
        conn.close()


def default_db_dir() -> Path:
    return records.default_dir(_DB_FOLDER)
