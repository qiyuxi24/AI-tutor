"""
B1.4 采集 API 测试（fastapi TestClient + mock manager，全部离线，零网络/零 LLM/零 KB）：

- POST /collector/search    ：候选下发 + mode/非法 mode(422)
- POST /collector/tasks     ：建任务入参（含候选转换）透传 + 500 兜底
- GET  /collector/tasks/{id}：进度读取 + 404
- POST /collector/tasks/{id}/cancel：协作取消 + 404
- GET  /collector/stats     ：覆盖率（学段→学科 已采/缺口）+ 资源状态聚合（临时库真实 store）

mock 手段：
- app.dependency_overrides[get_current_user] → 固定用户
- app.dependency_overrides[get_manager]     → FakeManager（不碰后台执行/网络/kb）
- stats 的 store 用真实 CollectorStoreManager(tmp_path)，仅本地 sqlite
"""
import pytest
from fastapi.testclient import TestClient

from app.core.collector.store import (
    RESOURCE_DUPLICATE,
    RESOURCE_INDEXED,
    CollectorStoreManager,
)
from app.core.collector.types import CollectCandidate

USER = 7


def _task(id_: int = 1, status: str = "pending", processed: int = 0,
          total: int = 0, cursor: str = "") -> dict:
    return {
        "id": id_, "user_id": USER, "subject": "数据结构与算法",
        "source": "wikipedia", "status": status, "cursor": cursor,
        "processed_count": processed, "total_count": total,
        "error": "", "created_at": "2026-09-05 00:00:00", "finished_at": None,
    }


class FakeManager:
    """离线替身：捕获 search/start_task 入参，返回预置结果；不跑真实后台"""

    def __init__(self, store_mgr=None):
        self.store_mgr = store_mgr
        self.candidates: list[CollectCandidate] = []
        self.task: dict | None = None
        self.raise_on_start = False
        self.calls = {"search": [], "start": []}

    async def search(self, subject, mode="personal"):
        self.calls["search"].append((subject, mode))
        return self.candidates

    async def start_task(self, user_id, subject, candidates,
                         mode="personal", source="", autostart=True):
        self.calls["start"].append({
            "user_id": user_id, "subject": subject,
            "candidates": list(candidates), "mode": mode, "source": source,
        })
        if self.raise_on_start:
            raise RuntimeError("store boom")
        return self.task

    def get_task(self, user_id, task_id):
        if self.task and self.task["id"] == task_id:
            return self.task
        return None

    def cancel(self, user_id, task_id):
        if self.task and self.task["id"] == task_id:
            t = dict(self.task)
            t["status"] = "cancelled"
            t["finished_at"] = "2026-09-05 00:01:00"
            return t
        return None


@pytest.fixture
def api(tmp_path, monkeypatch):
    """TestClient + 依赖覆盖（get_current_user / get_manager）"""
    from app.main import app
    from app.core.auth import get_current_user
    from app.api.v1.collector import get_manager

    store_mgr = CollectorStoreManager(tmp_path / "collector")
    fake = FakeManager(store_mgr=store_mgr)
    app.dependency_overrides[get_current_user] = lambda: USER
    app.dependency_overrides[get_manager] = lambda: fake
    monkeypatch.setattr("app.api.v1.collector.load_subjects", lambda: {})
    yield TestClient(app), fake, store_mgr
    app.dependency_overrides.clear()


# ── POST /collector/search ───────────────────────────────────

