"""KG-D4 / KG-D6：图谱 schema v3 两项改造的行为断言。

- KG-D6：`edges` 时间戳列 + `(user_id, from_node, to_node, relation)` 唯一索引
  —— 把"代码里 SELECT 一次查重"升级为**数据库约束**（脚本/并发直写也挡得住）；
  含老库迁移，以及"存量已有重复边时不阻断启动"的降级路径。
- §4.1 的 `nodes.updated_at`（回答"上周改过哪些节点"）：补列时回填 `= created_at`，
  改信息 / 改正文（含并轨）都刷新。
- KG-D4：`mastery_events` 掌握度证据链 —— 掌握度是**学习状态**（不是知识点本体属性），
  变更在唯一入口 `update_node_info` 内**同事务**记账，可回溯"谁改的、从多少到多少、凭什么"。

全离线：不碰 LLM，全部使用 tmp_path 临时库，不污染 `data/knowledge/`。
"""
import sqlite3
from pathlib import Path

import pytest

from app.core.knowledge_graph import KnowledgeGraph

VALID_RELATIONS = ("prerequisite", "related", "confusion", "extension")


# ── 夹具 ──────────────────────────────────────────────

@pytest.fixture
def kg(tmp_path):
    g = KnowledgeGraph(user_id=1, data_dir=tmp_path)
    with g._conn:  # nodes.user_id 是外键，先备 users 行
        g._conn.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (1, 'v3', 'x')")
    yield g
    g.close()


def _seed_nodes(g):
    g.add_node({"id": "a", "name": "递归", "tags": ["数据结构"]})
    g.add_node({"id": "b", "name": "动态规划", "tags": ["数据结构"]})


def _index_exists(kg, name: str) -> bool:
    row = kg._conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _make_legacy_edges_db(data_dir: Path, rows: list[tuple]) -> Path:
    """造「老库」：edges 表**没有 created_at / updated_at 列**，但已含存量边。"""
    data_dir.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(data_dir / "knowledge.db"))
    db.executescript("""
        CREATE TABLE nodes (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, file_path TEXT NOT NULL,
            tags TEXT DEFAULT '[]', board TEXT DEFAULT '', subject TEXT DEFAULT '',
            summary TEXT DEFAULT '', mastery INTEGER DEFAULT 0, difficulty INTEGER DEFAULT 3,
            estimated_minutes INTEGER DEFAULT 15, added_by TEXT DEFAULT 'human',
            created_at TEXT, confidence REAL, user_id INTEGER
        );
        CREATE TABLE edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_node TEXT NOT NULL, to_node TEXT NOT NULL, relation TEXT NOT NULL,
            label TEXT DEFAULT '', added_by TEXT DEFAULT 'human', confidence REAL,
            user_id INTEGER
        );
        INSERT INTO nodes (id, name, file_path, user_id, created_at)
            VALUES ('a', '递归', 'nodes/a.md', 1, '2026-01-01T00:00:00');
        INSERT INTO nodes (id, name, file_path, user_id) VALUES ('b', '动态规划', 'nodes/b.md', 1);
    """)
    for from_node, to_node, relation in rows:
        db.execute(
            "INSERT INTO edges (from_node, to_node, relation, user_id) VALUES (?, ?, ?, 1)",
            (from_node, to_node, relation),
        )
    db.commit()
    db.close()
    return data_dir


# ── KG-D6：边的时间戳 + 唯一约束 ──────────────────────

def test_legacy_edges_get_timestamp_columns_and_unique_index(tmp_path):
    """老库 edges 缺时间戳列 → 实例化即补齐，并建唯一索引；存量行留 NULL（未知，不瞎猜）。"""
    data_dir = _make_legacy_edges_db(tmp_path / "kg", [("a", "b", "prerequisite")])
    kg = KnowledgeGraph(user_id=1, data_dir=data_dir)
    try:
        cols = [r[1] for r in kg._conn.execute("PRAGMA table_info(edges)").fetchall()]
        assert {"created_at", "updated_at"} <= set(cols), "老库应被补上边的时间戳列"
        assert _index_exists(kg, "idx_edges_unique")
        assert kg._conn.execute("SELECT created_at FROM edges").fetchone()[0] is None, \
            "老行时间未知 → NULL，不伪造"
    finally:
        kg.close()


