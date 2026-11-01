"""知识库原件存储与预览（GET /kb/node/{id}/raw，全离线：临时 KB + 依赖覆盖）。

为什么要存原件：`documents.extract_text` 只是解析产物，前端要看「真 PDF」就得有原文。
原件落在 `data/kb/{uid}/files/{node_id}{ext}`（ext 取自 documents.file_type，路径可推导，
因此**不加表也不加列**）；删号由 `backend-admin::purge_user_storage` 整目录删覆盖。

覆盖：
- 上传 → 原件落盘，内容与上传字节一致
- GET /raw → 200 + 原始字节 + 正确 Content-Type + inline
- 老数据（本次改动前上传，只有解析文本）→ 404，前端回退文本预览
- 文件夹节点 → 400；节点不存在 → 404；未认证 → 401
- 删除文件 / 删除文件夹（递归）→ 原件一并清理
- 重复上传 / 回滚路径不留孤儿原件

零网络 / 零 LLM：vectorize=False 只写 whoosh 稀疏索引。
"""
import importlib

import pytest
from fastapi.testclient import TestClient

kb_manager_module = importlib.import_module("app.core.kb.kb_manager")
from app.core.kb.kb_manager import KbManager

USER = 7
OTHER = 8

# 入库文本质量下限 200 字符（kb_manager.MIN_PARSE_TEXT_LEN），故正文取 300 字符
BODY = "栈是后进先出的线性表。" * 25


def _upload(km: KbManager, user_id: int, filename: str, content: bytes,
            parent_id=None) -> int:
    import asyncio
    return asyncio.run(km.upload_and_index(
        user_id=user_id, filename=filename, content=content,
        parent_id=parent_id, vectorize=False,
    ))


@pytest.fixture
def api(tmp_path, monkeypatch):
    from app.main import app
    from app.core.auth import get_current_user

    km = KbManager(tmp_path / "kb")
    monkeypatch.setattr(kb_manager_module, "kb_manager", km)
    monkeypatch.setattr("app.api.v1.kb.kb_manager", km)

    node = _upload(km, USER, "讲义.txt", BODY.encode("utf-8"))
    folder = km.create_folder(USER, "教材", None)
    in_folder = _upload(km, USER, "第二章.txt", BODY.encode("utf-8"), folder)
    # 老数据：只有解析文本、没有原件（模拟本次改动前上传的文件）
    legacy = km._get_store(USER).add_file(
        user_id=USER, name="老文件.md", parent_id=None, file_type=".md",
        extract_text=BODY, file_size=len(BODY.encode("utf-8")),
    )
    empty_folder = km.create_folder(USER, "空目录", None)
    other = _upload(km, OTHER, "别人的.txt", BODY.encode("utf-8"))

    app.dependency_overrides[get_current_user] = lambda: USER
    yield {
        "client": TestClient(app), "km": km, "root": tmp_path / "kb",
        "node": node, "folder": folder, "in_folder": in_folder,
        "legacy": legacy, "empty_folder": empty_folder, "other": other,
    }
    app.dependency_overrides.clear()


def _raw(api, node_id: int):
    return api["client"].get(f"/api/v1/kb/node/{node_id}/raw")


def _raw_path(api, user_id: int, node_id: int, ext: str):
    return api["root"] / str(user_id) / "files" / f"{node_id}{ext}"


# ── 落盘 ─────────────────────────────────────────────────────

def test_upload_stores_raw_file(api):
    path = _raw_path(api, USER, api["node"], ".txt")
    assert path.is_file()
    assert path.read_bytes() == BODY.encode("utf-8")


def test_raw_endpoint_serves_original_bytes(api):
    resp = _raw(api, api["node"])
    assert resp.status_code == 200
    assert resp.content == BODY.encode("utf-8")
    assert resp.headers["content-type"].startswith("text/plain")
    assert resp.headers["content-disposition"].startswith("inline")


def test_raw_endpoint_utf8_filename(api):
    """非 ASCII 文件名不能直接进 header（latin-1 编码会炸），RFC 5987 百分号编码。"""
    resp = _raw(api, api["node"])
    assert "%E8%AE%B2%E4%B9%89.txt" in resp.headers["content-disposition"]


# ── 拒绝路径 ─────────────────────────────────────────────────

def test_legacy_node_without_raw_404(api):
    """改动前上传的文件没有原件 → 404（前端据状态码回退文本预览）。"""
    assert _raw(api, api["legacy"]).status_code == 404


def test_folder_rejected(api):
    resp = _raw(api, api["empty_folder"])
    assert resp.status_code == 400
    assert "文件" in resp.json()["detail"]


