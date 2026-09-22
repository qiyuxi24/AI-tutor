"""B3.3 来源质量统计测试（全离线：零网络/零 LLM/零真实 KB）：

- store  ：increment_times_referenced 累加/批量/只命中已入库文档；source_reference_stats 排行、空库
- sources：KbRagSource.retrieve 命中即累计（同文档多片段只计 1 次）、未命中不写、落库失败不影响检索
- 双路径：有事件循环 → 后台任务（不阻塞）；无事件循环 → 同步写一行；临时 loop（rag_search 同步桥）也必落地
- api    ：GET /collector/stats/sources 契约、空库、用户隔离、不遮蔽既有 /collector/stats

关键回归（2026-09-20 实测）：rag_search 的同步桥在 `asyncio.run` 临时 loop 里跑，
loop 关闭会取消尚未执行的后台任务 —— 后台协程**写库前若有 await 就必丢**，
故 _write_reference_stats_async 只能"进门就写"。test_bump_persists_inside_transient_loop 锁死这条。
"""
import asyncio
import importlib

import pytest
from fastapi.testclient import TestClient

from app.core.collector.store import (
    RESOURCE_INDEXED,
    CollectorStore,
    CollectorStoreManager,
)
from app.core.rag_pipeline import sources as sources_mod
from app.core.rag_pipeline.sources import KbRagSource
from app.core.rag_pipeline.types import RagContext

USER = 7

# 用**模块对象**打补丁：kb/__init__.py 把包属性 kb_manager 绑成了单例实例（遮蔽同名子模块），
# 字符串式 monkeypatch.setattr("app.core.kb.kb_manager.kb_manager", ...) 会解析到实例上而报错。
_COLLECTOR_MOD = importlib.import_module("app.core.collector.manager")
_KB_MOD = importlib.import_module("app.core.kb.kb_manager")


@pytest.fixture
def store(tmp_path) -> CollectorStore:
    return CollectorStore(tmp_path)


def _index_doc(store: CollectorStore, url: str, source: str, file_node_id: int) -> int:
    """造一条"已入库文档"资源（indexed + file_node_id = RAG 命中时的 node_id）"""
    rid = store.add_resource(url, source=source)
    store.update_resource_status(rid, RESOURCE_INDEXED, file_node_id=file_node_id)
    return rid


# ── store：累计 ──────────────────────────────────────────────

def test_increment_accumulates(store):
    rid = _index_doc(store, "https://w/a", "wikipedia", 101)
    assert store.increment_times_referenced(101) == 1
    store.increment_times_referenced(101)
    assert store.get_resource(rid)["times_referenced"] == 2


def test_increment_only_matches_indexed_doc(store):
    """未知 node 返回 0；未入库（无 file_node_id）不被误计"""
    rid = _index_doc(store, "https://w/a", "wikipedia", 101)
    pending = store.add_resource("https://w/b", source="wikipedia")
    assert store.increment_times_referenced(999) == 0
    assert store.get_resource(rid)["times_referenced"] == 0
    assert store.get_resource(pending)["times_referenced"] == 0


def test_increment_batch_one_statement(store):
    """一次检索命中多篇 → 一条 UPDATE 批量 +1"""
    r1 = _index_doc(store, "https://w/a", "wikipedia", 101)
    r2 = _index_doc(store, "https://w/b", "wikipedia", 102)
    assert store.increment_times_referenced([101, 102]) == 2
    assert store.get_resource(r1)["times_referenced"] == 1
    assert store.get_resource(r2)["times_referenced"] == 1


def test_increment_empty_or_none_is_noop(store):
    assert store.increment_times_referenced([]) == 0
    assert store.increment_times_referenced(None) == 0


# ── store：排行 ──────────────────────────────────────────────

def test_source_reference_stats_ranking(store):
    """按来源聚合降序；采了但没被引用过的源也在榜内（times_referenced=0）"""
    r1 = _index_doc(store, "https://w/a", "wikipedia", 101)
    r2 = _index_doc(store, "https://w/b", "wikipedia", 102)
    r3 = _index_doc(store, "https://o/c", "oiwiki", 201)
    _index_doc(store, "https://p/d", "web_page", 301)
    for _ in range(2):
        store.increment_times_referenced(101)
    store.increment_times_referenced(102)
    store.increment_times_referenced(201)

    stats = store.source_reference_stats()
    assert [s["source"] for s in stats] == ["wikipedia", "oiwiki", "web_page"]
    assert stats[0] == {"source": "wikipedia", "resource_count": 2,
                        "times_referenced": 3, "resource_type": "document"}
    assert stats[1]["times_referenced"] == 1
    assert stats[2]["times_referenced"] == 0
    # 明细行未被聚合查询污染
    assert store.get_resource(r1)["times_referenced"] == 2
    assert store.get_resource(r2)["times_referenced"] == 1
    assert store.get_resource(r3)["times_referenced"] == 1


