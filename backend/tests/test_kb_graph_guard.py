"""GQ-15 建图入口 per-user 互斥：同用户已有建图在跑 → 409；异常/取消/超时后**必须**释放。

为什么要专门测"释放"：互斥锁写错最常见的方式就是"占了不放"——
建图抛异常 / 请求被取消（客户端断开）时若不在 finally 释放，该用户会被**永久**锁死，
此后每次 POST /kb/graph/generate 都 409，只能重启服务，且现象难排查。

覆盖：
- 运行中再次请求同用户 → 409（detail 含"尚未完成"）
- 正常结束 → 释放锁
- 建图抛异常 → 释放锁（仍返回 500），修复后能再次建图
- 建图被取消（CancelledError 属 BaseException）→ 释放锁
- 两个并发请求只有一个进入真实建图
- 不同用户互不阻塞
- HTTP 层（TestClient）：409 / 200 与锁释放
"""
import asyncio

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.v1 import kb as kb_module
from app.api.v1.kb import GraphGenerateRequest, generate_graph

URL = "/api/v1/kb/graph/generate"


def _req(subject="数据结构", node_ids=(1,)):
    return GraphGenerateRequest(subject=subject, node_ids=list(node_ids))


@pytest.fixture(autouse=True)
def _clear_inflight():
    """互斥锁是模块级全局：每个用例前后都清空，避免用例之间互相污染。"""
    kb_module._GRAPH_INFLIGHT.clear()
    yield
    kb_module._GRAPH_INFLIGHT.clear()


# ── 直接调用端点协程（确定性，无需线程）──────────────────────────

def test_second_request_while_running_gets_409(monkeypatch):
    """建图进行中再次请求 → 409（不打断正在进行的那次）"""
    async def slow(user_id, subject, node_ids):
        with pytest.raises(HTTPException) as ei:
            await generate_graph(_req(), user_id=user_id)
        assert ei.value.status_code == 409
        assert "尚未完成" in ei.value.detail
        return {"subject": subject, "processed_books": 1}

    monkeypatch.setattr(kb_module.graph_generator, "generate_graph", slow)
    result = asyncio.run(generate_graph(_req(), user_id=42))

    assert result["status"] == "ok"
    assert 42 not in kb_module._GRAPH_INFLIGHT, "正常结束必须释放锁"


def test_lock_released_after_exception(monkeypatch):
    """建图抛异常后必须释放 —— 否则该用户被永久锁死（本任务最容易写错处）"""
    calls = {"n": 0}

    async def flaky(user_id, subject, node_ids):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return {"subject": subject, "processed_books": 1}

    monkeypatch.setattr(kb_module.graph_generator, "generate_graph", flaky)

    with pytest.raises(HTTPException) as ei:
        asyncio.run(generate_graph(_req(), user_id=7))
    assert ei.value.status_code == 500
    assert 7 not in kb_module._GRAPH_INFLIGHT, "异常后必须释放，否则用户被永久锁死"

    # 第二次（故障已排除）能正常建图 —— 证明锁确实放开了
    assert asyncio.run(generate_graph(_req(), user_id=7))["status"] == "ok"


def test_lock_released_after_cancel(monkeypatch):
    """请求被取消（CancelledError 属 BaseException，不被 except Exception 捕获）也要释放"""
    started = asyncio.Event()

    async def blocking(user_id, subject, node_ids):
        started.set()
        await asyncio.sleep(3600)
        return {"subject": subject, "processed_books": 1}

    monkeypatch.setattr(kb_module.graph_generator, "generate_graph", blocking)

    async def scenario():
        task = asyncio.create_task(generate_graph(_req(), user_id=9))
        await started.wait()
        assert 9 in kb_module._GRAPH_INFLIGHT, "运行中应占位"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert 9 not in kb_module._GRAPH_INFLIGHT, "取消后必须释放"

    asyncio.run(scenario())

    # 取消后可再次建图
    async def ok(user_id, subject, node_ids):
        return {"subject": subject, "processed_books": 1}

    monkeypatch.setattr(kb_module.graph_generator, "generate_graph", ok)
    assert asyncio.run(generate_graph(_req(), user_id=9))["status"] == "ok"


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
        t1 = asyncio.create_task(generate_graph(_req(), user_id=5))
        await asyncio.sleep(0)          # 让 t1 先跑到 await 处并占位
        with pytest.raises(HTTPException) as ei:
            await generate_graph(_req(), user_id=5)
        assert ei.value.status_code == 409
        release.set()
        assert (await t1)["status"] == "ok"

    asyncio.run(scenario())
    assert entered == [5], "第二次请求不得进入真实建图"


def test_different_users_not_blocked(monkeypatch):
    """锁按用户隔离：uid1 在建图，不影响 uid2"""
    async def ok(user_id, subject, node_ids):
        return {"subject": subject, "processed_books": 1}

    monkeypatch.setattr(kb_module.graph_generator, "generate_graph", ok)
    kb_module._GRAPH_INFLIGHT.add(1)

    assert asyncio.run(generate_graph(_req(), user_id=2))["status"] == "ok"


# ── HTTP 层（真实路由 + 状态码 + detail 文案）──────────────────

def test_endpoint_returns_409_when_inflight():
    from app.main import app
    from app.core.auth import get_current_user

    app.dependency_overrides[get_current_user] = lambda: 77
    kb_module._GRAPH_INFLIGHT.add(77)
    try:
        resp = TestClient(app).post(URL, json={"subject": "数据结构", "node_ids": [1]})
        assert resp.status_code == 409
        assert "尚未完成" in resp.json()["detail"]
    finally:
        kb_module._GRAPH_INFLIGHT.discard(77)
        app.dependency_overrides.clear()


def test_endpoint_ok_and_releases_lock(monkeypatch):
    from app.main import app
    from app.core.auth import get_current_user

    app.dependency_overrides[get_current_user] = lambda: 88

    async def ok(user_id, subject, node_ids):
        return {"subject": subject, "processed_books": 1, "created_nodes": []}

    monkeypatch.setattr(kb_module.graph_generator, "generate_graph", ok)
    try:
        resp = TestClient(app).post(URL, json={"subject": "数据结构", "node_ids": [1]})
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        assert 88 not in kb_module._GRAPH_INFLIGHT, "HTTP 正常返回也要释放锁"
    finally:
        app.dependency_overrides.clear()
