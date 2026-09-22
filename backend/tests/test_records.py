"""记录库底座（core/records）：连接 / 补列 / 过期清理。

各张记录表自己的 schema 与读写由对应模块的测试覆盖（test_agent_run_store /
test_debug_log / test_llm_usage），这里只锁底座的三条不变量。
"""
import sqlite3
import time

from app.core import records

_SCHEMA = """
CREATE TABLE IF NOT EXISTS t (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    ts   REAL NOT NULL,
    name TEXT NOT NULL DEFAULT ''
);
"""


def _connect(tmp_path):
    return records.connect("probe", "probe.db", _SCHEMA, tmp_path)


def test_connect_creates_dir_and_enables_wal(tmp_path):
    target = tmp_path / "nested"        # 父目录不存在 → 应自动创建
    conn = records.connect("probe", "probe.db", _SCHEMA, target)
    try:
        assert (target / "probe.db").exists()
        assert conn.row_factory is sqlite3.Row
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    finally:
        conn.close()
    # 幂等：同一路径再来一次不报错（CREATE TABLE IF NOT EXISTS）
    records.connect("probe", "probe.db", _SCHEMA, target).close()


def test_default_dir_is_under_backend_app_data():
    d = records.default_dir("agent_runs")
    assert d.name == "agent_runs"
    assert d.parent.name == "data"


def test_ensure_columns_adds_missing_and_is_idempotent(tmp_path):
    conn = _connect(tmp_path)
    try:
        records.ensure_columns(conn, "t", {"extra": "TEXT NOT NULL DEFAULT ''"})
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(t)")}
        assert "extra" in cols
        records.ensure_columns(conn, "t", {"extra": "TEXT NOT NULL DEFAULT ''"})  # 已存在则不动
        conn.execute("INSERT INTO t (ts, name, extra) VALUES (?, ?, ?)",
                     (time.time(), "a", "x"))
        conn.commit()
    finally:
        conn.close()


def test_prune_older_than_removes_only_expired_rows(tmp_path):
    """窗口内的行必须留着 —— 删多了等于静默丢数据。"""
    conn = _connect(tmp_path)
    try:
        now = time.time()
        conn.executemany("INSERT INTO t (ts, name) VALUES (?, ?)", [
            (now, "新"),
            (now - 10 * 86400, "10天前"),
            (now - 400 * 86400, "400天前"),
        ])
        conn.commit()
        assert records.prune_older_than(conn, "t", 30) == 1
        assert [r["name"] for r in conn.execute("SELECT name FROM t")] == ["新", "10天前"]
        assert records.prune_older_than(conn, "t", 30) == 0     # 再清一次无副作用
    finally:
        conn.close()
