"""结构化讲义四个端点的 HTTP 契约（全离线：临时 KB + 临时图谱 + 临时 RAG）。

为什么这些端点要单独测：它们是**前端已按契约写好**的接口，字段名与空值语义
一旦漂移，前端不会报错、只是面板空白（最难排查的一类）。所以每条断言都对着
模块 docstring 里的契约示例写，而不是对着当前实现写。

覆盖（每个端点都有正常路径 + 失败路径）：
- `GET /kb/lecture/{doc_id}`
  - 正常：视频身份（url/platform/uploader/duration）+ `structured` + 每段时间/链接/截图
  - `watch_url` / `t_url` 由后端拼好（含替换旧 `t` 的规则，前端不自己拼）
  - `slide_url` 拼 `asset_key` 目录；无 `asset_key` → 空串
  - `node_ok`：节点存在 true / 悬空 false（**跨库软引用降级，不 404**）
  - 没有 `lectures` 行 → 404 `[E-KB-001]`（前端据此当普通文件处理）
  - 未认证 → 401
- `GET /kb/lecture/{doc_id}/timeline`
  - 正常：cues + frames 来自 `raw_timeline`
  - `raw_timeline` 为空 → 两个空数组（**不 500**），frames 用切片截图兜底
  - 没有 `lectures` 行 → 404
- `POST /kb/lecture/{doc_id}/restructure`
  - 正常：入队即返回 `accepted`（不在请求内 await —— 300s 代理超时等不起）
  - 没有 `lectures` 行 → 404；`raw_timeline` 空 → 404 `[E-KB-002]`；已在跑 → 409 `[E-KB-003]`
  - 后台任务：切片落库 + `structured_at` 写入 + `kb_updated` 通知 + **锁在 finally 释放**
  - 后台任务异常 / 取消也发通知（漏一条前端永远停在"结构化中"）
- `GET /knowledge/node/{node_id}/video-sources`
  - 正常：doc_name / locator=t=754 / t_label / t_url / title
  - 节点不存在 → 空 `sources`（**不 404**）；没有切片 → 空
  - 视频没有 URL → `t_url` 空串（但 locator 仍在）
"""
import asyncio
import importlib
import urllib.parse

import pytest
from fastapi.testclient import TestClient

kb_manager_module = importlib.import_module("app.core.kb.kb_manager")
from app.api.v1 import kb as kb_module
from app.api.v1.kb import _run_restructure
from app.core.event_bus import KB_UPDATED
from app.core.kb.kb_manager import KbManager
from app.core.kb.timeline import dump_jsonl, FrameEvent, TranscriptEvent
from app.core.knowledge_graph import KnowledgeGraph

USER = 7
OTHER = 8
ASSET_KEY = "a" * 32
VIDEO_URL = "https://www.bilibili.com/video/BV1xx?p=26"


# ── 固定装置 ──────────────────────────────────────────────────

def _add_lecture(km: KbManager, user_id: int, name: str, asset_key: str = "",
                 text: str = "讲义正文") -> int:
    return km._get_store(user_id).add_file(
        user_id=user_id, name=name, parent_id=None, file_type=".md",
        extract_text=text, file_size=len(text.encode("utf-8")), asset_key=asset_key,
    )


def _raw_timeline() -> str:
    """一段真实形状的原始时间线：两张截图 + 两句语音（第二句带 ASR 原文）。"""
    return dump_jsonl(
        [FrameEvent(t=720.0, image="0001.jpg"), FrameEvent(t=1000.0, image="0002.jpg")],
        [TranscriptEvent(start=754.0, end=800.0, text="拉普拉斯变换的微分性质。",
                         raw="拉拉斯变换的微分性质"),
         TranscriptEvent(start=1000.0, end=1010.0, text="第二个知识点。")],
    )


