"""GQ-18 / GQ-19 第③步的存储层：`nodes.sources` 溯源 + `doc_node_marks` 增补队列。

对应 `TODO_Graph_Quality.md` §5.2；设计参照
`docs/知识图谱/知识图谱_多资料综合维护调研.md` §4 L0（溯源）/ L3（按资料增量）。

两条存储语义的分工（本测试逐条锁定）：
- `sources` = 「节点 ← 多份资料」的**长期事实**：**并入、按 (doc_id, chunk_id, section) 去重、
  绝不覆盖**已有来源；老节点为空数组（来源未知），不阻塞。
- `doc_node_marks` = 兼两职责：「资料 → 产出/影响的节点」**doc 级幂等账本**（编排层用
  `evidence.kind` 区分 new/hit，本表透存不校验）+ **增补队列**（`pending` 待增补 /
  `filled` 已增补）；幂等 upsert、**已 filled 不得降级回 pending**、状态机单向。

全离线：不碰 LLM，全部使用 tmp_path 临时库，不污染 `data/knowledge/`。
"""
import json
import sqlite3

import pytest

from app.core.knowledge_graph import (
    MARK_FILLED,
    MARK_PENDING,
    KnowledgeGraph,
    normalize_source_entry,
)


# ── 夹具 ──────────────────────────────────────────────

@pytest.fixture
def kg(tmp_path):
    g = KnowledgeGraph(user_id=1, data_dir=tmp_path)
    with g._conn:  # nodes.user_id 是外键，先备 users 行
        g._conn.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (1, 's1', 'x')")
    yield g
    g.close()


def _add_user(data_dir, uid: int) -> KnowledgeGraph:
    g = KnowledgeGraph(user_id=uid, data_dir=data_dir)
    with g._conn:
        g._conn.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (?, ?, 'x')",
            (uid, f"u{uid}"))
    return g


def _tables(g) -> set:
    return {r[0] for r in g._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}