def test_missing_node_404(api):
    assert _raw(api, 99999).status_code == 404


def test_requires_auth(api):
    from app.main import app
    from app.core.auth import get_current_user

    app.dependency_overrides.pop(get_current_user, None)
    assert TestClient(app).get(
        f"/api/v1/kb/node/{api['node']}/raw").status_code == 401
    app.dependency_overrides[get_current_user] = lambda: USER


def test_same_id_across_users_no_crosstalk(api):
    """同 id 不串号：USER/OTHER 的首个文件节点都是 id=1（per-user 库各自编号），
    原件也按 uid 分目录，请求者只可能拿到自己那份。"""
    assert api["node"] == api["other"]
    assert _raw(api, api["node"]).content == BODY.encode("utf-8")


# ── 删除清理 ─────────────────────────────────────────────────

def test_delete_file_removes_raw(api):
    path = _raw_path(api, USER, api["node"], ".txt")
    assert api["client"].delete(f"/api/v1/kb/node/{api['node']}").status_code == 200
    assert not path.exists()


def test_delete_folder_removes_raw_recursively(api):
    path = _raw_path(api, USER, api["in_folder"], ".txt")
    assert api["client"].delete(f"/api/v1/kb/node/{api['folder']}").status_code == 200
    assert not path.exists()


# ── 讲义截图（幻灯片）────────────────────────────────────────

SLIDE_KEY = "0123456789abcdef0123456789abcdef"   # 导入方用 uuid4().hex（32 位十六进制）


def _make_slide(api, user_id: int, key: str = SLIDE_KEY, name: str = "0001.jpg"):
    path = api["root"] / str(user_id) / "slides" / key / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff\xd8\xff\xd9")   # 最小 JPEG 头尾，内容不重要
    return path


def test_slide_served_with_auth(api):
    _make_slide(api, USER)
    resp = api["client"].get(f"/api/v1/kb/slides/{SLIDE_KEY}/0001.jpg")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"


def test_slide_not_readable_across_users(api):
    """截图按 uid 分目录：别人的 key 即使猜中，读到的也是自己目录（不存在）→ 404。"""
    _make_slide(api, OTHER)
    assert api["client"].get(
        f"/api/v1/kb/slides/{SLIDE_KEY}/0001.jpg").status_code == 404


@pytest.mark.parametrize("key,name", [
    ("../" + SLIDE_KEY, "0001.jpg"),       # key 带穿越
    (SLIDE_KEY, "../secret.jpg"),          # 文件名带穿越
    (SLIDE_KEY, "0001.png"),               # 非白名单文件名
])
def test_slide_path_traversal_rejected(api, key, name):
    _make_slide(api, USER)
    assert api["client"].get(
        f"/api/v1/kb/slides/{key}/{name}").status_code == 404


def test_delete_node_removes_slides(api):
    import asyncio

    km = api["km"]
    node = asyncio.run(km.upload_and_index(
        user_id=USER, filename="课程讲义-x.md", content=BODY.encode("utf-8"),
        parent_id=None, vectorize=False, asset_key=SLIDE_KEY,
    ))
    path = _make_slide(api, USER)

    assert api["client"].delete(f"/api/v1/kb/node/{node}").status_code == 200
    assert not path.exists(), "删讲义必须连截图目录一起删，不留孤儿字节"


# ── 回滚 ─────────────────────────────────────────────────────

def test_parse_failure_leaves_no_raw(api, monkeypatch):
    """解析失败在 add_file 之前抛出 → 目录树与原件都不该出现。"""
    km, root = api["km"], api["root"]
    with pytest.raises(ValueError):
        _upload(km, USER, "坏文件.txt", "短".encode("utf-8"))

    files_dir = root / str(USER) / "files"
    assert not any(p.name.startswith("坏") for p in files_dir.iterdir())
    assert all(
        n["name"] != "坏文件.txt" for n in km._get_store(USER).get_children(USER, None)
    )


def test_index_failure_rolls_back_raw(api, monkeypatch):
    """索引阶段炸掉 → 目录树回滚，原件也必须一起删（否则留下无主的孤儿字节）。"""
    km, root = api["km"], api["root"]
    before = {p.name for p in (root / str(USER) / "files").iterdir()}

    async def boom(*args, **kwargs):
        raise RuntimeError("索引炸了")

    monkeypatch.setattr(km, "_index_document", boom)
    with pytest.raises(RuntimeError):
        _upload(km, USER, "半途.txt", BODY.encode("utf-8"))

    assert {p.name for p in (root / str(USER) / "files").iterdir()} == before
