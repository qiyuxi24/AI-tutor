"""课程导入队列单测（全离线：桩掉取词/展开/入库，真跑 worker 与状态机）。

覆盖：
- 一次提交多链接 → 每个链接一集，串行跑完 → 任务 done、计数正确
- 单集失败不拖垮整批：后面的集继续跑，失败原因落在分集里
- 暂停 / 恢复 / 取消 / 重排（协作式，只在集与集之间生效）
- 开截图时把 asset_key 传给入库、把 slide URL 前缀传给取词
- 进程重启后 `resume_all` 能把未完成任务重新挂起
"""
import asyncio

import pytest

from app.core.kb import import_queue as iq
from app.core.kb.kb_manager import KbManager

USER = 7


@pytest.fixture()
def queue(tmp_path, monkeypatch):
    manager = KbManager(tmp_path / "kb")

    uploaded: list[dict] = []

    async def fake_upload(**kwargs):
        uploaded.append(kwargs)
        return len(uploaded)

    monkeypatch.setattr(manager, "upload_and_index", fake_upload)
    monkeypatch.setattr(iq, "kb_manager", manager)
    monkeypatch.setattr(iq.video_import, "expand",
                        lambda url: ("", [(url, "第 1 讲")]))

    async def fake_fetch(url, workdir, **kwargs):
        kwargs["on_progress"]("subtitle")
        if "boom" in url:
            raise RuntimeError("字幕和转写都拿不到")
        return "# 讲义\n", {"chars": 3, "source": "x", "source_label": "平台字幕",
                            "title": "第 1 讲"}

    monkeypatch.setattr(iq.video_import, "fetch_lecture", fake_fetch)
    return iq, manager, uploaded


def _run(coro):
    return asyncio.run(coro)


async def _drain(iq_mod, task_id, timeout=5.0):
    store = iq_mod.kb_manager.store(USER)
    for _ in range(int(timeout / 0.01)):
        task = store.get_import_task(task_id)
        if task["status"] in ("done", "failed", "canceled"):
            return task
        await asyncio.sleep(0.01)
    raise AssertionError(f"任务没在 {timeout}s 内收尾：{task}")


def test_multi_links_run_serially_to_done(queue):
    iq_mod, manager, uploaded = queue
    store = manager.store(USER)

    async def scenario():
        result = await iq_mod.submit(USER, ["https://a/1", "https://a/2"], expand=False)
        task = await _drain(iq_mod, result["task_id"])
        return result, task, store.list_import_tasks(USER)[0]

    result, task, detail = _run(scenario())

    assert result["items"] == 2
    assert task["status"] == "done" and task["done"] == 2 and task["failed"] == 0
    assert [item["status"] for item in detail["items"]] == ["done", "done"]
    assert len(uploaded) == 2


def test_failed_item_does_not_block_next(queue):
    iq_mod, manager, uploaded = queue

    async def scenario():
        result = await iq_mod.submit(
            USER, ["https://a/1", "https://a/boom", "https://a/3"], expand=False)
        return await _drain(iq_mod, result["task_id"])

    task = _run(scenario())

    assert task["status"] == "done", "还有成功的集 → 任务整体算完成"
    assert task["done"] == 2 and task["failed"] == 1
    detail = iq_mod.list_tasks(USER)[0]
    failed = [item for item in detail["items"] if item["status"] == "failed"]
    assert failed and "拿不到" in failed[0]["error"]


def test_all_failed_marks_task_failed(queue):
    iq_mod, _, _ = queue

    async def scenario():
        result = await iq_mod.submit(USER, ["https://a/boom"], expand=False)
        return await _drain(iq_mod, result["task_id"])

    assert _run(scenario())["status"] == "failed"


def test_pause_resume_cancel_state_machine(queue):
    iq_mod, manager, _ = queue
    store = manager.store(USER)
    task_id = store.create_import_task(
        USER, [("https://a/1", "一"), ("https://a/2", "二")])

    async def scenario():
        iq_mod.pause(USER, task_id)
        assert store.get_import_task(task_id)["status"] == "paused"
        assert store.next_open_task(USER) is None, "暂停的任务不该被 worker 取走"

        iq_mod.resume(USER, task_id)   # 会起 worker，必须有运行中的 loop
        assert store.get_import_task(task_id)["status"] == "pending"

        iq_mod.cancel(USER, task_id)
        assert store.get_import_task(task_id)["status"] == "canceled"
        assert all(item["status"] == "canceled"
                   for item in store.list_import_tasks(USER)[0]["items"])

    _run(scenario())


def test_reorder_swaps_pending_items(queue):
    iq_mod, manager, _ = queue
    store = manager.store(USER)
    task_id = store.create_import_task(
        USER, [("https://a/1", "一"), ("https://a/2", "二"), ("https://a/3", "三")])
    items = store.list_import_tasks(USER)[0]["items"]

    iq_mod.reorder(USER, task_id, items[2]["id"], -1)

    after = store.list_import_tasks(USER)[0]["items"]
    assert [item["url"] for item in after] == [
        "https://a/1", "https://a/3", "https://a/2"]


def test_slides_pass_asset_key_and_url_prefix(queue, monkeypatch):
    iq_mod, manager, uploaded = queue
    captured = {}

    async def fake_fetch(url, workdir, **kwargs):
        captured.update(kwargs)
        return "# 讲义\n", {"chars": 3, "source": "x", "source_label": "x", "title": "t"}

    monkeypatch.setattr(iq_mod.video_import, "fetch_lecture", fake_fetch)

    async def scenario():
        result = await iq_mod.submit(USER, ["https://a/1"], expand=False, slides=True)
        return await _drain(iq_mod, result["task_id"])

    _run(scenario())

    assert captured["capture_slides"] is True
    asset_key = captured["slide_dir"].name
    assert captured["slide_url_prefix"] == f"/api/v1/kb/slides/{asset_key}/"
    assert uploaded[0]["asset_key"] == asset_key


def test_resume_all_picks_up_unfinished_tasks(queue, monkeypatch):
    iq_mod, manager, _ = queue
    store = manager.store(USER)
    store.create_import_task(USER, [("https://a/1", "一")])

    spawned = []
    monkeypatch.setattr(iq_mod, "ensure_worker", lambda uid: spawned.append(uid))
    iq_mod.resume_all()

    assert spawned == [USER]
