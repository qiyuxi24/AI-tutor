"""KG-D1 / KG-D2 / KG-D3：图谱 schema v2 三项改造的行为断言。

- KG-D1：`nodes.subject` 列 —— 建表/迁移补列 + 老库一次性回填 + `node_subject` 优先读列；
- KG-D2：tags 不再写难度档（`一级/二级/三级` 只读兼容），难度归 `difficulty` 列；
- KG-D3：`node_aliases` 别名表 —— 同义不同名的零嵌入并轨（判重 L1.5 档）。

全离线：不碰 LLM，全部使用 tmp_path 临时库，不污染 `data/knowledge/`。
"""
import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

from app.core.kb import graph_generator as gg
from app.core.knowledge_graph import KnowledgeGraph, normalize_node_name


# ── 夹具 ──────────────────────────────────────────────

@pytest.fixture
def kg(tmp_path):
    g = KnowledgeGraph(user_id=1, data_dir=tmp_path)
    with g._conn:  # nodes.user_id 是外键，先备 users 行
        g._conn.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (1, 'v2', 'x')")
    yield g
    g.close()


def _make_legacy_db(data_dir: Path, rows: list[dict]) -> Path:
    """造一个「老库」：nodes 表**没有 subject 列**，已含用户可见的行（user_id=1）。"""
    data_dir.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(data_dir / "knowledge.db"))
    db.execute("""
        CREATE TABLE nodes (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, file_path TEXT NOT NULL,
            tags TEXT DEFAULT '[]', board TEXT DEFAULT '', summary TEXT DEFAULT '',
            mastery INTEGER DEFAULT 0, difficulty INTEGER DEFAULT 3,
            estimated_minutes INTEGER DEFAULT 15, added_by TEXT DEFAULT 'human',
            created_at TEXT, confidence REAL, user_id INTEGER
        )
    """)
    for r in rows:
        db.execute(
            "INSERT INTO nodes (id, name, file_path, tags, user_id) VALUES (?, ?, ?, ?, ?)",
            (r["id"], r["name"], f"nodes/{r['id']}.md",
             json.dumps(r.get("tags", []), ensure_ascii=False), 1),
        )
    db.commit()
    db.close()
    return data_dir


def _async(value):
    async def _fn(*args, **kwargs):
        return value
    return _fn


# ── KG-D1：subject 落库与回填 ─────────────────────────

def test_subject_column_added_and_backfilled_from_tags(tmp_path):
    """老库（无 subject 列）→ 实例化即补列并按旧 tags 规则回填；再实例化幂等、值不变。"""
    data_dir = _make_legacy_db(tmp_path / "kg", [
        {"id": "n1", "name": "栈", "tags": ["数据结构", "二级"]},   # 难度档在前，学科在后
        {"id": "n2", "name": "指针", "tags": ["C语言", "一级"]},
        {"id": "n3", "name": "无标签", "tags": []},
        {"id": "n4", "name": "只有难度档", "tags": ["二级"]},
    ])
    kg = KnowledgeGraph(user_id=1, data_dir=data_dir)
    try:
        cols = [r[1] for r in kg._conn.execute("PRAGMA table_info(nodes)").fetchall()]
        assert "subject" in cols, "老库应被补上 subject 列"

        assert kg.get_node("n1")["subject"] == "数据结构"
        assert kg.get_node("n2")["subject"] == "C语言"
        assert kg.get_node("n3")["subject"] == ""
        assert kg.get_node("n4")["subject"] == "", "整列难度档标签不算学科"

        idx = kg._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_nodes_user_subject'"
        ).fetchone()
        assert idx is not None, "应建立 (user_id, subject) 索引"
    finally:
        kg.close()

    # 幂等：再开一次不报错、值不变（回填只在补列那一支执行，不会重复全量写）
    again = KnowledgeGraph(user_id=1, data_dir=data_dir)
    try:
        assert again.get_node("n1")["subject"] == "数据结构"
        assert again.get_node("n4")["subject"] == ""
    finally:
        again.close()


def test_add_node_derives_subject_from_tags(kg):
    """写入兜底：显式 subject 优先，否则用旧 tags 规则推导后落 subject 列。"""
    kg.add_node({"id": "a", "name": "栈", "tags": ["数据结构", "二级"]})
    kg.add_node({"id": "b", "name": "矩阵", "tags": ["线性代数"], "subject": "数学"})

    assert kg.get_node("a")["subject"] == "数据结构"
    assert kg.get_node("b")["subject"] == "数学", "显式 subject 优先于 tags"


def test_node_subject_prefers_column_over_tags(kg):
    """node_subject：subject 列非空优先；为空回退 tags；保持 staticmethod 与签名。"""
    assert KnowledgeGraph.node_subject({"tags": ["强化学习"], "subject": "机器学习"}) == "机器学习"
    assert KnowledgeGraph.node_subject({"tags": ["强化学习"], "subject": ""}) == "强化学习"
    assert KnowledgeGraph.node_subject({"tags": ["强化学习"]}) == "强化学习"
    assert KnowledgeGraph.node_subject({"tags": ["强化学习"], "subject": "  机器学习  "}) == "机器学习"
    assert KnowledgeGraph.node_subject({"tags": ["一级", "二级"], "subject": ""}) == ""


