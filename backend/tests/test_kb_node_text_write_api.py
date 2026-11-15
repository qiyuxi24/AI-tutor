"""知识库正文写入测试（PUT /kb/node/{id}/text + GET full=1，全离线：临时 KB + 依赖覆盖）。

为什么单独一个文件：这是知识库第一次有「改正文」的写路径（此前 `documents.extract_text`
只在 upload 时写一次，`reindex_file` 只重建索引、不碰正文）。写正文一旦出错就是**数据丢失**，
所以每条拒绝路径与"两份真相都写到"都要钉住。

覆盖：
- GET 默认截断 / `full=true` 返回全文（编辑器专用，截断正文保存=静默丢数据）
- GET 返回 `editable`（md/txt 为真，pdf 为假）
- PUT 成功：正文落 `documents.extract_text`、原件落盘、返回字节数
- PUT 补建原件（历史文件原本没有 `files/{id}.md`）
- PUT 拒绝：404 不存在 / 400 文件夹 / 400 非白名单格式 / 400 超长 / 401 未认证
- 保存后的后台任务：成功与异常都投 `kb_updated` 通知（前端靠它刷新统计）
- `KbManager.write_document_text`：无文档记录 → ValueError；索引不在写路径里重建

零网络 / 零 LLM：文件节点直接用 `KbStore.add_file` 挂载，不解析、不向量化；
后台索引重建一律打桩。
"""
import asyncio
import importlib

import pytest
from fastapi.testclient import TestClient

kb_manager_module = importlib.import_module("app.core.kb.kb_manager")
from app.api.v1 import kb as kb_module
from app.api.v1.kb import KB_EDITABLE_EXTS, KB_PREVIEW_MAX_CHARS
from app.core.event_bus import KB_UPDATED
from app.core.kb.kb_manager import KbManager

USER = 7
OTHER = 8


