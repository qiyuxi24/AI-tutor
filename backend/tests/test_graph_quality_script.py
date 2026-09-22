"""体检脚本（GQ-3 / GQ-4）：判重口径与合并计划必须有断言，不能只靠肉眼看输出。

判重口径三处一致：写入层并轨（`KnowledgeGraph.find_node_by_name`）、Agent 写路径、
以及本脚本的存量体检 —— 所以这里锁的是"归一化 + 同学科 + 同用户"这三条边界。
"""
from app.core.knowledge_graph import KnowledgeGraph
from scripts.inspect_graph_quality import (
    dupe_groups,
    fuzzy_pairs,
    load_graph,
    merge_dupes,
    plan_merge,
)


def _node(nid: str, name: str, user_id: int = 1, subject: str = "数据结构", **kw) -> dict:
    """模拟 SQL 行：tags 是 JSON 字符串（脚本的数据来源是 sqlite 行）"""
    node = {"id": nid, "name": name, "user_id": user_id,
            "tags": f'["{subject}"]', "summary": "", "board": "",
            "confidence": None, "mastery": 0}
    node.update(kw)
    return node


def test_dupe_groups_respects_user_and_subject():
    """归一化同名才是同名；跨用户/跨学科不算（「树」在数据结构与操作系统是两回事）"""
    nodes = [
        _node("a", "队列"),
        _node("b", "队列（复习）"),            # 归一化后同名
        _node("c", "队列", user_id=2),          # 别的用户
        _node("d", "队列", subject="操作系统"),  # 别的学科
        _node("e", "栈"),
    ]

    groups = dupe_groups(nodes)

    assert len(groups) == 1
    assert sorted(n["id"] for n in groups[0]["nodes"]) == ["a", "b"]


def test_fuzzy_pairs_skips_exact_same_name():
    """完全同名归 L1，不重复报；只报字符串相近（含包含关系）但不同名的对"""
    nodes = [_node("a", "队列"), _node("b", "队列"), _node("c", "队列结构与实现")]
    pairs = fuzzy_pairs(nodes)

    assert [tuple(sorted((x["id"], y["id"]))) for x, y, _ in pairs] == [("a", "c"), ("b", "c")]


def test_plan_merge_prefers_non_fragment_name(tmp_path):
    """保留者优先取非碎片名（id 更干净），正文短的也会被并进去"""
    nodes_dir = tmp_path / "nodes" / "1"
    nodes_dir.mkdir(parents=True)
    (nodes_dir / "a.md").write_text("# 队列\n\n> 来源\n\n短正文", encoding="utf-8")
    (nodes_dir / "b.md").write_text("# 队列（复习）\n\n> 来源\n\n" + "长正文" * 50,
                                    encoding="utf-8")

    group = dupe_groups([_node("a", "队列"), _node("b", "队列（复习）")])
    plan = plan_merge(group, tmp_path / "nodes")

    assert plan[0]["keep"]["id"] == "a", "碎片名（复习）即使是长正文也不当保留者"
    assert [n["id"] for n in plan[0]["drop"]] == ["b"]


def test_merge_dupes_redirects_edges_and_deletes_redundant(tmp_path):
    """合并 = 边重定向 + 删冗余节点 + 删 MD + 正文并入（存量修复的完整动作）"""
    kg = KnowledgeGraph(user_id=1, data_dir=tmp_path)
    with kg._conn:  # nodes.user_id 是外键，先备 users 行
        kg._conn.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (1, 'w', 'x')")
    kg.create_node_with_content({"id": "stack", "name": "栈", "tags": ["数据结构"]},
                                origin="book")
    kg.create_node_with_content({"id": "queue", "name": "队列", "tags": ["数据结构"]},
                                "正文A", origin="book")
    kg.create_node_with_content({"id": "other", "name": "其他", "tags": ["数据结构"]},
                                origin="book")
    kg.add_node({"id": "queue_v2", "name": "队列", "tags": ["数据结构"]})  # 老库遗留重复
    (kg.nodes_dir / "queue_v2.md").write_text("# 队列\n\n> 来源\n\n正文B", encoding="utf-8")
    kg.add_edge({"from": "stack", "to": "queue_v2", "relation": "related"}, caller="human")
    kg.add_edge({"from": "queue_v2", "to": "other", "relation": "related"}, caller="human")
    kg.close()

    db_path = tmp_path / "knowledge.db"
    nodes, _ = load_graph(db_path)
    plan = plan_merge(dupe_groups(nodes), tmp_path / "nodes")
    stats = merge_dupes(db_path, plan)

    assert stats["merged"] == 1
    assert stats["edges_moved"] == 2
    assert stats["content_appended"] == 1
    assert not (tmp_path / "nodes" / "1" / "queue_v2.md").exists(), "冗余节点的 MD 要删掉"

    after = KnowledgeGraph(user_id=1, data_dir=tmp_path)
    try:
        assert sorted(n["id"] for n in after.nodes) == ["other", "queue", "stack"]
        assert {(e["from_node"], e["to_node"]) for e in after.edges} == {
            ("stack", "queue"), ("queue", "other")}
        assert "正文B" in after.get_node_content_preview("queue", max_chars=10_000)
    finally:
        after.close()
