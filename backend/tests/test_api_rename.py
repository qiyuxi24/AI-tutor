"""`PATCH /knowledge/subject`、`PATCH /knowledge/board`、`DELETE /knowledge/board` 的 HTTP 契约。

数据层行为（口径 / 隔离 / 拒绝规则）在 `test_kg_rename.py`；这里只锁 API 层：
- 参数原样透传给数据层（body 字段名不能错位，否则会静默改错对象）；
- 数据层的 `ValueError`（空名 / 同名 / 重名 / 无此板块）统一翻成 400，detail 透传；
- 「未分类」是后端合成的保留名，**必须在进数据层之前拦下**（新旧名双向）；
- 缺字段 / 缺 query 由框架翻 422。

全离线：KnowledgeGraph 被替换为假实现，不碰 `data/`。
"""
import pytest
from fastapi.testclient import TestClient

from app.api.v1 import knowledge as knowledge_module
from app.core.auth import get_current_user

SUBJECT_URL = "/api/v1/knowledge/subject"
BOARD_URL = "/api/v1/knowledge/board"
USER_ID = 5


class _FakeKG:
    """记录收到的调用参数；`raises` 非空时让下一次调用抛 ValueError（模拟校验失败）。"""

    calls: list = []
    raises: str | None = None

    def __init__(self, user_id):
        self.user_id = user_id

    def rename_subject(self, old, new):
        _FakeKG.calls.append(("rename_subject", old, new))
        if _FakeKG.raises:
            raise ValueError(_FakeKG.raises)
        return {"old_subject": old, "new_subject": new,
                "renamed_nodes": 3, "renamed_themes": 1}

    def rename_board(self, subject, old, new):
        _FakeKG.calls.append(("rename_board", subject, old, new))
        if _FakeKG.raises:
            raise ValueError(_FakeKG.raises)
        return {"subject": subject, "old_board": old, "new_board": new, "renamed_nodes": 2}

    def remove_board(self, subject, board):
        _FakeKG.calls.append(("remove_board", subject, board))
        if _FakeKG.raises:
            raise ValueError(_FakeKG.raises)
        return {"subject": subject, "board": board, "moved_nodes": 2}

    def close(self):
        pass


@pytest.fixture
def isolate(monkeypatch):
    _FakeKG.calls.clear()
    _FakeKG.raises = None
    monkeypatch.setattr(knowledge_module, "KnowledgeGraph", _FakeKG)


@pytest.fixture
def client():
    from app.main import app

    app.dependency_overrides[get_current_user] = lambda: USER_ID
    yield TestClient(app)
    app.dependency_overrides.clear()


# ── 学科改名 ──────────────────────────────────────────

def test_rename_subject_passes_names_through(client, isolate):
    r = client.patch(SUBJECT_URL, json={"old_name": "数据结构", "new_name": "数据结构与算法"})

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert (body["renamed_nodes"], body["renamed_themes"]) == (3, 1)
    assert _FakeKG.calls == [("rename_subject", "数据结构", "数据结构与算法")]


def test_rename_subject_rejects_unclassified_both_ways(client, isolate):
    r = client.patch(SUBJECT_URL, json={"old_name": "未分类", "new_name": "数据结构"})

    assert r.status_code == 400
    assert "未分类" in r.json()["detail"]

    r2 = client.patch(SUBJECT_URL, json={"old_name": "数据结构", "new_name": "未分类"})
    assert r2.status_code == 400
    assert _FakeKG.calls == [], "保留名必须在进数据层之前拦下"


def test_rename_subject_maps_validation_error_to_400(client, isolate):
    _FakeKG.raises = "学科「操作系统」已存在，请换一个名字"

    r = client.patch(SUBJECT_URL, json={"old_name": "数据结构", "new_name": "操作系统"})

    assert r.status_code == 400
    assert "已存在" in r.json()["detail"]


def test_rename_subject_requires_both_names(client, isolate):
    """漏字段 / 空串由 pydantic 拦成 422，不能退化成"改名成空串"。"""
    assert client.patch(SUBJECT_URL, json={"old_name": "数据结构"}).status_code == 422
    assert client.patch(SUBJECT_URL,
                        json={"old_name": "", "new_name": "x"}).status_code == 422


# ── 板块改名 ──────────────────────────────────────────

def test_rename_board_passes_args_through(client, isolate):
    r = client.patch(BOARD_URL, json={"subject": "数据结构", "old_name": "线性表",
                                      "new_name": "线性结构"})

    assert r.status_code == 200
    assert r.json()["renamed_nodes"] == 2
    assert _FakeKG.calls == [("rename_board", "数据结构", "线性表", "线性结构")]


def test_rename_board_missing_subject_is_422(client, isolate):
    assert client.patch(BOARD_URL,
                        json={"old_name": "线性表", "new_name": "线性结构"}).status_code == 422


def test_rename_board_maps_validation_error_to_400(client, isolate):
    _FakeKG.raises = "学科「数据结构」下没有板块「X」"

    r = client.patch(BOARD_URL, json={"subject": "数据结构", "old_name": "X", "new_name": "Y"})

    assert r.status_code == 400
    assert "没有板块" in r.json()["detail"]


# ── 解散板块 ──────────────────────────────────────────

def test_delete_board_ok(client, isolate):
    r = client.delete(BOARD_URL, params={"subject": "数据结构", "board": "线性表"})

    assert r.status_code == 200
    body = r.json()
    assert body["moved_nodes"] == 2
    assert _FakeKG.calls == [("remove_board", "数据结构", "线性表")]


def test_delete_board_missing_param_is_422(client, isolate):
    assert client.delete(BOARD_URL, params={"subject": "数据结构"}).status_code == 422


def test_delete_board_maps_validation_error_to_400(client, isolate):
    _FakeKG.raises = "学科「数据结构」下没有板块「X」"

    r = client.delete(BOARD_URL, params={"subject": "数据结构", "board": "X"})

    assert r.status_code == 400
    assert "没有板块" in r.json()["detail"]