def _indexes(g) -> set:
    return {r[0] for r in g._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'").fetchall()}


def _src(doc_id=1, name="教材A", section="第一章", chunk_id=None, at="2026-09-26T10:00:00"):
    return {"doc_id": doc_id, "doc_name": name, "section": section,
            "chunk_id": chunk_id, "extracted_at": at}


# ── schema：新库 ─────────────────────────────────────

def test_sources_column_and_marks_table_created(kg):
    cols = [r[1] for r in kg._conn.execute("PRAGMA table_info(nodes)").fetchall()]
    assert "sources" in cols
    assert {"doc_node_marks"} <= _tables(kg)
    assert "idx_doc_node_marks_doc" in _indexes(kg)


def test_new_node_sources_default_empty(kg):
    """新建节点默认 sources = []（来源未知，不阻塞）。"""
    kg.add_node({"id": "a", "name": "递归"})
    assert kg.get_sources("a") == []
    assert kg.get_node("a")["sources"] == []  # nodes 属性也反序列化成列表


# ── schema：老库迁移 ─────────────────────────────────

def _make_legacy_db(data_dir):
    """老库：nodes 无 sources / content_status / source_ref / updated_at（且带 1 行存量节点）。"""
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
            VALUES ('old', '老节点', 'nodes/old.md', 1, '2026-01-01T00:00:00');
    """)
    db.commit()
    db.close()


def test_legacy_db_gets_sources_column_and_marks_table(tmp_path):
    """老库实例化即补 sources 列（CREATE TABLE IF NOT EXISTS 做不到）+ 建标记表；幂等。"""
    data_dir = tmp_path / "kg"
    _make_legacy_db(data_dir)

    for _ in range(2):  # 两次打开 = 幂等
        g = KnowledgeGraph(user_id=1, data_dir=data_dir)
        try:
            cols = [r[1] for r in g._conn.execute("PRAGMA table_info(nodes)").fetchall()]
            assert "sources" in cols, "老库必须经 ALTER 补上 sources 列"
            assert {"doc_node_marks"} <= _tables(g)
            assert g.get_sources("old") == [], "存量节点来源未知 → 空数组，不阻塞"
        finally:
            g.close()


# ── add_sources / get_sources（GQ-18）────────────────

def test_add_sources_merges_and_dedupes(kg):
    """同 (doc_id, chunk_id, section) 只留一条；不同段落分别保留。"""
    kg.add_node({"id": "a", "name": "递归"})

    assert kg.add_sources("a", [
        _src(doc_id=1, section="第一章", chunk_id="c1"),
        _src(doc_id=1, section="第二章", chunk_id="c2"),
        _src(doc_id=1, section="第二章", chunk_id="c2"),  # 重复
    ]) is True

    got = kg.get_sources("a")
    assert len(got) == 2
    assert {(s["doc_id"], s["section"], s["chunk_id"]) for s in got} == {
        (1, "第一章", "c1"), (1, "第二章", "c2")}


def test_add_sources_is_idempotent(kg):
    """同一来源重复并入 → 无新增、返回 False（幂等信号）。"""
    kg.add_node({"id": "a", "name": "递归"})
    assert kg.add_sources("a", [_src()]) is True
    assert kg.add_sources("a", [_src()]) is False
    assert len(kg.get_sources("a")) == 1


def test_add_sources_never_overwrites_existing(kg):
    """绝不覆盖已有来源：同键但 doc_name/extracted_at 变了，也保留首次值。"""
    kg.add_node({"id": "a", "name": "递归"})
    kg.add_sources("a", [_src(name="教材A", at="2026-09-20T00:00:00")])

    assert kg.add_sources("a", [_src(name="教材A-改", at="2026-09-26T00:00:00")]) is False

    only = kg.get_sources("a")[0]
    assert only["doc_name"] == "教材A"
    assert only["extracted_at"] == "2026-09-20T00:00:00"


def test_add_sources_skips_invalid_entries(kg):
    """非法条目（非 dict / doc_id 不可转 int）静默跳过，不污染 sources。"""
    kg.add_node({"id": "a", "name": "递归"})

    assert kg.add_sources("a", ["坏", {"section": "无 doc_id"}, {"doc_id": "x"}, _src()]) is True

    assert len(kg.get_sources("a")) == 1


def test_add_sources_unknown_node_returns_false(kg):
    assert kg.add_sources("nope", [_src()]) is False


def test_add_sources_empty_is_noop(kg):
    kg.add_node({"id": "a", "name": "递归"})
    assert kg.add_sources("a", []) is False


def test_add_sources_isolated_per_user(tmp_path):
    """他人节点视同不存在：user2 既读不到、也写不进 user1 节点的来源。"""
    g1 = _add_user(tmp_path, 1)
    g1.add_node({"id": "mine", "name": "递归"})
    g1.add_sources("mine", [_src()])
    g1.close()

    g2 = _add_user(tmp_path, 2)
    try:
        assert g2.get_sources("mine") == []
        assert g2.add_sources("mine", [_src(doc_id=9)]) is False
    finally:
        g2.close()

    g1 = KnowledgeGraph(user_id=1, data_dir=tmp_path)
    try:
        assert len(g1.get_sources("mine")) == 1
    finally:
        g1.close()


def test_add_sources_refreshes_updated_at(kg):
    kg.add_node({"id": "a", "name": "递归"})
    with kg._conn:
        kg._conn.execute("UPDATE nodes SET updated_at = '2000-01-01T00:00:00' WHERE id = 'a'")

    kg.add_sources("a", [_src()])

    assert kg.get_node("a")["updated_at"] > "2000-01-01T00:00:00"


def test_normalize_source_entry_defaults_and_chunk_none():
    """空串 chunk_id 归一为 None；缺 extracted_at 自动补时间。"""
    e = normalize_source_entry({"doc_id": "3", "chunk_id": ""})
    assert e["doc_id"] == 3 and e["chunk_id"] is None
    assert e["doc_name"] == "" and e["section"] == "" and e["extracted_at"]
    assert normalize_source_entry({"doc_id": 1, "chunk_id": None})["chunk_id"] is None
    assert normalize_source_entry("坏") is None
    assert normalize_source_entry({"doc_id": "x"}) is None


# ── mark_doc_nodes / list_doc_marks / set_mark_status（GQ-19 ③）──

def test_mark_doc_nodes_inserts_pending(kg):
    kg.add_node({"id": "a", "name": "递归"})
    kg.add_node({"id": "b", "name": "动态规划"})

    assert kg.mark_doc_nodes(7, ["a", "b"], evidence={"method": "alias"}) == 2

    marks = kg.list_doc_marks(7)
    assert {m["node_id"] for m in marks} == {"a", "b"}
    assert all(m["status"] == MARK_PENDING for m in marks)
    assert marks[0]["evidence"] == {"method": "alias"}


def test_mark_doc_nodes_is_idempotent(kg):
    kg.add_node({"id": "a", "name": "递归"})
    assert kg.mark_doc_nodes(7, ["a"]) == 1
    assert kg.mark_doc_nodes(7, ["a"]) == 0, "重复标记同一 (doc, node) 不产生新行/变更"
    assert len(kg.list_doc_marks(7)) == 1


def test_mark_doc_nodes_does_not_downgrade_filled(kg):
    """已 filled 的标记不得被降级回 pending（否则重复增补）。"""
    kg.add_node({"id": "a", "name": "递归"})
    kg.mark_doc_nodes(7, ["a"])
    assert kg.set_mark_status(7, "a", MARK_FILLED) is True

    assert kg.mark_doc_nodes(7, ["a"], evidence={"method": "again"}) == 0

    mark = kg.list_doc_marks(7)[0]
    assert mark["status"] == MARK_FILLED


def test_mark_doc_nodes_refreshes_pending_evidence(kg):
    """pending 行的 evidence 被新值刷新（但状态不变）。"""
    kg.add_node({"id": "a", "name": "递归"})
    kg.mark_doc_nodes(7, ["a"], evidence={"m": 1})

    assert kg.mark_doc_nodes(7, ["a"], evidence={"m": 2}) == 1

    assert kg.list_doc_marks(7)[0]["evidence"] == {"m": 2}


def test_mark_doc_nodes_ignores_foreign_and_missing_nodes(tmp_path):
    """他人节点 / 不存在的节点静默跳过，只标记属当前用户的。"""
    g1 = _add_user(tmp_path, 1)
    g1.add_node({"id": "mine", "name": "递归"})
    g1.close()
    g2 = _add_user(tmp_path, 2)
    g2.add_node({"id": "theirs", "name": "动态规划"})
    g2.close()

    g1 = KnowledgeGraph(user_id=1, data_dir=tmp_path)
    try:
        assert g1.mark_doc_nodes(7, ["mine", "theirs", "ghost"]) == 1
        assert [m["node_id"] for m in g1.list_doc_marks(7)] == ["mine"]
    finally:
        g1.close()


def test_mark_doc_nodes_dedupes_input(kg):
    kg.add_node({"id": "a", "name": "递归"})
    assert kg.mark_doc_nodes(7, ["a", "a", "a"]) == 1


def test_list_doc_marks_filters_status_and_doc(kg):
    kg.add_node({"id": "a", "name": "递归"})
    kg.add_node({"id": "b", "name": "动态规划"})
    kg.mark_doc_nodes(7, ["a", "b"])
    kg.mark_doc_nodes(8, ["a"])
    kg.set_mark_status(7, "a", MARK_FILLED)

    assert {m["node_id"] for m in kg.list_doc_marks(7, status=MARK_PENDING)} == {"b"}
    assert {m["node_id"] for m in kg.list_doc_marks(7, status=MARK_FILLED)} == {"a"}
    assert len(kg.list_doc_marks(7)) == 2
    assert len(kg.list_doc_marks(8)) == 1, "不同资料的标记互不串扰"


def test_set_mark_status_only_pending_to_filled(kg):
    kg.add_node({"id": "a", "name": "递归"})
    kg.mark_doc_nodes(7, ["a"])

    assert kg.set_mark_status(7, "a", MARK_FILLED) is True
    assert kg.set_mark_status(7, "a", MARK_FILLED) is False, "已 filled 不再迁移"
    assert kg.set_mark_status(7, "ghost", MARK_FILLED) is False, "无此标记 → False"


def test_set_mark_status_cannot_reset_to_pending(kg):
    """回退不被支持：设置 pending 不改库。"""
    kg.add_node({"id": "a", "name": "递归"})
    kg.mark_doc_nodes(7, ["a"])
    kg.set_mark_status(7, "a", MARK_FILLED)

    assert kg.set_mark_status(7, "a", MARK_PENDING) is False
    assert kg.list_doc_marks(7)[0]["status"] == MARK_FILLED


def test_set_mark_status_invalid_value_raises(kg):
    kg.add_node({"id": "a", "name": "递归"})
    with pytest.raises(ValueError, match="非法标记状态"):
        kg.set_mark_status(7, "a", "done")


def test_marks_isolated_per_user(tmp_path):
    g1 = _add_user(tmp_path, 1)
    g1.add_node({"id": "mine", "name": "递归"})
    g1.mark_doc_nodes(7, ["mine"])
    g1.close()

    g2 = _add_user(tmp_path, 2)
    try:
        assert g2.list_doc_marks(7) == []
        assert g2.set_mark_status(7, "mine", MARK_FILLED) is False
    finally:
        g2.close()


def test_marks_cascade_with_node_delete(kg):
    """删节点经外键级联删标记（不留悬空工作项）。"""
    kg.add_node({"id": "a", "name": "递归"})
    kg.mark_doc_nodes(7, ["a"])

    kg.remove_node("a")

    assert kg.list_doc_marks(7) == []


def test_evidence_stored_as_json_text(kg):
    """底层 evidence 列存 JSON 文本（list_doc_marks 再反序列化）。"""
    kg.add_node({"id": "a", "name": "递归"})
    kg.mark_doc_nodes(7, ["a"], evidence={"score": 0.9})

    raw = kg._conn.execute(
        "SELECT evidence FROM doc_node_marks WHERE user_id = 1 AND doc_id = 7"
    ).fetchone()[0]
    assert json.loads(raw) == {"score": 0.9}
