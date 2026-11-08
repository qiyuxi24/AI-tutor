"""知识库检索范围：「空选区」必须等于「什么都检索不到」，而不是「全库检索」。

缺陷（2026-11-08 实测）：`kb_manager.search` 的 `node_ids` 参数把两种语义压进了
同一个值 —— `if node_ids:` 让 `[]` 和 `None` 走同一条 SQL 分支，都等于**不限范围**。
于是"用户选了范围，但范围内恰好没有文件"会被放大成"检索整个知识库"：

    ctx.kb = {"node_ids": [空文件夹]}  →  命中来自**别的文件夹**的片段

触发路径有三条，都不需要用户做错任何事：
  1. `KbRagSource.retrieve` 的 `node_ids=file_node_ids or None` —— 空列表被 `or` 吃掉
     （删掉选区里最后一个文件后，前端 store 里仍留着旧范围）；
  2. `GET /kb/search?node_id=<空文件夹>` —— 端点完全没有空范围判断；
  3. `quiz/generator._collect_kb_context` —— 出题依据会混进选区外的资料。

修法：两个模块级哨兵区分三种输入，谁都不许再用真值判断：
    `_UNSET`  = 调用方没传 node_ids        → 不限范围（历史行为，保持）
    `None`    = 显式不限范围               → 不限范围（既有调用方显式传 None 的口径）
    `[]`      = 给了范围，但里面没有文件   → **检索不到任何东西**

契约保持不变的部分（`tests/test_kb_source_availability.py` 锁着）：`ctx.kb is None`
（即"没选范围"）仍然检索该用户全部资料。范围 ≠ 开关，这条不能被本次修复改坏。
"""
import asyncio
import importlib

import pytest
from fastapi.testclient import TestClient

kb_manager_module = importlib.import_module("app.core.kb.kb_manager")
from app.core.kb.kb_manager import KbManager
from app.core.rag_pipeline.sources import KbRagSource
from app.core.rag_pipeline.types import RagContext

USER = 2026


def _run(coro):
    return asyncio.run(coro)


async def _no_embed(texts):
    """BM25-only：避免真实嵌入网络调用，同时保住 whoosh 这一条检索腿。"""
    return []


def _add_file(km: KbManager, name: str, parent_id, text: str) -> int:
    return km._get_store(USER).add_file(
        user_id=USER, name=name, parent_id=parent_id,
        file_type=".md", extract_text=text, file_size=len(text),
    )


@pytest.fixture
def km(tmp_path, monkeypatch):
    m = KbManager(tmp_path / "kb")
    m._embed = _no_embed
    monkeypatch.setattr(kb_manager_module, "kb_manager", m)
    return m


@pytest.fixture
def indexed(km):
    """一棵最小目录树：

    /空选区        ← 一个文件都没有的文件夹（本次缺陷的触发点）
    /有资料/笔记.md  ← 应当**只**在"不限范围"时才被检索到
    """
    empty = km.create_folder(USER, "空选区", None)
    scoped = km.create_folder(USER, "有资料", None)
    text = "stack is a LIFO linear list; push and pop at the tail. " * 8
    note = _add_file(km, "笔记.md", scoped, text)
    _run(km._index_document(USER, note, text, vectorize=True))
    return {"km": km, "empty": empty, "scoped": scoped, "note": note}


# ── 缺陷本体：空选区不得放大成全库 ──────────────────────────────

def test_empty_scope_returns_nothing(indexed):
    """`node_ids=[]` = 有范围但没文件 → 一条都不许返回。"""
    km = indexed["km"]
    assert isinstance(km.collect_files(USER, indexed["empty"]), list)
    hits = _run(km.search(USER, "linear list", node_ids=[], top_k=5))
    assert hits == [], f"空选区检索到了 {len(hits)} 条（来自选区外）"


def test_empty_scope_does_not_call_retrieval_legs(indexed, monkeypatch):
    """空选区应该在**进入检索腿之前**就退出，不做无意义的嵌入调用。"""
    km = indexed["km"]
    called = []

    async def counting_embed(texts):
        called.append(texts)
        return []

    monkeypatch.setattr(km, "_embed", counting_embed)
    _run(km.search(USER, "linear list", node_ids=[], top_k=5))
    assert called == [], "空选区仍然调用了嵌入（说明走了全库路径）"


def test_none_still_means_unscoped(indexed):
    """显式 None = 不限范围，历史行为必须保留（不许被本次修复收紧）。"""
    km = indexed["km"]
    hits = _run(km.search(USER, "linear list", node_ids=None, top_k=5))
    assert hits and all(h["node_id"] == indexed["note"] for h in hits)


def test_omitted_node_ids_means_unscoped(indexed):
    """压根不传 node_ids 也是不限范围（`_UNSET` 哨兵，不能退化成空选区）。"""
    km = indexed["km"]
    hits = _run(km.search(USER, "linear list", top_k=5))
    assert hits and all(h["node_id"] == indexed["note"] for h in hits)


def test_real_scope_is_unaffected(indexed):
    """正常范围的检索结果不受影响（防修过头）。"""
    km = indexed["km"]
    hits = _run(km.search(USER, "linear list",
                          node_ids=km.collect_files(USER, indexed["scoped"]), top_k=5))
    assert hits and all(h["node_id"] == indexed["note"] for h in hits)


# ── 触发路径 1：KbRagSource 的 `or None` ─────────────────────────

def test_rag_source_empty_selection_does_not_widen(indexed, monkeypatch):
    """用户选了空文件夹 → 源仍然参与检索，但**检索不到选区外的东西**。"""
    km = indexed["km"]
    seen = []

    async def fake_search(user_id, query, node_ids=None, top_k=5):
        seen.append(node_ids)
        return []

    monkeypatch.setattr(km, "search", fake_search)
    ctx = RagContext(user_id=USER, query="线性表",
                     kb={"node_ids": [indexed["empty"]], "name": "空选区"})
    src = KbRagSource()

    assert src.should_query(ctx) is True          # 范围≠开关，源照样参与
    hits = _run(src.retrieve(ctx))
    assert hits == []
    assert seen and seen[0] == [], \
        f"交给 search 的范围是 {seen[0]!r}，空选区被放大成了全库"


# ── 触发路径 2：GET /kb/search 端点 ──────────────────────────────

@pytest.fixture
def api(indexed, monkeypatch):
    from app.main import app
    from app.core.auth import get_current_user

    monkeypatch.setattr("app.api.v1.kb.kb_manager", indexed["km"])
    app.dependency_overrides[get_current_user] = lambda: USER
    yield {"client": TestClient(app), **indexed}
    app.dependency_overrides.clear()


def test_api_empty_folder_search_returns_no_results(api):
    """`GET /kb/search?node_id=<空文件夹>` 不得返回选区外的片段。"""
    resp = api["client"].get("/api/v1/kb/search",
                             params={"q": "linear list", "node_id": api["empty"]})
    assert resp.status_code == 200
    assert resp.json()["results"] == []


def test_api_no_node_id_searches_everything(api):
    """不传 node_id 仍然是"检索该用户全部文档"（端点口径不变）。"""
    resp = api["client"].get("/api/v1/kb/search", params={"q": "linear list"})
    assert resp.status_code == 200
    ids = {r["node_id"] for r in resp.json()["results"]}
    assert ids == {api["note"]}
