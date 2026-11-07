"""P0 回归：索引失败（抛异常 / 返回 0）的节点必须留在 dirty，下次 flush 能重试。

根因（Lead 逐行复核）：`node_indexer.flush` / `sync_flush` 先 `take_dirty` 取走并清空
dirty 集合，再 `index_nodes`；而 `index_nodes._work()` 逐节点 `try/except` 只
`logger.warning` 吞掉，失败节点不回填、调用方也拿不到失败清单（`index_node` 正文/分块
为空返回 0 同样不算成功，且 `manager.py:205-209` 已先删旧索引）→ 任一节点失败即永久
丢失脏标记，「旧索引已删、新索引半截」，S6 按需检索长期缺片、全程无报错。

本文件钉住修复后的语义（全离线、独占 user_id、零网络）：
- 失败节点仍留在 dirty（`dirty_count` 观测）、成功节点被移除；
- 再次 flush 会重试失败节点（成功即移除，仍失败继续留）；
- flush 执行期间新到的 `mark_dirty` 不被误删；
- `index_nodes(...)->int` 与 `take_dirty`/`mark_dirty_many`/`dirty_count` 语义不变；
- `sync_flush` 同步桥路径与 `flush` 一致。
"""
import asyncio

import pytest

from app.core.rag import node_indexer
from app.core.rag.manager import RagManager

UID = 991_902  # 独占：避免与其它并行测试的 dirty 登记撞同 user_id


@pytest.fixture
def env(tmp_path, monkeypatch):
    """临时 RAG 管理器（实例级空嵌入，零网络）+ 每用例干净 dirty 基线。"""
    mgr = RagManager(data_dir=tmp_path / "rag")

    async def _no_embed(texts):
        return []

    mgr._embed = _no_embed
    monkeypatch.setattr("app.core.rag.manager.rag_manager", mgr)
    node_indexer.take_dirty(UID)  # 干净基线（take_dirty 仍是取走并移除）
    yield mgr
    node_indexer.take_dirty(UID)  # 不把本文件登记留给后续用例
    node_indexer._locks.pop(UID, None)


def _install_indexer(monkeypatch, outcomes):
    """替换 index_node：outcomes[node_id] = 抛出的异常 / 返回 0 / 返回正数。

    默认（未登记的节点）返回 1。记录每次调用的节点 id 供「重试」断言。
    """
    calls = []

    async def _fake_index_node(self, user_id, node, kg=None):  # 类级 patch → 绑定方法收 self
        nid = node["id"]
        calls.append(nid)
        val = outcomes.get(nid, 1)
        if isinstance(val, Exception):
            raise val
        return val

    monkeypatch.setattr(RagManager, "index_node", _fake_index_node)
    return calls


class _FakeKG:
    """仅提供 index_nodes 需要的 get_node / close；不碰真实 SQLite。"""

    def __init__(self, missing=()):
        self.missing = set(missing)

    def get_node(self, nid):
        if nid in self.missing:
            return None
        return {"id": str(nid), "name": str(nid)}

    def close(self):
        pass


# ── 失败节点保留 / 成功节点移除 ──────────────────────────────────

def test_failed_nodes_stay_dirty_and_succeeded_are_removed(env, monkeypatch):
    """index_node 抛异常 + 返回 0 都算失败；两者留 dirty，成功节点移除。"""
    _install_indexer(monkeypatch, {"boom": RuntimeError("索引挂了"), "zero": 0})
    node_indexer.mark_dirty_many(UID, ["ok", "boom", "zero"])
    assert node_indexer.dirty_count(UID) == 3

    indexed = asyncio.run(node_indexer.flush(UID, _FakeKG()))

    assert indexed == 1
    assert node_indexer.take_dirty(UID) == {"boom", "zero"}  # 失败节点仍在，等待重试


def test_second_flush_retries_failed_nodes(env, monkeypatch):
    """首次失败留 dirty；修好后再次 flush 重试它们，成功后清空。"""
    outcomes = {"flaky": RuntimeError("首次失败")}
    calls = _install_indexer(monkeypatch, outcomes)
    node_indexer.mark_dirty(UID, "flaky")

    assert asyncio.run(node_indexer.flush(UID, _FakeKG())) == 0
    assert node_indexer.dirty_count(UID) == 1

    outcomes["flaky"] = 1  # 修好
    assert asyncio.run(node_indexer.flush(UID, _FakeKG())) == 1
    assert calls.count("flaky") == 2, "失败节点必须在下一次 flush 被重试"
    assert node_indexer.dirty_count(UID) == 0


