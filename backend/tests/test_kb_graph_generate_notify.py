"""建图完成后**必须**投一条 `graph_updated` 通知（2026-11-10，修「后端建完了前端不刷新」）。

为什么专门测：`POST /kb/graph/generate` 是分钟级任务（实测单本教材 15 分钟量级），
而前端 axios 与 Vite 代理的 timeout 都是 300s —— 一定先超时。旧实现把
「刷新图谱」挂在**HTTP 响应之后**（`chatStore.generateSubjectGraph` 里的 fetchGraph），
于是后台明明建完了、前端却纹丝不动，只能手动刷新。

2026-11-15 起建图**后台化**（`runtime.spawn`）：端点入队即返回，客户端断连/刷新
不再取消它。于是「结果」只有一条出口 —— 通知。本文件因此直接驱动后台协程
`_run_graph_build`（通知与锁释放都在它里面），端点本身的行为由
`test_kb_graph_guard.py` 覆盖。

断言的是**通知本身**，不是文案：
- 成功 / 业务失败（aggregate.error）/ 异常 / 取消四条路径都要发 ——
  前端靠它结束"生成中"，漏一条就永远停在生成中。
- payload 必须带 `subject`（前端判断"是不是我正在看的学科"）与 `ok`
  （区分成功/失败），以及 `failed_docs`/`skipped_docs`（GQ-25：未入图的资料要点名）。
"""
import asyncio

import pytest

from app.api.v1 import kb as kb_module
from app.api.v1.kb import _graph_generated_payload, _run_graph_build
from app.core.event_bus import GRAPH_UPDATED
from app.core.kb import graph_generator


@pytest.fixture(autouse=True)
def _clear_inflight():
    graph_generator._GRAPH_INFLIGHT.clear()
    yield
    graph_generator._GRAPH_INFLIGHT.clear()


@pytest.fixture
def sent(monkeypatch):
    """截获本模块的 notify（kb.py 里是 `from ... import notify`，故 patch 模块属性）。"""
    calls = []

    def fake_notify(user_id, event_type, data=None, **kw):
        calls.append({"user_id": user_id, "type": event_type, "data": data or {}})

    monkeypatch.setattr(kb_module, "notify", fake_notify)
    return calls


def _occupy(user_id: int, subject: str = "数据结构") -> None:
    """模拟端点已完成占位（`_run_graph_build` 假定锁已被端点拿到）。"""
    assert graph_generator.try_begin_graph_build(user_id, subject)


def test_success_sends_notification(monkeypatch, sent):
    async def ok(user_id, subject, node_ids):
        return {"subject": subject, "created_nodes": ["a", "b"], "created_edges": 3}

    monkeypatch.setattr(kb_module.graph_generator, "generate_graph", ok)
    _occupy(42)
    asyncio.run(_run_graph_build(42, "数据结构", [1]))

    assert len(sent) == 1, "建图结束必须恰好投一条通知（多投会让前端重复弹提示）"
    n = sent[0]
    assert n["user_id"] == 42 and n["type"] == GRAPH_UPDATED
    assert n["data"]["ok"] is True
    assert n["data"]["subject"] == "数据结构"
    assert n["data"]["created_nodes"] == 2 and n["data"]["created_edges"] == 3


def test_business_failure_still_notifies(monkeypatch, sent):
    """aggregate.error（如"没有可处理的书籍文本"）→ ok=False，但通知照发。

    不发的话前端永远停在"生成中"——用户等 15 分钟只看到按钮转圈。
    """
    async def bad(user_id, subject, node_ids):
        return {"subject": subject, "error": "没有可处理的书籍文本", "created_nodes": []}

    monkeypatch.setattr(kb_module.graph_generator, "generate_graph", bad)
    _occupy(7)
    asyncio.run(_run_graph_build(7, "数据结构", [1]))

    assert len(sent) == 1
    assert sent[0]["data"]["ok"] is False
    assert "没有可处理的书籍文本" in sent[0]["data"]["error"]


def test_exception_still_notifies(monkeypatch, sent):
    async def boom(user_id, subject, node_ids):
        raise RuntimeError("LLM 挂了")

    monkeypatch.setattr(kb_module.graph_generator, "generate_graph", boom)
    _occupy(8)
    asyncio.run(_run_graph_build(8, "数据结构", [1]))   # 后台任务不向任何人抛

    assert len(sent) == 1
    assert sent[0]["data"]["ok"] is False
    assert "LLM 挂了" in sent[0]["data"]["error"]


def test_cancel_still_notifies(monkeypatch, sent):
    """服务重启/关闭时后台任务被取消（CancelledError 属 BaseException）→ 通知照发。

    这条最容易被漏：`except Exception` 捕不到它。取消后前端若收不到任何信号，
    就会永远停在"生成中"。
    """
    started = asyncio.Event()

    async def blocking(user_id, subject, node_ids):
        started.set()
        await asyncio.sleep(3600)

    monkeypatch.setattr(kb_module.graph_generator, "generate_graph", blocking)
    _occupy(9)

    async def scenario():
        task = asyncio.create_task(_run_graph_build(9, "数据结构", [1]))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert len(sent) == 1, "取消也要通知，否则前端停在生成中"
    assert sent[0]["data"]["ok"] is False


def test_lock_released_even_with_notify(monkeypatch, sent):
    """通知不能破坏既有互斥语义：发完必须释放锁，否则用户被永久锁死（GQ-15）。"""
    async def ok(user_id, subject, node_ids):
        return {"subject": subject, "created_nodes": [], "created_edges": 0}

    monkeypatch.setattr(kb_module.graph_generator, "generate_graph", ok)
    _occupy(11)
    asyncio.run(_run_graph_build(11, "数据结构", [1]))
    assert not graph_generator.is_graph_building(11)


def test_payload_names_unprocessed_docs():
    """GQ-25：未入图的资料必须点名，且**成功与失败两种口径都要带得出去**。"""
    ok = _graph_generated_payload("数据结构", {
        "created_nodes": ["a"], "created_edges": 1, "merged_nodes": ["b", "c"],
        "failed_docs": ["坏书.pdf"], "skipped_docs": ["已建完.md"],
    }, "")
    assert ok["ok"] is True
    assert ok["merged_nodes"] == 2
    assert ok["failed_docs"] == ["坏书.pdf"] and ok["skipped_docs"] == ["已建完.md"]

    # 没有 aggregate（异常 / 取消）时也要给前端一个完整形状，别让前端到处判空
    empty = _graph_generated_payload("物理", None, "boom")
    assert empty["ok"] is False and empty["error"] == "boom"
    assert empty["created_nodes"] == 0 and empty["failed_docs"] == []


def test_notify_failure_does_not_mask_result(monkeypatch):
    """通知只算尽力而为：它自己炸了，不能顶掉真正的成功结果（也不能漏放锁）。

    `notify` 在 `finally` 里 —— finally 里抛出的异常会**覆盖**掉正在传播的返回值
    或原异常。若不单独兜住，一次落库抖动就会把"建图成功"变成失败，
    或者把真实原因换成通知报错，排查时完全找不到线索。
    """
    async def ok(user_id, subject, node_ids):
        return {"subject": subject, "created_nodes": ["a"], "created_edges": 1}

    def broken_notify(*a, **kw):
        raise RuntimeError("通知落库失败")

    monkeypatch.setattr(kb_module.graph_generator, "generate_graph", ok)
    monkeypatch.setattr(kb_module, "notify", broken_notify)
    _occupy(12)

    asyncio.run(_run_graph_build(12, "数据结构", [1]))   # 不抛 = 没被顶掉
    assert not graph_generator.is_graph_building(12)