def test_legacy_duplicate_edges_do_not_block_startup(tmp_path):
    """存量已有重复边 → 不阻断启动（否则整个后端起不来），只告警、不动存量行。"""
    data_dir = _make_legacy_edges_db(
        tmp_path / "kg", [("a", "b", "related"), ("a", "b", "related")])
    kg = KnowledgeGraph(user_id=1, data_dir=data_dir)
    try:
        assert not _index_exists(kg, "idx_edges_unique"), "有重复边时索引建不上（已降级）"
        assert kg._conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0] == 2, \
            "启动降级不得删改存量边"
    finally:
        kg.close()


def test_database_rejects_duplicate_edge_raw_insert(kg):
    """绕过 add_edge 的裸 SQL 重复插入被数据库拒绝（代码查重之外的兜底）。"""
    _seed_nodes(kg)
    kg.add_edge({"from": "a", "to": "b", "relation": "prerequisite"})

    with pytest.raises(sqlite3.IntegrityError):
        kg._conn.execute(
            "INSERT INTO edges (from_node, to_node, relation, user_id) VALUES ('a','b','prerequisite',1)")


def test_add_edge_fills_timestamps(kg):
    """新建边同时写 created_at / updated_at。"""
    _seed_nodes(kg)
    kg.add_edge({"from": "a", "to": "b", "relation": "prerequisite"})

    row = kg._conn.execute("SELECT * FROM edges").fetchone()
    assert row["created_at"] and row["created_at"] == row["updated_at"]


def test_update_edge_bumps_updated_at(kg):
    """改边（label/relation）会更新 updated_at。"""
    _seed_nodes(kg)
    kg.add_edge({"from": "a", "to": "b", "relation": "prerequisite"})
    eid = kg.edges[0]["id"]
    with kg._conn:  # 压到过去的时间，避免同微秒比较
        kg._conn.execute("UPDATE edges SET updated_at = '2000-01-01T00:00:00' WHERE id = ?", (eid,))

    kg.update_edge_by_id(eid, {"label": "先学递归"})

    row = kg._conn.execute("SELECT * FROM edges WHERE id = ?", (eid,)).fetchone()
    assert row["label"] == "先学递归"
    assert row["updated_at"] > "2000-01-01T00:00:00"


def test_updating_into_duplicate_triple_raises_value_error(kg):
    """把边的 relation 改成与既有边同三元组 → ValueError（不再是"默默多一条重复边"）。"""
    _seed_nodes(kg)
    kg.add_edge({"from": "a", "to": "b", "relation": "prerequisite"})
    kg.add_edge({"from": "a", "to": "b", "relation": "related"})
    related_id = next(e["id"] for e in kg.edges if e["relation"] == "related")

    with pytest.raises(ValueError, match="已存在"):
        kg.update_edge_by_id(related_id, {"relation": "prerequisite"})


def test_all_valid_relations_still_accepted(kg):
    """枚举内的 4 种关系互不冲突（唯一索引按 relation 区分，不放宽也不收紧语义）。"""
    _seed_nodes(kg)
    for rel in VALID_RELATIONS:
        kg.add_edge({"from": "a", "to": "b", "relation": rel})
    assert {e["relation"] for e in kg.edges} == set(VALID_RELATIONS)


# ── 节点 updated_at（§4.1，回答"上周改过哪些节点"）────

def test_legacy_nodes_backfill_updated_at_from_created_at(tmp_path):
    """老库补 updated_at 列时回填 = created_at（当时就是最后变更时间），幂等、不瞎猜。"""
    data_dir = _make_legacy_edges_db(tmp_path / "kg", [])

    kg = KnowledgeGraph(user_id=1, data_dir=data_dir)
    try:
        assert kg.get_node("a")["updated_at"] == "2026-01-01T00:00:00"
        assert kg.get_node("b")["updated_at"] is None, "created_at 本来为空 → 仍留空"
    finally:
        kg.close()

    again = KnowledgeGraph(user_id=1, data_dir=data_dir)   # 幂等：再开不重写
    try:
        assert again.get_node("a")["updated_at"] == "2026-01-01T00:00:00"
    finally:
        again.close()


def test_new_node_updated_at_equals_created_at(kg):
    _seed_nodes(kg)

    node = kg.get_node("a")
    assert node["updated_at"] == node["created_at"]


def test_updated_at_refreshed_on_info_change(kg):
    _seed_nodes(kg)
    with kg._conn:
        kg._conn.execute("UPDATE nodes SET updated_at = '2000-01-01T00:00:00' WHERE id = 'a'")

    kg.update_node_info("a", {"summary": "新摘要"})

    assert kg.get_node("a")["updated_at"] > "2000-01-01T00:00:00"