# ── 并发：flush 执行期间新 mark_dirty 不丢 ────────────────────────

def _index_node_with_incoming_mark(user_id, node, kg=None, seen=None):
    """首次被调用时模拟写路径在 flush 执行期间新登记一个节点。"""
    if not seen["marked"]:
        node_indexer.mark_dirty(user_id, "new")
        seen["marked"] = True
    return 0 if node["id"] == "fail" else 1


def test_new_mark_dirty_during_flush_is_not_lost(env, monkeypatch):
    """失败节点 + flush 期间新登记的节点，都不能被「结束后清空整集」误删。"""
    seen = {"marked": False}

    async def _fake(self, user_id, node, kg=None):  # 类级 patch → 绑定方法收 self
        return _index_node_with_incoming_mark(user_id, node, kg, seen)

    monkeypatch.setattr(RagManager, "index_node", _fake)
    node_indexer.mark_dirty_many(UID, ["a", "fail"])

    assert asyncio.run(node_indexer.flush(UID, _FakeKG())) == 1
    assert node_indexer.take_dirty(UID) == {"fail", "new"}


# ── 向后兼容：int 返回 / take_dirty / mark_dirty_many / dirty_count ──

def test_index_nodes_returns_int_and_collects_failed_ids(env, monkeypatch):
    """旧调用约定（返回成功计数 int）不变；可选 failed_ids 回填失败集合。"""
    _install_indexer(monkeypatch, {"bad": RuntimeError("挂"), "zero": 0})
    failed = set()

    result = asyncio.run(node_indexer.index_nodes(
        UID, ["good", "bad", "zero"], _FakeKG(), failed_ids=failed))

    assert result == 1
    assert failed == {"bad", "zero"}


def test_missing_node_is_dropped_not_retried_forever(env, monkeypatch):
    """已删节点（get_node None）：索引清理走删除路径，不该永远留在 dirty 空转重试。"""
    _install_indexer(monkeypatch, {})
    node_indexer.mark_dirty_many(UID, ["gone", "ok"])

    assert asyncio.run(node_indexer.flush(UID, _FakeKG(missing=["gone"]))) == 1
    assert node_indexer.dirty_count(UID) == 0  # gone 不空转，ok 已成功移除


def test_mark_dirty_many_and_take_dirty_unchanged(env):
    """兼容性护栏：这三个公开函数的语义在修复前后必须逐字节保持。"""
    node_indexer.mark_dirty(UID, "a")
    node_indexer.mark_dirty_many(UID, ["b", "b", "", None, "c"])
    assert node_indexer.dirty_count(UID) == 3
    assert node_indexer.take_dirty(UID) == {"a", "b", "c"}
    assert node_indexer.take_dirty(UID) == set()  # 取走并移除（其它测试依赖此语义）
    assert node_indexer.dirty_count(UID) == 0


# ── sync_flush 同步桥路径一致 ────────────────────────────────────

def test_sync_flush_keeps_failed_nodes_dirty(env, monkeypatch):
    _install_indexer(monkeypatch, {"fail": RuntimeError("挂"), "zero": 0})
    node_indexer.mark_dirty_many(UID, ["ok", "fail", "zero"])

    assert node_indexer.sync_flush(UID, _FakeKG()) == 1
    assert node_indexer.take_dirty(UID) == {"fail", "zero"}


def test_sync_flush_concurrent_mark_dirty_not_lost(env, monkeypatch):
    seen = {"marked": False}

    async def _fake(self, user_id, node, kg=None):  # 类级 patch → 绑定方法收 self
        return _index_node_with_incoming_mark(user_id, node, kg, seen)

    monkeypatch.setattr(RagManager, "index_node", _fake)
    node_indexer.mark_dirty_many(UID, ["a", "fail"])

    assert node_indexer.sync_flush(UID, _FakeKG()) == 1
    assert node_indexer.take_dirty(UID) == {"fail", "new"}