def test_source_reference_stats_excludes_quiz_and_pending(store):
    """口径=只统计文档类：题目源（无 file_node_id 的 dataset_quiz）与 pending 均不计入"""
    _index_doc(store, "https://w/a", "wikipedia", 101)
    store.add_resource("https://q/1", source="dataset_quiz")   # B3.1 题目源：走 quiz_id
    store.increment_times_referenced(101)
    stats = store.source_reference_stats()
    assert [s["source"] for s in stats] == ["wikipedia"]


def test_source_reference_stats_empty(store):
    assert store.source_reference_stats() == []


# ── sources：命中即累计 ──────────────────────────────────────

class FakeKbManager:
    """替代 kb_manager：只提供 KbRagSource.retrieve 用到的三个方法"""

    def __init__(self, results):
        self._results = results
        self.calls: list[dict] = []

    def collect_files(self, user_id, node_id):
        return []

    def allowed_node_ids(self, user_id, mode):
        return None

    async def search(self, user_id, query, node_ids=None, top_k=5):
        self.calls.append({"user_id": user_id, "query": query, "top_k": top_k})
        return self._results


class _FakeCollectorManager:
    def __init__(self, store_mgr):
        self.store_mgr = store_mgr


class _BoomStoreMgr:
    def _get_store(self, user_id):
        raise RuntimeError("collector.db is locked")


def _hit_row(node_id, content="栈是后进先出", chunk_id=0) -> dict:
    return {"node_id": node_id, "chunk_id": chunk_id, "content": content,
            "score": 0.9, "heading": "栈"}


def _ctx(user_id: int = USER) -> RagContext:
    return RagContext(user_id=user_id, query="什么是栈", top_k=3,
                      kb={"node_ids": [], "name": "知识库"})


@pytest.fixture
def store_mgr(tmp_path, monkeypatch):
    """把 collector 单例换成持有临时真实库的替身（不改真实 DB，也不留副作用）"""
    mgr = CollectorStoreManager(tmp_path / "collector")
    monkeypatch.setattr(_COLLECTOR_MOD, "collector_manager", _FakeCollectorManager(mgr))
    return mgr


@pytest.fixture
def wire_kb(monkeypatch):
    def _wire(results):
        fake = FakeKbManager(results)
        monkeypatch.setattr(_KB_MOD, "kb_manager", fake)
        return fake
    return _wire


def test_retrieve_hit_increments_once(store_mgr, wire_kb):
    store = store_mgr._get_store(USER)
    rid = _index_doc(store, "https://w/a", "wikipedia", 101)
    wire_kb([_hit_row(101)])

    hits = asyncio.run(KbRagSource().retrieve(_ctx()))
    assert len(hits) == 1
    assert store.get_resource(rid)["times_referenced"] == 1


def test_retrieve_same_doc_twice_counts_once(store_mgr, wire_kb):
    """同一文档多个片段命中 → 一次检索只计 1 次引用（按文档去重）"""
    store = store_mgr._get_store(USER)
    rid = _index_doc(store, "https://w/a", "wikipedia", 101)
    wire_kb([_hit_row(101, "片段1", 0), _hit_row(101, "片段2", 1)])

    hits = asyncio.run(KbRagSource().retrieve(_ctx()))
    assert len(hits) == 2
    assert store.get_resource(rid)["times_referenced"] == 1


def test_retrieve_multiple_docs_count_each(store_mgr, wire_kb):
    store = store_mgr._get_store(USER)
    r1 = _index_doc(store, "https://w/a", "wikipedia", 101)
    r2 = _index_doc(store, "https://o/c", "oiwiki", 201)
    wire_kb([_hit_row(101), _hit_row(201)])

    asyncio.run(KbRagSource().retrieve(_ctx()))
    assert store.get_resource(r1)["times_referenced"] == 1
    assert store.get_resource(r2)["times_referenced"] == 1


def test_retrieve_no_hit_does_not_write(store_mgr, wire_kb):
    """未命中（search 返回空）→ 不累计"""
    store = store_mgr._get_store(USER)
    rid = _index_doc(store, "https://w/a", "wikipedia", 101)
    wire_kb([])

    assert asyncio.run(KbRagSource().retrieve(_ctx())) == []
    assert store.get_resource(rid)["times_referenced"] == 0


