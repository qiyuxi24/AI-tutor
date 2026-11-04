"""conversations 表的索引与墓碑回收（2026-11-04 技术债）。

两条债，同一个根因 —— **这张表是按"单机单用户小数据集"设计的，多端一开就放大**：

1. **零索引**：`_create_table` 只建了 `PRIMARY KEY (id, user_id)`，而全部读路径都是
   `WHERE user_id = ? AND deleted_at IS NULL ORDER BY updated_at DESC`
   （`list_conversations` / `_all_conversations`）。→ 每次进 App 都是全表扫 + 无索引排序。
   实测量级对照：knowledge.db 有 5 个索引、agent_runs.db 有 4 个，唯独本库 0 个。

2. **墓碑永不回收**：删对话是墓碑（`deleted_at`，这是"删了还复活"的唯一防线），
   但 `conversation_store` 里没有任何 `DELETE FROM`，`main.py::_JOBS` 也没有它
   → 行只增不减。TODO.md §8 那条"遗留：墓碑无 GC"说的就是这里。

回收口径（**不能按"删了就走"**）：
  墓碑要在库里活够久，直到**每台设备都见过它**。若墓碑被物理删掉，
  而某台离线设备还留着一份，它下次 `sync` 就会把这个对话**复活**
  （`save_conversation` 的墓碑拦截只对"库里还有这行"生效）。
  所以保留期取 `_TOMBSTONE_RETENTION_DAYS = 90`：离线三个月的设备本就该重新拉全量。
"""
import time

import pytest

from app.core.conversation_store import ConversationStore, purge_tombstones


def _indexes(store) -> set[str]:
    return {r["name"] for r in store._conn.execute("PRAGMA index_list(conversations)")}


def _make(store, cid: str, updated_at: float = 0.0) -> None:
    store.save_conversation({
        "id": cid,
        "title": cid,
        "messages": [{"role": "user", "content": "hi"}],
        "created_at": 1000.0,
        "updated_at": updated_at or time.time(),
    })


# ════════════════════════════════════════════
#  债 1：索引
# ════════════════════════════════════════════

def test_list_path_has_a_covering_index(tmp_path):
    """`WHERE user_id AND deleted_at IS NULL ORDER BY updated_at DESC` 必须走索引。"""
    store = ConversationStore(user_id=1, data_dir=tmp_path)
    names = _indexes(store)
    assert "idx_conversations_user_updated" in names, f"缺列表索引，实际只有 {names}"
    store.close()


def test_tombstone_lookup_has_an_index(tmp_path):
    """`tombstones()` 的过滤条件也要有索引（多端每次 sync 都调）。"""
    store = ConversationStore(user_id=1, data_dir=tmp_path)
    assert "idx_conversations_tombstones" in _indexes(store)
    store.close()


def test_indexes_are_partial_so_they_stay_small(tmp_path):
    """两个索引都必须是 partial（带 WHERE），否则墓碑/活行互相污染索引体积。"""
    store = ConversationStore(user_id=1, data_dir=tmp_path)
    for name in ("idx_conversations_user_updated", "idx_conversations_tombstones"):
        sql = store._conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (name,)
        ).fetchone()["sql"]
        assert "WHERE" in sql.upper(), f"{name} 不是 partial index: {sql}"
    store.close()


def test_plan_actually_uses_the_index(tmp_path):
    """不是"建了就算"——让 SQLite 自己说它用了哪个索引（防索引写错列序）。"""
    store = ConversationStore(user_id=1, data_dir=tmp_path)
    _make(store, "c1")
    plan = store._conn.execute(
        "EXPLAIN QUERY PLAN "
        "SELECT id, title, messages, created_at, updated_at FROM conversations "
        "WHERE user_id = ? AND deleted_at IS NULL ORDER BY updated_at DESC",
        (1,),
    ).fetchall()
    detail = " ".join(r["detail"] for r in plan)
    # 允许 SCAN 后接 "USING INDEX"（SQLite 可能选覆盖索引顺序扫），但必须点到索引名
    assert "idx_conversations_user_updated" in detail, f"查询计划没走索引: {detail}"
    assert "TEMP B-TREE" not in detail, f"ORDER BY 仍在临时排序: {detail}"
    store.close()


def test_old_db_gets_indexes_added(tmp_path):
    """老库（文件已存在、无索引）打开后必须补上索引——不要求用户重建库。"""
    first = ConversationStore(user_id=1, data_dir=tmp_path)
    _make(first, "c1")
    for name in ("idx_conversations_user_updated", "idx_conversations_tombstones"):
        first._conn.execute(f"DROP INDEX IF EXISTS {name}")
    first._conn.commit()
    first.close()

    again = ConversationStore(user_id=1, data_dir=tmp_path)
    assert "idx_conversations_user_updated" in _indexes(again)
    # 数据没被动过
    assert again.get_conversation("c1") is not None
    again.close()


