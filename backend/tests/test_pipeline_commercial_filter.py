"""商用模式检索过滤测试（决策 #23，B1.8）：

覆盖 KbRagSource 在 personal / commercial 两种模式下的目录范围检索行为：
- personal：不限制，自动采集/L2（合理使用级）资料正常出现在 hits
- commercial：自动采集/L2 整棵子树从检索范围排除（目录前缀零迁移白名单）
  - 目录范围检索：search 收到的 node_ids 不含 L2 文件
  - 全库检索（node_ids=None）：同样转成显式白名单，不含 L2 文件
  - 检索范围恰好只剩 L2 内容 → 返回空，不触发 search

零网络依赖：只建目录树 + 挂文件节点，mock 掉 kb_manager.search。
"""
import asyncio
import importlib

import pytest

kb_manager_module = importlib.import_module("app.core.kb.kb_manager")
from app.core.kb.kb_manager import KbManager
from app.core.rag_pipeline.sources import KbRagSource
from app.core.rag_pipeline.types import RagContext

USER = 2026


def _run(coro):
    return asyncio.run(coro)


def _add_file(km: KbManager, user_id: int, name: str, parent_id) -> int:
    """只挂文件节点，不做解析/向量化（本测试检索被 mock）。"""
    return km._get_store(user_id).add_file(
        user_id=user_id, name=name, parent_id=parent_id,
        file_type=".md", extract_text=f"{name}的内容正文", file_size=10,
    )


@pytest.fixture
def kb(monkeypatch, tmp_path):
    """构造 KB 目录树：
    /根：直挂文件 direct.md
    /手动上传/笔记.md            (manual)
    /自动采集/L0/学科/正文.md    (l0，开放授权，商用可用)
    /自动采集/L2/学科/转载.md    (l2，合理使用，商用须排除)
    """
    km = KbManager(tmp_path)
    # 让 KbRagSource.retrieve 内部的模块级单例指向本测试实例
    monkeypatch.setattr(kb_manager_module, "kb_manager", km)

    user = USER
    direct = _add_file(km, user, "direct.md", None)

    manual_dir = km.create_folder(user, "手动上传", None)
    manual = _add_file(km, user, "笔记.md", manual_dir)

    auto_dir = km.create_folder(user, "自动采集", None)
    l0_dir = km.create_folder(user, "L0", auto_dir)
    l0_sub = km.create_folder(user, "学科", l0_dir)
    l0 = _add_file(km, user, "正文.md", l0_sub)

    l2_dir = km.create_folder(user, "L2", auto_dir)
    l2_sub = km.create_folder(user, "学科", l2_dir)
    l2 = _add_file(km, user, "转载.md", l2_sub)

    return {
        "km": km, "user": user,
        "direct": direct, "manual": manual, "l0": l0, "l2": l2,
        "manual_dir": manual_dir, "auto_dir": auto_dir,
        "l0_dir": l0_dir, "l2_dir": l2_dir,
    }


@pytest.fixture
def source(kb, monkeypatch):
    """用 fake search 捕获 node_ids，并为传入范围逐个产出命中。"""
    calls: list = []

    async def fake_search(user_id, query, node_ids=None, top_k=5):
        calls.append(node_ids)
        ids = node_ids if node_ids is not None \
            else [kb["direct"], kb["manual"], kb["l0"], kb["l2"]]
        return [
            {"node_id": i, "chunk_id": None, "content": f"片段{i}",
             "heading": "", "score": 0.9, "path": f"/doc{i}"}
            for i in ids
        ]

    monkeypatch.setattr(kb["km"], "search", fake_search)
    src = KbRagSource()
    return {"src": src, "calls": calls}


def _retrieve(source, kb, mode, scope):
    node_ids = scope if scope is not None else None
    ctx = RagContext(user_id=kb["user"], query="栈是什么", mode=mode,
                     kb={"node_ids": node_ids, "name": "知识库"})
    return _run(source["src"].retrieve(ctx))


# ── personal：不限制 ────────────────────────────────────────────────

def test_personal_scoped_includes_l2(source, kb):
    hits = _retrieve(source, kb, "personal", [kb["auto_dir"]])
    assert {h.metadata["node_id"] for h in hits} == {kb["l0"], kb["l2"]}


def test_personal_full_kb_still_none(source, kb):
    hits = _retrieve(source, kb, "personal", None)
    # 全库无限制保持 node_ids=None 语义（复用现有实现，不加显式白名单）
    assert {h.metadata["node_id"] for h in hits} == {
        kb["direct"], kb["manual"], kb["l0"], kb["l2"]}
    assert source["calls"][-1] is None


# ── commercial：自动采集/L2 被排除 ─────────────────────────────────

def test_commercial_scoped_excludes_l2(source, kb):
    hits = _retrieve(source, kb, "commercial", [kb["auto_dir"]])
    ids = [h.metadata["node_id"] for h in hits]
    assert kb["l2"] not in ids          # L2（合理使用）不出现在 hits
    assert kb["l0"] in ids              # L0（开放授权）保留


def test_commercial_full_kb_whitelist(source, kb):
    hits = _retrieve(source, kb, "commercial", None)
    ids = [h.metadata["node_id"] for h in hits]
    assert kb["l2"] not in ids
    assert kb["direct"] in ids and kb["manual"] in ids and kb["l0"] in ids
    # 全库不能传 None（会重新含入 L2），必须显式白名单
    assert source["calls"][-1] is not None


def test_commercial_scope_only_l2_returns_empty(source, kb):
    hits = _retrieve(source, kb, "commercial", [kb["l2_dir"]])
    assert hits == []
    assert source["calls"] == []        # 无可检索内容，跳过 search


# ── kb_manager.allowed_node_ids 直接单测 ───────────────────────────

def test_allowed_node_ids_personal_returns_none(kb):
    assert kb["km"].allowed_node_ids(USER, "personal") is None


def test_allowed_node_ids_commercial_excludes_l2(kb):
    allowed = kb["km"].allowed_node_ids(USER, "commercial")
    assert set(allowed) == {kb["direct"], kb["manual"], kb["l0"]}
    assert kb["l2"] not in allowed
