"""记录型 SQLite 库的统一底座（连接 / 建表 / 补列 / 过期清理）。

三张记录表分属两个库文件，但共用同一套范式 —— 本模块是那套范式的**唯一实现**：

    agent_runs.db   ← agent/store.py 的 `agent_runs` + llm/usage.py 的 `llm_usage`
                      （同库：两者都是"长期审计"口径，且要按 run_id 对照）
    debug_log.db    ← agent/debug_log.py 的 `agent_debug_logs`
                      （开发流水：写量大、14 天、可由 `AGENT_DEBUG_LOG=0` 整体关闭，
                        故独立成库做写放大隔离）

为什么要有它（2026-09-20）：
  store / debug_log / llm.usage 原先各抄一份 `_connect`（connect + Row 工厂 + WAL +
  executescript），库路径也各算各的 —— 于是 `llm/usage.py` 只能延迟导入
  `agent/store.py` 去拿目录（躲循环导入）。收敛后：**加一张记录表 = 写自己的 schema
  + 调一次 connect**，不再复制连接代码，也不用再编一个路径。

职责边界：**只管机制，不管内容**。
  这里：库路径 / 连接 / 幂等建表 / 老库补列 / 按时间清理。
  各表模块：表里有哪些列、怎么读写、保留多久。
  合并三张表的业务 API 会直接长成一个上帝模块（本项目刚拆过一个 `llm_client`，别引回来）。
"""
import sqlite3
import time
from pathlib import Path

# 记录库数据根：backend/app/data（⚠️ 不在 Docker 的 ./backend/data 卷内，
# 靠 docker-compose 单挂 ./backend/app/data，见其注释）
DATA_ROOT = Path(__file__).resolve().parents[1] / "data"


def default_dir(folder: str) -> Path:
    """某个记录库的默认目录（folder 即库所在的子目录名）。"""
    return DATA_ROOT / folder


def connect(folder: str, filename: str, schema: str, db_dir=None) -> sqlite3.Connection:
    """打开（必要时创建）一个记录库：建目录 + WAL + Row 工厂 + 幂等建表。

    参数:
        folder:   默认目录名（`db_dir` 为空时用 `DATA_ROOT/folder`）
        filename: 库文件名（如 `agent_runs.db`）
        schema:   `CREATE TABLE IF NOT EXISTS ...` 脚本，每次连接幂等执行
        db_dir:   覆盖目录（测试注入 tmp_path 用）

    用短连接：记录写入频率低，长驻连接反而要处理断线与跨线程问题
    （与 conversations 那种高频库区分）。
    """
    path = (Path(db_dir) if db_dir else default_dir(folder)) / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(schema)
    return conn


def ensure_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    """给已存在的老库就地补列 —— `CREATE TABLE IF NOT EXISTS` **不会**给老表加字段。幂等。

    参数:
        table:   表名
        columns: {列名: 列定义}，如 {"token_estimate": "TEXT NOT NULL DEFAULT '{}'"}

    ⚠️ 新增列必须同时写进调用方的补列清单，否则老库读新列会报 `no such column`。
    """
    existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    missing = {name: ddl for name, ddl in columns.items() if name not in existing}
    if not missing:
        return
    with conn:
        for name, ddl in missing.items():
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def prune_older_than(conn: sqlite3.Connection, table: str, days: float,
                     *, ts_col: str = "ts") -> int:
    """删掉时间戳早于 N 天的行，返回删除条数。

    只做整行删除。需要"分层保留"（先摘大字段、再整行删）的表自己实现，
    见 `agent/store.prune`。
    """
    cutoff = time.time() - days * 86400
    with conn:
        cur = conn.execute(f"DELETE FROM {table} WHERE {ts_col} < ?", (cutoff,))
    return cur.rowcount
