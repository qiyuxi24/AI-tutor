"""`graph_middleware.main_path` / `GET /knowledge/main-path` 的契约。

主路径 = **当前切片内** prerequisite 子图上的最长链（一条线），供前端"按下开关就亮出
一条该走的路 + 起点"。

这里只锁三件事：
  1. 选链规则：分叉取更长的那条；同样长取未掌握更多的；再同样长按 id 定序（必须可复现）。
  2. 语义字段：start_node_id = 链首（起步点）、end_node_id = 链尾、next_node_id = 链上第一个
     未掌握节点（全掌握时为 None）。
  3. 退化输入：没有先修边 / 先修成环 / 全已掌握 —— 一律不抛异常，且给出可读的 reason。

全离线：KnowledgeGraph 换成 FakeKG，不碰 data/。
"""
import pytest
from fastapi.testclient import TestClient

from app.api.v1 import knowledge as knowledge_module
from app.core.auth import get_current_user
from app.core.graph_middleware import MASTERY_NEXT_THRESHOLD, main_path, slice_graph

URL = "/api/v1/knowledge/main-path"
USER_ID = 7


class FakeKG:
    """最小 KnowledgeGraph 替身（只实现中间件依赖的方法）。"""

    def __init__(self, nodes, edges, subjects=None):
        self.nodes = nodes
        self.edges = edges
        self._subjects = subjects or []

    def get_subjects(self):
        return self._subjects

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


def _node(nid, subject="数据结构", board="", mastery=0):
    return {"id": nid, "name": f"节点{nid}", "subject": subject,
            "board": board, "mastery": mastery}


def _prereq(frm, to, subject="数据结构", board=""):
    return {"id": f"{frm}->{to}", "from_node": frm, "to_node": to,
            "relation": "prerequisite", "subject": subject, "board": board}


@pytest.fixture
def fork_kg():
    """a → b → c 与 a → d 分叉：最长链 = a→b→c（d 不该出现在主路径上）。"""
    return FakeKG(
        nodes=[_node("a"), _node("b"), _node("c"), _node("d")],
        edges=[_prereq("a", "b"), _prereq("b", "c"), _prereq("a", "d")],
    )


# ── 选链与语义 ────────────────────────────────────────

def test_longest_chain_wins_over_branch(fork_kg):
    got = main_path(fork_kg, subject="数据结构")

    assert got["ordered_nodes"] == ["a", "b", "c"]
    assert got["length"] == 3


def test_start_is_foundation_and_end_is_leaf(fork_kg):
    got = main_path(fork_kg, subject="数据结构")

    assert got["start_node_id"] == "a"
    assert got["end_node_id"] == "c"


def test_path_detail_carries_display_fields(fork_kg):
    got = main_path(fork_kg, subject="数据结构")

    assert [p["id"] for p in got["path"]] == ["a", "b", "c"]
    assert got["path"][0]["name"] == "节点a"
    assert "mastery" in got["path"][0]


def test_next_node_is_first_unmastered_on_path():
    """已掌握的前置不拦人：起点仍是 a，但"该动手的"是链上第一个未掌握节点。"""
    kg = FakeKG(
        nodes=[_node("a", mastery=90), _node("b", mastery=0), _node("c", mastery=0)],
        edges=[_prereq("a", "b"), _prereq("b", "c")],
    )
    got = main_path(kg, subject="数据结构")

    assert got["start_node_id"] == "a"
    assert got["next_node_id"] == "b"
    assert MASTERY_NEXT_THRESHOLD == 50


def test_all_mastered_has_no_next():
    kg = FakeKG(
        nodes=[_node("a", mastery=100), _node("b", mastery=80)],
        edges=[_prereq("a", "b")],
    )
    got = main_path(kg, subject="数据结构")

    assert got["ordered_nodes"] == ["a", "b"]
    assert got["next_node_id"] is None


def test_tie_prefers_chain_with_more_unmastered():
    """同样长的两条链 → 取"更没学会"的那条（更像该走的路）。"""
    kg = FakeKG(
        nodes=[_node("a1", mastery=100), _node("a2", mastery=100),
               _node("b1", mastery=0), _node("b2", mastery=0)],
        edges=[_prereq("a1", "a2"), _prereq("b1", "b2")],
    )
    got = main_path(kg, subject="数据结构")

    assert got["ordered_nodes"] == ["b1", "b2"]


