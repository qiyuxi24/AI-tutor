"""ApiEmbedder（语义去重侧嵌入）单元测试：与检索侧同口径 + 失败语义一致。

背景：同一个 text-embedding-v4 曾有两套硬编码（`llm/embed.py` 的常量 vs
`kb/embedder.py::ApiEmbedder` 的字面量），2026-09-15 收口为**共用检索侧常量**。
本测试锁死"不再分叉"：模型名/批大小必须取自 `llm/embed.py`，失败语义一致。

全部离线：客户端被替换为假对象，不发真实请求。
"""
from types import SimpleNamespace

from app.core.kb import embedder as kb_embedder
from app.core.llm.embed import EMBED_BATCH_SIZE, EMBEDDING_MODEL, MAX_EMBED_CHARS


class _FakeEmbeddings:
    """记录每次请求的 model/input，按输入条数返回假向量（可故意少返 drop 条）。"""

    def __init__(self, calls: list, drop: int):
        self._calls = calls
        self._drop = drop

    def create(self, *, model, input):
        self._calls.append({"model": model, "input": input})
        n = max(0, len(input) - self._drop)
        return SimpleNamespace(
            data=[SimpleNamespace(index=i, embedding=[0.1, 0.2]) for i in range(n)],
        )


def _client(calls: list, drop: int = 0):
    return SimpleNamespace(embeddings=_FakeEmbeddings(calls, drop))


def _make(monkeypatch, client) -> "kb_embedder.ApiEmbedder":
    """构造 ApiEmbedder：绕开真实配置与真实客户端。"""
    monkeypatch.setattr(
        kb_embedder, "settings",
        SimpleNamespace(dashscope_api_key="test-key", embed_base_url="http://localhost"),
    )
    monkeypatch.setattr(kb_embedder.ApiEmbedder, "_get_client", lambda self: client)
    return kb_embedder.ApiEmbedder()


# ─── 与检索侧同口径（防再次分叉） ───────────────────────────────

def test_shares_config_with_retrieval_side():
    """模型名 / 批大小必须来自 llm/embed.py（唯一来源），不得另立字面量。"""
    assert kb_embedder.ApiEmbedder.name == EMBEDDING_MODEL
    assert kb_embedder.ApiEmbedder.BATCH_SIZE == EMBED_BATCH_SIZE


def test_truncates_like_retrieval_side(monkeypatch):
    """超长文本按 MAX_EMBED_CHARS 截断，与检索侧一致（此前不截断会触发 API 400）。"""
    calls: list = []
    emb = _make(monkeypatch, _client(calls))

    out = emb.embed(["x" * (MAX_EMBED_CHARS + 50), "短"])

    assert calls[0]["model"] == EMBEDDING_MODEL
    assert len(calls[0]["input"][0]) == MAX_EMBED_CHARS
    assert len(out) == 2


def test_batches_by_shared_batch_size(monkeypatch):
    """按共用批大小切批（DashScope 单次最多 10 条，超限报 400）。"""
    calls: list = []
    emb = _make(monkeypatch, _client(calls))

    n = EMBED_BATCH_SIZE * 2 + 1
    out = emb.embed([f"t{i}" for i in range(n)])

    assert [len(c["input"]) for c in calls] == [EMBED_BATCH_SIZE, EMBED_BATCH_SIZE, 1]
    assert len(out) == n


# ─── 失败语义（与 embed_texts 一致：宁可整批作废，不错位） ───────

def test_partial_return_yields_empty(monkeypatch):
    """部分返回 → 整批返回 []（返回短列表会让调用方 zip(ids, vecs) 静默错位）。"""
    emb = _make(monkeypatch, _client([], drop=1))
    assert emb.embed(["a", "b", "c"]) == []


def test_api_error_yields_empty(monkeypatch):
    """调用异常 → []（由调用方决定 C5 弃权或退到 hash 兜底）。"""
    class _Boom:
        def create(self, *, model, input):
            raise RuntimeError("boom")

    emb = _make(monkeypatch, SimpleNamespace(embeddings=_Boom()))
    assert emb.embed(["a"]) == []


def test_no_key_and_empty_input(monkeypatch):
    """无 key / 空输入 → 不发请求，直接返回 []。"""
    calls: list = []
    client = _client(calls)
    monkeypatch.setattr(
        kb_embedder, "settings",
        SimpleNamespace(dashscope_api_key="", embed_base_url=""),
    )
    monkeypatch.setattr(kb_embedder.ApiEmbedder, "_get_client", lambda self: client)
    emb = kb_embedder.ApiEmbedder()

    assert emb.embed(["a"]) == []
    assert emb.embed([]) == []
    assert calls == []
