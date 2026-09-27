"""KG-D5 —— 图谱按需检索的「关键词兜底」与 chunk_index 修复（TDD）。

需求规格：docs/知识图谱/知识图谱_数据结构与检索通路_评审与改良方案.md
  §5.2（方案 A：嵌入不可用时按 name/summary 关键词召回） / §6 KG-D5。

背景：图谱检索原先只有向量一条腿，嵌入 API 欠费/失败时 `RagManager.search()`
直接返回空 → 整个按需检索静默失效。本文件钉住补上的第二条腿。

覆盖：
1. 兜底生效：嵌入返回空 → 按节点 name 命中，heading=="名称匹配"
2. 摘要命中：query 只出现在某节点 summary → heading=="摘要匹配"
3. 不命中就空：query 与任何 name/summary 都不沾边 → []
4. 不回归：嵌入正常 → 仍走向量路径（命中项不带"名称匹配"这类 heading）
5. chunk_index：`chunk_markdown` 节点内全局递增（0..n-1 两两不同）

隔离：临时 data_dir + 临时用户 + 类级 patch `_embed`，零网络、不碰仓库 data/。
"""
import asyncio
from pathlib import Path

import pytest

from app.core.knowledge_graph import KnowledgeGraph
from app.core.rag.chunker import chunk_markdown
from app.core.rag.manager import RagManager


USER = 9901  # 临时用户，避免与仓库真实数据撞车


async def _no_embed(self, texts):
    """模拟嵌入欠费/失败：返回空列表（非异常，与 embed_texts 的降级语义一致）。"""
    return []


async def _const_embed(self, texts):
    """模拟嵌入"正常"：返回与索引同维的固定向量（余弦恒为 1，稳妥走向量路径）。"""
    return [[1.0, 0.0, 0.0, 0.0] for _ in texts]


def _add(kg: KnowledgeGraph, nid: str, name: str, summary: str, body: str) -> None:
    """建节点 + 写 MD 正文（正文让 get_node_content_preview 有内容可读）。"""
    kg.add_node({"id": nid, "name": name, "summary": summary})
    kg.update_node_content(nid, body, mode="replace")


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    """临时图谱（3 节点 + 正文）+ 把 `KnowledgeGraph` 工厂指向该临时目录 + 临时 rag 目录。

    `search()`/`_keyword_search()` 内部按 `KnowledgeGraph(user_id=...)` 新建实例
    （不传 data_dir，默认仓库 data/）→ 这里用 monkeypatch 冒名顶替，确保测试
    全程只读写 tmp_path，不污染仓库真实 data/。
    """
    kg_dir = tmp_path / "knowledge"
    g = KnowledgeGraph(user_id=USER, data_dir=kg_dir)
    with g._conn:  # nodes.user_id 是外键（PRAGMA foreign_keys=ON），先备 users 行
        g._conn.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (?, ?, ?)",
            (USER, f"kw_fallback_{USER}", "x"),
        )
    _add(
        g, "stack", "堆栈", "后进先出的线性结构，只能在栈顶操作",
        "# 堆栈\n\n堆栈是后进先出的线性结构。插入与删除都只发生在栈顶，"
        "常用于函数调用栈与表达式求值。堆栈这个知识点是数据结构的基础。",
    )
    _add(
        g, "queue", "队列", "先进先出的线性结构，队尾入、队头出",
        "# 队列\n\n队列是先进先出的线性结构。元素从队尾入队、从队头出队，"
        "常用于广度优先搜索与任务调度。队列这个知识点是数据结构的基础。",
    )
    _add(
        g, "recursion", "递归", "函数调用自身的编程技巧，需要终止条件",
        "# 递归\n\n递归是函数调用自身来求解子问题的编程技巧，必须设置终止条件，"
        "否则会无限递归导致栈溢出。递归这个知识点是算法设计的基础。",
    )
    g.close()

    monkeypatch.setattr(
        "app.core.knowledge_graph.KnowledgeGraph",
        lambda user_id: KnowledgeGraph(user_id=user_id, data_dir=kg_dir),
    )
    return {"kg_dir": kg_dir, "rag_dir": tmp_path / "rag", "user": USER}


# ── 1. 兜底生效（名称命中）──────────────────────────────────

def test_fallback_name_hit_when_embed_empty(env, monkeypatch):
    monkeypatch.setattr(RagManager, "_embed", _no_embed)
    mgr = RagManager(data_dir=env["rag_dir"])

    hits = asyncio.run(mgr.search(env["user"], "堆栈"))

    assert hits, "嵌入失败时不得再返回空——应走关键词兜底"
    top = hits[0]
    assert top["node_name"] == "堆栈"
    assert top["heading"] == "名称匹配"
    assert top["score"] == 0.35
    assert top["content"].strip()          # content 来自 MD 预览，非空
    assert set(top.keys()) == {"node_id", "node_name", "heading", "content", "score"}


# ── 2. 摘要命中 ────────────────────────────────────────────

def test_fallback_summary_hit(env, monkeypatch):
    monkeypatch.setattr(RagManager, "_embed", _no_embed)
    mgr = RagManager(data_dir=env["rag_dir"])

    # "调用自身" 只出现在"递归"的 summary 里，不在任何节点 name 里
    hits = asyncio.run(mgr.search(env["user"], "调用自身"))

    assert len(hits) == 1
    assert hits[0]["node_name"] == "递归"
    assert hits[0]["heading"] == "摘要匹配"
    assert hits[0]["score"] == 0.30


# ── 3. 不命中就空（不抛错）──────────────────────────────────

def test_fallback_no_match_returns_empty(env, monkeypatch):
    monkeypatch.setattr(RagManager, "_embed", _no_embed)
    mgr = RagManager(data_dir=env["rag_dir"])

    assert asyncio.run(mgr.search(env["user"], "量子纠缠退相干")) == []


# ── 4. 不回归：嵌入正常仍走向量路径 ─────────────────────────

def test_vector_path_unchanged_when_embed_ok(env, monkeypatch):
    monkeypatch.setattr(RagManager, "_embed", _const_embed)
    mgr = RagManager(data_dir=env["rag_dir"])

    # 用真 KnowledgeGraph（临时目录）把节点写入向量库
    kg = KnowledgeGraph(user_id=env["user"], data_dir=env["kg_dir"])
    try:
        indexed = asyncio.run(mgr.index_node(env["user"], {"id": "stack", "name": "堆栈"}, kg))
    finally:
        kg.close()
    assert indexed > 0

    hits = asyncio.run(mgr.search(env["user"], "堆栈"))

    assert hits, "嵌入正常时向量路径应返回命中项"
    # 走向量路径时不会出现兜底通道专有的 heading
    assert all(h["heading"] not in ("名称匹配", "摘要匹配") for h in hits)


# ── 5. chunk_index 节点内全局递增 ──────────────────────────

def test_chunk_markdown_index_globally_increasing():
    md = "\n\n".join([
        "## 小节一\n\n" + "甲" * 60,
        "## 小节二\n\n" + "乙" * 60,
        "## 小节三\n\n" + "丙" * 60,
    ])

    chunks = chunk_markdown("n1", "节点", md)

    idx = [c["chunk_index"] for c in chunks]
    assert len(chunks) >= 3
    assert len(idx) == len(set(idx)), "同一节点内 chunk_index 不得重复"
    assert idx == list(range(len(chunks))), "chunk_index 应为节点内全局递增 0..n-1"
