"""课程导入队列单测（全离线：桩掉取词/展开/入库，真跑 worker 与状态机）。

覆盖：
- 一次提交多链接 → 每个链接一集，串行跑完 → 任务 done、计数正确
- 单集失败不拖垮整批：后面的集继续跑，失败原因落在分集里
- 暂停 / 恢复 / 取消 / 重排（协作式，只在集与集之间生效）
- 开截图时把 asset_key 传给入库、把 slide URL 前缀传给取词
- 进程重启后 `resume_all` 能把未完成任务重新挂起
- **进度落库**：阶段与开始时间写进分集（刷新页面后还能还原现场）、分集耗时/ETA
- 分集级操作：单集重试、单集取消（不再只能整批重来）
- 导入前预检：集数 / 总时长 / 字幕可用性 / 缺依赖
- **视频资产层**：有 cues 就把原始时间线落进 `lectures`（url 归一化 + cues 原文可回读）；
  没有 cues 不建行；落库失败不拖垮导入；平台名归一
- **收纳**：合集 → `视频课程/{合集}/{NN 集标题}/讲义.md`；单集 → `视频课程/{标题}/讲义.md`
- **一步到位出讲义**：`lecture` 档位在导入时就落切片 + `structured_at`；
  `transcript` 档位只存转写稿（省额度）
"""
import asyncio
import sqlite3

import pytest

from app.core.kb import import_queue as iq
from app.core.kb.kb_manager import KbManager
from app.core.kb.kb_store import KbStore
from app.core.kb.timeline import load_jsonl

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


def test_legacy_kb_db_gets_stage_columns(tmp_path):
    """老 kb.db 的 import_items 没有 stage/started_at → 打开时必须就地补上。

    `CREATE TABLE IF NOT EXISTS` 不会给已存在的表加字段，漏了补列就会在
    `set_import_item_stage` 上抛 "no such column"（表现为整批导入全失败）。
    """
    data_dir = tmp_path / "kb"
    data_dir.mkdir()
    conn = sqlite3.connect(str(data_dir / "kb.db"))
    conn.execute(
        "CREATE TABLE import_items (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " task_id TEXT, url TEXT, title TEXT, status TEXT, node_id INTEGER,"
        " chars INTEGER, source TEXT, error TEXT, position INTEGER,"
        " created_at TEXT, finished_at TEXT)")
    conn.commit()
    conn.close()

    store = KbStore(data_dir)
    cols = {row["name"] for row in store._conn.execute("PRAGMA table_info(import_items)")}
    store.close()

    assert {"stage", "started_at"} <= cols


def test_stage_and_timing_persist_per_item(queue):
    """刷新页面后要能回答"卡在哪一步、跑了多久" → 阶段与开始时间必须落库。"""
    iq_mod, manager, _ = queue
    store = manager.store(USER)

    async def scenario():
        result = await iq_mod.submit(USER, ["https://a/1"], expand=False)
        return await _drain(iq_mod, result["task_id"])

    _run(scenario())

    item = store.list_import_tasks(USER)[0]["items"][0]
    assert item["stage"] == "subtitle", "取词回调里的阶段要落库（done 不清空）"
    assert item["started_at"], "running 时记 started_at"
    assert item["duration_secs"] is not None, "完成后给得出本集耗时"
    assert item["duration_secs"] >= 0


def test_task_progress_reports_elapsed_and_eta(queue):
    iq_mod, manager, _ = queue
    store = manager.store(USER)
    task_id = store.create_import_task(USER, [("https://a/1", "一"), ("https://a/2", "二")])
    first = store.list_import_tasks(USER)[0]["items"][0]

    # 手工造一集"跑了 10 秒"的历史 → 剩 1 集时 ETA 应约等于 10 秒
    store.set_import_item_status(first["id"], "running")
    store._conn.execute(
        "UPDATE import_items SET started_at = datetime('now', '-10 seconds'),"
        " finished_at = datetime('now'), status = 'done' WHERE id = ?", (first["id"],))
    store._conn.commit()

    progress = store.import_task_progress(task_id)
    assert progress["avg_item_secs"] == 10
    assert progress["eta_secs"] == 10
    assert progress["elapsed_secs"] >= 10


