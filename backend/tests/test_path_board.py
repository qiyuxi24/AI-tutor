"""`graph_middleware.path_board` / `GET /knowledge/path-board` 的契约。

「学习任务栏」= 分层看板：无前置的并排在第 1 层，下层等前置掌握后解锁；
某个知识点没掌握时，沿前置反向追溯到**最根源的未掌握知识点**。

这里锁四件事：
  1. 分层取**最长**前置链（`a→c` 与 `a→b→c` 并存时 c 必须在 b 之后，不能按最短提到 b 层）；
  2. 解锁判定只看**直接前置**是否全部掌握（≥70）；
  3. 追溯沿**全部祖先**走 —— "直接前置已掌握、但它自己的前置没掌握"必须能追到根源；
  4. 退化输入：无前置边、成环、空图 —— 一律不抛异常。

全离线：KnowledgeGraph 换成 FakeKG，不碰 data/。
"""
import pytest
from fastapi.testclient import TestClient

from app.api.v1 import knowledge as knowledge_module
from app.core.auth import get_current_user
from app.core.graph_middleware import MASTERY_MASTERED, path_board

URL = "/api/v1/knowledge/path-board"
USER_ID = 9


class FakeKG:
    """最小 KnowledgeGraph 替身（只实现中间件依赖的方法）。"""

    def __init__(self, nodes, edges):
        self.nodes = nodes
        self.edges = edges

    def get_nodes_by_subject(self, subject):
        return [n for n in self.nodes if n.get("subject") == subject]

    def get_edges_by_subject(self, subject):
        return [e for e in self.edges if e.get("subject") == subject]

    def get_nodes_by_board(self, subject, board):
        return [n for n in self.nodes
                if n.get("subject") == subject and n.get("board") == board]

    def get_edges_by_board(self, subject, board):
        return [e for e in self.edges
                if e.get("subject") == subject and e.get("board") == board]

    @staticmethod
    def node_subject(node):
        return node.get("subject", "")


def _node(nid, mastery=0, subject="数据结构", board="", name=None):
    return {"id": nid, "name": name or f"节点{nid}", "summary": f"{nid} 的摘要",
            "subject": subject, "board": board, "mastery": mastery}


def _prereq(frm, to, subject="数据结构", board=""):
    return {"id": f"{frm}->{to}", "from_node": frm, "to_node": to,
            "relation": "prerequisite", "subject": subject, "board": board}


def _by_id(got):
    return {it["id"]: it for it in got["items"]}


# ── 分层 ────────────────────────────────────────────────

def test_roots_share_first_level():
    """没有前置的知识点并排在同一个层（level 0 = 第 1 层）。"""
    kg = FakeKG(
        nodes=[_node("a"), _node("b"), _node("c"), _node("d")],
        edges=[_prereq("a", "c"), _prereq("b", "d")],
    )
    got = path_board(kg, subject="数据结构")
    items = _by_id(got)

    assert items["a"]["level"] == 0
    assert items["b"]["level"] == 0
    assert items["c"]["level"] == 1
    assert items["d"]["level"] == 1
    assert [it["id"] for it in got["items"] if it["level"] == 0] == ["a", "b"]


def test_level_uses_longest_chain():
    """`a→c` 与 `a→b→c` 并存：c 必须排到 b 之后（层号取最长，不取最短）。"""
    kg = FakeKG(
        nodes=[_node("a"), _node("b"), _node("c")],
        edges=[_prereq("a", "b"), _prereq("b", "c"), _prereq("a", "c")],
    )
    got = path_board(kg, subject="数据结构")
    items = _by_id(got)

    assert items["c"]["level"] == 2
    assert [it["id"] for it in got["items"]] == ["a", "b", "c"]


def test_items_sorted_by_level_then_id():
    """卡片位置稳定：按 (层号, id) 排，学会一个不会让整板重排。"""
    kg = FakeKG(
        nodes=[_node("z"), _node("a"), _node("m")],
        edges=[_prereq("z", "m")],
    )
    got = path_board(kg, subject="数据结构")

    assert [it["id"] for it in got["items"]] == ["a", "z", "m"]


