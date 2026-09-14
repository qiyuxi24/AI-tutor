"""
试卷导出接口测试 —— GET /quiz/export

覆盖：
- 三种格式（docx / pdf / md）都能返回正确媒体类型与非空字节流
- 中文文件名走 RFC 5987 的 filename*=UTF-8'' 编码（HTTP 头只能 latin-1，
  直接写中文会抛 UnicodeEncodeError —— 这是 knowledge 导出踩过的坑）
- 学生卷（with_answer=false）不含答案与解析
- 题库为空时返回 404
- 数据库行的 id 是 int、Question.id 是 str，导出不得因严格校验而丢题

全程不碰 LLM / embedding：导出是纯函数 + 读库，用固定 sample 题目即可。
"""
from urllib.parse import unquote

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import quiz as quiz_api
from app.core.auth import get_current_user
from app.core.quiz.quiz_store import QuizStore

SAMPLE = [
    {
        "id": "q1",  # Question.id 是必填（出题时生成 q1/q2…）
        "type": "single",
        "question": "在二叉树的先序遍历中，根结点的访问顺序是？",
        "options": [
            {"value": "A", "label": "最先访问根结点"},
            {"value": "B", "label": "最后访问根结点"},
            {"value": "C", "label": "根结点在左右子树之间访问"},
            {"value": "D", "label": "不确定"},
        ],
        "answer": ["A"],
        "analysis": "先序遍历顺序为：根 → 左子树 → 右子树。",
        "points": 10,
        "knowledge_point": "二叉树遍历",
    },
    {
        "id": "q2",
        "type": "multiple",
        "question": "下列关于平衡二叉树的说法，正确的有？（多选）",
        "options": [
            {"value": "A", "label": "任一结点的左右子树高度差不超过 1"},
            {"value": "B", "label": "查找时间复杂度为 O(log n)"},
        ],
        "answer": ["A", "B"],
        "analysis": "AVL 树要求平衡因子绝对值 ≤ 1。",
        "points": 10,
    },
]

UUID_HEADER = ("Content-Disposition", "filename*=UTF-8''")


@pytest.fixture()
def store(tmp_path):
    """独立临时题库，存两条题目（subject 中文，触发中文文件名路径）"""
    s = QuizStore(tmp_path / "1")
    s.save_questions(SAMPLE, subject="二叉树遍历", difficulty="medium")
    yield s
    s.close()


@pytest.fixture()
def client(store, monkeypatch):
    app = FastAPI()
    app.include_router(quiz_api.router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: 1
    monkeypatch.setattr(quiz_api.quiz_manager, "_get_store", lambda user_id: store)
    return TestClient(app)


def _filename(resp) -> str:
    disp = resp.headers["content-disposition"]
    assert "filename*=UTF-8''" in disp, "必须用 RFC 5987 编码，否则中文名会 500"
    return unquote(disp.split("filename*=UTF-8''", 1)[1])


@pytest.mark.parametrize(
    "fmt,mime,ext",
    [
        ("docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx"),
        ("pdf", "application/pdf", "pdf"),
        ("md", "text/markdown", "md"),
    ],
)
def test_export_formats(client, fmt, mime, ext):
    resp = client.get("/api/v1/quiz/export", params={"format": fmt, "subject": "二叉树遍历"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith(mime)
    assert len(resp.content) > 200, "导出内容不应是空的"
    name = _filename(resp)
    assert name.endswith(f".{ext}")
    assert name.startswith("二叉树遍历")


def test_export_content_has_question_and_answer(client):
    resp = client.get("/api/v1/quiz/export", params={"format": "md"})
    text = resp.content.decode("utf-8")
    assert "在二叉树的先序遍历中" in text
    assert "**A.** 最先访问根结点" in text
    assert "> **答案**" in text
    assert "> **解析**" in text


def test_student_paper_has_no_answer(client):
    resp = client.get("/api/v1/quiz/export",
                      params={"format": "md", "with_answer": "false"})
    text = resp.content.decode("utf-8")
    assert "在二叉树的先序遍历中" in text   # 题目还在
    assert "答案" not in text
    assert "解析" not in text
    assert "学生卷" in _filename(resp)


def test_export_by_ids_keeps_order(client, store):
    ids = store.save_questions(
        [dict(SAMPLE[1], id="q9", question="第二题题干占位")], subject="二叉树遍历"
    )
    resp = client.get("/api/v1/quiz/export",
                      params={"format": "md", "ids": str(ids[0])})
    text = resp.content.decode("utf-8")
    assert "第二题题干占位" in text
    assert "在二叉树的先序遍历中" not in text   # 只导出指定 id


def test_export_empty_bank_returns_404(tmp_path, monkeypatch):
    empty = QuizStore(tmp_path / "2")
    app = FastAPI()
    app.include_router(quiz_api.router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: 2
    monkeypatch.setattr(quiz_api.quiz_manager, "_get_store", lambda user_id: empty)
    resp = TestClient(app).get("/api/v1/quiz/export", params={"format": "docx"})
    assert resp.status_code == 404
    empty.close()


def test_bad_format_rejected(client):
    resp = client.get("/api/v1/quiz/export", params={"format": "xlsx"})
    assert resp.status_code == 422  # Query pattern 校验拦截