def test_item_level_retry_and_cancel(queue):
    iq_mod, manager, _ = queue
    store = manager.store(USER)
    task_id = store.create_import_task(USER, [("https://a/1", "一"), ("https://a/2", "二")])
    items = store.list_import_tasks(USER)[0]["items"]

    store.set_import_item_status(items[0]["id"], "failed", error="boom")
    assert store.retry_import_item(task_id, items[0]["id"]) is True
    assert store.get_import_task(task_id)["status"] == "pending", "失败集重试要把任务翻回待办"
    after = store.list_import_tasks(USER)[0]["items"][0]
    assert after["status"] == "pending" and after["error"] == ""

    assert store.cancel_import_item(task_id, items[1]["id"]) is True
    assert store.list_import_tasks(USER)[0]["items"][1]["status"] == "canceled"
    assert store.cancel_import_item(task_id, items[0]["id"]) is True
    # 已经开始跑的那集取消不了（协作式：跑完为止）
    store.set_import_item_status(items[1]["id"], "running")
    assert store.cancel_import_item(task_id, items[1]["id"]) is False


def test_inspect_reports_episodes_duration_and_subtitle(queue, monkeypatch):
    iq_mod, _, _ = queue
    monkeypatch.setattr(iq_mod.video_import, "discover", lambda url: (
        "合集",
        [{"url": "https://b/1", "title": "第一讲", "duration": 600},
         {"url": "https://b/2", "title": "第二讲", "duration": 900}],
    ))
    monkeypatch.setattr(iq_mod.video_import, "subtitle_probe",
                        lambda url: "bilibili-track")

    data = _run(iq_mod.inspect(USER, ["https://b/video"]))
    row = data["links"][0]

    assert row["title"] == "合集"
    assert row["episodes"] == 2
    assert row["duration_secs"] == 1500
    assert row["subtitle"] == "bilibili-track"
    assert row["sampled"] is True, "合集只抽查第一集，要标明"
    assert "deps" in data


def test_inspect_keeps_going_when_one_link_fails(queue, monkeypatch):
    iq_mod, _, _ = queue

    def boom(url):
        raise RuntimeError("接口挂了")

    monkeypatch.setattr(iq_mod.video_import, "discover", boom)

    data = _run(iq_mod.inspect(USER, ["https://b/bad"]))
    row = data["links"][0]

    assert "接口挂了" in row["note"], "单条失败写进 note，不炸整批"
    assert row["episodes"] == 0


# ── 视频资产层：导入时顺手把原始时间线落进 `lectures`（2026-11-15 二期）──
#
# 为什么要有这一层：原先转写正文直接覆盖进 documents.extract_text，改一次提示词
# 就要重花一次 ASR 的钱，而且**视频链接根本没存**。现在 cues 落到 `lectures.raw_timeline`
# （JSONL，只追加不改），`/kb/lecture/{id}/restructure` 直接读它重跑结构化。

def _with_cues(monkeypatch, iq_mod, cues, frames=None, title="第 1 讲"):
    async def fake_fetch(url, workdir, **kwargs):
        meta = {"chars": 3, "source": "bilibili-track", "source_label": "平台字幕",
                "title": title, "cues": cues}
        if frames is not None:
            meta["frames"] = frames
        return "# 讲义\n", meta

    monkeypatch.setattr(iq_mod.video_import, "fetch_lecture", fake_fetch)


def test_import_stores_raw_timeline(queue, monkeypatch):
    """导入成功 → `lectures` 有 url/title/raw_timeline，且 cues 原文可回读。"""
    iq_mod, manager, _ = queue
    _with_cues(monkeypatch, iq_mod,
               [{"start": 0.0, "end": 5.0, "text": "第一句。", "raw": "第一句。"},
                {"start": 5.0, "end": 9.0, "text": "第二句。", "raw": "第二具。"}],
               frames=[{"t": 0.0, "image": "0001.jpg"}])

    async def scenario():
        result = await iq_mod.submit(
            USER, ["https://www.bilibili.com/video/BV1xx411c7mD"], expand=False)
        return await _drain(iq_mod, result["task_id"])

    task = _run(scenario())
    store = manager.store(USER)
    docs = store.list_lecture_docs()
    assert len(docs) == 1, "有 cues 的导入要留下视频资产行"
    lecture = store.get_lecture(docs[0])
    assert lecture["url"] == "https://www.bilibili.com/video/BV1xx411c7mD"
    assert lecture["title"] == "第 1 讲"
    assert lecture["source_kind"] == "bilibili-track"
    assert lecture["platform"] == "bilibili"
    assert lecture["video_id"] == "BV1xx411c7mD"
    assert lecture["raw_timeline"], "原始时间线必须落库（重跑结构化不重花 ASR 的钱）"
    frames, speech = load_jsonl(lecture["raw_timeline"])
    assert [f.image for f in frames] == ["0001.jpg"]
    assert [e.raw for e in speech] == ["第一句。", "第二具。"]
    assert task["status"] == "done"


