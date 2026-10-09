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


# ── 建库工具：新版库 6 张表 / 老版库 5 张表（无 doc_node_marks）──

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
CREATE TABLE doc_node_marks (
    user_id    INTEGER NOT NULL,
    doc_id     INTEGER NOT NULL,
    node_id    TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'pending',
    evidence   TEXT DEFAULT '',
    created_at TEXT,
    PRIMARY KEY (user_id, doc_id, node_id)
);
"""

# 老库：还没有 doc_node_marks
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
    """给 uid 在全部 6 张表各插一行（口径与生产一致：user_id 区分）。"""
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
        "INSERT INTO doc_node_marks (user_id, doc_id, node_id, status)"
        " VALUES (?, ?, ?, 'pending')",
        (uid, uid, f"n{uid}"),
    )


_TABLES = (
    "users",
    "nodes",
    "edges",
    "node_aliases",
    "mastery_events",
    "doc_node_marks",
)


def _count(conn: sqlite3.Connection, table: str, uid: int) -> int:
    col = "id" if table == "users" else "user_id"
    return conn.execute(
        f"SELECT COUNT(*) FROM {table} WHERE {col} = ?", (uid,)
    ).fetchone()[0]


def test_delete_user_rows_clears_all_tables_and_spares_others(tmp_path):
    """删 uid=1：6 张表该用户数据全清，uid=2 数据一条不动。"""
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


def test_delete_user_rows_tolerates_legacy_db(tmp_path):
    """老库（无 doc_node_marks）调用不得抛异常，且原有 5 表照常清。"""
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


def _make_sqlite(path: Path, script: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(script)
        conn.commit()
    finally:
        conn.close()


# 按用户分目录的域（主系统 backend/data/{kind}/{uid}）
_KINDS = ("kb", "rag", "quiz", "collector", "llm")


def test_purge_user_storage_covers_all_media(tmp_path, monkeypatch):
    """删号必须清干净**每一类**介质，且不动别的用户。

    口径 = 方案 §3 A1a 步骤 5：四张表（conversations / agent_runs / llm_usage /
    agent_debug_logs）+ 按用户目录（nodes 与 kb|rag|quiz|collector|llm）+ 两个单文件
    （画像 JSON、头像 PNG）。历史回归：roots 漏过题库（2026-09-26）、单文件头像漏过
    （2026-09-28）、records 落点错到 backend/data（2026-10-07）。
    """
    data_root = tmp_path / "backend_data"
    records_root = tmp_path / "records"
    knowledge_dir = tmp_path / "knowledge"
    profiles_dir = tmp_path / "profiles"

    # 目录型：uid=1 全部造出；uid=2 各留一份作「不得误删」对照
    dirs_1 = [knowledge_dir / "nodes" / "1"] + [data_root / k / "1" for k in _KINDS]
    dirs_2 = [data_root / k / "2" for k in _KINDS]
    for d in dirs_1 + dirs_2:
        d.mkdir(parents=True)
        (d / "keep.txt").write_text("x", encoding="utf-8")

    # 单文件型：画像 JSON + 头像
    avatar = profiles_dir / "avatars" / "1.png"
    avatar.parent.mkdir(parents=True)
    avatar.write_bytes(b"\x89PNG")
    (profiles_dir / "1.json").write_text("{}", encoding="utf-8")
    other_profile = profiles_dir / "2.json"
    other_profile.write_text("{}", encoding="utf-8")

    # 表型：四个库各造 uid=1 / uid=2 两行
    _make_sqlite(tmp_path / "conversations.db",
                 "CREATE TABLE conversations (id INTEGER, user_id INTEGER);"
                 "INSERT INTO conversations VALUES (1, 1), (2, 2);")
    _make_sqlite(records_root / "agent_runs" / "agent_runs.db",
                 "CREATE TABLE agent_runs (run_id TEXT, user_id INTEGER);"
                 "INSERT INTO agent_runs VALUES ('a', 1), ('b', 2);"
                 "CREATE TABLE llm_usage (id INTEGER, user_id INTEGER);"
                 "INSERT INTO llm_usage VALUES (1, 1), (2, 2);")
    _make_sqlite(records_root / "agent_debug" / "debug_log.db",
                 "CREATE TABLE agent_debug_logs (id INTEGER, user_id INTEGER);"
                 "INSERT INTO agent_debug_logs VALUES (1, 1), (2, 2);")

    monkeypatch.setattr(admin_db, "settings", types.SimpleNamespace(
        db_path=str(knowledge_dir / "knowledge.db"),
        backend_data_dir=str(data_root),
        records_dir=str(records_root),
        conversations_db=str(tmp_path / "conversations.db"),
        profiles_dir=str(profiles_dir),
    ))

    admin_db.purge_user_storage(1)

    for d in dirs_1:
        assert not d.exists(), f"{d} 未被清理"
    for d in dirs_2:
        assert d.exists(), f"{d} 被误删"
    assert not avatar.exists(), "头像文件未被清理"
    assert not (profiles_dir / "1.json").exists(), "画像 JSON 未被清理"
    assert other_profile.exists(), "他人画像被误删"

    def _remaining(db_file: Path, table: str) -> list[int]:
        conn = sqlite3.connect(str(db_file))
        try:
            return [r[0] for r in conn.execute(
                f"SELECT user_id FROM {table} ORDER BY user_id")]
        finally:
            conn.close()

    assert _remaining(tmp_path / "conversations.db", "conversations") == [2]
    assert _remaining(records_root / "agent_runs" / "agent_runs.db", "agent_runs") == [2]
    assert _remaining(records_root / "agent_runs" / "agent_runs.db", "llm_usage") == [2]
    assert _remaining(records_root / "agent_debug" / "debug_log.db",
                      "agent_debug_logs") == [2]