def test_updated_at_refreshed_on_content_change(kg):
    """并轨/正文追加也刷新（`_merge_content_into` 走的就是这条路径）。"""
    _seed_nodes(kg)
    with kg._conn:
        kg._conn.execute("UPDATE nodes SET updated_at = '2000-01-01T00:00:00' WHERE id = 'a'")

    kg.update_node_content("a", "追加的正文")

    assert kg.get_node("a")["updated_at"] > "2000-01-01T00:00:00"


# ── KG-D4：掌握度事件 ─────────────────────────────────

def test_mastery_change_records_event(kg):
    """掌握度真的变了 → 记一条带 before/after/delta/reason/evidence 的事件。"""
    _seed_nodes(kg)
    assert kg.get_mastery_events("a") == [], "建节点（mastery=0）不是变更，不该有事件"

    kg.update_node_info("a", {"mastery": 20}, mastery_reason="quiz_correct",
                        mastery_evidence="question:7")

    events = kg.get_mastery_events("a")
    assert len(events) == 1
    e = events[0]
    assert (e["before_val"], e["after_val"], e["delta"]) == (0, 20, 20)
    assert e["reason"] == "quiz_correct"
    assert e["evidence"] == "question:7"
    assert kg.get_node("a")["mastery"] == 20


def test_mastery_event_reason_defaults_to_manual(kg):
    """不传 reason（前端/手动更新）→ 记 manual，事件链不断档。"""
    _seed_nodes(kg)
    kg.update_node_info("a", {"mastery": 60})

    assert kg.get_mastery_events("a")[0]["reason"] == "manual"


def test_same_value_records_no_event(kg):
    """重复赋同值不算变更（避免把审计表刷成流水）。"""
    _seed_nodes(kg)
    kg.update_node_info("a", {"mastery": 0})

    assert kg.get_mastery_events("a") == []


def test_non_mastery_update_records_no_event(kg):
    """改摘要/难度等其他字段不写掌握度事件。"""
    _seed_nodes(kg)
    kg.update_node_info("a", {"summary": "递归是把问题拆成同类子问题", "difficulty": 4})

    assert kg.get_mastery_events("a") == []


def test_unparsable_mastery_keeps_legacy_behaviour(kg):
    """非法 mastery 沿用既有行为（照写库、不抛新异常），只是不记账（算不出可信 delta）。"""
    _seed_nodes(kg)

    kg.update_node_info("a", {"mastery": "abc"})       # 不抛

    assert kg.get_mastery_events("a") == []


def test_null_mastery_records_event(kg):
    """存量 mastery 为 NULL（老行）时按 0 起算，不因取值为空而漏记。"""
    _seed_nodes(kg)
    with kg._conn:
        kg._conn.execute("UPDATE nodes SET mastery = NULL WHERE id = 'a'")

    kg.update_node_info("a", {"mastery": 30})

    e = kg.get_mastery_events("a")[0]
    assert (e["before_val"], e["after_val"]) == (0, 30)


def test_events_ordered_newest_first(kg):
    """事件倒序（最近在前），并逐条留痕：0→20→40。"""
    _seed_nodes(kg)
    kg.update_node_info("a", {"mastery": 20})
    kg.update_node_info("a", {"mastery": 40})

    assert [e["after_val"] for e in kg.get_mastery_events("a")] == [40, 20]


def test_mastery_events_cascade_with_node(kg):
    """删节点 → 外键级联删事件（不留悬空证据）。"""
    _seed_nodes(kg)
    kg.update_node_info("a", {"mastery": 20})

    kg.remove_node("a")

    assert kg._conn.execute(
        "SELECT COUNT(*) FROM mastery_events WHERE node_id = 'a'").fetchone()[0] == 0


def test_mastery_events_are_isolated_per_user(tmp_path):
    """user 隔离：查不到别人的掌握度历史。"""
    for uid in (1, 2):
        g = KnowledgeGraph(user_id=uid, data_dir=tmp_path)
        with g._conn:
            g._conn.execute(
                "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (?, ?, 'x')",
                (uid, f"u{uid}"))
        g.add_node({"id": f"n{uid}", "name": "递归", "tags": ["数据结构"]})
        g.update_node_info(f"n{uid}", {"mastery": 20})
        g.close()

    g1 = KnowledgeGraph(user_id=1, data_dir=tmp_path)
    try:
        assert [e["node_id"] for e in g1.get_mastery_events("n1")] == ["n1"]
        assert g1.get_mastery_events("n2") == [], "不得读到其他用户节点的证据"
    finally:
        g1.close()