@pytest.fixture
def api(tmp_path, monkeypatch):
    """临时 KB + 临时图谱（`KnowledgeGraph` 的 data_dir 指到 tmp_path，别碰真实库）。"""
    from app.main import app
    from app.core.auth import get_current_user

    km = KbManager(tmp_path / "kb")
    monkeypatch.setattr(kb_manager_module, "kb_manager", km)
    monkeypatch.setattr("app.api.v1.kb.kb_manager", km)
    monkeypatch.setattr("app.api.v1.knowledge.kb_manager", km)

    kg_data = tmp_path / "kg"
    monkeypatch.setattr(kb_module, "KnowledgeGraph",
                        lambda user_id: KnowledgeGraph(user_id=user_id, data_dir=kg_data))

    store = km._get_store(USER)

    # ① 结构化讲义：有 lectures 行 + 两段切片 + 截图目录
    doc = _add_lecture(km, USER, "第二十六讲 拉普拉斯变换解微分方程.md", ASSET_KEY)
    store.upsert_lecture(
        doc, url=VIDEO_URL, platform="bilibili", video_id="BV1xx", page=26,
        title="第二十六讲 拉普拉斯变换解微分方程", uploader="翔翔的学习频道",
        duration=3725.0, source_kind="bilibili-track",
        raw_timeline=_raw_timeline(), structured_at="2026-11-15T20:00:00",
    )
    store.replace_lecture_segments(doc, [
        {"seq": 1, "title": "拉普拉斯变换的微分性质", "kind": "formula",
         "brief": "n 阶导数的 s 域表达", "content": "## 微分性质\n\nL{f'} = sF(s) - f(0)",
         "t_start": 754.0, "t_end": 1020.0, "slide": "0001.jpg", "node_id": "node-ok"},
        {"seq": 2, "title": "逆变换", "kind": "custom", "brief": "", "content": "",
         "t_start": 1020.0, "t_end": 1400.0, "slide": "", "node_id": "node-gone"},
    ])

    # ② 有视频资产但还没结构化、且没有原始时间线
    pending = _add_lecture(km, USER, "第二十七讲.md")
    store.upsert_lecture(pending, url="https://www.bilibili.com/video/BV1xx?p=27",
                         title="第二十七讲", duration=600.0)

    # ③ 普通上传的文件（**没有** lectures 行）—— 404 的正常分支
    plain = _add_lecture(km, USER, "普通笔记.md", text="不是我")

    # ④ 别人的视频（隔离：per-user 库各自编号）
    other = _add_lecture(km, OTHER, "别人的讲义.md")

    # 图谱：一个真节点（node_ok=true）+ 一个只在切片里出现过的悬空 id（node_ok=false）
    kg = KnowledgeGraph(user_id=USER, data_dir=kg_data)
    try:
        with kg._conn:
            kg._conn.execute(
                "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (?, ?, ?)",
                (USER, "lecture_api", "x"))
        kg.create_node_with_content(
            {"id": "node-ok", "name": "拉普拉斯变换的微分性质", "subject": "信号与系统"},
            "# 拉普拉斯变换的微分性质\n\nL{f'} = sF(s) - f(0)\n")
    finally:
        kg.close()

    app.dependency_overrides[get_current_user] = lambda: USER
    yield {"client": TestClient(app), "km": km, "store": store,
           "doc": doc, "pending": pending, "plain": plain, "other": other}
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _clear_restructure_inflight():
    """进程内锁是模块级全局：用例前后都清，避免互相污染。"""
    kb_module._RESTRUCTURE_INFLIGHT.clear()
    yield
    kb_module._RESTRUCTURE_INFLIGHT.clear()


# ══════════════════════════════════════════════════════════════
#  1. GET /kb/lecture/{doc_id}
# ══════════════════════════════════════════════════════════════

