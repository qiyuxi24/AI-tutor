"""删除节点/学科时同步清 RAG 索引 —— 收口在 `KnowledgeGraph`，覆盖所有删除路径。

背景（硬伤，2026-10-11）：`remove_node` / `remove_subject` 原先只删 MD + nodes 行 +
题库孤儿题，**不清 RAG 索引**；清索引的 `rag_manager.delete_node_index` 只有 API 层
两处显式调用，于是 Agent 的 `delete_node` 工具、AI 建议采纳（`merge_ai_suggestion`）
这两个「走 kg 直删」的路径会把节点删掉却留下索引 → 已删知识点仍被 S6 按需检索注入。
收口到 kg 层后，所有删除路径自动覆盖，调用方无需再记得手调。

全离线：conftest 已把 rag_manager 隔离成 tmp 实例，这里再换成记录调用的假实现。
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


@pytest.fixture
def rag_calls(monkeypatch):
    """记录 delete_node_index 调用（user_id, node_id）。"""
    from app.core.rag import manager as rag_mod

    calls: list[tuple] = []
    monkeypatch.setattr(rag_mod.rag_manager, "delete_node_index",
                        lambda user_id, node_id: calls.append((user_id, node_id)))
    return calls


def test_remove_node_purges_rag_index(kg, rag_calls):
    kg.create_node_with_content({"id": "n1", "name": "栈", "subject": "数据结构"}, "正文")

    kg.remove_node("n1")

    assert rag_calls == [(1, "n1")], "删节点必须清掉它的 RAG 索引"


def test_remove_subject_purges_rag_index_for_each_node(kg, rag_calls):
    kg.create_node_with_content({"id": "d1", "name": "数组", "subject": "数据结构"}, "x")
    kg.create_node_with_content({"id": "d2", "name": "链表", "subject": "数据结构"}, "x")
    kg.create_node_with_content({"id": "o1", "name": "进程", "subject": "操作系统"}, "x")

    kg.remove_subject("数据结构")

    assert sorted(rag_calls) == [(1, "d1"), (1, "d2")], "只清本学科节点，别课不动"


def test_rag_purge_failure_does_not_block_delete(kg, rag_calls, monkeypatch):
    """清索引是增强项：失败只记 warning，绝不阻断删图（图谱已删才是主操作）。"""
    from app.core.rag import manager as rag_mod

    def _boom(user_id, node_id):
        raise RuntimeError("索引库损坏")

    monkeypatch.setattr(rag_mod.rag_manager, "delete_node_index", _boom)
    kg.create_node_with_content({"id": "n1", "name": "栈", "subject": "数据结构"}, "x")

    kg.remove_node("n1")           # 不抛异常

    assert kg.get_node("n1") is None
