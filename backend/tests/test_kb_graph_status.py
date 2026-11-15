"""`GET /kb/graph/status` —— 建图后台化后，前端靠它恢复"生成中"状态（TODO §11.3）。

为什么需要这个端点：建图已改成入队即返回（见 `kb.py::generate_graph`），
请求返回后任务还在后台跑；前端只知道"我发过请求"，一刷新就丢。
`is_graph_building` 原先只用于判 409，**没有任何端点能查**。

进度口径 = `filled / total`（TODO §11.1）：阶段①落 `skeleton` 节点，
阶段②逐概念写正文翻 `filled` —— `content_status` 是进度真源，不另建表。
⚠ 建图是增量补全，比例不从 0 开始，所以 payload 必须同时给绝对数。

覆盖：
- 没在建图 → building=False / subject 空 / progress=None（且**不碰图谱**）
- 在建图 → 读得到学科名 + total/filled/percent
- 进度按 content_status 算（skeleton 不计入 filled）
- 空学科（total=0）→ percent=0 不除零
- 统计失败只降级（progress=None）不抛 —— 进度是锦上添花，不能把状态查询搞挂
- kg 一定被 close（否则连接泄漏）
"""
import asyncio

import pytest

from app.api.v1 import kb as kb_module
from app.api.v1.kb import graph_status
from app.core.kb import graph_generator
from app.core.knowledge_graph import CONTENT_STATUS_FILLED, CONTENT_STATUS_SKELETON


@pytest.fixture(autouse=True)
def _clear_inflight():
    graph_generator._GRAPH_INFLIGHT.clear()
    yield
    graph_generator._GRAPH_INFLIGHT.clear()


class _FakeKg:
    """最小替身：只提供 status 端点用到的那几个方法。"""

    def __init__(self, nodes_by_subject=None, all_nodes=None, boom=False):
        self._by_subject = nodes_by_subject or {}
        self._all = all_nodes or []
        self._boom = boom
        self.closed = False

    def get_nodes_by_subject(self, subject):
        if self._boom:
            raise RuntimeError("db down")
        return self._by_subject.get(subject, [])

    @property
    def nodes(self):
        if self._boom:
            raise RuntimeError("db down")
        return self._all

    def close(self):
        self.closed = True


def _install(monkeypatch, fake):
    monkeypatch.setattr(kb_module, "KnowledgeGraph", lambda user_id: fake)
    return fake


def test_not_building_returns_empty_and_skips_graph(monkeypatch):
    """没在建图 → 立刻返回空状态，**不打开图谱**（这个端点会被前端轮询）"""
    def _boom(*a, **kw):
        raise AssertionError("没在建图时不该打开知识图谱")

    monkeypatch.setattr(kb_module, "KnowledgeGraph", _boom)

    got = asyncio.run(graph_status(user_id=1))
    assert got == {"building": False, "subject": "", "progress": None}


def test_building_reports_subject_and_progress(monkeypatch):
    fake = _install(monkeypatch, _FakeKg({
        "数据结构": [
            {"name": "栈", "content_status": CONTENT_STATUS_FILLED},
            {"name": "队列", "content_status": CONTENT_STATUS_FILLED},
            {"name": "树", "content_status": CONTENT_STATUS_SKELETON},
            {"name": "图", "content_status": CONTENT_STATUS_SKELETON},
        ]
    }))
    assert graph_generator.try_begin_graph_build(7, "数据结构")

    got = asyncio.run(graph_status(user_id=7))
    assert got["building"] is True
    assert got["subject"] == "数据结构"
    assert got["progress"] == {"total": 4, "filled": 2, "percent": 50}
    assert fake.closed, "kg 必须 close，否则连接泄漏"


def test_progress_counts_only_filled(monkeypatch):
    """skeleton 是阶段①的骨架，没正文 —— 不能算进度"""
    _install(monkeypatch, _FakeKg({
        "物理": [
            {"name": "力", "content_status": CONTENT_STATUS_FILLED},
            {"name": "功", "content_status": CONTENT_STATUS_SKELETON},
            {"name": "能", "content_status": CONTENT_STATUS_SKELETON},
        ]
    }))
    assert graph_generator.try_begin_graph_build(3, "物理")

    got = asyncio.run(graph_status(user_id=3))
    assert got["progress"]["filled"] == 1
    assert got["progress"]["percent"] == 33


def test_empty_subject_does_not_divide_by_zero(monkeypatch):
    """刚占位、节点还没落库时 total=0 → percent=0，不能崩"""
    fake = _install(monkeypatch, _FakeKg({}))
    assert graph_generator.try_begin_graph_build(5, "化学")

    got = asyncio.run(graph_status(user_id=5))
    assert got["progress"] == {"total": 0, "filled": 0, "percent": 0}
    assert fake.closed


def test_unknown_subject_falls_back_to_all_nodes(monkeypatch):
    """subject 为空（老调用方不传学科名）→ 退回全图统计"""
    fake = _install(monkeypatch, _FakeKg(
        all_nodes=[
            {"name": "a", "content_status": CONTENT_STATUS_FILLED},
            {"name": "b", "content_status": CONTENT_STATUS_SKELETON},
        ]
    ))
    assert graph_generator.try_begin_graph_build(9, "")

    got = asyncio.run(graph_status(user_id=9))
    assert got["subject"] == ""
    assert got["progress"] == {"total": 2, "filled": 1, "percent": 50}
    assert fake.closed


def test_stats_failure_degrades_not_raises(monkeypatch):
    """统计失败只降级（progress=None）—— 状态查询不能因为进度算不出来就 500"""
    fake = _install(monkeypatch, _FakeKg(boom=True))
    assert graph_generator.try_begin_graph_build(11, "数学")

    got = asyncio.run(graph_status(user_id=11))
    assert got["building"] is True
    assert got["subject"] == "数学"
    assert got["progress"] is None
    assert fake.closed, "统计抛异常也必须 close"