def test_get_lecture_returns_identity_and_segments(api):
    resp = api["client"].get(f"/api/v1/kb/lecture/{api['doc']}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["doc_id"] == api["doc"]
    assert body["title"] == "第二十六讲 拉普拉斯变换解微分方程"
    assert body["url"] == VIDEO_URL
    assert body["platform"] == "bilibili"
    assert body["uploader"] == "翔翔的学习频道"
    assert body["duration"] == 3725.0
    assert body["structured"] is True
    assert len(body["segments"]) == 2
    assert [s["title"] for s in body["segments"]] == ["拉普拉斯变换的微分性质", "逆变换"]
    # `structured_at` 单列：前端重跑结构化时靠"它变了没有"判断这一轮跑完了没有。
    # 只给 `structured` 布尔的话，已结构化的再重跑会判成"立刻完成"。
    assert body["structured_at"] == "2026-11-15T20:00:00"


def test_get_lecture_unstructured_has_empty_structured_at(api):
    """未结构化时 `structured` 为 false 且 `structured_at` 是空串（两者必须一致）。"""
    store = api["store"]
    doc = _add_lecture(api["km"], USER, "未结构化.md")
    store.upsert_lecture(doc, url=VIDEO_URL, title="未结构化")

    body = api["client"].get(f"/api/v1/kb/lecture/{doc}").json()
    assert body["structured"] is False
    assert body["structured_at"] == ""


def test_get_lecture_segment_fields_match_contract(api):
    """每段字段名与语义逐条锁住（前端按这些键渲染时间轴）。"""
    seg = api["client"].get(f"/api/v1/kb/lecture/{api['doc']}").json()["segments"][0]

    assert seg["seq"] == 1
    assert seg["kind"] == "formula"
    assert seg["brief"] == "n 阶导数的 s 域表达"
    assert seg["content"].startswith("## 微分性质")
    assert seg["t_start"] == 754.0 and seg["t_end"] == 1020.0
    assert seg["t_label"] == "12:34", "t_label 必须是 mm:ss（≥1h 才带小时）"
    assert seg["t_url"] == f"{VIDEO_URL}&t=754"
    assert seg["slide_url"] == f"/api/v1/kb/slides/{ASSET_KEY}/0001.jpg"
    assert seg["node_id"] == "node-ok"
    assert seg["node_ok"] is True


def test_get_lecture_blank_slide_gives_empty_url(api):
    """没有截图的切片 → `slide_url` 空串（前端据此不渲染 <img>，不是 404）。"""
    seg = api["client"].get(f"/api/v1/kb/lecture/{api['doc']}").json()["segments"][1]
    assert seg["slide_url"] == ""


def test_get_lecture_without_asset_key_gives_empty_slide_url(api):
    """`documents.asset_key` 为空（老数据 / 没抽截图）→ slide_url 空串。"""
    store = api["store"]
    doc = _add_lecture(api["km"], USER, "无截图讲义.md")
    store.upsert_lecture(doc, url=VIDEO_URL, title="无截图")
    store.replace_lecture_segments(doc, [
        {"seq": 1, "title": "一", "t_start": 10.0, "t_end": 20.0, "slide": "0001.jpg"}])

    seg = api["client"].get(f"/api/v1/kb/lecture/{doc}").json()["segments"][0]
    assert seg["slide_url"] == "", "没有 asset_key 就拼不出目录，只能空"


def test_get_lecture_dangling_node_degrades_to_false(api):
    """悬空 `node_id`（跨库软引用失效）→ `node_ok: false`，**整段仍然返回**。

    这条是这套设计的核心取舍：切片不能因为图谱节点被删就消失 ——
    时间轴是视频自己的事实，节点关联只是它的一个标注。
    """
    seg = api["client"].get(f"/api/v1/kb/lecture/{api['doc']}").json()["segments"][1]
    assert seg["node_id"] == "node-gone"
    assert seg["node_ok"] is False


def test_get_lecture_empty_node_id_is_not_ok(api):
    """空 `node_id` → `node_ok: false`（不是 None —— 前端只需判真假）。"""
    doc = _add_lecture(api["km"], USER, "无关联.md")
    api["store"].upsert_lecture(doc, url=VIDEO_URL)
    api["store"].replace_lecture_segments(doc, [
        {"seq": 1, "title": "一", "t_start": 0.0, "t_end": 10.0}])

    seg = api["client"].get(f"/api/v1/kb/lecture/{doc}").json()["segments"][0]
    assert seg["node_id"] == "" and seg["node_ok"] is False


def test_get_lecture_watch_url_points_at_first_segment(api):
    """`watch_url` = 第一个知识点的时间点（"从头看这一集"的反面：直接跳到学的地方）。"""
    body = api["client"].get(f"/api/v1/kb/lecture/{api['doc']}").json()
    assert body["watch_url"] == f"{VIDEO_URL}&t=754"


def test_get_lecture_replaces_existing_t_param(api):
    """视频链接里已有 `t` 时**替换**而不是追加第二个（前端直接用它当 href）。"""
    doc = _add_lecture(api["km"], USER, "带旧时间戳.md")
    api["store"].upsert_lecture(doc, url="https://www.youtube.com/watch?v=abc&t=4&list=PLx",
                                title="带旧 t")
    api["store"].replace_lecture_segments(doc, [
        {"seq": 1, "title": "一", "t_start": 90.0, "t_end": 100.0}])

    body = api["client"].get(f"/api/v1/kb/lecture/{doc}").json()
    assert body["watch_url"] == "https://www.youtube.com/watch?v=abc&list=PLx&t=90"
    assert body["segments"][0]["t_url"] == body["watch_url"]
    query = urllib.parse.parse_qs(urllib.parse.urlparse(body["watch_url"]).query)
    assert query["t"] == ["90"], "旧 t 必须被**替换**，不能留下两个 t 参数"


def test_get_lecture_without_url_gives_empty_links(api):
    """没有视频链接（本地文件 / 老数据）→ `watch_url` 与 `t_url` 空串，其余照常。"""
    doc = _add_lecture(api["km"], USER, "无链接.md")
    api["store"].upsert_lecture(doc, title="无链接", platform="local")
    api["store"].replace_lecture_segments(doc, [
        {"seq": 1, "title": "一", "t_start": 65.0, "t_end": 70.0}])

    body = api["client"].get(f"/api/v1/kb/lecture/{doc}").json()
    assert body["url"] == ""
    assert body["watch_url"] == ""
    assert body["segments"][0]["t_url"] == ""
    assert body["segments"][0]["t_label"] == "01:05", "没有链接也照样给可读时间"


def test_get_lecture_no_segments_gives_empty_watch_url(api):
    """有视频资产但还没切片 → `segments: []` 且 `watch_url` 空串（不能说"跳到 t=0"）。"""
    body = api["client"].get(f"/api/v1/kb/lecture/{api['pending']}").json()
    assert body["segments"] == []
    assert body["watch_url"] == ""
    assert body["structured"] is False, "structured_at 为 NULL → 未结构化"


def test_get_plain_file_returns_404_not_lecture(api):
    """普通文件 → 404 `[E-KB-001]`：前端据此回退到"普通文件"渲染（**正常分支**）。"""
    resp = api["client"].get(f"/api/v1/kb/lecture/{api['plain']}")

    assert resp.status_code == 404
    assert resp.json()["detail"].startswith("[E-KB-001]")


def test_get_lecture_empty_raw_timeline_is_still_200(api):
    """结构化状态与原始时间线解耦：没有 raw_timeline 也能读切片。"""
    doc = api["pending"]
    api["store"].replace_lecture_segments(doc, [
        {"seq": 1, "title": "一", "t_start": 0.0, "t_end": 10.0}])

    resp = api["client"].get(f"/api/v1/kb/lecture/{doc}")
    assert resp.status_code == 200
    assert len(resp.json()["segments"]) == 1


def test_get_lecture_requires_auth(api):
    from app.main import app
    from app.core.auth import get_current_user

    app.dependency_overrides.pop(get_current_user, None)
    try:
        assert api["client"].get(f"/api/v1/kb/lecture/{api['doc']}").status_code == 401
    finally:
        app.dependency_overrides[get_current_user] = lambda: USER


# ══════════════════════════════════════════════════════════════
#  2. GET /kb/lecture/{doc_id}/timeline
# ══════════════════════════════════════════════════════════════

def test_timeline_returns_cues_and_frames(api):
    resp = api["client"].get(f"/api/v1/kb/lecture/{api['doc']}/timeline")

    assert resp.status_code == 200
    body = resp.json()
    assert [f["image"] for f in body["frames"]] == ["0001.jpg", "0002.jpg"]
    assert body["frames"][0]["t"] == 720.0
    assert len(body["cues"]) == 2
    first = body["cues"][0]
    assert first["start"] == 754.0 and first["end"] == 800.0
    assert first["text"] == "拉普拉斯变换的微分性质。"
    assert first["raw"] == "拉拉斯变换的微分性质", "原文必须一起给（校对可回退）"
    assert body["cues"][1]["raw"] is None, "没有原文就是 None，不是空串"


def test_timeline_empty_raw_timeline_returns_empty_arrays(api):
    """`raw_timeline` 为空 → 两个空数组，**不 500 不 404**（老数据是常态，不是故障）。"""
    resp = api["client"].get(f"/api/v1/kb/lecture/{api['pending']}/timeline")

    assert resp.status_code == 200
    assert resp.json() == {"cues": [], "frames": []}


def test_timeline_falls_back_to_segment_slides(api):
    """`raw_timeline` 里没有帧（老数据只存了 cues）→ 用切片的截图当作帧。"""
    store = api["store"]
    doc = _add_lecture(api["km"], USER, "只有切片.md")
    store.upsert_lecture(
        doc, url=VIDEO_URL,
        raw_timeline=dump_jsonl([], [TranscriptEvent(start=1.0, end=2.0, text="就一句")]))
    store.replace_lecture_segments(doc, [
        {"seq": 1, "title": "一", "t_start": 1.0, "t_end": 60.0, "slide": "0007.jpg"}])

    body = api["client"].get(f"/api/v1/kb/lecture/{doc}/timeline").json()
    assert body["frames"] == [{"t": 1.0, "image": "0007.jpg"}]
    assert len(body["cues"]) == 1


def test_timeline_plain_file_returns_404(api):
    resp = api["client"].get(f"/api/v1/kb/lecture/{api['plain']}/timeline")
    assert resp.status_code == 404
    assert resp.json()["detail"].startswith("[E-KB-001]")


# ══════════════════════════════════════════════════════════════
#  3. POST /kb/lecture/{doc_id}/restructure
# ══════════════════════════════════════════════════════════════

@pytest.fixture
def spawned(monkeypatch):
    """截获 `runtime.spawn`：只记录协程并关掉（不真的跑），断言"端点把活交出去了"。"""
    coros = []

    def fake_spawn(coro):
        coros.append(coro)
        coro.close()                 # 不让 asyncio 报 "coroutine was never awaited"
        return None

    monkeypatch.setattr(kb_module.runtime, "spawn", fake_spawn)
    return coros


def test_restructure_returns_accepted_immediately(api, spawned):
    """入队即返回 —— 请求内不 await（Vite/apiClient 的 300s 代理超时等不起）。"""
    resp = api["client"].post(f"/api/v1/kb/lecture/{api['doc']}/restructure")

    assert resp.status_code == 200
    assert resp.json() == {"status": "accepted"}
    assert len(spawned) == 1, "必须交给 runtime.spawn，而不是在请求内 await"
    assert kb_module._RESTRUCTURE_INFLIGHT.get(api["doc"]) is True, "锁在后台任务释放前应被占"


def test_restructure_plain_file_returns_404(api, spawned):
    resp = api["client"].post(f"/api/v1/kb/lecture/{api['plain']}/restructure")
    assert resp.status_code == 404
    assert resp.json()["detail"].startswith("[E-KB-001]")
    assert spawned == []


def test_restructure_without_raw_timeline_returns_404(api, spawned):
    """没有原始时间线 → **不能**重跑（重跑等于重新 ASR，冤枉钱）：直接 404 E-KB-002。"""
    resp = api["client"].post(f"/api/v1/kb/lecture/{api['pending']}/restructure")

    assert resp.status_code == 404
    assert resp.json()["detail"].startswith("[E-KB-002]")
    assert spawned == []


def test_restructure_while_inflight_returns_409(api, spawned):
    """已在跑 → 409（否则点两下就并发写同一集的切片）。"""
    kb_module._RESTRUCTURE_INFLIGHT[api["doc"]] = True

    resp = api["client"].post(f"/api/v1/kb/lecture/{api['doc']}/restructure")
    assert resp.status_code == 409
    assert resp.json()["detail"].startswith("[E-KB-003]")
    assert spawned == []


# ── 后台任务本体（直接驱动协程，确定性）──────────────────────────

@pytest.fixture
def sent(monkeypatch):
    calls = []
    monkeypatch.setattr(kb_module, "notify",
                        lambda uid, et, data=None, **kw: calls.append(
                            {"user_id": uid, "type": et, "data": data or {}}))
    return calls


def _occupy(doc_id: int) -> None:
    """模拟端点已完成占位（`_run_restructure` 假定锁已被端点拿到）。"""
    kb_module._RESTRUCTURE_INFLIGHT[doc_id] = True


def test_restructure_task_writes_segments_and_notifies(api, sent):
    """成功路径：切片落库 + `structured_at` 写入 + 通知 + 锁释放。"""
    doc = api["doc"]
    _occupy(doc)
    asyncio.run(_run_restructure(USER, doc, "第二十六讲.md"))

    lecture = api["store"].get_lecture(doc)
    assert lecture["structured_at"], "必须有结构化时间戳（NULL 表示未结构化）"
    segments = api["store"].list_lecture_segments(doc)
    assert segments, "兜底切片也必须落库（否则前端点开是空的）"
    assert all(s["t_end"] >= s["t_start"] for s in segments)
    assert segments[0]["t_start"] == 0.0, "兜底是按整集时长等分，从 0 起"
    assert segments[-1]["t_end"] == 3725.0, "最后一段要覆盖到媒体末尾"

    assert len(sent) == 1, "恰好一条通知（多投前端会重复弹提示）"
    assert sent[0]["user_id"] == USER and sent[0]["type"] == KB_UPDATED
    assert sent[0]["data"]["lecture"] is True
    assert sent[0]["data"]["structured"] is True
    assert sent[0]["data"]["node_id"] == doc
    assert not kb_module._RESTRUCTURE_INFLIGHT, "锁必须在 finally 里释放"


def test_restructure_notify_and_db_share_one_timestamp(api, sent):
    """通知里的 `structured_at` 必须与落库值**逐字节相同**。

    前端的完成判据是「详情接口的 `structured_at` != 提交时记下的旧值」，而通知
    到达时它也拿通知里的值做一次比对。两处各调一次 `_now_iso()` 会差一秒，
    比对就永远不成立 → 前端死等 60 轮然后提示"还在处理中"。
    """
    doc = api["doc"]
    _occupy(doc)
    asyncio.run(_run_restructure(USER, doc, "第二十六讲.md"))

    stored = api["store"].get_lecture(doc)["structured_at"]
    assert sent[0]["data"]["structured_at"] == stored, "通知与落库必须是同一个时间戳"


def test_restructure_failure_notify_carries_empty_timestamp(api, sent, monkeypatch):
    """失败时通知里的 `structured_at` 是空串（没跑完就不该让前端以为跑完了）。"""
    doc = api["doc"]
    monkeypatch.setattr(kb_module.lecture_structurer, "structure",
                        lambda *a, **k: {"segments": [], "degraded": True, "note": ""})
    _occupy(doc)
    asyncio.run(_run_restructure(USER, doc, "第二十六讲.md"))

    assert sent[0]["data"]["structured"] is False
    assert sent[0]["data"]["structured_at"] == ""


def test_restructure_task_keeps_raw_timeline(api, sent):
    """重跑**不重新取词**：原始时间线必须原样留着（再改一次提示词不用再花 ASR 的钱）。"""
    doc = api["doc"]
    before = api["store"].get_lecture(doc)["raw_timeline"]
    _occupy(doc)
    asyncio.run(_run_restructure(USER, doc, "第二十六讲.md"))

    after = api["store"].get_lecture(doc)["raw_timeline"]
    assert after == before


def test_restructure_task_replaces_old_segments(api, sent):
    """整批替换：上一版多出来的切片不能留（否则用户看到"删不掉的知识点"）。"""
    doc = api["doc"]
    assert len(api["store"].list_lecture_segments(doc)) == 2
    _occupy(doc)
    asyncio.run(_run_restructure(USER, doc, "第二十六讲.md"))

    seqs = [s["seq"] for s in api["store"].list_lecture_segments(doc)]
    assert seqs == list(range(1, len(seqs) + 1)), "seq 必须从 1 连续（时间轴按它排序）"


def test_restructure_task_releases_lock_on_error(api, sent, monkeypatch):
    """后台任务异常 → 通知 + **锁照样释放**（否则该文件被永久锁死，此后每次 409）。"""
    def boom(*a, **kw):
        raise RuntimeError("切分挂了")

    monkeypatch.setattr(kb_module.lecture_structurer, "structure", boom)
    doc = api["doc"]
    _occupy(doc)
    asyncio.run(_run_restructure(USER, doc, "第二十六讲.md"))   # 不向任何人抛

    assert not kb_module._RESTRUCTURE_INFLIGHT
    assert len(sent) == 1
    assert sent[0]["data"]["structured"] is False
    assert "切分挂了" in sent[0]["data"]["error"]


def test_restructure_task_notifies_on_cancel(api, sent):
    """取消（服务关停，`CancelledError` 属 BaseException）→ 也得发通知 + 释放锁。"""
    doc = api["doc"]

    async def scenario():
        _occupy(doc)
        task = asyncio.ensure_future(_run_restructure(USER, doc, "第二十六讲.md"))
        await asyncio.sleep(0)          # 让任务进到第一个 await 之前
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())

    assert not kb_module._RESTRUCTURE_INFLIGHT
    assert len(sent) == 1
    assert sent[0]["data"]["structured"] is False
    assert sent[0]["data"]["error"], "取消也要给一句原因（前端不能停在无限加载）"


