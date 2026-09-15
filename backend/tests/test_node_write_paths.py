"""节点写入收口：四条写路径全部落 `KnowledgeGraph.create_node_with_content()`。

背景：原先「写一个节点 MD」有 4 套内联模板散在 knowledge_writer / kb/graph_generator /
api/v1/knowledge.py（create_node、decompose），改一处忘一处
（见 `docs/知识图谱_模块结构与封装调研.md` §3.3 / §7 第二步）。

两条防线：
1. 行为 —— 模板只剩一份（来源标注降级成参数）、ID 冲突不留孤儿 MD、同名并轨不重复建节点；
2. 结构 —— 四条路径各自**原样传递自己的 origin**。将来有人新增第五条写路径时，
   这条断言会逼他复用同一个模板，而不是再内联一套。

全离线：不碰 LLM（各路径的归属判定被替换为直通），不碰真实 data/ 目录。
"""
import asyncio

import pytest

from app.api.v1 import knowledge as kapi
from app.core import knowledge_writer as kw
from app.core.kb import graph_generator as gg
from app.core.knowledge_graph import KnowledgeGraph


@pytest.fixture
def kg(tmp_path):
    g = KnowledgeGraph(user_id=1, data_dir=tmp_path)
    with g._conn:  # nodes.user_id 是外键，先备 users 行
        g._conn.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (1, 'w', 'x')")
    yield g
    g.close()


@pytest.fixture
def origins(monkeypatch):
    """记录四条路径实际传给原子方法的 origin（结构断言用）"""
    seen = []
    monkeypatch.setattr(KnowledgeGraph, "create_node_with_content",
                        lambda self, node_data, content="", origin="manual":
                        seen.append(origin))
    return seen


def _md(kg: KnowledgeGraph, node_id: str) -> str:
    return (kg.nodes_dir / f"{node_id}.md").read_text(encoding="utf-8")


# ── 行为 ──────────────────────────────────────────────

def test_skeleton_md_uses_single_template(kg):
    """无正文的骨架节点：模板由 ORIGIN_NOTES 决定，摘要附在来源标注下"""
    kg.create_node_with_content({"id": "n1", "name": "队列", "summary": "先进先出"},
                                origin="manual")

    assert _md(kg, "n1") == "# 队列\n\n> 手动创建\n> 先进先出\n\n## 概述\n\n待完善...\n"
    assert kg.get_node("n1") is not None


def test_full_markdown_written_verbatim(kg):
    """正文已是完整文档（以 # 开头）→ 原样落盘，不再套模板"""
    doc = "# 队列\n\n> 我自己写的\n\n正文"
    kg.create_node_with_content({"id": "n1", "name": "队列"}, doc, origin="ai")

    assert _md(kg, "n1") == doc


def test_id_conflict_raises_and_leaves_no_orphan_md(kg):
    """ID 冲突（全局主键）→ 抛 ValueError，且不覆盖已有 MD"""
    kg.create_node_with_content({"id": "n1", "name": "队列"}, origin="manual")

    with pytest.raises(ValueError):
        kg.create_node_with_content({"id": "n1", "name": "另一个概念"}, origin="book")

    assert _md(kg, "n1") == "# 队列\n\n> 手动创建\n\n## 概述\n\n待完善...\n"


def test_ai_path_merges_same_name_instead_of_duplicating(kg):
    """同名不同 ID → 并轨到已有节点（实测重复来源：鸿蒙开发入门 ×2 个 ID）"""
    kw.create_node_from_ai(kg, "harmony", "鸿蒙开发入门", summary="入门",
                           subject="移动开发", board="基础")
    kw.create_node_from_ai(kg, "harmonyos_intro", "鸿蒙开发入门", content="补充内容")

    assert [n["id"] for n in kg.nodes] == ["harmony"], "同名新 ID 不该建出第二个节点"
    assert "> 由 AI 自动创建" in _md(kg, "harmony")
    assert "补充内容" in _md(kg, "harmony"), "第二次的内容应并入已有节点的 MD"


# ── 结构：四条路径都走同一个落点 ────────────────────────

def test_ai_write_layer_origin(kg, origins):
    kw.create_node_from_ai(kg, "n1", "队列", subject="数据结构", board="线性表")
    assert origins == ["ai"]


def test_book_generation_origin(kg, origins, monkeypatch):
    gen = gg.GraphGenerator(user_id=1)
    monkeypatch.setattr(gen, "_find_dedup_candidates", _async({}))
    monkeypatch.setattr(gen, "_confirm_synonyms", _async({}))

    asyncio.run(gen._write_to_graph(kg, "数据结构", {"nodes": [{"id": "n1", "name": "队列"}],
                                                     "edges": []},
                                    existing_nodes=[]))

    assert origins == ["book"]


def test_manual_api_origin(kg, origins, monkeypatch):
    _patch_api_user(kg, monkeypatch)
    asyncio.run(kapi.create_node(data={"id": "n1", "name": "队列"}, user_id=1))

    assert origins == ["manual"]


def test_decompose_api_origin(kg, origins, monkeypatch):
    _patch_api_user(kg, monkeypatch)

    class _FakeAnalyzer:
        def __init__(self, kg_):
            pass

        async def decompose_question(self, question):
            return {"target": "n1", "nodes": [{"id": "n1", "name": "队列"}], "edges": []}

    monkeypatch.setattr(kapi, "GraphAnalyzer", _FakeAnalyzer)
    asyncio.run(kapi.decompose_question(kapi.DecomposeRequest(question="怎么学队列"), user_id=1))

    assert origins == ["decompose"]


# ── 工具 ──────────────────────────────────────────────

def _async(value):
    async def _fn(*args, **kwargs):
        return value
    return _fn


def _patch_api_user(kg, monkeypatch):
    """让路由用测试实例、且不真的关掉它；归属判定直通（不调 LLM）"""
    monkeypatch.setattr(kapi, "KnowledgeGraph", lambda user_id: kg)
    monkeypatch.setattr(KnowledgeGraph, "close", lambda self: None)
    monkeypatch.setattr(kapi, "assign_taxonomy", _async(None))
    monkeypatch.setattr(kapi, "publish", lambda *a, **kw: None)