# ── 前置与解锁 ──────────────────────────────────────────

def test_prerequisites_and_unlocks_are_symmetric():
    kg = FakeKG(
        nodes=[_node("a"), _node("b")],
        edges=[_prereq("a", "b")],
    )
    got = path_board(kg, subject="数据结构")
    items = _by_id(got)

    assert [p["id"] for p in items["b"]["prerequisites"]] == ["a"]
    assert [s["id"] for s in items["a"]["unlocks"]] == ["b"]


def test_blocked_by_only_counts_direct_prereq_below_mastered():
    """解锁只看**直接**前置是否全部掌握（≥70）；已掌握的前置不算阻碍。"""
    kg = FakeKG(
        nodes=[_node("a", mastery=MASTERY_MASTERED), _node("b", mastery=69),
               _node("c", mastery=0)],
        edges=[_prereq("a", "c"), _prereq("b", "c")],
    )
    got = path_board(kg, subject="数据结构")
    items = _by_id(got)

    assert items["c"]["blocked_by"] == ["b"]          # a 已掌握，不挡路
    assert "先学完" in items["c"]["hint"]
    assert "节点b" in items["c"]["hint"]
    # b 自己就是未掌握的那个 → 追溯焦点落在 b（没有更深层缺口）
    assert items["c"]["trace"]["focus_id"] == "b"
    assert items["c"]["trace"]["chain"] == ["b", "c"]

    ok = path_board(FakeKG(nodes=[_node("a", mastery=70), _node("b")],
                           edges=[_prereq("a", "b")]), subject="数据结构")
    assert _by_id(ok)["b"]["blocked_by"] == []


def test_status_follows_mastery_bucket():
    """状态直接复用四档口径，不在前端另算。"""
    kg = FakeKG(
        nodes=[_node("n0", 0), _node("weak", 20), _node("learn", 50),
               _node("ok", 70)],
        edges=[],
    )
    got = path_board(kg, subject="数据结构")
    items = _by_id(got)

    assert items["n0"]["status"] == "unstarted"
    assert items["weak"]["status"] == "weak"
    assert items["learn"]["status"] == "learning"
    assert items["ok"]["status"] == "mastered"


def test_stats_counts_each_bucket_and_locked():
    kg = FakeKG(
        nodes=[_node("a", 70), _node("b", 50), _node("c", 20), _node("d", 0),
               _node("e", 0)],
        edges=[_prereq("d", "e")],          # e 被未掌握的 d 挡住
    )
    stats = path_board(kg, subject="数据结构")["stats"]

    assert stats["total"] == 5
    assert stats["mastered"] == 1
    assert stats["learning"] == 1
    assert stats["weak"] == 1
    assert stats["unstarted"] == 2
    assert stats["locked"] == 1


# ── 向前追溯 ────────────────────────────────────────────

def test_trace_back_finds_direct_unmastered_prereq():
    kg = FakeKG(
        nodes=[_node("graph", 0, name="图的存储"), _node("bfs", 20, name="BFS 与 DFS")],
        edges=[_prereq("graph", "bfs")],
    )
    got = path_board(kg, subject="数据结构")
    trace = _by_id(got)["bfs"]["trace"]

    assert trace["focus_id"] == "graph"
    assert trace["unmastered_count"] == 1
    assert trace["chain"] == ["graph", "bfs"]          # 链上元素是 id（名字从 items 里取）
    assert "图的存储" in _by_id(got)["bfs"]["hint"]