def _add_file(km: KbManager, user_id: int, name: str, parent_id,
              text: str, file_type: str = ".md") -> int:
    """只挂文件节点（不做解析/向量化），extract_text 即"已解析正文"。"""
    return km._get_store(user_id).add_file(
        user_id=user_id, name=name, parent_id=parent_id,
        file_type=file_type, extract_text=text,
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
    pdf = _add_file(km, USER, "论文.pdf", None, "PDF 解析出来的正文", file_type=".pdf")

    app.dependency_overrides[get_current_user] = lambda: USER
    yield {
        "client": TestClient(app), "km": km,
        "note": note, "folder": folder, "long": long_file, "pdf": pdf,
    }
    app.dependency_overrides.clear()


def _get(api, node_id: int, **params):
    return api["client"].get(f"/api/v1/kb/node/{node_id}/text", params=params)


def _put(api, node_id: int, content: str):
    return api["client"].put(f"/api/v1/kb/node/{node_id}/text", json={"content": content})


# ── GET：全文与可编辑标记 ────────────────────────────────────

def test_full_returns_whole_text(api):
    """full=true 不截断 —— 编辑器拿到的必须是全文（否则保存回去就是丢数据）。"""
    resp = _get(api, api["long"], full="true")
    assert resp.status_code == 200
    body = resp.json()
    assert body["truncated"] is False
    assert body["chars"] == KB_PREVIEW_MAX_CHARS + 100
    assert len(body["markdown"]) == KB_PREVIEW_MAX_CHARS + 100


def test_default_still_truncated(api):
    body = _get(api, api["long"]).json()
    assert body["truncated"] is True
    assert body["chars"] == KB_PREVIEW_MAX_CHARS


def test_editable_flag_follows_whitelist(api):
    assert _get(api, api["note"]).json()["editable"] is True
    assert _get(api, api["pdf"]).json()["editable"] is False


# ── PUT：成功路径 ────────────────────────────────────────────

def test_put_overwrites_text_and_returns_bytes(api, monkeypatch):
    monkeypatch.setattr(kb_module, "runtime",
                        type("R", (), {"spawn": staticmethod(lambda coro: coro.close())}))
    new_text = "# 队列\n\n先进先出。"
    resp = _put(api, api["note"], new_text)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["chars"] == len(new_text)
    assert body["total_chars"] == len(new_text)
    assert body["bytes"] == len(new_text.encode("utf-8"))
    assert body["truncated"] is False

    # 正文已落库，且再读回来就是新内容（不留截断残影）
    assert api["km"].get_document_text(USER, api["note"]) == new_text
    assert _get(api, api["note"]).json()["markdown"] == new_text


def test_put_also_writes_raw_file(api, monkeypatch):
    """原件必须与正文同步 —— 只写一份就会分叉（下载下来还是旧内容）。"""
    monkeypatch.setattr(kb_module, "runtime",
                        type("R", (), {"spawn": staticmethod(lambda coro: coro.close())}))
    assert api["km"].get_raw_path(USER, api["note"]) is None, "夹具不建原件，验补建"

    _put(api, api["note"], "# 新正文")

    path = api["km"].get_raw_path(USER, api["note"])
    assert path is not None, "保存后原件应已补建"
    assert path.read_text(encoding="utf-8") == "# 新正文"


def test_put_allows_emptying(api, monkeypatch):
    """清空一个文件是合法操作（不是校验错误）。"""
    monkeypatch.setattr(kb_module, "runtime",
                        type("R", (), {"spawn": staticmethod(lambda coro: coro.close())}))
    resp = _put(api, api["note"], "")
    assert resp.status_code == 200
    assert resp.json()["chars"] == 0
    assert api["km"].get_document_text(USER, api["note"]) == ""


def test_put_spawns_reindex_in_background(api, monkeypatch):
    """索引重建交给后台任务（不在请求里 await）—— 大文件分块+嵌入是分钟级。"""
    spawned = []
    monkeypatch.setattr(kb_module, "runtime",
                        type("R", (), {"spawn": staticmethod(spawned.append)}))

    assert _put(api, api["note"], "正文").status_code == 200
    assert len(spawned) == 1, "必须恰好起一个后台重建任务"
    spawned[0].close()          # 协程没被调度，显式关闭避免 RuntimeWarning


# ── PUT：拒绝路径 ────────────────────────────────────────────

def test_put_missing_node_404(api):
    assert _put(api, 99999, "x").status_code == 404


def test_put_folder_400(api):
    resp = _put(api, api["folder"], "x")
    assert resp.status_code == 400
    assert "文件夹" in resp.json()["detail"]


def test_put_non_editable_format_400(api):
    """PDF 的正文只是原件的投影，改它拼不回原件 → 拒绝，并指路「在浏览器中打开」。"""
    resp = _put(api, api["pdf"], "改一下")
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "不支持编辑" in detail
    assert "/".join(KB_EDITABLE_EXTS) in detail
    assert api["km"].get_document_text(USER, api["pdf"]) == "PDF 解析出来的正文"


def test_put_too_long_400(api, monkeypatch):
    """超限明确 400，**不静默截断**（截断保存等于丢数据）。"""
    monkeypatch.setattr(kb_module, "KB_EDIT_MAX_CHARS", 5)
    resp = _put(api, api["note"], "123456")
    assert resp.status_code == 400
    assert "正文过长" in resp.json()["detail"]


def test_put_requires_auth(api):
    from app.main import app
    from app.core.auth import get_current_user

    app.dependency_overrides.pop(get_current_user, None)
    try:
        client = TestClient(app)
        resp = client.put(f"/api/v1/kb/node/{api['note']}/text", json={"content": "x"})
        assert resp.status_code == 401
    finally:
        app.dependency_overrides[get_current_user] = lambda: USER


def test_put_writes_only_own_user_db(api):
    """跨用户隔离由 **per-user 库各自编号**提供：两个用户的首个节点都是 id=1。

    所以「拿别人的 id 来写」在本架构里根本不存在独立的那一行 —— 同 id 只会命中
    自己的库。这条钉住的是「写入落在请求者的库里，别人的正文纹丝不动」。
    """
    other_node = _add_file(api["km"], OTHER, "别人的.md", None, "别人的正文")
    assert other_node == api["note"], "夹具确实构造了同 id（各库独立编号）"

    api["km"].write_document_text(USER, api["note"], "我改的")

    assert api["km"].get_document_text(USER, api["note"]) == "我改的"
    assert api["km"].get_document_text(OTHER, other_node) == "别人的正文"


def test_put_by_user_without_that_node_404(api):
    """请求者自己的库里没有这个 id → 404（不是"改到了别人的文件"）。"""
    from app.main import app
    from app.core.auth import get_current_user

    app.dependency_overrides[get_current_user] = lambda: OTHER   # OTHER 库里还没有节点
    try:
        resp = _put(api, api["note"], "改")
        assert resp.status_code == 404
    finally:
        app.dependency_overrides[get_current_user] = lambda: USER
    assert api["km"].get_document_text(USER, api["note"]) == "# 栈\n\n后进先出。"


# ── 后台任务：通知 ───────────────────────────────────────────

@pytest.fixture
def sent(monkeypatch):
    """截获本模块的 notify（kb.py 里是 `from ... import notify`，故 patch 模块属性）。"""
    calls = []

    def fake_notify(user_id, event_type, data=None, **kw):
        calls.append({"user_id": user_id, "type": event_type, "data": data or {}})

    monkeypatch.setattr(kb_module, "notify", fake_notify)
    return calls


def test_reindex_success_notifies(monkeypatch, sent):
    async def fake_reindex(user_id, node_id):
        return 12

    monkeypatch.setattr(kb_module.kb_manager, "reindex_file", fake_reindex)
    asyncio.run(kb_module._reindex_after_save(USER, 3, "笔记.md"))

    assert len(sent) == 1, "保存结束必须恰好一条通知（多投会重复弹提示）"
    n = sent[0]
    assert n["user_id"] == USER and n["type"] == KB_UPDATED
    assert n["data"]["ok"] is True
    assert n["data"]["chunks"] == 12
    assert n["data"]["node_id"] == 3
    assert n["data"]["reason"] == "text_saved"


def test_reindex_failure_still_notifies(monkeypatch, sent):
    """重建索引失败也要通知 —— 否则前端永远不知道"保存完了没有"。"""
    async def boom(user_id, node_id):
        raise RuntimeError("嵌入欠费")

    monkeypatch.setattr(kb_module.kb_manager, "reindex_file", boom)
    asyncio.run(kb_module._reindex_after_save(USER, 3, "笔记.md"))

    assert len(sent) == 1
    assert sent[0]["data"]["ok"] is False
    assert "嵌入欠费" in sent[0]["data"]["error"]


def test_reindex_cancel_is_not_swallowed(monkeypatch, sent):
    """CancelledError 是 BaseException：必须继续抛（服务关停要能真停），且不发通知。"""
    async def cancelled(user_id, node_id):
        raise asyncio.CancelledError()

    monkeypatch.setattr(kb_module.kb_manager, "reindex_file", cancelled)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(kb_module._reindex_after_save(USER, 3, "笔记.md"))
    assert sent == []


# ── KbManager.write_document_text ────────────────────────────

def test_write_document_text_without_document_raises(tmp_path):
    km = KbManager(tmp_path / "kb")
    folder = km.create_folder(USER, "空目录", None)
    with pytest.raises(ValueError, match="没有文档记录"):
        km.write_document_text(USER, folder, "x")


def test_write_document_text_does_not_touch_index(api, monkeypatch):
    """写正文**不重建索引**（调用方负责）—— 否则保存响应会被分钟级嵌入拖死。"""
    called = []

    async def boom(*a, **kw):
        called.append(a)
        raise AssertionError("写路径不该调索引")

    monkeypatch.setattr(api["km"], "_index_document", boom)
    written = api["km"].write_document_text(USER, api["note"], "# 新")
    assert written == len("# 新".encode("utf-8"))
    assert called == []
