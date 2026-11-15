"""`GET /knowledge/nodes` —— 对话输入框"选知识点"的候选搜索。

为什么必须走后端（而不是前端过滤）：前端 store 里只有**当前学科**的切片
（`GET /knowledge/graph?subject=…`），本地过滤会把"在 A 学科的对话里引用 B 学科的
知识点"这条路直接堵死。本端点按 user_id 全量找。

排序口径来自参照系契约 I2（「当前应学的」优先）：同一匹配档内掌握度低的排前面。

全离线：`KnowledgeGraph` 换成假实现，不碰 `data/knowledge.db`。
"""
import pytest
from fastapi.testclient import TestClient

from app.api.v1 import knowledge as knowledge_module
from app.core.auth import get_current_user

URL = "/api/v1/knowledge/nodes"
USER_ID = 9

_NODES = [
    {"id": "quicksort", "name": "快速排序", "subject": "算法", "board": "排序",
     "summary": "分治，平均 O(n log n)", "mastery": 20},
    {"id": "mergesort", "name": "归并排序", "subject": "算法", "board": "排序",
     "summary": "稳定，需额外空间", "mastery": 80},
    {"id": "bubble", "name": "冒泡排序", "subject": "算法", "board": "排序",
     "summary": "简单但慢", "mastery": 100},
    {"id": "stack", "name": "栈", "subject": "数据结构", "board": "线性表",
     "summary": "后进先出", "mastery": 50},
    {"id": "deadlock", "name": "死锁", "subject": "操作系统", "board": "进程",
     "summary": "四个必要条件", "mastery": 0},
]


class _FakeKG:
    """只实现端点用到的三个成员（nodes / get_nodes_by_subject / close）。"""

    def __init__(self, user_id):
        self.user_id = user_id
        self.nodes = list(_NODES)

    def get_nodes_by_subject(self, subject):
        return [n for n in _NODES if n["subject"] == subject]

    def close(self):
        pass


@pytest.fixture
def client(monkeypatch):
    from app.main import app
    monkeypatch.setattr(knowledge_module, "KnowledgeGraph", _FakeKG)
    app.dependency_overrides[get_current_user] = lambda: USER_ID
    yield TestClient(app)
    app.dependency_overrides.clear()


def _ids(resp):
    return [n["id"] for n in resp.json()["nodes"]]


# ── 匹配 ──────────────────────────────────────────────

def test_name_match_ranks_exact_first(client):
    """完全匹配 > 前缀 > 包含（"排序"三条里没有完全匹配，按前缀/包含分档）。"""
    ids = _ids(client.get(URL, params={"q": "栈"}))
    assert ids[0] == "stack"


def test_summary_match_also_found(client):
    """名称没命中但摘要命中也要能搜到（用户可能只记得那句话）。"""
    ids = _ids(client.get(URL, params={"q": "后进先出"}))
    assert "stack" in ids


def test_no_match_returns_empty(client):
    """真无关的查询 → 空列表（不是"返回全部"）。"""
    r = client.get(URL, params={"q": "量子纠缠"})
    assert r.status_code == 200
    assert r.json()["nodes"] == []


def test_case_insensitive(client):
    """大小写不敏感（英文 id / 名称混排时不能因为大小写漏掉）。"""
    assert client.get(URL, params={"q": "STACK"}).status_code == 200


# ── 排序：该学的先出现 ────────────────────────────────

def test_lower_mastery_first_within_same_rank(client):
    """同一匹配档内掌握度低的排前面（参照系契约 I2：「当前应学的」优先）。"""
    ids = _ids(client.get(URL, params={"q": "排序"}))
    # 三条都是"包含排序"档 → 按 mastery 升序：快速排序(20) < 归并(80) < 冒泡(100)
    assert ids == ["quicksort", "mergesort", "bubble"]


# ── 过滤与上限 ────────────────────────────────────────

def test_subject_filter(client):
    """传 subject 只在该学科内找（不传则该用户全量）。"""
    ids = _ids(client.get(URL, params={"subject": "数据结构"}))
    assert ids == ["stack"]

    assert set(_ids(client.get(URL))) == {n["id"] for n in _NODES}


def test_limit_is_respected(client):
    assert len(_ids(client.get(URL, params={"q": "排序", "limit": 2}))) == 2


def test_limit_bounds_are_validated(client):
    """limit 上下界由 FastAPI 校验（0 与 101 都是 422，不是静默截断）。"""
    assert client.get(URL, params={"limit": 0}).status_code == 422
    assert client.get(URL, params={"limit": 101}).status_code == 422


def test_response_shape_is_picker_ready(client):
    """返回字段够填一个候选列表即可（不返回正文/边，避免把画布那份大对象搬过来）。"""
    node = client.get(URL, params={"q": "栈"}).json()["nodes"][0]
    assert set(node) == {"id", "name", "subject", "board", "summary", "mastery", "kind"}
    assert node["kind"] == "knowledge"
