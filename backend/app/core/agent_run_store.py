"""Agent 运行记录存储 —— 事件/证据的唯一持久化出口（agent_runs 表）

整合背景（2026-09-08）：
  旧体系里同一次 agent 运行产生 3 份互不关联的记录：EventBus（内存易失）+
  前端视图快照（conversations）+ jsonl trace（data/traces/），三种 schema、无统一主键。
  本模块把「运行记录」收敛为唯一事实源：一次 run_agent_loop = 一行 agent_runs，
  列表/详情/历史 token 统计都从这一个 SQLite 出口读；EventBus 只保留实时推送职责。

设计要点：
  - evidence 列存证据级事件序列（thinking 全文 / 完整 tool arguments / 完整 tool 返回），
    JSON 数组按 ts 有序，可完整回放一次 agent 运行。
  - 元数据单列（started_at/status/token_usage…）支撑列表页与统计，列表查询不读 evidence。
  - 每次操作短连接（WAL），调用频率低，无需长驻连接（与 conversations 高频路径区分）。

清理与保留（2026-09-08）：
  分层保留，不做无脑删：
    近 RETENTION_FULL_DAYS        完整保留（evidence 可回放）；
    FULL_DAYS ~ STRIP_DAYS 的行    摘除 evidence（回放价值过期，token 审计/历史预估仍要）；
    超过 STRIP_DAYS                整行删除。
  prune() 幂等、按时间全局执行，由应用启动 + 每日后台任务触发（见 app/main.py）。

写入示例（run_agent_loop 内组装）：
    save_run({
        "run_id": run_id, "user_id": user_id, "status": "ok"|"error",
        "started_at": ..., "ended_at": ...,
        "total_llm_calls": n, "context_tokens": n,
        "token_usage": {...}, "estimated_prompt_tokens": n,
        "final_text": "...", "evidence": [...],
    })
"""
import json
import sqlite3
import time
from pathlib import Path
from typing import Optional

logger = None  # 惰性导入避免启动开销，见 _log()

# 默认库目录 backend/data/agent_runs（与 kb/quiz 索引同数据根，随 Docker
# ./backend/data 卷持久化；旧位置「根 data/agent_runs」已废弃，迁移前无残留数据）
_DEFAULT_DB_DIR = Path(__file__).resolve().parents[2] / "data" / "agent_runs"
_DB_FILE = "agent_runs.db"

# 单条 tool 结果/参数存库上限（防御失控工具写爆 DB；正常工具结果远小于此。
# ponytail: 简单截断护栏，真需要全量时调大即可）
_RECORD_TEXT_LIMIT = 200_000

