"""
出题历史（题集）HTTP 契约测试。

覆盖：
- `GET /quiz/batches`       列出题集（最新在前）+ `total`，支持 limit/offset；
- `GET /quiz/batches/{id}/questions`  取某题集的题目；不存在 → 404。

离线：题目直接写独立临时题库，不碰 LLM / embedding。
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import quiz as quiz_api
from app.core.auth import get_current_user
from app.core.quiz.quiz_store import QuizStore

from tests.test_quiz_store import _make_question


@pytest.fixture()
def store(tmp_path):
    """两次出题 = 两个题集：A 三题、B 两题（B 更新）。"""
    s = QuizStore(tmp_path / "1")
    s.save_questions([_make_question(id=f"a{i}") for i in range(3)], subject="数据结构")
    s.save_questions([_make_question(id=f"b{i}") for i in range(2)], subject="算法")
    yield s
    s.close()


@pytest.fixture()
def client(store, monkeypatch):
    app = FastAPI()
    app.include_router(quiz_api.router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: 1
    monkeypatch.setattr(quiz_api.quiz_manager, "_get_store", lambda user_id: store)
    return TestClient(app)


def test_list_batches_returns_sets_and_total(client):
    resp = client.get("/api/v1/quiz/batches")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    batches = data["batches"]
    assert len(batches) == 2
    assert batches[0]["subject"] == "算法", "最新题集在前"
    assert batches[0]["question_count"] == 2
    assert batches[1]["question_count"] == 3
    assert batches[0]["created_at"]


def test_list_batches_pagination(client):
    resp = client.get("/api/v1/quiz/batches", params={"limit": 1, "offset": 1})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2, "total 是全量题集数，不受分页影响"
    assert len(data["batches"]) == 1
    assert data["batches"][0]["subject"] == "数据结构"


def test_get_batch_questions(client):
    batch_id = client.get("/api/v1/quiz/batches").json()["batches"][0]["batch_id"]
    resp = client.get(f"/api/v1/quiz/batches/{batch_id}/questions")
    assert resp.status_code == 200
    data = resp.json()
    assert data["batch_id"] == batch_id
    assert data["questions"], "题集里应有题目"
    assert all(q["batch_id"] == batch_id for q in data["questions"])


def test_get_batch_questions_not_found(client):
    resp = client.get("/api/v1/quiz/batches/不存在的题集/questions")
    assert resp.status_code == 404
