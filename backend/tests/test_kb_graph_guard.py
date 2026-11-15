"""GQ-15 建图入口 per-user 互斥：同用户已有建图在跑 → 409；异常/取消后**必须**释放。

为什么要专门测"释放"：互斥锁写错最常见的方式就是"占了不放"——
建图抛异常 / 被取消（服务关停）时若不在 finally 释放，该用户会被**永久**锁死，
此后每次 POST /kb/graph/generate 都 409，只能重启服务，且现象难排查。

2026-11-15 起建图**后台化**（`runtime.spawn`）：端点只做三件事 ——
校验 / 占位 / 起后台任务，随即返回 `{"status": "accepted"}`。
因此本文件断言的是「端点的准入判断 + 后台任务的生命周期」，不再有 422/500 同步返回
（那两条路径的结果现在只走 `graph_updated` 通知，见 `test_kb_graph_generate_notify.py`）。

覆盖：
- 运行中再次请求同用户 → 409（detail 含"尚未完成"）
- 正常结束 → 后台任务释放锁
- 建图抛异常 → 后台任务释放锁，修复后能再次建图
- 后台任务被取消（CancelledError 属 BaseException）→ 释放锁
- 两个并发请求只有一个进入真实建图
- 不同用户互不阻塞
- HTTP 层（TestClient）：409 / 202 与锁释放
- 端点把活交给 runtime.spawn（不是请求内 await）
"""
import asyncio

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.v1 import kb as kb_module
from app.api.v1.kb import GraphGenerateRequest, generate_graph
from app.core import runtime
from app.core.kb import graph_generator

URL = "/api/v1/kb/graph/generate"


def _req(subject="数据结构", node_ids=(1,)):
    return GraphGenerateRequest(subject=subject, node_ids=list(node_ids))


@pytest.fixture(autouse=True)
def _clear_inflight():
    """互斥锁是模块级全局：每个用例前后都清空，避免用例之间互相污染。

    清空是测试专属操作（生产只有 try_begin / end 两个入口）→ 直接碰私有集合。
    """
    graph_generator._GRAPH_INFLIGHT.clear()
    yield
    graph_generator._GRAPH_INFLIGHT.clear()


async def _drain_bg():
    """等后台任务跑完（端点已 spawn 出去，锁在后台任务的 finally 里释放）。"""
    while runtime.pending_bg_tasks():
        await asyncio.sleep(0.01)


# ── 直接调用端点协程（确定性，无需线程）──────────────────────────

def test_second_request_while_running_gets_409(monkeypatch):
    """建图进行中再次请求 → 409（不打断正在进行的那次）"""
    async def slow(user_id, subject, node_ids):
        await asyncio.sleep(3600)

    monkeypatch.setattr(kb_module.graph_generator, "generate_graph", slow)

    async def scenario():
        first = await generate_graph(_req(), user_id=42)
        assert first["status"] == "accepted"
        assert graph_generator.is_graph_building(42), "端点返回后锁应仍被后台任务持有"

        with pytest.raises(HTTPException) as ei:
            await generate_graph(_req(), user_id=42)
        assert ei.value.status_code == 409
        assert "尚未完成" in ei.value.detail

        # slow 还在 sleep(3600) —— 必须显式取消，否则 _drain_bg 永远等不完（用例挂死）
        for task in list(runtime._bg_tasks):
            task.cancel()
        await _drain_bg()

    asyncio.run(scenario())


def test_lock_released_after_task_finishes(monkeypatch):
    """后台任务跑完必须释放锁，否则该用户被永久锁死（本任务最容易写错处）"""
    async def ok(user_id, subject, node_ids):
        return {"subject": subject, "processed_books": 1}

    monkeypatch.setattr(kb_module.graph_generator, "generate_graph", ok)

    async def scenario():
        await generate_graph(_req(), user_id=11)
        assert graph_generator.is_graph_building(11)
        await _drain_bg()
        assert not graph_generator.is_graph_building(11), "后台任务结束必须释放锁"

        # 释放后能再次建图
        again = await generate_graph(_req(), user_id=11)
        assert again["status"] == "accepted"
        await _drain_bg()

    asyncio.run(scenario())


def test_lock_released_after_exception(monkeypatch):
    """后台任务抛异常后必须释放 —— 否则该用户被永久锁死"""
    calls = {"n": 0}

    async def flaky(user_id, subject, node_ids):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return {"subject": subject, "processed_books": 1}

    monkeypatch.setattr(kb_module.graph_generator, "generate_graph", flaky)

    async def scenario():
        await generate_graph(_req(), user_id=7)
        await _drain_bg()
        assert not graph_generator.is_graph_building(7), "异常后必须释放，否则用户被永久锁死"

        # 第二次（故障已排除）能正常建图 —— 证明锁确实放开了
        assert (await generate_graph(_req(), user_id=7))["status"] == "accepted"
        await _drain_bg()

    asyncio.run(scenario())