# 分层保留窗口（天），prune() 的清理边界，可覆盖传入：
_RETENTION_FULL_DAYS = 30    # ≤此区间完整保留（含 evidence，供回放）
_RETENTION_STRIP_DAYS = 180  # 到期摘 evidence 留元数据；再老整行删

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_runs (
    run_id      TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL,
    status      TEXT NOT NULL DEFAULT 'ok',
    started_at  REAL NOT NULL,
    ended_at    REAL,
    total_llm_calls     INTEGER NOT NULL DEFAULT 0,
    context_tokens      INTEGER NOT NULL DEFAULT 0,
    token_usage         TEXT NOT NULL DEFAULT '{}',
    estimated_prompt_tokens INTEGER NOT NULL DEFAULT 0,
    final_text  TEXT NOT NULL DEFAULT '',
    evidence    TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_agent_runs_user_time
    ON agent_runs(user_id, started_at DESC);
"""


def _log():
    global logger
    if logger is None:
        import logging
        logger = logging.getLogger("ai-tutor")
    return logger


def _db_path(db_dir: Path | None) -> Path:
    return Path(db_dir or _DEFAULT_DB_DIR) / _DB_FILE


def _connect(db_dir: Path | None) -> sqlite3.Connection:
    path = _db_path(db_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    return conn


def _truncate(text: str, limit: int = _RECORD_TEXT_LIMIT) -> str:
    """防御性截断超长文本，保证 evidence 落库不失控。"""
    return text if len(text) <= limit else text[:limit] + "\n…(记录超长截断)"


# ═══════════════════ 写入 ═══════════════════

def save_run(run: dict, db_dir: Path | None = None) -> None:
    """upsert 一次 agent 运行记录。失败仅记日志，不影响主流程。"""
    conn = _connect(db_dir)
    try:
        with conn:
            conn.execute(
                """INSERT INTO agent_runs (
                       run_id, user_id, status, started_at, ended_at,
                       total_llm_calls, context_tokens, token_usage,
                       estimated_prompt_tokens, final_text, evidence)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(run_id) DO UPDATE SET
                       status = excluded.status,
                       ended_at = excluded.ended_at,
                       total_llm_calls = excluded.total_llm_calls,
                       context_tokens = excluded.context_tokens,
                       token_usage = excluded.token_usage,
                       estimated_prompt_tokens = excluded.estimated_prompt_tokens,
                       final_text = excluded.final_text,
                       evidence = excluded.evidence""",
                (
                    run["run_id"], run["user_id"], run.get("status", "ok"),
                    run["started_at"], run.get("ended_at"),
                    run.get("total_llm_calls", 0), run.get("context_tokens", 0),
                    json.dumps(run.get("token_usage", {}), ensure_ascii=False),
                    run.get("estimated_prompt_tokens", 0),
                    run.get("final_text", ""),
                    json.dumps(run.get("evidence", []), ensure_ascii=False),
                ),
            )
    except sqlite3.Error as e:
        _log().warning(f"agent run 落库失败: {e}")
    finally:
        conn.close()


# ═══════════════════ 读取 ═══════════════════

def _row_to_run(row: sqlite3.Row) -> dict:
    return {
        "run_id": row["run_id"],
        "user_id": row["user_id"],
        "status": row["status"],
        "started_at": row["started_at"],
        "ended_at": row["ended_at"],
        "total_llm_calls": row["total_llm_calls"],
        "context_tokens": row["context_tokens"],
        "token_usage": json.loads(row["token_usage"] or "{}"),
        "estimated_prompt_tokens": row["estimated_prompt_tokens"],
        "final_text": row["final_text"],
        "evidence": json.loads(row["evidence"] or "[]"),
    }


def list_runs(user_id: int, limit: int = 50, offset: int = 0,
              db_dir: Path | None = None) -> list[dict]:
    """某用户的运行列表（元数据，不含 evidence 大字段），按开始时间倒序。

    offset 用于分页，配合 count_runs() 返回 total。
    """
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    conn = _connect(db_dir)
    try:
        rows = conn.execute(
            """SELECT run_id, user_id, status, started_at, ended_at,
                      total_llm_calls, context_tokens, token_usage,
                      estimated_prompt_tokens, final_text
               FROM agent_runs WHERE user_id = ?
               ORDER BY started_at DESC LIMIT ? OFFSET ?""",
            (user_id, limit, offset),
        ).fetchall()
        runs = []
        for r in rows:
            d = dict(r)
            d["token_usage"] = json.loads(d["token_usage"] or "{}")
            d.pop("evidence", None)
            runs.append(d)
        return runs
    finally:
        conn.close()


def get_run(user_id: int, run_id: str,
            db_dir: Path | None = None) -> Optional[dict]:
    """某用户的单次运行详情（含 evidence）。不存在或非本人返回 None。"""
    conn = _connect(db_dir)
    try:
        row = conn.execute(
            "SELECT * FROM agent_runs WHERE run_id = ? AND user_id = ?",
            (run_id, user_id),
        ).fetchone()
        return _row_to_run(row) if row is not None else None
    finally:
        conn.close()


def recent_completion_tokens(user_id: int, n: int = 20,
                             db_dir: Path | None = None) -> list[int]:
    """该用户最近 n 次运行的 completion_tokens（历史 token 预估用），旧的在前。

    注：清理只摘除过期行的 evidence、保留 token_usage 元数据，本函数不受影响。
    """
    conn = _connect(db_dir)
    try:
        rows = conn.execute(
            """SELECT token_usage FROM agent_runs
               WHERE user_id = ? ORDER BY started_at DESC LIMIT ?""",
            (user_id, max(1, min(int(n), 200))),
        ).fetchall()
        out = []
        for r in reversed(rows):
            try:
                ct = int(json.loads(r["token_usage"] or "{}").get("completion_tokens", 0))
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
            if ct > 0:
                out.append(ct)
        return out
    finally:
        conn.close()


# ═══════════════════ 治理（清理 / 删除 / 统计） ═══════════════════

def prune(db_dir: Path | None = None,
          full_days: int = _RETENTION_FULL_DAYS,
          strip_days: int = _RETENTION_STRIP_DAYS) -> dict:
    """分层保留清理：过期行摘 evidence，更老整行删。

    幂等、全局按 started_at 执行（跨用户），返回 {"deleted", "stripped"}。
    删除不 VACUUM——SQLite 会复用释放的页，VACUUM 只在该库几乎全删时才值得。
    """
    now = time.time()
    conn = _connect(db_dir)
    try:
        with conn:  # 先删更老的，避免它们再被 UPDATE 白写一遍
            cur = conn.execute(
                "DELETE FROM agent_runs WHERE started_at < ?",
                (now - strip_days * 86400,),
            )
            deleted = cur.rowcount
        with conn:
            cur = conn.execute(
                "UPDATE agent_runs SET evidence = '[]' "
                "WHERE started_at < ? AND evidence != '[]'",
                (now - full_days * 86400,),
            )
            stripped = cur.rowcount
        return {"deleted": deleted, "stripped": stripped}
    finally:
        conn.close()


def delete_run(user_id: int, run_id: str,
               db_dir: Path | None = None) -> bool:
    """删除本人的一条运行记录。不存在或非本人返回 False。"""
    conn = _connect(db_dir)
    try:
        with conn:
            cur = conn.execute(
                "DELETE FROM agent_runs WHERE run_id = ? AND user_id = ?",
                (run_id, user_id),
            )
            return cur.rowcount > 0
    finally:
        conn.close()


def delete_user_runs(user_id: int, db_dir: Path | None = None) -> int:
    """清空某用户的全部运行记录（含 evidence），返回删除条数。"""
    conn = _connect(db_dir)
    try:
        with conn:
            cur = conn.execute(
                "DELETE FROM agent_runs WHERE user_id = ?", (user_id,),
            )
            return cur.rowcount
    finally:
        conn.close()


def count_runs(user_id: int, db_dir: Path | None = None) -> int:
    """某用户的运行总数（列表分页 total 用）。"""
    conn = _connect(db_dir)
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM agent_runs WHERE user_id = ?", (user_id,),
        ).fetchone()[0]
    finally:
        conn.close()


def run_stats(user_id: int, db_dir: Path | None = None) -> dict:
    """某用户运行概览：条数/成功率/LLM 调用/累计 token/记录体积。"""
    conn = _connect(db_dir)
    try:
        row = conn.execute(
            """SELECT COUNT(*) AS total,
                      COALESCE(SUM(CASE WHEN status='error' THEN 1 ELSE 0 END), 0) AS errors,
                      COALESCE(SUM(total_llm_calls), 0) AS llm_calls,
                      COALESCE(SUM(json_extract(token_usage, '$.prompt_tokens')), 0) AS prompt_tokens,
                      COALESCE(SUM(json_extract(token_usage, '$.completion_tokens')), 0) AS completion_tokens,
                      COALESCE(SUM(LENGTH(evidence)), 0) AS evidence_bytes
               FROM agent_runs WHERE user_id = ?""",
            (user_id,),
        ).fetchone()
        return {
            "total": row["total"],
            "ok": row["total"] - row["errors"],
            "errors": row["errors"],
            "llm_calls": row["llm_calls"],
            "prompt_tokens": row["prompt_tokens"],
            "completion_tokens": row["completion_tokens"],
            "evidence_bytes": row["evidence_bytes"],
        }
    finally:
        conn.close()


def default_db_dir() -> Path:
    """默认库目录（供调用方/脚本显式传参）。"""
    return _DEFAULT_DB_DIR


def ts_now() -> float:
    """当前时间戳（与 started_at/ended_at 统一 REAL 秒）。"""
    return time.time()
