"""小节正文写入端点 `PUT /knowledge/node/{id}/section/{sid}` 的 HTTP 契约断言。

为什么单独补这一条写路径（2026-11-15）：节点小节化之后，一个节点的正文分散在
多个平行 MD 里，**没有主文件**。既有的 `PUT /knowledge/node/{id}` 走
`update_node_content` 只写 `nodes/{id}.md` —— 对有小节的节点写下去是没人会读的
孤儿文件，于是"节点详情里改正文"这件事长期只有老节点（无 manifest）能做。
本文件锁住新的写路径与它的边界。

断言覆盖：
- 成功：正文落盘、`write_section` 收 `filled` 状态、返回 `chars/total_chars`；
- **空串合法**（清空一节是正常操作，不是错误）；
- 写完调 `flush` 一次（改名后检索里不能还是旧正文）；
- 节点不存在 → 404；小节不存在 → 404；超长 → 400 且**不落盘**；
- 跨用户：另一个 user_id 的 KG 拿不到这节（per-user 隔离，不靠调用方记得过滤）；
- `write_section` 抛 `ValueError` → 404（前端能显示 detail，不是 500）。

全离线：`KnowledgeGraph` 用假实现（`write_section` 写**真临时目录**，让落盘断言
仍是真实文件读取），不碰 `data/`、不碰 LLM、不碰真实索引。
"""
import pytest
from fastapi.testclient import TestClient

from app.api.v1 import knowledge as knowledge_module
from app.core.auth import get_current_user

USER_ID = 5
OTHER_USER_ID = 9

_BODY_S01 = "# 定义\n\n二重积分的定义是……"


class _FakeKG:
    """只实现写端点用到的四件事：get_node / write_section / close / （flush 的 kg 参数）。"""

    def __init__(self, user_id, *, nodes_dir, nodes, sections):
        self.user_id = user_id
        self.nodes_dir = nodes_dir
        self._nodes = nodes
        self._sections = sections          # node_id -> {section_id: 文件名}
        self.written = []                  # [(node_id, section_id, content)]
        self.flush_calls = []

    # ── 端点用到的读接口 ──
    def get_node(self, node_id):
        return self._nodes.get(node_id)

    def close(self):
        pass

    # ── 写接口（落真文件，让"真的写进去了"这件事仍然被真实校验） ──
    def write_section(self, node_id, section_id, content):
        files = self._sections.get(node_id)
        if not files or section_id not in files:
            raise ValueError(f"小节不存在：{node_id}/{section_id}")
        target = self.nodes_dir / node_id
        target.mkdir(parents=True, exist_ok=True)
        (target / files[section_id]).write_text(content, encoding="utf-8")
        self.written.append((node_id, section_id, content))


@pytest.fixture
def api(tmp_path, monkeypatch):
    """TestClient + 假 KG（真临时 nodes_dir）+ 固定登录态 + 打桩 flush。"""
    from app.main import app
    import app.core.rag.node_indexer as node_indexer

    nodes_dir = tmp_path / "nodes"
    (nodes_dir / "n1").mkdir(parents=True)
    (nodes_dir / "n1" / "s01_定义.md").write_text(_BODY_S01, encoding="utf-8")

    fk = _FakeKG(
        USER_ID,
        nodes_dir=nodes_dir,
        nodes={"n1": {"id": "n1", "name": "二重积分"}},
        sections={"n1": {"s01": "s01_定义.md", "s02": "s02_例题.md"}},
    )
    monkeypatch.setattr(knowledge_module, "KnowledgeGraph", lambda **kw: fk)

    async def _fake_flush(user_id, kg=None):
        fk.flush_calls.append((user_id, kg))
        return 0

    monkeypatch.setattr(node_indexer, "flush", _fake_flush)

    # 广播打桩：端点会 broadcast(GRAPH_UPDATED)，真事件总线在测试里没有订阅者也无害，
    # 但打桩后能顺带断言"写正文确实通知了图谱"
    broadcasts = []
    monkeypatch.setattr(knowledge_module, "broadcast", lambda *a, **kw: broadcasts.append((a, kw)))

    app.dependency_overrides[get_current_user] = lambda: USER_ID
    yield {"client": TestClient(app), "kg": fk, "broadcasts": broadcasts,
           "nodes_dir": nodes_dir}
    app.dependency_overrides.clear()


# ── 成功路径 ────────────────────────────────────────────────