def test_restructure_task_notify_failure_does_not_raise(api, monkeypatch):
    """发通知本身抛错不能顶掉结果（`finally` 里的异常会覆盖原错误）。"""
    def boom(*a, **kw):
        raise RuntimeError("总线挂了")

    monkeypatch.setattr(kb_module, "notify", boom)
    doc = api["doc"]
    _occupy(doc)
    asyncio.run(_run_restructure(USER, doc, "第二十六讲.md"))   # 不能抛

    assert api["store"].get_lecture(doc)["structured_at"], "落库不受通知失败影响"


# ══════════════════════════════════════════════════════════════
#  4. GET /knowledge/node/{node_id}/video-sources
# ══════════════════════════════════════════════════════════════

def _video_sources(api, node_id: str):
    return api["client"].get(f"/api/v1/knowledge/node/{node_id}/video-sources")


def test_video_sources_lists_time_points(api):
    resp = _video_sources(api, "node-ok")

    assert resp.status_code == 200
    sources = resp.json()["sources"]
    assert len(sources) == 1
    got = sources[0]
    assert got["doc_id"] == api["doc"]
    assert got["doc_name"] == "第二十六讲 拉普拉斯变换解微分方程.md"
    assert got["locator"] == "t=754", "locator 是机器可读的第 6 个来源键"
    assert got["t_label"] == "12:34"
    assert got["t_url"] == f"{VIDEO_URL}&t=754"
    assert got["title"] == "拉普拉斯变换的微分性质"