def test_update_node_info_accepts_subject(kg):
    """update_node_info 允许改 subject（并影响后续 node_subject）。"""
    kg.add_node({"id": "a", "name": "栈", "tags": ["数据结构"]})
    kg.update_node_info("a", {"subject": "计算机"})

    assert kg.get_node("a")["subject"] == "计算机"
    assert kg.node_subject(kg.get_node("a")) == "计算机"


# ── KG-D2：tags 不再写难度档 ──────────────────────────

def test_graph_generator_tags_exclude_difficulty(kg, monkeypatch):
    """建图写入路径：新节点 tags 不含 一级/二级/三级，难度落在 difficulty 列，学科落 subject。"""
    gen = gg.GraphGenerator(user_id=1)
    monkeypatch.setattr(gen, "_find_dedup_candidates", _async({}))
    monkeypatch.setattr(gen, "_confirm_synonyms", _async({}))

    asyncio.run(gen._write_to_graph(
        kg, "数据结构",
        {"nodes": [
            {"id": "stack", "name": "栈", "difficulty": 2},
            {"id": "tree", "name": "树", "difficulty": 5},
        ], "edges": []},
        existing_nodes=[]))

    stack = kg.get_node("stack")
    assert stack["tags"] == ["数据结构"], "tags 只放学科名"
    assert not (set(stack["tags"]) & {"一级", "二级", "三级"})
    assert stack["difficulty"] == 2, "难度由 difficulty 列承载"
    assert stack["subject"] == "数据结构"
    assert kg.get_node("tree")["difficulty"] == 5


# ── KG-D3：别名并轨 ───────────────────────────────────

def test_register_alias_rejects_empty_or_foreign_node(kg):
    """空别名 / 不属于当前用户的 node_id → 不写，返回 False。"""
    kg.add_node({"id": "a", "name": "栈", "tags": ["数据结构"]})
    assert kg.register_alias("", "a") is False
    assert kg.register_alias("   ", "a") is False
    assert kg.register_alias("堆栈", "不存在") is False


def test_register_alias_is_first_wins_idempotent(kg):
    """先到先得：重复登记同一 alias_key 返回 False，且原映射不被覆盖。"""
    kg.add_node({"id": "a", "name": "栈", "tags": ["数据结构"]})
    kg.add_node({"id": "b", "name": "队列", "tags": ["数据结构"]})

    assert kg.register_alias("堆栈", "a") is True
    assert kg.register_alias("堆栈", "b") is False
    assert kg.register_alias("堆栈", "a") is False
    row = kg._conn.execute(
        "SELECT node_id FROM node_aliases WHERE alias_key = ? AND user_id = 1",
        (normalize_node_name("堆栈"),)).fetchone()
    assert row["node_id"] == "a", "首次写入的映射不被后来的覆盖"


def test_alias_merge_does_not_create_new_node(kg):
    """先建「栈」→ 登记别名「堆栈」→ 建「堆栈」时并轨不新建、返回栈的 id、正文并入。"""
    # added_by="ai"：AI 写的节点才允许被 AI 路径并正文（人类节点有权限护栏，会静默跳过）
    kg.create_node_with_content({"id": "stack", "name": "栈", "tags": ["数据结构"],
                                 "added_by": "ai"}, "栈的定义", origin="book")
    assert kg.register_alias("堆栈", "stack") is True

    merged_id = kg.create_node_with_content(
        {"id": "heapstack", "name": "堆栈", "tags": ["数据结构"]},
        "堆栈补充内容", origin="book")

    assert merged_id == "stack", "别名命中 → 落点是已有节点 id"
    assert [n["id"] for n in kg.nodes] == ["stack"], "不该新建第二个节点"
    body = (kg.nodes_dir / "stack.md").read_text(encoding="utf-8")
    assert "堆栈补充内容" in body, "正文应并入保留者"

    row = kg._conn.execute(
        "SELECT node_id FROM node_aliases WHERE alias_key = ? AND user_id = 1",
        (normalize_node_name("堆栈"),)).fetchone()
    assert row is not None and row["node_id"] == "stack", "别名表应有该记录"


def test_alias_respects_subject_filter(kg):
    """别名命中但学科不符 → 不并轨（不同学科的同名/同义概念是两个东西）。"""
    kg.create_node_with_content({"id": "ds_tree", "name": "树", "tags": ["数据结构"]},
                                origin="book")  # 自名「树」已自动登记为别名 → ds_tree

    # 操作系统下的「树」（同义同名）应各自独立，不并到数据结构的树上
    assert kg.create_node_with_content({"id": "os_tree", "name": "树", "tags": ["操作系统"]},
                                       origin="book") == "os_tree"
    assert sorted(n["id"] for n in kg.nodes) == ["ds_tree", "os_tree"]


def test_delete_node_cascades_aliases(kg):
    """删节点 → FK ON DELETE CASCADE 连带删别名，别名查询随之返回 None。"""
    kg.create_node_with_content({"id": "stack", "name": "栈", "tags": ["数据结构"]}, "正文")
    kg.register_alias("堆栈", "stack")
    assert kg.find_node_by_name("堆栈") is not None

    kg.remove_node("stack")

    assert kg.find_node_by_name("堆栈") is None
    assert kg._conn.execute(
        "SELECT COUNT(*) FROM node_aliases WHERE node_id = 'stack'").fetchone()[0] == 0