def test_write_section_ok(api):
    """保存成功：正文落盘 + 返回字数 + 调一次 flush + 通知图谱。"""
    r = api["client"].put(
        "/api/v1/knowledge/node/n1/section/s01",
        json={"content": "# 新的定义\n\n改写后的正文。"},
    )

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["node_id"] == "n1"
    assert body["section_id"] == "s01"
    assert body["chars"] == len("# 新的定义\n\n改写后的正文。")
    assert body["total_chars"] == body["chars"]

    # 真的写进文件了（本节就是它自己的 MD，没有"主文件"这回事）
    on_disk = (api["nodes_dir"] / "n1" / "s01_定义.md").read_text(encoding="utf-8")
    assert on_disk == "# 新的定义\n\n改写后的正文。"
    assert api["kg"].written == [("n1", "s01", "# 新的定义\n\n改写后的正文。")]


def test_write_section_flushes_index(api):
    """写完必须 flush：否则按需检索里一直是旧正文（与既有正文写入同一口径）。"""
    api["client"].put("/api/v1/knowledge/node/n1/section/s01", json={"content": "x"})

    assert len(api["kg"].flush_calls) == 1
    user_id, kg = api["kg"].flush_calls[0]
    assert user_id == USER_ID
    assert kg is api["kg"], "flush 必须拿到同一个 KG 实例（否则索引按另一个连接写）"


def test_write_section_broadcasts(api):
    """content_status / updated_at 变了 → 图谱与学习看板要能看到。"""
    api["client"].put("/api/v1/knowledge/node/n1/section/s01", json={"content": "x"})

    assert len(api["broadcasts"]) == 1


def test_empty_content_allowed(api):
    """清空一节是合法操作（不是错误）：写空串、返回 chars=0。"""
    r = api["client"].put("/api/v1/knowledge/node/n1/section/s01", json={"content": ""})

    assert r.status_code == 200
    assert r.json()["chars"] == 0
    assert (api["nodes_dir"] / "n1" / "s01_定义.md").read_text(encoding="utf-8") == ""


# ── 拒绝路径 ────────────────────────────────────────────────

def test_missing_node_404(api):
    r = api["client"].put("/api/v1/knowledge/node/nope/section/s01", json={"content": "x"})

    assert r.status_code == 404
    assert "节点不存在" in r.json()["detail"]


def test_missing_section_404(api):
    """节点在、小节不在 → 404，且 detail 指出是小节（前端能直接展示）。"""
    r = api["client"].put("/api/v1/knowledge/node/n1/section/nope", json={"content": "x"})

    assert r.status_code == 404
    assert "小节不存在" in r.json()["detail"]


def test_too_long_400_and_not_written(api):
    """超长直接 400 —— 静默截断等于数据丢失，宁可不写。"""
    limit = knowledge_module.SECTION_EDIT_MAX_CHARS
    r = api["client"].put(
        "/api/v1/knowledge/node/n1/section/s01",
        json={"content": "a" * (limit + 1)},
    )

    assert r.status_code == 400
    assert "过长" in r.json()["detail"]
    assert api["kg"].written == [], "超长请求不得落盘"
    # 原内容保持不变
    assert (api["nodes_dir"] / "n1" / "s01_定义.md").read_text(encoding="utf-8") == _BODY_S01


def test_requires_auth(api):
    """未认证 → 401（写路径不能匿名可及）。"""
    from app.main import app

    api["client"].app.dependency_overrides.clear()
    r = api["client"].put("/api/v1/knowledge/node/n1/section/s01", json={"content": "x"})
    assert r.status_code == 401


# ── per-user 隔离 ───────────────────────────────────────────

def test_section_write_isolated_per_user(api, monkeypatch):
    """别的用户拿同一个 (node_id, section_id) 写不到本用户的文件。

    隔离由 `KnowledgeGraph(user_id)` 自己保证（每个用户一套 nodes 目录），
    不是靠端点记得带 user_id —— 这里用一个只认识别人节点的假 KG 验证这条边界。
    """
    other_dir = api["nodes_dir"].parent / "other_nodes"
    (other_dir / "n1").mkdir(parents=True)
    (other_dir / "n1" / "s01_定义.md").write_text("别人的正文", encoding="utf-8")
    other = _FakeKG(
        OTHER_USER_ID,
        nodes_dir=other_dir,
        # 关键：别人的 KG 里没有这个节点 → 端点必须 404，而不是写进同一份文件
        nodes={},
        sections={"n1": {"s01": "s01_定义.md"}},
    )
    monkeypatch.setattr(knowledge_module, "KnowledgeGraph", lambda **kw: other)

    r = api["client"].put("/api/v1/knowledge/node/n1/section/s01", json={"content": "入侵"})

    assert r.status_code == 404
    assert other.written == []
    assert (other_dir / "n1" / "s01_定义.md").read_text(encoding="utf-8") == "别人的正文"
