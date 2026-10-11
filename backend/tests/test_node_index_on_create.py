"""P0-1 创建即入索引：各写路径「建完 → **立刻**可被图谱检索命中」（全离线）。

背景（《知识图谱加载与注入_诊断报告》§2.2）：全仓库 `rag_manager.index_node` 原先只有
一个调用点（后台图谱分析），工具建节点 / 手动建节点 / 问题拆解 / 书籍建图四条写路径
建完都不入索引 → 图谱"按需检索"（S6）长期接近全空（某用户 122 节点只有 5 个可检索）。

本文件钉住收口 `core/rag/node_indexer`：
- 写路径在 `KnowledgeGraph` 出口 `mark_dirty`，各自在合适时机 flush；
- 四条路径建/改完，`rag_manager.search` 必须能命中该节点（不等后台分析）。

隔离：临时 `KnowledgeGraph` + 临时 `RagManager`（`_embed` 置空，零网络）+ 归属判定直通。
"""
import asyncio

import pytest

from app.api.v1 import knowledge as kapi
from app.core import knowledge_writer as kw
from app.core.agent_tools.tools import add_knowledge_node as akn
from app.core.agent_tools.tools import update_node_content as unoc
from app.core.kb import graph_generator as gg
from app.core.knowledge_graph import KnowledgeGraph
from app.core.rag.manager import RagManager

USER = 9911

HANOI_BODY = (
    "# 探针_汉诺塔\n\n"
    "汉诺塔是递归思想的经典示例，规则为每次只能移动一个盘子，且大盘不能压在小盘上。\n\n"
    "## 递归解法\n"
    "把 n 个盘子从 A 柱移到 C 柱：先把上方的 n-1 个盘子移到辅助柱 B，"
    "再把最大的盘子从 A 移到 C，最后把那 n-1 个盘子从 B 移到 C。\n"
)
SORT_BODY = (
    "# 排序\n\n排序是把任意序列调整为有序序列的算法族，常见有冒泡、快排与归并。\n"
)


def _async(value):
    async def _fn(*args, **kwargs):
        return value
    return _fn


@pytest.fixture
def env(tmp_path, monkeypatch):
    """临时图谱 + 临时 RAG 管理器（实例级空嵌入）。"""
    kg = KnowledgeGraph(user_id=USER, data_dir=tmp_path)
    with kg._conn:  # nodes.user_id 是外键，先备 users 行
        kg._conn.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (?, ?, ?)",
            (USER, "idx_create", "x"),
        )
    mgr = RagManager(data_dir=tmp_path / "rag")

    async def _no_embed(texts):
        return []

    mgr._embed = _no_embed              # 实例级：隔离网络，只验证"索引有没有被写"
    monkeypatch.setattr("app.core.rag.manager.rag_manager", mgr)
    # 归属判定直通（不调 LLM / 不读真实库）
    monkeypatch.setattr(kw, "assign_taxonomy_sync", lambda kg_, node: None)
    return {"kg": kg, "mgr": mgr}


def _hits(env, query):
    return asyncio.run(env["mgr"].search(USER, query))


def _assert_hit(env, node_id, query):
    env["mgr"]._get_sparse(USER)        # 触发索引目录就绪
    ids = {h["node_id"] for h in _hits(env, query)}
    assert node_id in ids, f"建完立刻应可被检索命中：{node_id} / {query!r}，实际 {ids}"


# ── 路径 ①：Agent 工具 add_knowledge_node ─────────────────────────

def test_tool_create_indexes_immediately(env):
    kg = env["kg"]

    out = akn.handler({
        "id": "hanoi", "name": "探针_汉诺塔", "tags": ["递归"],
        "subject": "数据结构", "board": "递归与分治",
        "summary": "汉诺塔递归解法的探针节点", "content": HANOI_BODY,
    }, kg)

    assert "已创建节点" in out
    assert env["mgr"].stats(USER)["sparse_chunks"] > 0, "建完 BM25 腿应从 0 变正"
    _assert_hit(env, "hanoi", "汉诺塔")


# ── 路径 ②：改正文（工具 update_node_content）→ 重索引 ─────────────