def test_retrieve_survives_store_failure(monkeypatch, wire_kb):
    """落库失败（如 sqlite 被占用）必须静默：检索结果照常返回，两条路径都不抛"""
    monkeypatch.setattr(_COLLECTOR_MOD, "collector_manager",
                        _FakeCollectorManager(_BoomStoreMgr()))
    wire_kb([_hit_row(101)])

    hits = asyncio.run(KbRagSource().retrieve(_ctx()))      # 有 loop → 后台任务路径
    assert len(hits) == 1
    sources_mod._bump_reference_stats(USER, [101])          # 无 loop → 同步路径
    sources_mod._bump_reference_stats(USER, [None])         # 脏数据也不抛


# ── sources：双路径（后台任务 / 同步写） ─────────────────────

def test_bump_uses_background_task_when_loop_running(monkeypatch):
    seen: list = []

    async def _fake_async(uid, ids):
        seen.append((uid, ids))

    def _fake_sync(uid, ids):                               # pragma: no cover
        raise AssertionError("有事件循环时不应同步写")

    monkeypatch.setattr(sources_mod, "_write_reference_stats_async", _fake_async)
    monkeypatch.setattr(sources_mod, "_write_reference_stats", _fake_sync)

    async def _go():
        sources_mod._bump_reference_stats(USER, [102, 101, 101, None])
        await asyncio.sleep(0)                              # 让 create_task 排期执行

    asyncio.run(_go())
    assert seen == [(USER, [101, 102])]                     # 去重 + 排序


def test_bump_writes_synchronously_without_loop(monkeypatch):
    seen: list = []

    async def _no_task(uid, ids):                           # pragma: no cover
        raise AssertionError("无事件循环时不应起后台任务")

    monkeypatch.setattr(sources_mod, "_write_reference_stats", lambda uid, ids: seen.append((uid, ids)))
    monkeypatch.setattr(sources_mod, "_write_reference_stats_async", _no_task)

    sources_mod._bump_reference_stats(USER, [101])
    assert seen == [(USER, [101])]


def test_bump_persists_inside_transient_loop(store_mgr, monkeypatch):
    """回归：rag_search 同步桥的临时 loop（asyncio.run）下计数也必须落地"""
    store = store_mgr._get_store(USER)
    rid = _index_doc(store, "https://w/a", "wikipedia", 101)

    async def _go():
        sources_mod._bump_reference_stats(USER, [101])

    asyncio.run(_go())
    assert store.get_resource(rid)["times_referenced"] == 1


# ── api：GET /collector/stats/sources ────────────────────────

@pytest.fixture
def api(tmp_path, monkeypatch):
    """TestClient + 依赖覆盖（真实临时库，仅本地 sqlite）"""
    from app.main import app
    from app.core.auth import get_current_user
    from app.api.v1.collector import get_manager

    mgr = CollectorStoreManager(tmp_path / "api_collector")
    app.dependency_overrides[get_current_user] = lambda: USER
    app.dependency_overrides[get_manager] = lambda: _FakeCollectorManager(mgr)
    yield TestClient(app), mgr
    app.dependency_overrides.clear()


def test_api_sources_ranking(api):
    client, mgr = api
    store = mgr._get_store(USER)
    _index_doc(store, "https://w/a", "wikipedia", 101)
    _index_doc(store, "https://o/c", "oiwiki", 201)
    for _ in range(2):
        store.increment_times_referenced(101)
    store.increment_times_referenced(201)

    resp = client.get("/api/v1/collector/stats/sources")
    assert resp.status_code == 200
    assert resp.json()["sources"] == [
        {"source": "wikipedia", "resource_count": 1,
         "times_referenced": 2, "resource_type": "document"},
        {"source": "oiwiki", "resource_count": 1,
         "times_referenced": 1, "resource_type": "document"},
    ]


def test_api_sources_empty(api):
    client, mgr = api
    mgr._get_store(USER)                        # 建库但无资源
    resp = client.get("/api/v1/collector/stats/sources")
    assert resp.status_code == 200
    assert resp.json()["sources"] == []


def test_api_sources_user_isolated(api):
    """别的用户的引用不进当前用户的排行"""
    client, mgr = api
    other = mgr._get_store(USER + 1)
    _index_doc(other, "https://w/a", "wikipedia", 101)
    other.increment_times_referenced(101)
    assert client.get("/api/v1/collector/stats/sources").json()["sources"] == []


def test_api_existing_stats_not_shadowed(api, monkeypatch):
    """新端点不得遮蔽既有 /collector/stats（路径前缀相同）"""
    client, mgr = api
    monkeypatch.setattr("app.api.v1.collector.load_subjects", lambda: {})
    mgr._get_store(USER)
    assert client.get("/api/v1/collector/stats").json() == {"coverage": []}
