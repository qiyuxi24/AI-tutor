"""删除整个学科图谱（`KnowledgeGraph.remove_subject`）—— 口径、级联与隔离断言。

本测试逐条锁定三条设计要点：
- **口径同源**：删的是「读得到的那一批」—— 复用 `get_nodes_by_subject`（内部走
  `node_subject` = subject 列 + tags 回退）。裸 `DELETE ... WHERE subject = ?`
  会漏掉 subject 列为空、只靠 tags 认课的老节点，前端看得见却删不掉。
- **级联清干净**：edges / node_aliases / mastery_events / doc_node_marks 随 nodes
  行级联删除（本连接已开 foreign_keys=ON），一个不留。
- **只删这一课**：其他学科的节点/边/别名/账本/MD 一律不动；跨学科边随被删
  端点一起消失（端点没了，边不可能留着）。

全离线：不碰 LLM，tmp_path 临时库，不污染 `data/knowledge/`。
"""
import pytest

from app.core.knowledge_graph import KnowledgeGraph


@pytest.fixture
def kg(tmp_path):
    g = KnowledgeGraph(user_id=1, data_dir=tmp_path)
    with g._conn:  # nodes.user_id 是外键，先备 users 行
        g._conn.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (1, 't', 'x')")
    yield g
    g.close()


def _seed_two_subjects(kg):
    """数据结构：数组/链表（含边/别名/账本/掌握度事件）；操作系统：进程。"""
    kg.create_node_with_content(
        {"id": "d1", "name": "数组", "tags": ["数据结构"], "subject": "数据结构"}, "数组正文")
    kg.create_node_with_content(
        {"id": "d2", "name": "链表", "tags": ["数据结构"], "subject": "数据结构"}, "链表正文")
    kg.create_node_with_content(
        {"id": "o1", "name": "进程", "tags": ["操作系统"], "subject": "操作系统"}, "进程正文")

    kg.add_edge({"from": "d1", "to": "d2", "relation": "related"})
    kg.add_edge({"from": "o1", "to": "d1", "relation": "related"})  # 跨学科：删 d1 后应消失

    kg.mark_doc_nodes(doc_id=7, node_ids=["d1", "o1"])
    kg.update_node_info("d1", {"mastery": 60})   # 产生一条 mastery_events


def _count(kg, sql: str) -> int:
    return kg._conn.execute(sql).fetchone()[0]


# ── 口径与隔离 ────────────────────────────────────────

def test_removes_only_target_subject(kg):
    _seed_two_subjects(kg)

    kg.remove_subject("数据结构")

    assert {n["id"] for n in kg.nodes} == {"o1"}, "其他学科的节点必须原样保留"


def test_returns_counts(kg):
    _seed_two_subjects(kg)

    r = kg.remove_subject("数据结构")

    assert r["subject"] == "数据结构"
    assert r["deleted_nodes"] == 2
    assert r["deleted_edges"] == 2, "学科内 1 条 + 跨学科 1 条（端点消失，边跟着走）"


def test_legacy_tag_only_node_is_removed(kg):
    """subject 列为空、只靠 tags 认课的老节点也要删（与读取侧同一口径）。"""
    kg.create_node_with_content(
        {"id": "legacy", "name": "树", "tags": ["数据结构"], "subject": "数据结构"}, "树正文")
    kg.create_node_with_content(
        {"id": "keep", "name": "队列", "tags": ["算法"], "subject": "算法"}, "队列正文")
    with kg._conn:  # 模拟老库：列是空的，学科只存在于 tags
        kg._conn.execute("UPDATE nodes SET subject = '' WHERE id = 'legacy'")
    kg._invalidate_cache()

    assert {n["id"] for n in kg.get_nodes_by_subject("数据结构")} == {"legacy"}

    kg.remove_subject("数据结构")

    assert kg.get_node("legacy") is None
    assert kg.get_node("keep") is not None


def test_other_user_nodes_untouched(kg):
    """同名同学科但属于别的用户 → 一行都不能动（按 user_id 隔离）。"""
    _seed_two_subjects(kg)
    with kg._conn:
        kg._conn.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (2, 'u2', 'x')")
    other = KnowledgeGraph(user_id=2, data_dir=kg.data_dir)
    try:
        other.create_node_with_content(
            {"id": "d1_other", "name": "数组", "tags": ["数据结构"], "subject": "数据结构"}, "x")
        kg.remove_subject("数据结构")
        assert {n["id"] for n in other.nodes} == {"d1_other"}
    finally:
        other.close()


# ── 级联 ──────────────────────────────────────────────

def test_cascades_all_child_tables(kg):
    _seed_two_subjects(kg)

    kg.remove_subject("数据结构")

    assert _count(kg, "SELECT COUNT(*) FROM edges"
                      " WHERE from_node IN ('d1','d2') OR to_node IN ('d1','d2')") == 0
    assert _count(kg, "SELECT COUNT(*) FROM node_aliases WHERE node_id IN ('d1','d2')") == 0
    assert _count(kg, "SELECT COUNT(*) FROM mastery_events WHERE node_id = 'd1'") == 0
    assert _count(kg, "SELECT COUNT(*) FROM doc_node_marks WHERE node_id IN ('d1','d2')") == 0
    # 别课的账本 / 别名必须留着
    assert _count(kg, "SELECT COUNT(*) FROM node_aliases WHERE node_id = 'o1'") == 1
    assert _count(kg, "SELECT COUNT(*) FROM doc_node_marks WHERE node_id = 'o1'") == 1


def test_removes_md_files(kg):
    _seed_two_subjects(kg)
    assert (kg.nodes_dir / "d1.md").exists()

    kg.remove_subject("数据结构")

    assert not (kg.nodes_dir / "d1.md").exists()
    assert (kg.nodes_dir / "o1.md").exists(), "别课的正文不能删"


def test_rejects_blank_subject(kg):
    with pytest.raises(ValueError, match="学科"):
        kg.remove_subject("   ")


def test_empty_subject_is_noop_after_removal(kg):
    """已删空的学科再删一次：计数归零，不抛异常（前端重复点击 / SSE 乱序都安全）。"""
    _seed_two_subjects(kg)
    kg.remove_subject("数据结构")

    r = kg.remove_subject("数据结构")

    assert r["deleted_nodes"] == 0 and r["deleted_edges"] == 0