def test_tool_content_update_reindexes(env):
    kg = env["kg"]
    akn.handler({"id": "hanoi", "name": "探针_汉诺塔", "tags": ["递归"],
                 "subject": "数据结构", "summary": "探针", "content": HANOI_BODY}, kg)

    unoc.handler({"node_id": "hanoi", "op": "replace",
                  "content": "# 探针_汉诺塔\n\n改写后的正文引入新标记 ZXCV 用于检索验证。"}, kg)

    _assert_hit(env, "hanoi", "ZXCV")


# ── 路径 ③：手动建节点 POST /knowledge/node ──────────────────────

def test_manual_api_indexes_immediately(env, monkeypatch):
    kg = env["kg"]
    monkeypatch.setattr(kapi, "KnowledgeGraph", lambda user_id: kg)
    monkeypatch.setattr(KnowledgeGraph, "close", lambda self: None)
    monkeypatch.setattr(kapi, "assign_taxonomy", _async(None))
    monkeypatch.setattr(kapi, "broadcast", lambda *a, **kw: None)

    resp = asyncio.run(kapi.create_node(
        data={"id": "sort", "name": "排序", "subject": "算法",
              "summary": "排序算法族", "content": SORT_BODY},
        user_id=USER))

    assert resp["status"] == "ok"
    _assert_hit(env, "sort", "排序")


# ── 路径 ④：问题拆解 POST /knowledge/decompose ────────────────────

def test_decompose_api_indexes_immediately(env, monkeypatch):
    kg = env["kg"]
    monkeypatch.setattr(kapi, "KnowledgeGraph", lambda user_id: kg)
    monkeypatch.setattr(KnowledgeGraph, "close", lambda self: None)
    monkeypatch.setattr(kapi, "assign_taxonomy", _async(None))
    monkeypatch.setattr(kapi, "broadcast", lambda *a, **kw: None)

    class _FakeAnalyzer:
        def __init__(self, kg_):
            pass

        async def decompose_question(self, question):
            return {"target": {"id": "quicksort", "name": "快速排序"},
                    "nodes": [{"id": "quicksort", "name": "快速排序",
                               "tags": ["算法"], "summary": "分治排序"}],
                    "edges": []}

    monkeypatch.setattr(kapi, "GraphAnalyzer", _FakeAnalyzer)

    asyncio.run(kapi.decompose_question(
        kapi.DecomposeRequest(question="快速排序怎么学"), user_id=USER))

    _assert_hit(env, "quicksort", "快速排序")


# ── 路径 ⑤：书籍建图（整批一次 flush）─────────────────────────────

def test_book_generation_indexes_after_generate(env, monkeypatch):
    kg = env["kg"]

    def _fake_load(user_id, node_ids):          # 生产实现是**同步** staticmethod
        return [{"node_id": 1, "name": "算法书", "text": "正文" * 50}]

    async def _fake_process(self, kg_, subject, book, board, aggregate):
        kg_.create_node_with_content(
            {"id": "sorting", "name": "排序", "file": "nodes/sorting.md",
             "tags": ["算法"], "subject": "算法", "summary": "排序算法族"},
            SORT_BODY, origin="book")
        aggregate["created_nodes"].append("sorting")

    monkeypatch.setattr(gg.GraphGenerator, "_load_book_texts", staticmethod(_fake_load))
    monkeypatch.setattr(gg.GraphGenerator, "_process_book", _fake_process)
    # 不碰真实 KB（_resolve_files 会查 kb_manager 建库）
    monkeypatch.setattr(gg.GraphGenerator, "_resolve_files", lambda self, ids: ([1], ""))

    asyncio.run(gg.GraphGenerator(USER).generate_graph(kg, "算法", [1]))

    _assert_hit(env, "sorting", "排序")


# ── 反证：不 flush 就不该有索引（保证断言真的在测收口）──────────────

def test_no_flush_means_not_searchable(env):
    kg = env["kg"]
    kw.create_node_from_ai(kg, "ghost", "幽灵节点", subject="算法", summary="未落索引")
    # 只登记 dirty、未 flush → BM25 腿仍为空
    assert env["mgr"].stats(USER)["sparse_chunks"] == 0