def test_search_returns_candidates(api):
    client, fake, _ = api
    fake.candidates = [CollectCandidate(
        title="栈", source_url="https://zh.wikipedia.org/wiki/栈",
        source="wikipedia", license_level="L0", description="后进先出",
        size_bytes=2048)]
    resp = client.post("/api/v1/collector/search",
                       json={"subject": "数据结构与算法", "mode": "personal"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert fake.calls["search"] == [("数据结构与算法", "personal")]
    cand = body["candidates"][0]
    assert cand == {
        "title": "栈", "source_url": "https://zh.wikipedia.org/wiki/栈",
        "source": "wikipedia", "license_level": "L0",
        "description": "后进先出", "size_bytes": 2048,
    }


def test_search_empty_when_no_candidates(api):
    client, _, _ = api
    resp = client.post("/api/v1/collector/search",
                       json={"subject": "线性代数"})
    assert resp.status_code == 200
    assert resp.json()["candidates"] == []


def test_search_rejects_bad_mode(api):
    client, _, _ = api
    resp = client.post("/api/v1/collector/search",
                       json={"subject": "数据结构与算法", "mode": "hacker"})
    assert resp.status_code == 422


def test_search_requires_subject(api):
    client, _, _ = api
    resp = client.post("/api/v1/collector/search", json={"mode": "personal"})
    assert resp.status_code == 422


# ── POST /collector/tasks ────────────────────────────────────

def test_create_task_passes_candidates_and_mode(api):
    client, fake, _ = api
    fake.task = _task(id_=3, status="processing", processed=1, total=2)
    resp = client.post("/api/v1/collector/tasks", json={
        "subject": "数据结构与算法",
        "mode": "commercial",
        "source": "wikipedia",
        "candidates": [
            {"title": "栈", "source_url": "https://zh.wikipedia.org/wiki/栈",
             "license_level": "L0"},
            {"title": "队列", "source_url": "https://zh.wikipedia.org/wiki/队列",
             "license_level": "L0"},
        ],
    })
    assert resp.status_code == 200
    call = fake.calls["start"][0]
    assert call["user_id"] == USER
    assert call["subject"] == "数据结构与算法"
    assert call["mode"] == "commercial"
    assert call["source"] == "wikipedia"
    got = {c.source_url: c for c in call["candidates"]}
    assert got["https://zh.wikipedia.org/wiki/栈"].title == "栈"
    assert got["https://zh.wikipedia.org/wiki/队列"].license_level == "L0"

    task = resp.json()["task"]
    # 领域状态 processing → UI 状态 running（契约翻译）
    assert task["id"] == 3 and task["status"] == "running"
    assert task["processed_count"] == 1 and task["total_count"] == 2


def test_create_task_status_translation_pending_to_queued(api):
    """pending → queued（建任务即刻返回、后台尚未开跑的场景）"""
    client, fake, _ = api
    fake.task = _task(id_=4, status="pending", processed=0, total=2)
    resp = client.post("/api/v1/collector/tasks", json={
        "subject": "数据结构与算法",
        "candidates": [{"title": "x", "source_url": "https://a/1",
                        "license_level": "L0"}],
    })
    assert resp.json()["task"]["status"] == "queued"


def test_create_task_rejects_illegal_license(api):
    """L9 不是合法授权等级 → Pydantic Literal 422"""
    client, _, _ = api
    resp = client.post("/api/v1/collector/tasks", json={
        "subject": "数据结构与算法",
        "candidates": [{"title": "x", "source_url": "https://a/1",
                        "license_level": "L9"}],
    })
    assert resp.status_code == 422


def test_create_task_commercial_rejects_l2(api):
    """商用模式仅 L0：带 L2 候选直接 422（防绕过前端勾选 curl 直传，合规边界）"""
    client, fake, _ = api
    resp = client.post("/api/v1/collector/tasks", json={
        "subject": "数据结构与算法", "mode": "commercial",
        "candidates": [
            {"title": "x", "source_url": "https://a/1", "license_level": "L2"}],
    })
    assert resp.status_code == 422
    assert "https://a/1" in resp.json()["detail"]
    assert fake.calls["start"] == []      # 未到达 manager


def test_create_task_500_on_manager_error(api):
    client, fake, _ = api
    fake.raise_on_start = True
    resp = client.post("/api/v1/collector/tasks", json={
        "subject": "数据结构与算法", "candidates": []})
    assert resp.status_code == 500
    assert "E-COLL-002" in resp.json()["detail"]


# ── GET /collector/tasks/{id} ────────────────────────────────

def test_get_task_ok(api):
    client, fake, _ = api
    fake.task = _task(id_=9, status="completed", processed=3, total=3,
                      cursor="https://w/z")
    resp = client.get("/api/v1/collector/tasks/9")
    assert resp.status_code == 200
    task = resp.json()["task"]
    assert task["status"] == "completed"
    assert task["processed_count"] == task["total_count"] == 3
    assert task["cursor"] == "https://w/z"


def test_get_task_404(api):
    client, _, _ = api
    resp = client.get("/api/v1/collector/tasks/999")
    assert resp.status_code == 404
    assert "E-COLL-001" in resp.json()["detail"]


# ── POST /collector/tasks/{id}/cancel ────────────────────────

def test_cancel_task_ok(api):
    client, fake, _ = api
    fake.task = _task(id_=1)
    resp = client.post("/api/v1/collector/tasks/1/cancel")
    assert resp.status_code == 200
    assert resp.json()["task"]["status"] == "cancelled"


def test_cancel_task_404(api):
    client, _, _ = api
    resp = client.post("/api/v1/collector/tasks/999/cancel")
    assert resp.status_code == 404
    assert "E-COLL-001" in resp.json()["detail"]


# ── GET /collector/stats ─────────────────────────────────────

def _seed_resources(store_mgr: CollectorStoreManager, user: int) -> None:
    """向临时库预置：数据结构已采2条(含1去重)、算法失败1条、空学科未归类0"""
    store = store_mgr._get_store(user)
    r1 = store.add_resource("https://w/stack", title="栈",
                            source="wikipedia", license_level="L0",
                            subject="数据结构与算法")
    store.update_resource_status(r1, RESOURCE_INDEXED)
    r2 = store.add_resource("https://w/queue", title="队列",
                            source="wikipedia", license_level="L0",
                            subject="数据结构与算法")
    store.update_resource_status(r2, RESOURCE_DUPLICATE)
    r3 = store.add_resource("https://w/matrix", title="矩阵",
                            source="wikipedia", license_level="L0",
                            subject="线性代数")
    store.update_resource_status(r3, RESOURCE_INDEXED)
    r4 = store.add_resource("https://w/bad", title="坏源",
                            source="wikipedia", license_level="L0",
                            subject="数据结构与算法")
    store.update_resource_status(r4, "failed")
    store.add_resource("https://w/pending", title="排队中",
                       source="wikipedia", license_level="L0",
                       subject="概率论")  # status 保持 pending


def test_stats_coverage_with_tree(api, monkeypatch):
    """有目录数据：学段→学科 已采份数 + 缺口（前端契约：coverage 数组）"""
    client, _, store_mgr = api
    _seed_resources(store_mgr, USER)
    monkeypatch.setattr("app.api.v1.collector.load_subjects", lambda: {
        "stages": [{
            "id": "college", "name": "大学",
            "subjects": [
                {"id": "ds", "name": "数据结构与算法"},
                {"id": "la", "name": "线性代数"},
                {"id": "prob", "name": "概率论"},
            ],
        }],
    })

    resp = client.get("/api/v1/collector/stats")
    assert resp.status_code == 200
    cov = resp.json()["coverage"]

    assert len(cov) == 1
    stage = cov[0]
    assert stage["stage"] == "college"
    assert stage["stage_name"] == "大学"
    assert stage["total"] == 3
    assert stage["collected"] == 2          # 已采学科数（prob 为缺口不计）

    subs = {s["subject"]: s for s in stage["subjects"]}
    # 数据结构：indexed 1 + duplicate 1 = 2（内容已在库也算已采）
    assert subs["数据结构与算法"]["collected"] == 2
    assert subs["数据结构与算法"]["boards_total"] == 0
    assert subs["数据结构与算法"]["boards_collected"] == 0
    # 线性代数 indexed=1 → 已采；概率论仅 pending → collected 0 = 缺口
    assert subs["线性代数"]["collected"] == 1
    assert subs["概率论"]["collected"] == 0


def test_stats_empty_without_tree(api):
    """无学科目录数据（subjects.json 缺失，B1.6 前）：返回 coverage=[]，不报错"""
    client, _, store_mgr = api
    store_mgr._get_store(USER)              # 建库但无目录数据
    resp = client.get("/api/v1/collector/stats")
    assert resp.status_code == 200
    assert resp.json()["coverage"] == []


# ── 认证隔离 ─────────────────────────────────────────────────

def test_requires_auth(api):
    """去掉 get_current_user 覆盖后未带 token → 401"""
    from app.main import app
    from app.core.auth import get_current_user
    app.dependency_overrides.pop(get_current_user, None)
    client = TestClient(app)
    resp = client.get("/api/v1/collector/stats")
    assert resp.status_code == 401
    app.dependency_overrides[get_current_user] = lambda: USER