def test_video_sources_unknown_node_returns_empty(api):
    """节点不存在 → 空 `sources`，**不 404**：UI 上两者都是"没有出处"。"""
    resp = _video_sources(api, "node-does-not-exist")

    assert resp.status_code == 200
    assert resp.json() == {"sources": []}


def test_video_sources_node_without_segments_returns_empty(api):
    assert _video_sources(api, "node-ok-but-unused").json() == {"sources": []}


def test_video_sources_without_url_keeps_locator(api):
    """视频没有链接（本地文件）→ `t_url` 空串，但 `locator`/`t_label` 仍在。"""
    store = api["store"]
    doc = _add_lecture(api["km"], USER, "本地视频.md")
    store.upsert_lecture(doc, title="本地视频", platform="local")
    store.replace_lecture_segments(doc, [
        {"seq": 1, "title": "本地的一节", "t_start": 65.0, "t_end": 70.0,
         "node_id": "node-local"}])

    got = _video_sources(api, "node-local").json()["sources"][0]
    assert got["locator"] == "t=65"
    assert got["t_label"] == "01:05"
    assert got["t_url"] == ""
    assert got["doc_name"] == "本地视频.md"


def test_video_sources_multiple_lectures_are_all_listed(api):
    """同一知识点被多集讲过 → 每条各带时间点（"视频出处"自然长出来，不用新表）。"""
    store = api["store"]
    doc = _add_lecture(api["km"], USER, "第十讲.md")
    store.upsert_lecture(doc, url="https://www.bilibili.com/video/BV1yy?p=10", title="第十讲")
    store.replace_lecture_segments(doc, [
        {"seq": 1, "title": "又讲一次", "t_start": 30.0, "t_end": 90.0,
         "node_id": "node-ok"}])

    sources = _video_sources(api, "node-ok").json()["sources"]
    assert len(sources) == 2
    assert {(s["doc_id"], s["locator"]) for s in sources} == {
        (api["doc"], "t=754"), (doc, "t=30")}
    assert all(s["t_url"] for s in sources)