def test_tie_is_deterministic():
    """两次调用结果必须一致（前端按 id 高亮，抖动会闪）。"""
    kg = FakeKG(
        nodes=[_node("n1"), _node("n2"), _node("m1"), _node("m2")],
        edges=[_prereq("n1", "n2"), _prereq("m1", "m2")],
    )

    assert main_path(kg, subject="数据结构")["ordered_nodes"] == \
        main_path(kg, subject="数据结构")["ordered_nodes"]


# ── 切片边界 ──────────────────────────────────────────

def test_slice_is_subject_scoped():
    """只在本学科内找链：另一个学科的更长链不能被选中。"""
    kg = FakeKG(
        nodes=[_node("x", subject="数据结构"), _node("y", subject="数据结构"),
               _node("p", subject="C语言"), _node("q", subject="C语言"),
               _node("r", subject="C语言")],
        edges=[_prereq("x", "y", subject="数据结构", board="线性结构"),
               _prereq("p", "q", subject="C语言"), _prereq("q", "r", subject="C语言")],
    )
    got = main_path(kg, subject="数据结构")

    assert got["ordered_nodes"] == ["x", "y"]


def test_board_slice_only_uses_board_edges():
    kg = FakeKG(
        nodes=[_node("a", board="线性结构"), _node("b", board="线性结构"),
               _node("c", board="树结构"), _node("d", board="树结构")],
        edges=[_prereq("a", "b", board="线性结构"),
               _prereq("c", "d", board="树结构")],
    )
    got = main_path(kg, subject="数据结构", board="树结构")

    assert got["ordered_nodes"] == ["c", "d"]


# ── 退化输入 ──────────────────────────────────────────

def test_non_prerequisite_edges_yield_no_path():
    kg = FakeKG(
        nodes=[_node("a"), _node("b")],
        edges=[{"id": "e", "from_node": "a", "to_node": "b",
                "relation": "related", "subject": "数据结构"}],
    )
    got = main_path(kg, subject="数据结构")

    assert got["path"] == []
    assert got["start_node_id"] is None
    assert got["prerequisite_edges"] == 0
    assert got["reason"]          # 前端要拿它给用户一句人话


def test_cycle_does_not_crash_and_keeps_acyclic_part():
    kg = FakeKG(
        nodes=[_node("a"), _node("b"), _node("c"), _node("d")],
        edges=[_prereq("a", "b"), _prereq("b", "a"),   # 环
               _prereq("c", "d")],
    )
    got = main_path(kg, subject="数据结构")

    assert got["ordered_nodes"] == ["c", "d"]


def test_empty_graph_is_safe():
    got = main_path(FakeKG(nodes=[], edges=[]), subject="数据结构")

    assert got["path"] == []
    assert got["length"] == 0


def test_slice_helpers_still_agree_with_slice_graph():
    """主路径与 /knowledge/graph 必须同源切片，否则高亮会指到画布上没有的节点。"""
    kg = FakeKG(nodes=[_node("a"), _node("b")], edges=[_prereq("a", "b")])
    sub = slice_graph(kg, subject="数据结构")
    got = main_path(kg, subject="数据结构")

    assert {p["id"] for p in got["path"]} <= {n["id"] for n in sub["nodes"]}


# ── HTTP 契约 ────────────────────────────────────────

class _FakeKGApi:
    """端点层替身：记录收到的构造与切片参数。"""

    calls: list = []

    def __init__(self, user_id):
        self.user_id = user_id
        _FakeKGApi.calls.append(("init", user_id))
        self.nodes = [_node("a"), _node("b")]
        self.edges = [_prereq("a", "b")]

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


def test_main_path_endpoint_returns_path(api_client):
    r = api_client.get(URL, params={"subject": "数据结构"})

    assert r.status_code == 200
    body = r.json()
    assert body["ordered_nodes"] == ["a", "b"]
    assert body["start_node_id"] == "a"
    assert body["subject"] == "数据结构"
    assert ("nodes", "数据结构") in _FakeKGApi.calls


def test_main_path_endpoint_passes_board(api_client):
    r = api_client.get(URL, params={"subject": "数据结构", "board": "线性结构"})

    assert r.status_code == 200
    assert r.json()["board"] == "线性结构"


def test_main_path_endpoint_without_subject_is_ok(api_client):
    """不传 subject = 全量切片（与 /knowledge/graph 同一口径），不报错。"""
    r = api_client.get(URL)

    assert r.status_code == 200
    assert r.json()["subject"] is None