def test_trace_back_skips_mastered_middle_layer():
    """
    核心用例：直接前置 b 被"背题蒙过"70 分，但 b 的前置 a 还是 0 分。
    只盯直接前置会判断"没有未掌握前置"→ 把人继续按在 b 上卡住；
    必须沿祖先追到 a。
    """
    kg = FakeKG(
        nodes=[_node("a", 0, name="递归"), _node("b", 75, name="树与二叉树"),
               _node("c", 20, name="AVL 树")],
        edges=[_prereq("a", "b"), _prereq("b", "c")],
    )
    got = path_board(kg, subject="数据结构")
    c = _by_id(got)["c"]

    assert c["blocked_by"] == []                     # 直接前置 b 已掌握 → 不算被挡
    assert c["trace"]["focus_id"] == "a"             # 但根源缺口在 a
    assert c["trace"]["chain"] == ["a", "b", "c"]
    assert "递归" in c["hint"]


def test_trace_chain_is_ordered_root_to_node():
    kg = FakeKG(
        nodes=[_node("a"), _node("b"), _node("c")],
        edges=[_prereq("a", "b"), _prereq("b", "c")],
    )
    chain = _by_id(path_board(kg, subject="数据结构"))["c"]["trace"]["chain"]

    assert chain == ["a", "b", "c"]


def test_trace_back_picks_weakest_root():
    """多条根源并存时先补最弱的那个（掌握度最低）。"""
    kg = FakeKG(
        nodes=[_node("strong", 60), _node("weak", 5), _node("top", 0)],
        edges=[_prereq("strong", "top"), _prereq("weak", "top")],
    )
    trace = _by_id(path_board(kg, subject="数据结构"))["top"]["trace"]

    assert trace["focus_id"] == "weak"
    assert sorted(trace["focus_ids"]) == ["strong", "weak"]
    assert trace["unmastered_count"] == 2


def test_mastered_node_has_no_trace():
    kg = FakeKG(
        nodes=[_node("a", 0), _node("b", 90)],
        edges=[_prereq("a", "b")],
    )
    got = path_board(kg, subject="数据结构")

    assert _by_id(got)["b"]["trace"] is None
    assert _by_id(got)["b"]["hint"] == ""


def test_node_without_prereq_says_can_start():
    kg = FakeKG(nodes=[_node("a")], edges=[])
    got = path_board(kg, subject="数据结构")

    assert _by_id(got)["a"]["hint"] == "可以直接开始"
    assert _by_id(got)["a"]["trace"] is None


# ── 推荐顺序 ────────────────────────────────────────────

def test_recommended_only_unlocked_and_unmastered():
    kg = FakeKG(
        nodes=[_node("a", 90), _node("b", 0), _node("c", 40)],
        edges=[_prereq("a", "b"), _prereq("b", "c")],
    )
    got = path_board(kg, subject="数据结构")

    # a 已掌握 → 不进推荐；c 被未掌握的 b 挡住 → 不进推荐
    assert [it["id"] for it in got["recommended"]] == ["b"]


def test_recommended_order_is_by_level_then_weakest():
    kg = FakeKG(
        nodes=[_node("a", 30), _node("b", 10), _node("c", 0)],
        edges=[_prereq("a", "c"), _prereq("b", "c")],
    )
    got = path_board(kg, subject="数据结构")

    # a / b 都在第 1 层且没被挡 → 可推；层内取最弱的先补
    # c 被 a、b 两个未掌握的前置挡住 → 不进推荐
    assert [it["id"] for it in got["recommended"]] == ["b", "a"]
    assert _by_id(got)["c"]["blocked_by"] == ["a", "b"]


# ── 切片与退化输入 ──────────────────────────────────────

def test_slice_is_subject_scoped():
    kg = FakeKG(
        nodes=[_node("x", subject="数据结构"), _node("y", subject="数据结构"),
               _node("p", subject="C语言"), _node("q", subject="C语言")],
        edges=[_prereq("x", "y"), _prereq("p", "q", subject="C语言")],
    )
    got = path_board(kg, subject="数据结构")

    assert sorted(it["id"] for it in got["items"]) == ["x", "y"]
    assert got["subject"] == "数据结构"


