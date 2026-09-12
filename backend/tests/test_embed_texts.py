"""嵌入统一出口（llm/embed.py）契约测试 + kb/rag 两处 `_embed` 确为薄委托（零网络）。

覆盖 2026-09-12 技术债收敛：原先 kb_manager / rag_manager 各有一份重复的 `_embed()`，
现统一到 `llm.embed.embed_texts`（模型名/截断/失败兜底集中一处）。
"""
import asyncio
import importlib
from types import SimpleNamespace

from app.core.llm import embed as embed_mod
from app.core.llm.embed import MAX_EMBED_CHARS, embed_texts
from app.core.kb.kb_manager import KbManager
from app.core.rag.manager import RagManager


def _resp(pairs: list[tuple[int, list[float]]]):
    """构造 OpenAI 风格响应：pairs = [(index, embedding), ...]"""
    return SimpleNamespace(
        data=[SimpleNamespace(index=i, embedding=e) for i, e in pairs]
    )


def _client(monkeypatch, *, resp=None, exc: Exception | None = None):
    """把 llm.embed.embed_client 换成记录调用的假客户端"""
    class _FakeClient:
        def __init__(self):
            self.calls: list[dict] = []
            outer = self

            async def create(**kwargs):
                outer.calls.append(kwargs)
                if exc:
                    raise exc
                return resp

            self.embeddings = SimpleNamespace(create=create)

    fake = _FakeClient()
    monkeypatch.setattr(embed_mod, "embed_client", fake)
    return fake


def test_returns_vectors_aligned_by_index(monkeypatch):
    fake = _client(monkeypatch, resp=_resp([(1, [0.2, 0.3]), (0, [0.1, 0.1])]))
    out = asyncio.run(embed_texts(["甲", "乙"]))
    assert out == [[0.1, 0.1], [0.2, 0.3]]         # 按 index 归位，而非按返回顺序
    assert fake.calls[0]["input"] == ["甲", "乙"]  # 未超限则原样送入


def test_truncates_to_max_chars(monkeypatch):
    fake = _client(monkeypatch, resp=_resp([(0, [1.0])]))
    asyncio.run(embed_texts(["x" * (MAX_EMBED_CHARS + 100)]))
    assert len(fake.calls[0]["input"][0]) == MAX_EMBED_CHARS


def test_partial_result_degrades_to_empty(monkeypatch):
    """部分返回：宁可整批作废，也不能让 chunk 与向量错位入库"""
    _client(monkeypatch, resp=_resp([(0, [1.0])]))   # 2 条只回 1 条
    assert asyncio.run(embed_texts(["甲", "乙"])) == []


def test_api_failure_returns_empty(monkeypatch):
    _client(monkeypatch, exc=RuntimeError("boom"))
    assert asyncio.run(embed_texts(["甲"])) == []


def test_empty_input_skips_api_call(monkeypatch):
    fake = _client(monkeypatch, resp=_resp([]))
    assert asyncio.run(embed_texts([])) == []
    assert fake.calls == []


def test_managers_delegate_to_shared_embedder(monkeypatch, tmp_path):
    """两处 `_embed` 是薄委托，不再各自持有供应商调用（防重新内联）"""
    seen: list[list[str]] = []

    async def fake_embed_texts(texts: list[str]) -> list[list[float]]:
        seen.append(list(texts))
        return [[1.0] for _ in texts]

    # 注意：`app.core.kb.kb_manager` 属性被同名单例遮蔽，须走 importlib 取真模块
    kb_mod = importlib.import_module("app.core.kb.kb_manager")
    rag_mod = importlib.import_module("app.core.rag.manager")
    monkeypatch.setattr(kb_mod, "embed_texts", fake_embed_texts)
    monkeypatch.setattr(rag_mod, "embed_texts", fake_embed_texts)

    km = KbManager(tmp_path / "kb")
    rm = RagManager(tmp_path / "rag")
    assert asyncio.run(km._embed(["甲"])) == [[1.0]]
    assert asyncio.run(rm._embed(["乙"])) == [[1.0]]
    assert seen == [["甲"], ["乙"]]
