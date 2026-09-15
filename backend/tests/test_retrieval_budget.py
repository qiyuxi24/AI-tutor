"""S6/S7 检索段独立配额 + query 双端放置（不变量 B-6 / 框架 §4.2）。

现状问题（改前）：`_build_retrieval_context` 调 `pipeline.run(top_k=5)` 后**按 source 分组
直接拼字符串**，无任何体量约束 —— 图谱源命中 5 段长正文就能把整个检索段撑到 10K+ token，
且两源共用一个 top_k，谁多谁少没有依据。

本文件锁住三条：
1. 两源各自 ≤ `RETRIEVAL_SEGMENT_MAX_TOKENS`，截断必须标注展示范围（不标注 = 模型以为只有这些依据）
2. **一源超长不挤占另一源**（独立截断，不是先到先得）
3. 当前问题出现在检索块**前后**两端（Lost in the Middle：KV 检索 45.6%→100%）
"""
import asyncio

import pytest

from app.core import rag_pipeline as rp
from app.core.rag_pipeline.types import RagHit
from app.services import chat_service as cs

_LONG = "递归是函数调用自身的技巧，包含基线条件与递归步。" * 200  # ≈4,000 字符 ≫ 单段上限
_SHORT = "递归需要基线条件。" * 8


class _FakePipeline:
    """替换 rag_pipeline.pipeline：按固定命中集返回，零网络/零索引依赖。"""

    def __init__(self, hits):
        self._hits = hits

    async def run(self, _ctx):
        return self._hits


@pytest.fixture
def install_pipeline(monkeypatch):
    def _install(hits):
        monkeypatch.setattr(rp, "pipeline", _FakePipeline(hits))
    return _install


def _hit(source: str, content: str, i: int = 0, path: str = "") -> RagHit:
    return RagHit(source=source, content=content, score=1.0 - i * 0.01,
                  path=path or f"{source}-{i}", metadata={"node_name": f"节点{i}"})


def _run(query: str = "递归和迭代的区别是什么？") -> str:
    return asyncio.run(cs._build_retrieval_context(query, user_id=1))


def _blocks(out: str) -> tuple[str, str]:
    """按区块标题切出 (图谱块, 知识库块)，缺一则为空串。"""
    if "## 知识库参考" not in out:
        return out, ""
    head, tail = out.split("## 知识库参考", 1)
    return head, "## 知识库参考" + tail


# ─── 独立截断 ────────────────────────────────────────────────

def test_each_source_trimmed_to_own_quota(install_pipeline):
    """两源各 5 条超长片段 → 各自截断到上限，且都标注展示范围。"""
    install_pipeline([_hit("graph", _LONG, i) for i in range(5)]
                     + [_hit("kb", _LONG, i) for i in range(5)])

    out = _run()
    graph_block, kb_block = _blocks(out)

    for block, name in ((graph_block, "graph"), (kb_block, "kb")):
        assert f"### 片段 1" in block, name
        assert block.count("### 片段") < 5          # 确实截断了
        assert "已截断" in block                    # 且标注了展示范围
    # 单条正文已超上限 → 仍保留 1 条（下限保护，不允许"全裁成空"）
    assert graph_block.count("### 片段") == 1
    assert kb_block.count("### 片段") == 1


def test_long_source_does_not_starve_other(install_pipeline):
    """图谱源超长时，知识库源的配额不被挤占（独立截断，不是先到先得）。"""
    install_pipeline([_hit("graph", _LONG, i) for i in range(5)]
                     + [_hit("kb", _SHORT, i) for i in range(2)])

    out = _run()
    graph_block, kb_block = _blocks(out)

    assert "已截断" in graph_block
    assert graph_block.count("### 片段") == 1
    # KB 两条短片段全部保留，且没有截断说明
    assert kb_block.count("### 片段") == 2
    assert "已截断" not in kb_block


def test_no_hits_returns_empty(install_pipeline):
    """无命中 → 空串（不注入 query echo，避免白占固定段预算）。"""
    install_pipeline([])
    assert _run() == ""


# ─── query-aware 双端放置 ────────────────────────────────────

def test_query_echoed_both_ends(install_pipeline):
    install_pipeline([_hit("kb", _SHORT, 0)])

    out = _run("递归和迭代的区别是什么？")

    assert out.startswith("## 当前问题")
    assert out.rstrip().endswith("递归和迭代的区别是什么？")
    assert out.count("递归和迭代的区别是什么？") == 2   # 前后各一次


def test_query_echo_is_capped(install_pipeline):
    """超长提问只复述前 N 字符（双端各一次，不能把提问整段复制两遍）。"""
    install_pipeline([_hit("kb", _SHORT, 0)])

    out = _run("问" * 2_000)

    assert out.count("问" * cs.RETRIEVAL_QUERY_ECHO_MAX_CHARS) == 2
    assert "问" * (cs.RETRIEVAL_QUERY_ECHO_MAX_CHARS + 10) not in out
