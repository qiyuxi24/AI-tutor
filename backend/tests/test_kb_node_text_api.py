"""知识库文件节点正文预览测试（GET /kb/node/{id}/text，全离线：临时 KB + 依赖覆盖）。

覆盖：
- 文件节点 → 200，返回节点名 + Markdown 原文 + chars/total_chars/truncated
- 超长正文 → 截断到 KB_PREVIEW_MAX_CHARS 且 truncated=True（chars < total_chars）
- 文件夹节点 → 400（只有文件才有正文）
- 节点不存在 → 404；同 id 跨用户不串号（per-user 库各自编号，隔离由分库提供）
- 正文为空（未解析/解析失败）→ 404
- 未认证 → 401
- KbManager.get_document_text 薄委托：读正文唯一出口，节点不存在返回 ""

零网络 / 零 LLM：文件节点直接用 KbStore.add_file 挂载，不解析、不向量化。
"""
import importlib

import pytest
from fastapi.testclient import TestClient

kb_manager_module = importlib.import_module("app.core.kb.kb_manager")
from app.api.v1.kb import KB_PREVIEW_MAX_CHARS
from app.core.kb.kb_manager import KbManager

USER = 7
OTHER = 8


def _add_file(km: KbManager, user_id: int, name: str, parent_id, text: str) -> int:
    """只挂文件节点（不做解析/向量化），extract_text 即"已解析正文"。"""
    return km._get_store(user_id).add_file(
        user_id=user_id, name=name, parent_id=parent_id,
        file_type=".md", extract_text=text,
        file_size=len(text.encode("utf-8")),
    )


@pytest.fixture
def api(tmp_path, monkeypatch):
    """TestClient + 临时 KbManager（模块单例与本文件同时打桩）+ 固定用户"""
    from app.main import app
    from app.core.auth import get_current_user

    km = KbManager(tmp_path / "kb")
    monkeypatch.setattr(kb_manager_module, "kb_manager", km)
    monkeypatch.setattr("app.api.v1.kb.kb_manager", km)

    note = _add_file(km, USER, "笔记.md", None, "# 栈\n\n后进先出。")
    folder = km.create_folder(USER, "数据结构", None)
    long_text = "长" * (KB_PREVIEW_MAX_CHARS + 100)
    long_file = _add_file(km, USER, "长文.md", folder, long_text)
    empty = _add_file(km, USER, "空.md", None, "   \n")
    other_node = _add_file(km, OTHER, "别人的.md", None, "别人的正文")

    app.dependency_overrides[get_current_user] = lambda: USER
    yield {
        "client": TestClient(app), "km": km,
        "note": note, "folder": folder, "long": long_file,
        "empty": empty, "other": other_node,
    }
    app.dependency_overrides.clear()


def _get(api, node_id: int):
    return api["client"].get(f"/api/v1/kb/node/{node_id}/text")


# ── 正常读取 ─────────────────────────────────────────────────

def test_file_node_returns_markdown(api):
    resp = _get(api, api["note"])
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["name"] == "笔记.md"
    assert body["markdown"] == "# 栈\n\n后进先出。"
    assert body["total_chars"] == len("# 栈\n\n后进先出。")
    assert body["chars"] == body["total_chars"]
    assert body["truncated"] is False


def test_long_text_truncated(api):
    resp = _get(api, api["long"])
    assert resp.status_code == 200
    body = resp.json()
    assert body["truncated"] is True
    assert body["chars"] == KB_PREVIEW_MAX_CHARS
    assert len(body["markdown"]) == KB_PREVIEW_MAX_CHARS
    assert body["total_chars"] == KB_PREVIEW_MAX_CHARS + 100


# ── 拒绝路径 ─────────────────────────────────────────────────

def test_folder_rejected(api):
    resp = _get(api, api["folder"])
    assert resp.status_code == 400
    assert "文件" in resp.json()["detail"]


def test_missing_node_404(api):
    resp = _get(api, 99999)
    assert resp.status_code == 404


def test_same_id_across_users_no_crosstalk(api):
    """同 id 跨用户不串号：USER 与 OTHER 的首个节点都是 id=1（per-user 库各自编号），
    请求者只可能拿到自己库里的那份正文——隔离由 per-user kb.db 提供。"""
    assert api["note"] == api["other"]      # 夹具确实构造了同 id
    resp = _get(api, api["note"])
    assert resp.status_code == 200
    assert resp.json()["markdown"] == "# 栈\n\n后进先出。"


def test_empty_text_404(api):
    resp = _get(api, api["empty"])
    assert resp.status_code == 404


def test_requires_auth(api):
    from app.main import app
    from app.core.auth import get_current_user

    app.dependency_overrides.pop(get_current_user, None)
    client = TestClient(app)
    assert client.get(f"/api/v1/kb/node/{api['note']}/text").status_code == 401
    app.dependency_overrides[get_current_user] = lambda: USER


# ── 薄委托（读正文唯一出口）─────────────────────────────────

def test_manager_get_document_text_delegates(api):
    km = api["km"]
    assert km.get_document_text(USER, api["note"]) == "# 栈\n\n后进先出。"
    assert km.get_document_text(USER, 99999) == ""