def test_index_creation_is_idempotent(tmp_path):
    """反复打开（每次请求都会 new 一个 store）不能报 "index already exists"。"""
    for _ in range(3):
        store = ConversationStore(user_id=1, data_dir=tmp_path)
        store.close()


# ════════════════════════════════════════════
#  债 2：墓碑回收
# ════════════════════════════════════════════

def test_purge_removes_only_old_tombstones(tmp_path):
    store = ConversationStore(user_id=1, data_dir=tmp_path)
    _make(store, "alive")
    _make(store, "recent-deleted")
    _make(store, "ancient-deleted")
    store.soft_delete("recent-deleted")
    store.soft_delete("ancient-deleted")

    old = time.time() - 100 * 86400
    with store._conn:
        store._conn.execute(
            "UPDATE conversations SET deleted_at = ? WHERE id = ?", (old, "ancient-deleted")
        )
    store.close()

    res = purge_tombstones(data_dir=tmp_path)
    assert res == {"deleted": 1, "stripped": 0}, res

    check = ConversationStore(user_id=1, data_dir=tmp_path)
    ids = {r["id"] for r in check._conn.execute("SELECT id FROM conversations")}
    assert ids == {"alive", "recent-deleted"}, ids
    check.close()


def test_purge_never_touches_live_conversations(tmp_path):
    """活对话哪怕一年没改也不能删——本函数只认 deleted_at，不认 updated_at。"""
    store = ConversationStore(user_id=1, data_dir=tmp_path)
    _make(store, "ancient-alive")
    with store._conn:
        store._conn.execute(
            "UPDATE conversations SET updated_at = ?, created_at = ?",
            (time.time() - 400 * 86400, time.time() - 400 * 86400),
        )
    store.close()

    purge_tombstones(data_dir=tmp_path)
    check = ConversationStore(user_id=1, data_dir=tmp_path)
    assert check.get_conversation("ancient-alive") is not None
    check.close()


def test_purge_is_idempotent_and_returns_prune_contract(tmp_path):
    """返回形状必须对齐 `main.py::_prune_once` 的 `_JOBS` 契约（dict 含 deleted/stripped）。"""
    store = ConversationStore(user_id=1, data_dir=tmp_path)
    _make(store, "gone")
    store.soft_delete("gone")
    with store._conn:
        store._conn.execute(
            "UPDATE conversations SET deleted_at = ?", (time.time() - 200 * 86400,)
        )
    store.close()

    assert purge_tombstones(data_dir=tmp_path) == {"deleted": 1, "stripped": 0}
    assert purge_tombstones(data_dir=tmp_path) == {"deleted": 0, "stripped": 0}


def test_purge_purges_across_users(tmp_path):
    """清理是全局的（跨用户），与 agent_runs/notices 同款——每日 GC 不该按 uid 跑。"""
    old = time.time() - 200 * 86400
    for uid in (1, 2):
        s = ConversationStore(user_id=uid, data_dir=tmp_path)
        _make(s, f"c{uid}")
        s.soft_delete(f"c{uid}")
        with s._conn:
            s._conn.execute("UPDATE conversations SET deleted_at = ?", (old,))
        s.close()

    assert purge_tombstones(data_dir=tmp_path)["deleted"] == 2


def test_purge_on_empty_dir_does_not_create_files(tmp_path):
    """库不存在时直接返 0，**不得建目录/建空库**（每日 GC 会扫所有用户）。"""
    empty = tmp_path / "nope"
    assert purge_tombstones(data_dir=empty) == {"deleted": 0, "stripped": 0}
    assert not empty.exists(), "空目录被创建了 —— GC 不该产生副作用"


def test_retention_window_is_generous_enough_for_offline_devices():
    """保留期是安全参数：太短 = 离线设备把对话复活。钉死下限，防有人改成 7 天。"""
    from app.core.conversation_store import _TOMBSTONE_RETENTION_DAYS
    assert _TOMBSTONE_RETENTION_DAYS >= 90


def test_tombstone_purge_is_wired_into_daily_gc():
    """光有函数不算修好——必须真的挂进 `main.py::_JOBS`，否则永远没人调它。"""
    import inspect

    from app import main
    src = inspect.getsource(main.lifespan)
    assert "purge_tombstones" in src, "main.lifespan 的 _JOBS 里没有 conversations 清理"
