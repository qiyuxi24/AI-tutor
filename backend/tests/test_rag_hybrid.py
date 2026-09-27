"""图谱 RAG 混合检索（向量 + BM25 → RRF）—— 2026-09-23 新增能力。

背景：图谱侧原先只有向量一条腿（`TODO.md` 遗留技术债「图谱 RAG 未接入 hybrid_search
双检索」），嵌入一挂就只剩 name/summary 子串兜底。本次把 whoosh BM25 腿接进
`RagManager`，并让**两条索引腿解耦**：BM25 写入不依赖嵌入成功。

覆盖：
1. 解耦：嵌入返回空时节点仍进 BM25，`search` 能命中（且不是"名称匹配"兜底）
2. 融合生效：向量腿无区分度时，BM25 精确命中的节点被 RRF 顶到第一
3. 兜底阶梯第 3 级：两条腿都空 → 仍回退 `_keyword_search`（KG-D5 行为不回归）
4. 删除节点索引时 BM25 一起清
5. `stats` 暴露 sparse_chunks
6. 重新索引替换旧 BM25 分块（不残留旧正文）

隔离：临时图谱目录 + 临时 rag 目录 + 类级 patch `_embed`，零网络、不碰仓库 data/。
"""
import asyncio
from pathlib import Path

import pytest

from app.core.knowledge_graph import KnowledgeGraph
from app.core.rag.manager import RagManager

USER = 9902  # 临时用户，避免与仓库真实数据撞车

STACK_BODY = (
    "# 堆栈\n\n堆栈是后进先出的线性结构，只能在栈顶插入和删除。"
    "函数调用栈与表达式求值都依赖它，递归的实现也离不开堆栈。"
)
QUEUE_BODY = (
    "# 队列\n\n队列是先进先出的线性结构，队尾入队、队头出队。"
    "广度优先搜索与任务调度都依赖它，属于数据结构的基础内容。"
)
RECURSION_BODY = (
    "# 递归\n\n递归是函数调用自身来求解子问题的编程技巧，必须设置终止条件，"
    "否则会无限递归导致栈溢出，这是算法设计中最基础也最容易写错的内容之一。"
)

# 节点 id → 展示名（BM25 腿透传 node_name，断言时要与之一致）
NAMES = {"stack": "堆栈", "queue": "队列", "recursion": "递归"}


async def _no_embed(self, texts):
    """嵌入欠费/失败：返回空列表（与 embed_texts 的降级语义一致）。"""
    return []


async def _const_embed(self, texts):
    """嵌入"正常"但**无区分度**（所有文本同向量）→ 向量腿并列，排序优劣全看 BM25 腿。"""
    return [[1.0, 0.0, 0.0, 0.0] for _ in texts]


def _add(kg: KnowledgeGraph, nid: str, name: str, summary: str, body: str) -> None:
    kg.add_node({"id": nid, "name": name, "summary": summary})
    kg.update_node_content(nid, body, mode="replace")


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    """临时图谱（3 节点 + 正文）+ 把 `KnowledgeGraph` 工厂指向临时目录 + 临时 rag 目录。"""
    kg_dir = tmp_path / "knowledge"
    g = KnowledgeGraph(user_id=USER, data_dir=kg_dir)
    with g._conn:  # nodes.user_id 是外键（PRAGMA foreign_keys=ON），先备 users 行
        g._conn.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (?, ?, ?)",
            (USER, f"rag_hybrid_{USER}", "x"),
        )
    _add(g, "stack", "堆栈", "后进先出的线性结构", STACK_BODY)
    _add(g, "queue", "队列", "先进先出的线性结构", QUEUE_BODY)
    _add(g, "recursion", "递归", "函数调用自身的编程技巧", RECURSION_BODY)
    g.close()

    monkeypatch.setattr(
        "app.core.knowledge_graph.KnowledgeGraph",
        lambda user_id: KnowledgeGraph(user_id=user_id, data_dir=kg_dir),
    )
    return {"kg_dir": kg_dir, "rag_dir": tmp_path / "rag", "user": USER}


def _index_all(mgr: RagManager, env, ids=("stack", "queue", "recursion")) -> None:
    """按给定顺序串行索引（顺序可控，便于断言稳定排序）。"""
    kg = KnowledgeGraph(user_id=env["user"], data_dir=env["kg_dir"])
    try:
        for nid in ids:
            asyncio.run(mgr.index_node(env["user"], {"id": nid, "name": NAMES[nid]}, kg))
    finally:
        kg.close()


# ── 1. 索引两条腿解耦：嵌入失败也进 BM25，且检索不走子串兜底 ──────

