"""不限范围检索要**加严**：没选资料时不能动不动把整个知识库翻一遍。

背景（2026-11-15，对话输入框支持选资料时一并收紧）：
- 用户没选任何资料 → `node_ids=None` → 全库检索。此时旧口径"BM25 有词面匹配即算有效"
  会把"只共享一个 bigram"的无关文档也塞进提示词（实测 `排序算法复杂度` 带出 0.8032 的
  死锁文档、0.7990 的进程文档），而知识库正文的噪声代价很高（Chroma 实测 1 个干扰项即有害）。
- 用户**显式选了范围** → 那是他自己圈定的资料，不能加严（范围内本来就没几条，漏了更糟）。

锁住三条：
1. 不限范围时噪声被过滤，真命中仍在
2. 选了范围时口径不变（同一 query 在范围内仍然拿得到）
3. 加严不得把"本轮确实没有相关资料"变成"检索到了些微相关的东西"

⚠ BM25 侧必须用**相对阈值**：`min(1, s/(s+1))` 把 s≫1 全压到 0.9+，绝对阈值分不开
"真相关"与"只共享一个 bigram"（实测同一查询三条命中只差 0.003）。见
`kb_manager.UNSCOPED_BM25_REL_RATIO` 的注释。
"""
import asyncio

import pytest

from app.core.kb.kb_manager import KbManager, UNSCOPED_BM25_REL_RATIO

USER = 2027

# 语料刻意做成"共享 bigram 但不相关"：噪声文档含「算法」「复杂度」，与查询有词面交集，
# 但与「排序」无关 —— 这正是旧口径会误召回、加严后应当丢掉的那一类。
_DOCS = {
    "排序.md": "快速排序平均时间复杂度 O(n log n)，最坏 O(n^2)。归并排序稳定但需要额外空间。",
    "死锁.md": "死锁的四个必要条件是互斥、占有并等待、不可抢占、循环等待。银行家算法可以避免死锁。",
    "数值分析.md": "牛顿迭代法的收敛速度分析涉及误差阶与复杂度的权衡。",
}


async def _no_embed(texts):
    """BM25-only：不碰真实嵌入网络，但保住 whoosh 这条检索腿（本次加严就挂在它上面）。"""
    return []


def _add(km: KbManager, name: str, text: str) -> int:
    return km._get_store(USER).add_file(
        user_id=USER, name=name, parent_id=None,
        file_type=".md", extract_text=text, file_size=len(text),
    )


@pytest.fixture
def km(tmp_path, monkeypatch):
    import importlib
    mod = importlib.import_module("app.core.kb.kb_manager")
    m = KbManager(tmp_path / "kb")
    m._embed = _no_embed
    monkeypatch.setattr(mod, "kb_manager", m)
    ids = {}
    for name, text in _DOCS.items():
        ids[name] = _add(m, name, text * 3)
    for name, nid in ids.items():
        asyncio.run(m._index_document(USER, nid, _DOCS[name] * 3, vectorize=True))
    m._ids = ids
    return m


def _names(km: KbManager, hits: list[dict]) -> set[str]:
    by_id = {v: k for k, v in km._ids.items()}
    return {by_id.get(h["node_id"], str(h["node_id"])) for h in hits}


def test_unscoped_drops_bigram_only_noise(km):
    """不限范围：只共享一个 bigram 的无关文档必须被丢掉，真命中要留下。

    `数值分析.md` 虽然也提到「复杂度」，但那是**真词面交集**（不是噪声），所以不断言它
    被丢掉 —— 相对阈值的作用是剔除"差得太远"的，不是替模型判断哪份更相关。
    """
    hits = asyncio.run(km.search(USER, "排序算法复杂度", node_ids=None, top_k=5))
    names = _names(km, hits)
    assert "排序.md" in names, f"真命中被误杀：{names}"
    assert "死锁.md" not in names, f"噪声未被过滤：{names}"


def test_scoped_keeps_permissive_gate(km):
    """选了范围：口径不变 —— 范围内该拿到的仍然拿得到（宁多勿漏）。"""
    ids = list(km._ids.values())
    hits = asyncio.run(km.search(USER, "排序算法复杂度", node_ids=ids, top_k=5))
    names = _names(km, hits)
    assert "排序.md" in names


def test_unscoped_relative_ratio_is_used(km):
    """不限范围走的是**相对**阈值，不是绝对阈值（绝对阈值对 BM25 归一化分无效）。"""
    assert 0.0 < UNSCOPED_BM25_REL_RATIO < 1.0
    hits = asyncio.run(km.search(USER, "排序算法复杂度", node_ids=None, top_k=5))
    # 相对阈值的定义：留下来的每条都 ≥ 最优分 × ratio
    sparse = km._get_sparse(USER).search("排序算法复杂度", top_k=30)
    kept_ids = {h["chunk_id"] for h in hits}
    if sparse and kept_ids:
        best = max(r["score"] for r in sparse)
        for r in sparse:
            if r["chunk_id"] in kept_ids:
                assert r["score"] >= best * UNSCOPED_BM25_REL_RATIO


def test_unrelated_query_still_returns_nothing(km):
    """加严不能变成"加了点相关也算命中"：真无关的查询仍然是空。"""
    assert asyncio.run(km.search(USER, "今天的天气怎么样", node_ids=None, top_k=5)) == []


def test_empty_scope_still_empty(km):
    """空选区语义不受本次改动影响（`tests/test_kb_empty_scope.py` 的另一半保险）。"""
    assert asyncio.run(km.search(USER, "排序", node_ids=[], top_k=5)) == []