def test_import_without_cues_writes_no_lecture_row(queue, monkeypatch):
    """没有 cues（老调用方 / 字幕缺失）→ **不建 lectures 行**：没有原料的视频资产毫无用处。"""
    iq_mod, manager, _ = queue

    async def fake_fetch(url, workdir, **kwargs):
        return "# 讲义\n", {"chars": 3, "source": "x", "source_label": "x", "title": "t"}

    monkeypatch.setattr(iq_mod.video_import, "fetch_lecture", fake_fetch)

    async def scenario():
        result = await iq_mod.submit(USER, ["https://a/1"], expand=False)
        return await _drain(iq_mod, result["task_id"])

    _run(scenario())
    assert manager.store(USER).list_lecture_docs() == []


def test_timeline_failure_does_not_break_import(queue, monkeypatch):
    """时间线落库失败**绝不能**拖垮导入（那集讲义已经生成好了）。"""
    iq_mod, manager, uploaded = queue
    _with_cues(monkeypatch, iq_mod, [{"start": 0.0, "end": 1.0, "text": "一", "raw": None}])

    def boom(*a, **kw):
        raise RuntimeError("磁盘满了")

    monkeypatch.setattr(KbStore, "upsert_lecture", boom)   # 存储层抛错，主流程照走

    async def scenario():
        result = await iq_mod.submit(USER, ["https://a/1"], expand=False)
        return await _drain(iq_mod, result["task_id"])

    task = _run(scenario())
    assert task["status"] == "done", "导入仍然成功"
    assert len(uploaded) == 1


def test_platform_of_covers_bilibili_youtube_local_and_other():
    """平台名归一：bilibili / youtube / local / other（别的站不硬塞成 youtube）。"""
    from app.core.kb.bili import urls as bili_urls

    assert iq._platform_of(bili_urls.normalize("BV1xx411c7mD"), "") == "bilibili"
    assert iq._platform_of(bili_urls.normalize("https://youtu.be/abc"),
                           "https://youtu.be/abc") == "youtube"
    assert iq._platform_of(bili_urls.normalize("https://www.youtube.com/watch?v=abc"),
                           "https://www.youtube.com/watch?v=abc") == "youtube"
    assert iq._platform_of(bili_urls.normalize("file:///C:/a.mp4"),
                           "file:///C:/a.mp4") == "local"
    assert iq._platform_of(bili_urls.normalize("https://coursera.org/x"),
                           "https://coursera.org/x") == "other"


# ── 收纳：按视频 / 合集落成文件夹 ────────────────────────────────
#
# 原先所有集都平铺在根目录（合集只建一个空壳目录），几十集混在一起没法找。
# 现在复用知识库目录树：`视频课程/{合集}/{NN 集标题}/讲义.md`，序号前缀保证
# 目录按讲次排列（节点按 name 排序，中文标题本身不保序）。

def _chain_names(manager, node_id, limit=5):
    """从节点向上取目录链（`[集, 合集, 书架]`），用来断言收纳层级。"""
    names = []
    node = manager.get_node(USER, node_id)
    while node and len(names) < limit:
        names.append(node["name"])
        if node["parent_id"] is None:
            break
        node = manager.get_node(USER, node["parent_id"])
    return names


