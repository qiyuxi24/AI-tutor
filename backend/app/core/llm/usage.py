"""LLM 用量记账 —— **每次真实 API 调用一行**的 token 明细（`llm_usage` 表）。

为什么需要它（2026-09-20）：
  `agent_runs.token_usage` 是 **run 级汇总**（一次对话多轮 LLM 合并成一个 JSON），
  且表里没有 model 列 —— 回答不了"钱花在哪"：哪个模型、哪个功能、何时降级到了备用
  模型。更严重的是 `call_llm` 那条线的 usage 此前只打日志就丢，而出题/判分/建图才是
  大批量消耗。

三处记录的分工（别混）：
  - `agent_runs`（agent/store.py）run 级：一次对话一行的汇总真值 + 发送前预估；
  - `llm_usage`（本模块）      调用级：一次真实 HTTP 调用一行，按 kind / model / 天 / 用户聚合；
  - `agent_debug_logs`         调试流水：14 天、JSON 原文、可由 `AGENT_DEBUG_LOG=0` 关闭
                               —— 不是审计口径，别拿它做统计。

设计约束：
  - **不新建库**：表落在 `agent/store.py` 那同一个 db 文件（复用同一套短连接 + WAL 范式），
    避免第三处 token 真相源；
  - **只记账，不判错**：任何异常都吞掉只打 warning —— 记账失败绝不影响主流程；
  - 网关不返回 usage 时不写空行。

写入方（新增 LLM 出口时请同步加一处 `record()`）：
  `agent/loop.py`（每轮 + 强制收尾）/ `llm/call.py`（一次性调用，含空回复重试）
  / `llm/embed.py`（嵌入）。
"""
import logging
import sqlite3
import time
from pathlib import Path

from app.core import records

logger = logging.getLogger("ai-tutor")

# 与 agent/store.py 同库（两者都是长期审计口径，且要按 run_id 对照）；
# 落点/连接/建表统一走 core/records.py。
_DB_FOLDER = "agent_runs"
_DB_FILE = "agent_runs.db"

# 账本留得比 agent_runs（30 全量 / 180 摘 evidence / 180+ 删）久：
# 纯数值统计、体积小，跨年度对比有价值。
_RETENTION_DAYS = 365

# 聚合维度白名单。值是 SQL 片段、直接拼进查询，所以只认这四个 key，其余退回 kind。
_GROUPS = {
    "kind": "kind",
    "model": "model",
    "day": "date(ts, 'unixepoch', 'localtime')",
    "user": "user_id",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_usage (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    ts   REAL NOT NULL,
    kind TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    user_id INTEGER,
    run_id  TEXT,
    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    cached_tokens     INTEGER NOT NULL DEFAULT 0,
    reasoning_tokens  INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER
);
CREATE INDEX IF NOT EXISTS idx_llm_usage_ts ON llm_usage(ts DESC);
CREATE INDEX IF NOT EXISTS idx_llm_usage_kind_ts ON llm_usage(kind, ts DESC);
"""


def _connect(db_dir: Path | None) -> sqlite3.Connection:
    # 走 core/records（只依赖 stdlib）—— 收敛后不必再延迟导入 agent.store 躲循环导入
    return records.connect(_DB_FOLDER, _DB_FILE, _SCHEMA, db_dir)


def record(kind: str, model: str, usage, *,
           user_id: int | None = None, run_id: str | None = None,
           duration_ms: int | None = None, db_dir: Path | None = None) -> None:
    """记一次真实 LLM / 嵌入调用的 token 明细（失败只告警，不影响主流程）。

    参数:
        kind:   用途标签（agent_loop / quiz_generate / quiz_grade / graph_analyze /
                kb_graph_extract / kb_graph_dedupe / taxonomy / embedding …）。
                按它聚合才能分清"钱花在哪一个功能"。
        model:  实际应答的模型名，**取 `resp.model` 而不是请求时的 MODEL_NAME** ——
                主模型失败会静默降级到备用服务，只有响应里的才是真身。
        usage:  `TokenUsage`（`token_counter.extract_usage` 的产物）。
    """
    prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
    completion = int(getattr(usage, "completion_tokens", 0) or 0)
    if prompt <= 0 and completion <= 0:
        return  # 网关不返回 usage：不写没有信息的空行
    try:
        conn = _connect(db_dir)
        try:
            with conn:
                conn.execute(
                    "INSERT INTO llm_usage (ts, kind, model, user_id, run_id,"
                    " prompt_tokens, completion_tokens, cached_tokens,"
                    " reasoning_tokens, duration_ms) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (time.time(), kind, model or "", user_id, run_id,
                     prompt, completion,
                     int(getattr(usage, "cached_tokens", 0) or 0),
                     int(getattr(usage, "reasoning_tokens", 0) or 0),
                     duration_ms),
                )
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 —— 记账是旁路，任何失败都不能打断对话
        logger.warning(f"llm_usage 记账失败（已忽略）: {e}")


def summary(*, since_hours: float = 168, group_by: str = "kind",
            user_id: int | None = None, db_dir: Path | None = None) -> dict:
    """聚合一段时间内的用量（默认近 7 天、按功能分组），按消耗从大到小排。

    group_by 取 kind / model / day / user，**白名单外一律退回 kind**。
    user_id 非空则只看该用户（目前只有 agent loop 的记账带 user_id）。
    """
    dim = _GROUPS.get(group_by, "kind")
    where, params = ["ts >= ?"], [time.time() - since_hours * 3600.0]
    if user_id is not None:
        where.append("user_id = ?")
        params.append(user_id)
    sql = (
        f"SELECT {dim} AS dim, COUNT(*) AS calls,"
        " COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,"
        " COALESCE(SUM(completion_tokens), 0) AS completion_tokens,"
        " COALESCE(SUM(cached_tokens), 0) AS cached_tokens,"
        " COALESCE(SUM(reasoning_tokens), 0) AS reasoning_tokens"
        " FROM llm_usage WHERE " + " AND ".join(where) +
        " GROUP BY dim ORDER BY (SUM(prompt_tokens) + SUM(completion_tokens)) DESC"
    )
    conn = _connect(db_dir)
    try:
        groups = [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()
    return {
        "since_hours": since_hours,
        "group_by": group_by if group_by in _GROUPS else "kind",
        "total": {
            "calls": sum(g["calls"] for g in groups),
            "prompt_tokens": sum(g["prompt_tokens"] for g in groups),
            "completion_tokens": sum(g["completion_tokens"] for g in groups),
            "total_tokens": sum(
                g["prompt_tokens"] + g["completion_tokens"] for g in groups),
        },
        "groups": groups,
    }


def prune(days: float = _RETENTION_DAYS, db_dir: Path | None = None) -> int:
    """删掉超过保留窗口的用量明细，返回删除条数（由启动/每日 GC 触发）。"""
    conn = _connect(db_dir)
    try:
        return records.prune_older_than(conn, "llm_usage", days)
    finally:
        conn.close()
