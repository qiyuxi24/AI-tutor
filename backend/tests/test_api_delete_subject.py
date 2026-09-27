"""`DELETE /knowledge/graph`（删除整个学科图谱）的 HTTP 层契约断言。

数据层行为（口径同源 / 级联 / 用户隔离）在 `test_kg_delete_subject.py`；这里只锁 API：
- 「未分类」与空学科名一律 400 —— 前者是后端合成的散落节点分组，不提供"整科删除"；
- 该用户建图进行中 → 409（否则建图后段会把刚删掉的节点写回来）；
- 正常删除 → 200 + 计数透传，并逐个清掉该学科节点的 RAG 索引；
- 漏传 subject → 422（框架层必填校验），不是"删全库"。

全离线：KnowledgeGraph 与 rag_manager 均被替换为假实现，不碰 `data/`。
"""
import pytest
from fastapi.testclient import TestClient

from app.api.v1 import kb as kb_module
from app.api.v1 import knowledge as knowledge_module
from app.core.auth import get_current_user

URL = "/api/v1/knowledge/graph"
USER_ID = 5


class _FakeKG:
    """只实现端点用到的方法（`get_nodes_by_subject` / `remove_subject` / `close`）。"""

    def __init__(self, user_id):
        self.user_id = user_id

    def get_nodes_by_subject(self, subject):
        return [{"id": "n1"}, {"id": "n2"}]

    def remove_subject(self, subject):
        return {"subject": subject, "deleted_nodes": 2,
                "deleted_edges": 1, "deleted_themes": 1}

    def close(self):
        pass


@pytest.fixture
def isolate(monkeypatch):
    """假 KG + 假 RAG + 清空模块级互斥锁；返回被清索引的 node_id 列表。"""
    from app.core.rag.manager import rag_manager

    kb_module._GRAPH_INFLIGHT.clear()
    monkeypatch.setattr(knowledge_module, "KnowledgeGraph", _FakeKG)
    indexed: list[str] = []
    monkeypatch.setattr(rag_manager, "delete_node_index",
                        lambda user_id, node_id: indexed.append(node_id))
    try:
        yield indexed
    finally:
        kb_module._GRAPH_INFLIGHT.clear()


@pytest.fixture
def client():
    from app.main import app

    app.dependency_overrides[get_current_user] = lambda: USER_ID
    yield TestClient(app)
    app.dependency_overrides.clear()


# ── 输入校验 ──────────────────────────────────────────

def test_rejects_unclassified(client, isolate):
    r = client.delete(URL, params={"subject": "未分类"})

    assert r.status_code == 400
    assert "未分类" in r.json()["detail"]


def test_rejects_blank_subject(client, isolate):
    assert client.delete(URL, params={"subject": "   "}).status_code == 400


def test_missing_subject_is_422(client, isolate):
    """漏传 subject 是框架层 422，绝不能退化成"删全库"。"""
    assert client.delete(URL).status_code == 422


# ── 并发护栏 ──────────────────────────────────────────

def test_rejects_while_building_graph(client, isolate):
    kb_module._GRAPH_INFLIGHT.add(USER_ID)

    r = client.delete(URL, params={"subject": "数据结构"})

    assert r.status_code == 409
    assert "建图" in r.json()["detail"]


# ── 正常删除 ──────────────────────────────────────────

def test_deletes_and_clears_rag_index(client, isolate):
    r = client.delete(URL, params={"subject": "数据结构"})

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["subject"] == "数据结构"
    assert (body["deleted_nodes"], body["deleted_edges"]) == (2, 1)
    assert isolate == ["n1", "n2"], "该学科每个节点的 RAG 索引都要清"
