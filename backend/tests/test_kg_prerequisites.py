"""
先修边方向的回归测试

背景：`get_prerequisites` 曾把 SQL 方向写反——沿 `from_node = ?` 取 `to_node`，
拿到的其实是「后继」而不是「前置」，导致 `get_next_to_learn` 把更高级的内容
当作该巩固的先修推荐、`get_learning_path(target)` 丢掉目标的整条前置链。

全库统一语义（add_edge / topological_sort / knowledge_writer / graph_generator
四处一致）：**A → B (prerequisite) 表示 A 是 B 的前置，必须先学 A**。
因此「取某节点的前置」必须沿边反向走。

本文件钉住两处同方向约定：KnowledgeGraph.get_prerequisites 与
RagManager._expand_prerequisites（图谱检索扩跳）。
"""
import pytest

from app.core.knowledge_graph import KnowledgeGraph


@pytest.fixture
def kg(tmp_path):
    """链：函数调用 → 递归 ← 终止条件；递归 → 记忆化 → 动态规划"""
    g = KnowledgeGraph(user_id=1, data_dir=tmp_path)
    with g._conn:  # nodes.user_id 是外键，先备 users 行
        g._conn.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (1, 'prereq', 'x')")
    for nid, name, mastery in [
        ("func", "函数调用", 90),
        ("base", "终止条件", 85),
        ("rec", "递归", 70),
        ("memo", "记忆化", 20),
        ("dp", "动态规划", 0),
    ]:
        g.add_node({"id": nid, "name": name, "tags": ["算法"], "mastery": mastery})
        # 扩跳要读节点内容预览，无内容会被跳过
        g.update_node_content(nid, f"# {name}\n\n这是{name}的知识点说明。", mode="replace")
    for src, dst in [("func", "rec"), ("base", "rec"), ("rec", "memo"), ("memo", "dp")]:
        g.add_edge({"from": src, "to": dst, "relation": "prerequisite"}, caller="human")
    yield g
    g.close()


def test_get_prerequisites_returns_ancestors(kg):
    """返回的是祖先（前置），绝不能是后代（后继）"""
    assert set(kg.get_prerequisites("rec")) == {"func", "base"}
    assert set(kg.get_prerequisites("dp")) == {"memo", "rec", "func", "base"}
    assert kg.get_prerequisites("func") == []            # 根节点没有前置
    assert "memo" not in kg.get_prerequisites("rec")     # 后继不得混入
    assert "dp" not in kg.get_prerequisites("rec")


def test_expand_prerequisites_walks_backward(kg, monkeypatch):
    """图谱检索扩跳补的是前置知识，不是进阶内容"""
    from app.core.rag import manager as rag_manager_mod

    # _expand_prerequisites 内部按 user_id 新建 KG，这里换成隔离实例
    monkeypatch.setattr("app.core.knowledge_graph.KnowledgeGraph", lambda **_kw: kg)

    mgr = rag_manager_mod.RagManager()
    extra = mgr._expand_prerequisites(1, [{"node_id": "rec", "score": 1.0}],
                                      top_k=5, hops=1)
    assert {e["node_id"] for e in extra} == {"func", "base"}
    # 分数按 decay^depth 衰减，保证排在语义初检之后
    assert all(e["score"] < 1.0 for e in extra)
