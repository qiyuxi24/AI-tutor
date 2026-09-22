"""节点写入收口：四条写路径全部落 `KnowledgeGraph.create_node_with_content()`。

背景：原先「写一个节点 MD」有 4 套内联模板散在 knowledge_writer / kb/graph_generator /
api/v1/knowledge.py（create_node、decompose），改一处忘一处
（见 `docs/知识图谱/知识图谱_模块结构与封装调研.md` §3.3 / §7 第二步）。

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
from app.core.kb.embedder import HashEmbedder
from app.core.knowledge_graph import KnowledgeGraph, normalize_node_name


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


# ── GQ-1：写入层同名合并（所有写路径共享）──────────────

def test_create_node_returns_landing_id(kg):
    """返回值 = 实际落点 ID，并轨时是已有节点 ID（调用方拿它建边/回执）"""
    assert kg.create_node_with_content({"id": "n1", "name": "队列"}) == "n1"
    assert kg.create_node_with_content({"id": "n2", "name": "队列"}, origin="book") == "n1"

    assert [n["id"] for n in kg.nodes] == ["n1"]


def test_same_name_merge_normalizes(kg):
    """归一化判重：空白 / 全角括号 / 碎片后缀都不该长成第二个节点"""
    assert normalize_node_name("奖励函数（扩展）") == "奖励函数"
    assert normalize_node_name("Bar_Reward_Function_Extended") == "bar_reward_function"
    assert normalize_node_name("复习") == "复习", "整名就是碎片词时不剥成空串"

    kg.create_node_with_content({"id": "a", "name": "奖励 函数", "tags": ["强化学习"]},
                                origin="book")
    assert kg.create_node_with_content({"id": "b", "name": "奖励函数（扩展）",
                                        "tags": ["强化学习"]},
                                       origin="book") == "a"
    assert [n["id"] for n in kg.nodes] == ["a"]


def test_same_name_merge_keeps_subjects_apart(kg):
    """不同学科的「树」是两个概念：学科不同不并轨"""
    kg.create_node_with_content({"id": "ds_tree", "name": "树", "tags": ["数据结构"]},
                                origin="book")
    assert kg.create_node_with_content({"id": "os_tree", "name": "树", "tags": ["操作系统"]},
                                       origin="book") == "os_tree"
    assert sorted(n["id"] for n in kg.nodes) == ["ds_tree", "os_tree"]


def test_book_path_merges_same_name_instead_of_duplicating(kg, monkeypatch):
    """建图路径（书籍）同名不同 ID → 并轨，边跟着指向已有节点（GQ-1 原漏洞 #4）"""
    gen = gg.GraphGenerator(user_id=1)
    monkeypatch.setattr(gen, "_find_dedup_candidates", _async({}))
    monkeypatch.setattr(gen, "_confirm_synonyms", _async({}))

    asyncio.run(gen._write_to_graph(
        kg, "数据结构",
        {"nodes": [{"id": "stack", "name": "栈"}, {"id": "queue", "name": "队列"}],
         "edges": []},
        existing_nodes=[]))
    stats = asyncio.run(gen._write_to_graph(
        kg, "数据结构",
        {"nodes": [{"id": "queue_v2", "name": "队列", "content": "补充：循环队列"}],
         "edges": [{"from": "stack", "to": "queue_v2", "relation": "related"}]},
        existing_nodes=[]))

    assert sorted(n["id"] for n in kg.nodes) == ["queue", "stack"]
    assert stats["merged_nodes"] == ["队列"]
    assert stats["created_nodes"] == []
    assert {(e["from_node"], e["to_node"]) for e in kg.edges} == {("stack", "queue")}


def test_book_path_merge_is_idempotent(kg, monkeypatch):
    """同一本书重跑：同一段正文不叠两遍（节点 MD 不线性膨胀）"""
    gen = gg.GraphGenerator(user_id=1)
    monkeypatch.setattr(gen, "_find_dedup_candidates", _async({}))
    monkeypatch.setattr(gen, "_confirm_synonyms", _async({}))
    payload = {"nodes": [{"id": "queue", "name": "队列", "content": "循环队列：队尾追上队头"}],
               "edges": []}

    for nid in ("queue", "queue_v2", "queue_v3"):
        asyncio.run(gen._write_to_graph(
            kg, "数据结构",
            {"nodes": [dict(payload["nodes"][0], id=nid)], "edges": []},
            existing_nodes=[]))

    assert _md(kg, "queue").count("循环队列：队尾追上队头") == 1


def test_dedup_status_reported_when_embed_unavailable(kg, monkeypatch):
    """嵌入不可用/退化不得静默：状态要带进 stats（GQ-2）"""
    gen = gg.GraphGenerator(user_id=1)
    monkeypatch.setattr(gen, "_confirm_synonyms", _async({}))
    existing = [{"id": "stack", "name": "栈", "tags": ["数据结构"]}]
    payload = {"nodes": [{"id": "queue", "name": "队列"}], "edges": []}

    monkeypatch.setattr(gg, "get_embedder", lambda **kw: _DeadEmbedder())
    stats = asyncio.run(gen._write_to_graph(kg, "数据结构", payload, existing_nodes=existing))
    assert stats["dedup_status"] == "unavailable"

    # hash 兜底（无 key / 无本地模型）算退化，不算不可用
    monkeypatch.setattr(gg, "get_embedder", lambda **kw: HashEmbedder())
    stats = asyncio.run(gen._write_to_graph(kg, "数据结构", payload, existing_nodes=existing))
    assert stats["dedup_status"] == "degraded"


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


class _DeadEmbedder:
    """嵌入服务不可用的替身（欠费/网络故障都长这样：返回空列表）"""
    name = "dead"

    def embed(self, texts):
        return []


def _patch_api_user(kg, monkeypatch):
    """让路由用测试实例、且不真的关掉它；归属判定直通（不调 LLM）"""
    monkeypatch.setattr(kapi, "KnowledgeGraph", lambda user_id: kg)
    monkeypatch.setattr(KnowledgeGraph, "close", lambda self: None)
    monkeypatch.setattr(kapi, "assign_taxonomy", _async(None))
    monkeypatch.setattr(kapi, "publish", lambda *a, **kw: None)
