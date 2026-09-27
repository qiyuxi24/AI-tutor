"""知识库源的自证可用性：源是否参与检索，由**它自己的数据**决定。

回归（2026-09-27）：`KbRagSource.should_query` 曾写成 `bool(ctx.kb)` —— 把"源是否启用"
外包给了调用方构造的 ctx.kb。chat 侧按前端 `kb_node_ids` 判空、rag_search 侧只在
`source=="kb"` 时构造，两处都错 → 上传的资料对 LLM **整源不可见且不报任何错**。

现契约：范围 ≠ 开关。调用方漏传/传错 `kb` 不该有权力关掉一个数据源。
"""
import asyncio
import importlib

import pytest

kb_manager_module = importlib.import_module("app.core.kb.kb_manager")
from app.core.kb.kb_manager import KbManager
from app.core.rag_pipeline.sources import KbRagSource
from app.core.rag_pipeline.types import RagContext

USER = 4242


@pytest.fixture
def km(tmp_path, monkeypatch):
    """让 KbRagSource.retrieve 内部的模块级单例指向本测试实例。"""
    m = KbManager(tmp_path)
    monkeypatch.setattr(kb_manager_module, "kb_manager", m)
    return m


def _ctx(**kw) -> RagContext:
    return RagContext(user_id=USER, query="什么是栈", **kw)


def _index_one_chunk(km: KbManager) -> int:
    """挂一个文件节点 + 写一条分块（embedding 为 NULL 的 BM25-only 也算已索引）。"""
    node_id = km._get_store(USER).add_file(
        USER, "栈.md", None, ".md", "栈是后进先出的线性结构。" * 30, 10)
    km._get_vec_store(USER).upsert_chunk(
        user_id=USER, node_id=node_id, doc_id=node_id, chunk_index=0,
        content="栈是后进先出（LIFO）的线性数据结构", heading="栈", embedding=None)
    return node_id


# ── should_query：只有"有没有数据"说了算 ──────────────────────────

def test_no_indexed_content_means_source_stays_out(km):
    """没有任何已索引资料 → 不参与（哪怕调用方传了完整的 kb 范围）。"""
    src = KbRagSource()
    assert src.should_query(_ctx(kb={"node_ids": [], "name": "知识库"})) is False


def test_missing_kb_param_does_not_disable_source(km):
    """关键回归：漏传 kb（None）不再等于关掉源 —— 有数据就必须参与。"""
    _index_one_chunk(km)
    src = KbRagSource()
    assert src.should_query(_ctx()) is True
    assert src.should_query(_ctx(kb={"node_ids": [1], "name": "知识库"})) is True


# ── retrieve：kb 只用于收窄范围，缺省 = 不限范围 ────────────────────

def test_retrieve_without_scope_searches_all(km, monkeypatch):
    node_id = _index_one_chunk(km)
    seen = {}

    async def fake_search(user_id, query, node_ids=None, top_k=5):
        seen["node_ids"] = node_ids
        return [{"node_id": node_id, "chunk_id": 1, "content": "栈是后进先出",
                 "heading": "栈", "score": 0.9, "path": "/栈.md"}]

    monkeypatch.setattr(km, "search", fake_search)
    hits = asyncio.run(KbRagSource().retrieve(_ctx()))     # 不传 kb

    assert seen["node_ids"] is None                        # 不限范围 = 查全部
    assert hits and hits[0].content == "栈是后进先出"
    assert hits[0].metadata["kb_name"] == "我的知识库"      # 缺省名有兜底


# ── 探测必须零副作用 ──────────────────────────────────────────────

def test_probe_creates_nothing(tmp_path, monkeypatch):
    """不许给从未用过知识库的用户凭空建库（has_indexed_content 的唯一副作用要求）。"""
    root = tmp_path / "kbroot"
    m = KbManager(root)
    monkeypatch.setattr(kb_manager_module, "kb_manager", m)

    assert m.has_indexed_content(USER) is False
    assert not (root / str(USER)).exists()
