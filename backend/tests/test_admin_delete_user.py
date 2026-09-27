"""backend-admin 删号数据清理的最小回归：delete_user_rows 必须清干净 knowledge.db。

为什么单独按文件路径加载 admin 模块（不走 `import app.*`）：
- `backend-admin/` 不在本测试套件的 sys.path 上（conftest 只注入 `backend/`）；
- admin 的 `db.py` 顶部 `from app.core.config import settings` 会命中 backend 那份
  同名 settings（admin 的配置与 backend 的配置是两个不同的 Settings），且 admin 的
  `settings.db_path` 等字段 backend 那份并没有 —— 直接 import 会一碰就 AttributeError。

绕法：用 importlib 按文件路径加载 admin/db.py，加载期间用桩把 `app.core.config`
顶掉（delete_user_rows 根本不读 settings，只需让模块级 import 不炸）。
这样不改 conftest、不动 sys.path、也不为测试改生产代码结构。
"""
import importlib.util
import sqlite3
import sys
import types
from pathlib import Path
from unittest import mock

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ADMIN_DB_PATH = _REPO_ROOT / "backend-admin" / "app" / "core" / "db.py"


def _load_admin_db():
    """按路径加载 backend-admin/app/core/db.py，桩掉 app.core.config。

    返回已加载的模块（含 delete_user_rows）。
    """
    # 造一条最小 `app.core.config` 模块链，只为满足模块级 import
    app_pkg = types.ModuleType("app")
    app_pkg.__path__ = []
    core_pkg = types.ModuleType("app.core")
    core_pkg.__path__ = []
    fake_config = types.ModuleType("app.core.config")
    fake_config.settings = types.SimpleNamespace(
        db_path="", backend_data_dir="", conversations_db=""
    )
    app_pkg.core = core_pkg
    core_pkg.config = fake_config

    with mock.patch.dict(
        sys.modules,
        {"app": app_pkg, "app.core": core_pkg, "app.core.config": fake_config},
    ):
        spec = importlib.util.spec_from_file_location("admin_core_db", _ADMIN_DB_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


admin_db = _load_admin_db()


# ── 建库工具：新版库 7 张表 / 老版库 5 张表（无 themes、node_themes）──

_FULL_SCHEMA = """
CREATE TABLE users (
    id       INTEGER PRIMARY KEY,
    username TEXT
);
CREATE TABLE nodes (
    id      TEXT PRIMARY KEY,
    user_id INTEGER
);
CREATE TABLE edges (
    id        INTEGER PRIMARY KEY,
    from_node TEXT,
    to_node   TEXT,
    user_id   INTEGER
);
CREATE TABLE node_aliases (
    alias_key TEXT NOT NULL,
    node_id   TEXT NOT NULL,
    user_id   INTEGER NOT NULL,
    PRIMARY KEY (alias_key, user_id)
);
CREATE TABLE mastery_events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    node_id TEXT NOT NULL
);
CREATE TABLE themes (
    id        TEXT PRIMARY KEY,
    user_id   INTEGER NOT NULL,
    name      TEXT NOT NULL,
    parent_id TEXT,
    level     INTEGER NOT NULL
);
CREATE TABLE node_themes (
    node_id  TEXT NOT NULL,
    theme_id TEXT NOT NULL,
    user_id  INTEGER NOT NULL,
    PRIMARY KEY (node_id, theme_id)
);
"""

# 老库：还没有 KG-T1 的 themes / node_themes
_LEGACY_SCHEMA = """
CREATE TABLE users (
    id       INTEGER PRIMARY KEY,
    username TEXT
);
CREATE TABLE nodes (
    id      TEXT PRIMARY KEY,
    user_id INTEGER
);
CREATE TABLE edges (
    id        INTEGER PRIMARY KEY,
    from_node TEXT,
    to_node   TEXT,
    user_id   INTEGER
);
CREATE TABLE node_aliases (
    alias_key TEXT NOT NULL,
    node_id   TEXT NOT NULL,
    user_id   INTEGER NOT NULL,
    PRIMARY KEY (alias_key, user_id)
);
CREATE TABLE mastery_events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    node_id TEXT NOT NULL
);
"""


def _connect(path: Path, schema: str) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.executescript(schema)
    return conn


def _seed_full(conn: sqlite3.Connection, uid: int) -> None:
    """给 uid 在全部 7 张表各插一行（口径与生产一致：user_id 区分）。"""
    conn.execute("INSERT INTO users (id, username) VALUES (?, ?)", (uid, f"u{uid}"))
    conn.execute("INSERT INTO nodes (id, user_id) VALUES (?, ?)", (f"n{uid}", uid))
    conn.execute(
        "INSERT INTO edges (id, from_node, to_node, user_id) VALUES (?, ?, ?, ?)",
        (uid, f"n{uid}", f"n{uid}", uid),
    )
    conn.execute(
        "INSERT INTO node_aliases (alias_key, node_id, user_id) VALUES (?, ?, ?)",
        (f"a{uid}", f"n{uid}", uid),
    )
    conn.execute(
        "INSERT INTO mastery_events (user_id, node_id) VALUES (?, ?)", (uid, f"n{uid}")
    )
    conn.execute(
        "INSERT INTO themes (id, user_id, name, parent_id, level) VALUES (?, ?, ?, ?, ?)",
        (f"t{uid}", uid, f"theme{uid}", None, 1),
    )
    conn.execute(
        "INSERT INTO node_themes (node_id, theme_id, user_id) VALUES (?, ?, ?)",
        (f"n{uid}", f"t{uid}", uid),
    )


_TABLES = (
    "users",
    "nodes",
    "edges",
    "node_aliases",
    "mastery_events",
    "themes",
    "node_themes",
)


def _count(conn: sqlite3.Connection, table: str, uid: int) -> int:
    col = "id" if table == "users" else "user_id"
    return conn.execute(
        f"SELECT COUNT(*) FROM {table} WHERE {col} = ?", (uid,)
    ).fetchone()[0]


def test_delete_user_rows_clears_all_tables_and_spares_others(tmp_path):
    """删 uid=1：7 张表该用户数据全清，uid=2 数据一条不动。"""
    conn = _connect(tmp_path / "knowledge.db", _FULL_SCHEMA)
    try:
        _seed_full(conn, 1)
        _seed_full(conn, 2)

        admin_db.delete_user_rows(conn, 1)

        for table in _TABLES:
            assert _count(conn, table, 1) == 0, f"uid=1 的 {table} 未清干净"
            assert _count(conn, table, 2) == 1, f"uid=2 的 {table} 被误删"
    finally:
        conn.close()


def test_delete_user_rows_tolerates_legacy_db_without_theme_tables(tmp_path):
    """老库（无 themes / node_themes）调用不得抛异常，且原有 5 表照常清。"""
    conn = _connect(tmp_path / "knowledge.db", _LEGACY_SCHEMA)
    try:
        conn.execute("INSERT INTO users (id, username) VALUES (1, 'u1')")
        conn.execute("INSERT INTO nodes (id, user_id) VALUES ('n1', 1)")
        conn.execute(
            "INSERT INTO edges (id, from_node, to_node, user_id) VALUES (1, 'n1', 'n1', 1)"
        )
        conn.execute(
            "INSERT INTO node_aliases (alias_key, node_id, user_id) VALUES ('a1', 'n1', 1)"
        )
        conn.execute("INSERT INTO mastery_events (user_id, node_id) VALUES (1, 'n1')")

        # 不应因缺表抛 sqlite3.OperationalError: no such table
        admin_db.delete_user_rows(conn, 1)

        for table in ("users", "nodes", "edges", "node_aliases", "mastery_events"):
            assert _count(conn, table, 1) == 0, f"老库 uid=1 的 {table} 未清干净"
    finally:
        conn.close()


def test_purge_user_storage_removes_all_per_user_dirs(tmp_path, monkeypatch):
    """删号后的磁盘清理必须覆盖「节点 MD / kb / rag / **quiz**」四个按用户分目录。

    回归来源（2026-09-26 审计）：roots 漏了题库（`core/quiz/quiz_store.py::_QUIZ_DIR`
    = `backend/data/quiz/<uid>`，与 kb/rag 同数据根）→ 删号后该用户题目与判分记录残留。
    """
    data_root = tmp_path / "backend_data"
    mine = [data_root / kind / "1" for kind in ("kb", "rag", "quiz")]
    mine.append(tmp_path / "knowledge" / "nodes" / "1")
    for d in mine:
        d.mkdir(parents=True)
        (d / "keep.txt").write_text("x", encoding="utf-8")
    other = data_root / "quiz" / "2"
    other.mkdir(parents=True)

    monkeypatch.setattr(admin_db, "settings", types.SimpleNamespace(
        db_path=str(tmp_path / "knowledge" / "knowledge.db"),
        backend_data_dir=str(data_root),
        conversations_db=str(tmp_path / "conversations.db"),
    ))

    admin_db.purge_user_storage(1)

    for d in mine:
        assert not d.exists(), f"{d} 未被清理"
    assert other.exists(), "其他用户的目录不得被删"