def test_bm25_leg_written_when_embed_fails(env, monkeypatch):
    monkeypatch.setattr(RagManager, "_embed", _no_embed)
    mgr = RagManager(data_dir=env["rag_dir"])
    _index_all(mgr, env, ids=("stack",))

    hits = asyncio.run(mgr.search(env["user"], "堆栈"))

    assert hits, "嵌入失败时 BM25 腿应能召回（不再只有子串兜底）"
    assert hits[0]["node_id"] == "stack"
    assert hits[0]["heading"] not in ("名称匹配", "摘要匹配"), "应走 whoosh BM25 腿，而非 _keyword_search"
    assert hits[0]["node_name"] == "堆栈", "BM25 腿应透传节点名（无需回查图谱）"


# ── 2. 融合生效：向量腿分不出高下时，BM25 精确命中者被顶到第一 ──────

def test_hybrid_ranks_bm25_precise_hit_first(env, monkeypatch):
    monkeypatch.setattr(RagManager, "_embed", _const_embed)
    mgr = RagManager(data_dir=env["rag_dir"])
    # 故意让"堆栈"在向量腿里排最后（向量同分 → 稳定排序保持索引顺序）
    _index_all(mgr, env, ids=("queue", "recursion", "stack"))

    # "栈顶"只出现在堆栈正文里 → BM25 腿单侧命中（若换成"后进先出"会与队列的
    # "先进先出"共享 bigram，两路排名互换后 RRF 分数对称，测不出融合的作用）
    hits = asyncio.run(mgr.search(env["user"], "栈顶"))

    assert hits[0]["node_id"] == "stack", "BM25 精确命中应经 RRF 顶到向量腿的首位之上"


# ── 3. 兜底阶梯第 3 级：从未建索引 + 嵌入失败 → _keyword_search ────

def test_keyword_search_fallback_kept_when_both_legs_empty(env, monkeypatch):
    monkeypatch.setattr(RagManager, "_embed", _no_embed)
    mgr = RagManager(data_dir=env["rag_dir"])   # 不调 index_node → 两条腿都空

    hits = asyncio.run(mgr.search(env["user"], "堆栈"))

    assert hits and hits[0]["node_name"] == "堆栈"
    assert hits[0]["heading"] == "名称匹配", "KG-D5 的子串兜底不得回归掉"


# ── 4. 删除节点索引：BM25 腿一起清 ─────────────────────────────

def test_delete_node_index_clears_bm25(env, monkeypatch):
    monkeypatch.setattr(RagManager, "_embed", _no_embed)
    mgr = RagManager(data_dir=env["rag_dir"])
    _index_all(mgr, env, ids=("stack",))
    sparse = mgr._get_sparse(env["user"])
    assert sparse.count() > 0
    assert asyncio.run(mgr.search(env["user"], "栈顶")), "删除前应能召回正文"

    mgr.delete_node_index(env["user"], "stack")

    assert sparse.count() == 0, "删除节点索引必须同时清 BM25 腿"
    # 用"栈顶"（只在正文里）而非"堆栈"（节点名）—— 后者会被 _keyword_search 兜底捞回
    assert asyncio.run(mgr.search(env["user"], "栈顶")) == []


# ── 5. stats 暴露两条腿 ────────────────────────────────────────

def test_stats_exposes_sparse_chunks(env, monkeypatch):
    monkeypatch.setattr(RagManager, "_embed", _const_embed)
    mgr = RagManager(data_dir=env["rag_dir"])
    _index_all(mgr, env, ids=("stack", "queue"))

    st = mgr.stats(env["user"])

    assert st["chunks"] > 0
    assert st["sparse_chunks"] > 0


# ── 6. 重新索引替换旧 BM25 分块 ────────────────────────────────

def test_reindex_replaces_old_bm25_chunks(env, monkeypatch):
    monkeypatch.setattr(RagManager, "_embed", _no_embed)
    mgr = RagManager(data_dir=env["rag_dir"])
    kg = KnowledgeGraph(user_id=env["user"], data_dir=env["kg_dir"])
    # 用两个无共同 bigram 的英文词做标记：中文 bigram 下"旧标记词/新标记词"会共享
    # "标记"、"记词"，搜旧词会命中新正文，测不出替换是否生效
    try:
        kg.update_node_content("stack", "# 堆栈\n\n" + "甲" * 60 + " QWER", mode="replace")
        asyncio.run(mgr.index_node(env["user"], {"id": "stack", "name": "堆栈"}, kg))
        assert asyncio.run(mgr.search(env["user"], "QWER")), "旧正文应可召回"

        kg.update_node_content("stack", "# 堆栈\n\n" + "乙" * 60 + " ZXCV", mode="replace")
        asyncio.run(mgr.index_node(env["user"], {"id": "stack", "name": "堆栈"}, kg))
    finally:
        kg.close()

    assert asyncio.run(mgr.search(env["user"], "QWER")) == [], "旧分块应被替换，不残留"
    assert asyncio.run(mgr.search(env["user"], "ZXCV")), "新正文应可召回"
