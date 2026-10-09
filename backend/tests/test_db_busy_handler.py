"""SQLite 写锁竞争 → 503（而非 500）：多端并发写时的可重试语义（2026-10-07）。

背景：同一账户可在多端登录 → 同一个 knowledge.db 上真并发写。SQLite 是单写者
模型，后到者在连接级 `busy_timeout` 内排队等锁；等不到就抛 `database is locked`。
这属于**临时**竞争（稍后重试即可），不该报成"服务器内部错误 500" —— 前端也无法
区分它和真故障。

不变量：
  1. `database is locked` → 503 + `Retry-After`（可重试）
  2. 其它 `OperationalError`（如 no such table / 语法错）→ 仍是 500（真故障不掩盖）
"""
import asyncio
import sqlite3

from starlette.requests import Request

from app.main import sqlite_busy_handler


def _req() -> Request:
    """构造一个最小可用的 Request（handler 只用到 path / method）。"""
    return Request({
        "type": "http", "http_version": "1.1", "method": "POST",
        "path": "/api/v1/chat", "raw_path": b"/api/v1/chat",
        "query_string": b"", "headers": [], "client": ("127.0.0.1", 1),
        "server": ("testserver", 80), "scheme": "http",
    })


def test_database_locked_maps_to_503_with_retry_after():
    """写锁等待超时 → 503 + Retry-After，提示可重试。"""
    resp = asyncio.run(
        sqlite_busy_handler(_req(), sqlite3.OperationalError("database is locked")))

    assert resp.status_code == 503
    assert resp.headers.get("retry-after") == "1"
    assert "重试" in resp.body.decode("utf-8")


def test_database_table_locked_also_maps_to_503():
    """`database table is locked` 同属竞争，一并转 503。"""
    resp = asyncio.run(
        sqlite_busy_handler(_req(), sqlite3.OperationalError("database table is locked")))

    assert resp.status_code == 503


def test_other_operational_error_still_500():
    """非锁竞争的真故障（表不存在）不能被 503 掩盖。"""
    resp = asyncio.run(
        sqlite_busy_handler(_req(), sqlite3.OperationalError("no such table: nodes")))

    assert resp.status_code == 500