def test_lock_released_after_cancel(monkeypatch):
    """后台任务被取消（服务关停）也要释放锁（CancelledError 不被 except Exception 捕获）"""
    started = asyncio.Event()

    async def blocking(user_id, subject, node_ids):
        started.set()
        await asyncio.sleep(3600)

    monkeypatch.setattr(kb_module.graph_generator, "generate_graph", blocking)

    async def scenario():
        await generate_graph(_req(), user_id=9)
        await started.wait()
        assert graph_generator.is_graph_building(9), "运行中应占位"

        # 模拟服务关停：取消所有后台任务
        for task in list(runtime._bg_tasks):
            task.cancel()
        await _drain_bg()
        assert not graph_generator.is_graph_building(9), "取消后必须释放"

    asyncio.run(scenario())


def test_concurrent_requests_only_one_enters(monkeypatch):
    """两个并发请求：只有第一个真正进入建图，第二个 409"""
    release = asyncio.Event()
    entered = []

    async def slow(user_id, subject, node_ids):
        entered.append(user_id)
        await release.wait()
        return {"subject": subject, "processed_books": 1}

    monkeypatch.setattr(kb_module.graph_generator, "generate_graph", slow)

    async def scenario():
        assert (await generate_graph(_req(), user_id=5))["status"] == "accepted"
        await asyncio.sleep(0)          # 让后台任务先跑到 await 处
        with pytest.raises(HTTPException) as ei:
            await generate_graph(_req(), user_id=5)
        assert ei.value.status_code == 409
        release.set()
        await _drain_bg()

    asyncio.run(scenario())
    assert entered == [5], "第二次请求不得进入真实建图"


def test_different_users_not_blocked(monkeypatch):
    """锁按用户隔离：uid1 在建图，不影响 uid2"""
    async def ok(user_id, subject, node_ids):
        return {"subject": subject, "processed_books": 1}

    monkeypatch.setattr(kb_module.graph_generator, "generate_graph", ok)
    graph_generator.try_begin_graph_build(1, "数据结构")

    async def scenario():
        assert (await generate_graph(_req(), user_id=2))["status"] == "accepted"
        await _drain_bg()

    asyncio.run(scenario())


def test_endpoint_uses_spawn_not_await(monkeypatch):
    """端点是**后台化**的：返回时生成器还没跑完，且任务由 runtime 持强引用。

    这是本次改动的核心 —— 直接 `await` 的话客户端断连就会取消建图（TODO §11.3）。
    """
    gate = asyncio.Event()
    finished = {"v": False}

    async def slow(user_id, subject, node_ids):
        await gate.wait()
        finished["v"] = True
        return {"subject": subject, "processed_books": 1}

    monkeypatch.setattr(kb_module.graph_generator, "generate_graph", slow)

    async def scenario():
        resp = await generate_graph(_req(), user_id=13)
        assert resp["status"] == "accepted"
        assert runtime.pending_bg_tasks() == 1, "必须由 runtime.spawn 持有强引用"
        assert not finished["v"], "端点返回时建图不应已跑完（后台化）"
        gate.set()
        await _drain_bg()
        assert finished["v"]

    asyncio.run(scenario())


# ── HTTP 层（真实路由 + 状态码 + detail 文案）──────────────────

def test_endpoint_returns_409_when_inflight():
    from app.main import app
    from app.core.auth import get_current_user

    app.dependency_overrides[get_current_user] = lambda: 77
    graph_generator.try_begin_graph_build(77, "数据结构")
    try:
        resp = TestClient(app).post(URL, json={"subject": "数据结构", "node_ids": [1]})
        assert resp.status_code == 409
        assert "尚未完成" in resp.json()["detail"]
    finally:
        graph_generator.end_graph_build(77)
        app.dependency_overrides.clear()


def test_endpoint_accepts_and_spawns(monkeypatch):
    from app.main import app
    from app.core.auth import get_current_user

    app.dependency_overrides[get_current_user] = lambda: 88

    async def ok(user_id, subject, node_ids):
        return {"subject": subject, "processed_books": 1, "created_nodes": []}

    monkeypatch.setattr(kb_module.graph_generator, "generate_graph", ok)
    try:
        resp = TestClient(app).post(URL, json={"subject": "数据结构", "node_ids": [1]})
        assert resp.status_code == 200
        assert resp.json()["status"] == "accepted"
        assert resp.json()["subject"] == "数据结构"
    finally:
        app.dependency_overrides.clear()