def test_board_slice_only_uses_board_edges():
    kg = FakeKG(
        nodes=[_node("a", board="线性结构"), _node("b", board="线性结构"),
               _node("c", board="树结构"), _node("d", board="树结构")],
        edges=[_prereq("a", "b", board="线性结构"),
               _prereq("c", "d", board="树结构")],
    )
    got = path_board(kg, subject="数据结构", board="树结构")

    assert sorted(it["id"] for it in got["items"]) == ["c", "d"]


def test_non_prerequisite_edges_are_ignored():
    kg = FakeKG(
        nodes=[_node("a"), _node("b")],
        edges=[{"id": "e", "from_node": "a", "to_node": "b",
                "relation": "related", "subject": "数据结构"}],
    )
    got = path_board(kg, subject="数据结构")
    items = _by_id(got)

    assert items["b"]["prerequisites"] == []
    assert items["b"]["level"] == 0


def test_cycle_does_not_crash():
    """成环时先断 back-edge 再分层：环下游的节点也要拿到合理的层号，不能全挤在第 1 层。"""
    kg = FakeKG(
        nodes=[_node("a"), _node("b"), _node("c")],
        edges=[_prereq("a", "b"), _prereq("b", "a"), _prereq("b", "c")],
    )
    got = path_board(kg, subject="数据结构")
    items = _by_id(got)

    assert len(got["items"]) == 3
    assert items["a"]["level"] == 0
    assert items["b"]["level"] == 1
    assert items["c"]["level"] == 2


def test_empty_graph_is_safe():
    got = path_board(FakeKG(nodes=[], edges=[]), subject="数据结构")

    assert got["items"] == []
    assert got["recommended"] == []
    assert got["stats"]["total"] == 0


# ── HTTP 契约 ──────────────────────────────────────────

class _FakeKGApi:
    calls: list = []

    def __init__(self, user_id):
        self.user_id = user_id
        _FakeKGApi.calls.append(("init", user_id))
        self.nodes = [_node("a"), _node("b"), _node("c", 20)]
        self.edges = [_prereq("a", "b"), _prereq("b", "c")]

    def get_nodes_by_subject(self, subject):
        _FakeKGApi.calls.append(("nodes", subject))
        return self.nodes

    def get_edges_by_subject(self, subject):
        _FakeKGApi.calls.append(("edges", subject))
        return self.edges

    def get_nodes_by_board(self, subject, board):
        _FakeKGApi.calls.append(("nodes_board", subject, board))
        return self.nodes

    def get_edges_by_board(self, subject, board):
        _FakeKGApi.calls.append(("edges_board", subject, board))
        return self.edges

    def close(self):
        _FakeKGApi.calls.append(("close",))


@pytest.fixture
def api_client(monkeypatch):
    _FakeKGApi.calls.clear()
    monkeypatch.setattr(knowledge_module, "KnowledgeGraph", _FakeKGApi)
    from app.main import app

    app.dependency_overrides[get_current_user] = lambda: USER_ID
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_path_board_endpoint_returns_items(api_client):
    r = api_client.get(URL, params={"subject": "数据结构"})

    assert r.status_code == 200
    body = r.json()
    assert [it["id"] for it in body["items"]] == ["a", "b", "c"]
    assert body["items"][2]["trace"]["focus_id"] == "a"
    assert body["subject"] == "数据结构"
    assert ("nodes", "数据结构") in _FakeKGApi.calls


def test_path_board_endpoint_passes_board(api_client):
    r = api_client.get(URL, params={"subject": "数据结构", "board": "线性结构"})

    assert r.status_code == 200
    assert r.json()["board"] == "线性结构"
    assert ("nodes_board", "数据结构", "线性结构") in _FakeKGApi.calls


def test_path_board_endpoint_without_subject_is_ok(api_client):
    """不传 subject = 全量切片（与 /knowledge/graph 同一口径），不报错。"""
    r = api_client.get(URL)

    assert r.status_code == 200
    assert r.json()["subject"] is None