def test_video_sources_other_user_cannot_see(api):
    """per-user kb.db 隔离：别人的切片查不到（同一个 node_id 也不串）。

    注意 `doc_id` 是**每用户各自编号**（per-user 库），所以不能用 id 判归属 ——
    要按文件名判：别人的"别人的讲义.md"不能出现在本用户的出处里。
    """
    store = api["km"]._get_store(OTHER)
    store.upsert_lecture(api["other"], url="https://www.bilibili.com/video/BV1zz")
    store.replace_lecture_segments(api["other"], [
        {"seq": 1, "title": "别人的一集", "t_start": 5.0, "t_end": 10.0,
         "node_id": "node-ok"}])

    sources = _video_sources(api, "node-ok").json()["sources"]
    assert all(s["doc_name"] != "别人的讲义.md" for s in sources)
    assert all(s["title"] != "别人的一集" for s in sources)


def test_video_sources_deleted_doc_is_absent(api):
    """删掉文件 → 其出处不再出现（`delete_node_recursive` 已级联清切片）。"""
    doc = api["pending"]
    api["store"].upsert_lecture(doc, url=VIDEO_URL)
    api["store"].replace_lecture_segments(doc, [
        {"seq": 1, "title": "待删", "t_start": 1.0, "t_end": 2.0, "node_id": "node-tmp"}])
    assert _video_sources(api, "node-tmp").json()["sources"]

    api["store"].delete_node_recursive(USER, doc)
    assert _video_sources(api, "node-tmp").json() == {"sources": []}


def test_video_sources_is_read_only(api):
    """只读端点：GET 两次结果相同，且不改动任何行（零副作用）。"""
    first = _video_sources(api, "node-ok").json()
    before = api["store"].list_lecture_segments(api["doc"])
    second = _video_sources(api, "node-ok").json()

    assert first == second
    assert api["store"].list_lecture_segments(api["doc"]) == before