def test_collection_import_nests_each_episode_in_its_own_folder(queue, monkeypatch):
    iq_mod, manager, uploaded = queue
    monkeypatch.setattr(
        iq_mod.video_import, "expand",
        lambda url: ("高数下册", [(url, "第 1 讲"), (url + "?p=2", "第 2 讲")]))

    async def fake_fetch(url, workdir, **kwargs):
        title = "第 2 讲" if "p=2" in url else "第 1 讲"
        return "# 转写\n", {"chars": 3, "source": "x", "source_label": "平台字幕",
                             "title": title}

    monkeypatch.setattr(iq_mod.video_import, "fetch_lecture", fake_fetch)

    async def scenario():
        result = await iq_mod.submit(
            USER, ["https://www.bilibili.com/video/BV1xx411c7mD"],
            expand=True, lecture_level="transcript")
        return await _drain(iq_mod, result["task_id"])

    _run(scenario())

    assert len(uploaded) == 2
    for upload, episode in zip(uploaded, ("01 第 1 讲", "02 第 2 讲")):
        assert _chain_names(manager, upload["parent_id"]) == [episode, "高数下册",
                                                              iq.VIDEO_SHELF]
        assert upload["filename"] == iq.LECTURE_FILE_NAME


def test_single_video_gets_its_own_folder(queue):
    """单集不套合集层：`视频课程/{标题}/讲义.md`，点两层就看到讲义。"""
    iq_mod, manager, uploaded = queue

    async def scenario():
        result = await iq_mod.submit(USER, ["https://a/1"], expand=False,
                                     lecture_level="transcript")
        return await _drain(iq_mod, result["task_id"])

    _run(scenario())

    assert _chain_names(manager, uploaded[0]["parent_id"]) == ["第 1 讲", iq.VIDEO_SHELF]


def test_transcript_level_skips_lecture_builder(queue, monkeypatch):
    """`transcript` 档位只存转写稿：结构化很贵，不该在"只想留个字幕"时偷偷跑。"""
    iq_mod, manager, uploaded = queue
    called = []

    async def fake_build(*args, **kwargs):
        called.append(kwargs)
        return {"text": "# 讲义\n", "segments": [], "degraded": True, "note": ""}

    monkeypatch.setattr(iq_mod.lecture_builder, "build", fake_build)
    _with_cues(monkeypatch, iq_mod, [{"start": 0.0, "end": 5.0, "text": "第一句。"}])

    async def scenario():
        result = await iq_mod.submit(USER, ["https://a/1"], expand=False,
                                     lecture_level="transcript")
        return await _drain(iq_mod, result["task_id"])

    assert _run(scenario())["status"] == "done"
    assert called == []
    assert uploaded[0]["content"] == "# 讲义\n".encode("utf-8")


def test_lecture_level_writes_segments_and_marks_structured(queue, monkeypatch):
    """`lecture` 档位（默认）：导入即落讲义正文 + 切片 + `structured_at`。

    `structured_at` 是目录树「看讲义」入口与课时页时间轴的唯一判据 ——
    不写它，用户导入完仍然看到"未结构化"，等于还得手动点一次。
    """
    iq_mod, manager, uploaded = queue
    segments = [{"seq": 1, "title": "定义", "kind": "definition", "brief": "先立定义",
                 "content": "正文", "t_start": 0.0, "t_end": 30.0,
                 "slide": "", "node_id": ""}]

    async def fake_build(*args, **kwargs):
        return {"text": "# 讲义\n\n## 1. 定义\n\n正文\n", "segments": segments,
                "degraded": False, "note": ""}

    monkeypatch.setattr(iq_mod.lecture_builder, "build", fake_build)
    _with_cues(monkeypatch, iq_mod, [{"start": 0.0, "end": 30.0, "text": "定义是这样。"}],
               title="第 1 讲")

    async def scenario():
        result = await iq_mod.submit(USER, ["https://a/1"], expand=False)
        return await _drain(iq_mod, result["task_id"])

    assert _run(scenario())["status"] == "done"
    assert uploaded[0]["content"].startswith("# 讲义".encode("utf-8")), \
        "入库正文 = 构建器产出的讲义（不是 fetch 的转写稿）"

    store = manager.store(USER)
    doc = store.list_lecture_docs()[0]
    assert store.get_lecture(doc)["structured_at"], "导入即结构化，别再等用户手点"
    assert [s["title"] for s in store.list_lecture_segments(doc)] == ["定义"]
