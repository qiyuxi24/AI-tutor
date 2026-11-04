"""SQLite 连接参数口径统一（2026-11-04 技术债）。

问题不是"某个库慢"，是**同一进程里三套标准**：

| 库 | timeout | busy_timeout | WAL |
|---|---|---|---|
| `knowledge.db`（`core/kg/core.py:45`） | 10s | 10000 | ✅ |
| `conversations.db`（`core/conversation_store.py:57,61`） | 10s | 10000 | ✅ |
| `collector.db`（`core/collector/store.py:41`） | **默认 5s** | **无** | ✅ |
| `quiz.db`（`core/quiz/quiz_store.py:34`） | **默认 5s** | **无** | ✅ |
| `rag.db` 向量库（`core/rag/vector_store.py:32`） | **默认 5s** | **无** | ✅ |

Python `sqlite3.connect` 默认 `timeout=5.0`；不设 `busy_timeout` PRAGMA 时，
WAL 下 `BEGIN IMMEDIATE` 撞上别人的写事务会在这个窗口内等，超时才抛
`database is locked`。症状是**偶发**冒到用户面前——最难复现的那一类。

本文件钉死「所有长驻业务库连接必须显式等锁 10s」，防止以后新增第 6 个 store 时又漏。
"""
import sqlite3

import pytest

from app.core.collector.store import CollectorStore
from app.core.quiz.quiz_store import QuizStore
from app.core.rag.vector_store import VectorStore

_PENDING_MS = 10000


def _busy_timeout(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA busy_timeout").fetchone()[0]


def _journal_mode(conn: sqlite3.Connection) -> str:
    return conn.execute("PRAGMA journal_mode").fetchone()[0].lower()


@pytest.mark.parametrize(
    "factory,name",
    [
        (lambda p: CollectorStore(data_dir=p), "collector.db"),
        (lambda p: QuizStore(data_dir=p), "quiz.db"),
        (lambda p: VectorStore(data_dir=p), "rag 向量库"),
    ],
)
def test_store_waits_for_write_lock(tmp_path, factory, name):
    """三个曾被漏掉的 store 都必须等锁 10s，而不是用 sqlite3 默认的 5s。"""
    store = factory(tmp_path)
    try:
        assert _busy_timeout(store._conn) == _PENDING_MS, f"{name} 未设 busy_timeout"
        assert _journal_mode(store._conn) == "wal", f"{name} 不是 WAL"
    finally:
        store._conn.close()


@pytest.mark.parametrize(
    "factory",
    [
        lambda p: CollectorStore(data_dir=p),
        lambda p: QuizStore(data_dir=p),
        lambda p: VectorStore(data_dir=p),
    ],
)
def test_connect_timeout_is_explicit(tmp_path, factory, monkeypatch):
    """`timeout=` 必须显式传，且与 `busy_timeout` 同量级 —— 否则
    "PRAGMA 等 10s 但 connect 层面 5s 就放弃"的自相矛盾会回来。
    """
    seen = {}
    real_connect = sqlite3.connect

    def spy(*args, **kwargs):
        seen.update(kwargs)
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", spy)
    store = factory(tmp_path)
    try:
        assert seen.get("timeout") == 10.0, f"connect(timeout=) 未显式传 10.0: {seen.get('timeout')}"
    finally:
        store._conn.close()


def test_all_long_lived_stores_agree(tmp_path):
    """五个长驻库口径一致 —— 这是本债的最终验收：**不存在两套标准**。"""
    from app.core.conversation_store import ConversationStore
    from app.core.knowledge_graph import KnowledgeGraph

    store_conns = {}
    try:
        for label, obj in (
            ("collector", CollectorStore(data_dir=tmp_path / "c")),
            ("quiz", QuizStore(data_dir=tmp_path / "q")),
            ("vector", VectorStore(data_dir=tmp_path / "v")),
            ("conversations", ConversationStore(user_id=1, data_dir=tmp_path / "conv")),
            ("knowledge", KnowledgeGraph(user_id=1, data_dir=tmp_path / "kg")),
        ):
            store_conns[label] = obj
        got = {label: _busy_timeout(o._conn) for label, o in store_conns.items()}
        assert set(got.values()) == {_PENDING_MS}, f"口径不一致: {got}"
    finally:
        for label, obj in store_conns.items():
            if label == "knowledge":
                obj.close()
            else:
                obj._conn.close()
