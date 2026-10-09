"""定向通知的落库与补拉（重构 Phase A2-3 / P1-4）。

要解决的问题：`quiz_ready` 是**后台任务约 40s 后**才产生的（见 core/quiz/chat_quiz.py）。
用户在这段时间里关掉页面、切到别的端、或网络抖一下，实时推送就无处可投
—— 用户视角是"AI 说稍等片刻，然后永远没有下文"。

A2-3 的落点：
  · `core/notices.py`（本节被测对象）= `pending_notices` 落库 / 取走 / 过期清理，
    落 `agent_runs.db`（与 agent_runs / llm_usage 同库，走 core/records.connect）；
  · `event_bus.notify()` = **先落库再推**；推成功的那条立刻标记已投递，
    使"实时推送"与"重连补拉"互斥（否则同一张题卡会渲染两遍）；
  · `GET /knowledge/notices/pending` = 前端重连后的补拉口（本文件测存储层，
    端点只是它的一层薄封装）。

⚠️ 存储层必须显式传 `db_dir`：`tests/conftest.py::_isolate_notices_db` 会把"不传
  db_dir"的写入挡掉，避免用例往开发库塞测试通知。
"""
import asyncio
import time

import pytest

from app.core import event_bus, notices
from app.core.event_bus import QUIZ_READY, get_user_queue, notify


def _reset_state():
    """重置 event_bus 全局状态（避免跨 test 的 event loop 绑定冲突）。"""
    event_bus._global_queue = None
    event_bus._user_queues.clear()
    event_bus._user_queue_last_access.clear()
    event_bus._active_subs.clear()
    event_bus._loop = None


@pytest.fixture(autouse=True)
def _clean_bus():
    _reset_state()
    yield
    _reset_state()


# ── 核心验收：无订阅者也不丢 ──


def test_notify_without_subscriber_is_persisted_and_replayable(tmp_path):
    """**没有订阅者**时 notify → 落库 → 重连后 take_pending 能原样拉到。

    这是 A2-3 的验收口径（"无订阅者时 quiz_ready 仍落库，重连后能拉到"）。
    """
    data = {"ok": True, "node_id": "n1", "questions": [{"q": "题干"}]}

    notify(7, QUIZ_READY, data, db_dir=tmp_path)   # 此刻 user 7 一条订阅都没有

    pending = notices.take_pending(7, db_dir=tmp_path)
    assert pending == [{"type": QUIZ_READY, **data}]

    # 取走即标记：再拉一次为空 —— 否则前端会把同一张题卡渲染两遍
    assert notices.take_pending(7, db_dir=tmp_path) == []


def test_notify_with_subscriber_pushes_and_leaves_nothing_pending(tmp_path):
    """有活跃订阅者时：实时推到队列，且**不留** pending（两种投递路径互斥）。"""
    async def _run():
        q = get_user_queue(1)
        notify(1, QUIZ_READY, {"ok": True, "node_id": "n1"}, db_dir=tmp_path)
        # notify 走 call_soon_threadsafe，需要一轮事件循环调度
        await asyncio.sleep(0.05)
        return q.get_nowait()

    event = asyncio.run(_run())
    assert event == {"type": QUIZ_READY, "ok": True, "node_id": "n1"}
    assert notices.take_pending(1, db_dir=tmp_path) == []


# ── 存储层的边界 ──


def test_take_pending_is_scoped_per_user(tmp_path):
    """通知按 user_id 分区：别人的补拉不会串到我的通知。"""
    notices.record(7, QUIZ_READY, {"n": 7}, db_dir=tmp_path)
    notices.record(8, QUIZ_READY, {"n": 8}, db_dir=tmp_path)

    assert [n["n"] for n in notices.take_pending(7, db_dir=tmp_path)] == [7]
    assert [n["n"] for n in notices.take_pending(8, db_dir=tmp_path)] == [8]


def test_take_pending_since_filters_by_time(tmp_path):
    """`since` 只取其后产生的通知（前端重连时用它跳过"已经在线处理过"的窗口）。

    刻意不 sleep 造时间差：Windows 的 `time.time()` 粒度粗（~15ms），
    sleep 出来的先后顺序会让用例随机变红。用"未来时间戳"反证更稳。
    """
    notices.record(7, QUIZ_READY, {"n": 1}, db_dir=tmp_path)
    assert notices.take_pending(7, since=0, db_dir=tmp_path) != []

    notices.record(7, QUIZ_READY, {"n": 2}, db_dir=tmp_path)
    assert notices.take_pending(7, since=time.time() + 10, db_dir=tmp_path) == []
    # 上面那次没取走任何东西 → 第二条仍在库里，可被正常取到
    assert [n["n"] for n in notices.take_pending(7, since=0, db_dir=tmp_path)] == [2]


def test_prune_matches_prune_once_job_contract(tmp_path):
    """`prune()` 的返回值形状必须与 `main.py::_prune_once` 的 `_JOBS` 契约一致
    （那里直接读 `res["deleted"] + res["stripped"]`）。"""
    notices.record(7, QUIZ_READY, {}, db_dir=tmp_path)

    # days=-1 = 保留期已过（不依赖时钟精度）
    res = notices.prune(days=-1, db_dir=tmp_path)
    assert res == {"deleted": 1, "stripped": 0}
    assert notices.take_pending(7, db_dir=tmp_path) == []


# ── 端点（薄封装：把存储层接到 tmp_path 后走一次真实 HTTP）──


def _client_for(user_id: int):
    from fastapi.testclient import TestClient

    from app.core.auth import get_current_user
    from app.main import app

    app.dependency_overrides[get_current_user] = lambda: user_id
    return app, TestClient(app)


def test_pending_endpoint_replays_notices_for_caller(tmp_path, monkeypatch):
    """`GET /knowledge/notices/pending` 只吐**调用者自己**的待投递通知，且取走即标记。"""
    from app.core import notices as notices_mod
    real_connect = notices_mod._connect
    monkeypatch.setattr(notices_mod, "_connect", lambda db_dir=None: real_connect(tmp_path))

    notices.record(7, QUIZ_READY, {"ok": True, "node_id": "n1"}, db_dir=tmp_path)
    notices.record(8, QUIZ_READY, {"ok": True, "node_id": "别人的"}, db_dir=tmp_path)

    app, client = _client_for(7)
    try:
        resp = client.get("/api/v1/knowledge/notices/pending")
        assert resp.status_code == 200
        assert resp.json()["notices"] == [{"type": QUIZ_READY, "ok": True, "node_id": "n1"}]
        # 取走即标记 → 再拉为空（不会与实时推送重复渲染同一张题卡）
        assert client.get(
            "/api/v1/knowledge/notices/pending").json()["notices"] == []
    finally:
        app.dependency_overrides.clear()

    # user 8 的通知没被这次补拉动过
    assert len(notices.take_pending(8, db_dir=tmp_path)) == 1


def test_pending_endpoint_requires_auth():
    from fastapi.testclient import TestClient

    from app.core.auth import get_current_user
    from app.main import app

    app.dependency_overrides.pop(get_current_user, None)
    assert TestClient(app).get(
        "/api/v1/knowledge/notices/pending").status_code == 401

