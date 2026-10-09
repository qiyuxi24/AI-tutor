"""B3-1/B3-2：**掌握度进入学习顺序** + 「还没学会」阈值收敛为 1 处。

要锁的三件事
------------
1. `KnowledgeGraph.topological_sort()` 的**同层顺序**由掌握度决定（B3-1）；
   同层 = "当前都是入度 0、都能学"的那批节点 —— 旧实现是 FIFO，谁先学只看数据库行序，
   与"哪一节更弱"无关，学生拿到的"下一步"等于随机。
2. **前置达标优先**：前置还没学会的节点排在"前置已达标"的后面，不把人推上没有地基的楼。
3. 阈值**只有一个定义**：`core/graph_order.MASTERY_NEXT_THRESHOLD` ——
   `knowledge_graph` 与 `graph_middleware` 都不得再写 `< 50`（B3-2 的验收口径）。

⚠️ 两套排序（`topological_sort` 的全序 / `graph_middleware._layers` 的层号）**没有合并** ——
破环策略与"相关概念并入同层"的语义不同，硬合会同时破坏两边的契约；
判定与证据见 `docs/架构/明早边角清单.md` §B3-1。本文件只锁"同层口径同源"。

全离线：tmp_path 真实 KnowledgeGraph，不碰 LLM / 网络。
"""
import re
from pathlib import Path

import pytest

from app.core.graph_order import MASTERY_NEXT_THRESHOLD, is_unmastered, order_key
from app.core.knowledge_graph import KnowledgeGraph

USER_ID = 88
APP_DIR = Path(__file__).resolve().parents[1] / "app"


@pytest.fixture
def kg(tmp_path):
    g = KnowledgeGraph(user_id=USER_ID, data_dir=tmp_path / "kg")
    with g._conn:
        g._conn.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (?, 's', 'x')",
            (USER_ID,))
    yield g
    g.close()


def _node(kg, nid: str, mastery: int, name: str = ""):
    kg.add_node({"id": nid, "name": name or nid, "subject": "数据结构", "mastery": mastery})


def _prereq(kg, frm: str, to: str):
    kg.add_edge({"from": frm, "to": to, "relation": "prerequisite"}, caller="human")


# ── 1. 同层：掌握度升序 ──────────────────────────────────────────

def test_same_layer_ordered_by_mastery_ascending(kg):
    """同层三节点（互不为前置）→ 掌握度最低的先学。

    `a/b/c` 都是根（入度 0），把它们按 id 升序插库（旧 FIFO 会原样返回 a,b,c）——
    掌握度反着给，新口径必须给出 c(0) → b(40) → a(90)。
    """
    for nid, mastery in [("a", 90), ("b", 40), ("c", 0)]:
        _node(kg, nid, mastery)

    assert kg.topological_sort() == ["c", "b", "a"]


def test_same_layer_tie_is_stable_by_id(kg):
    """同层同分 → 按 id 升序（结果必须确定、可复现：前端高亮位置跟着它）。"""
    for nid in ["z", "m", "a"]:
        _node(kg, nid, 30)

    assert kg.topological_sort() == ["a", "m", "z"]


def test_mastery_does_not_break_prerequisite_order(kg):
    """前置关系优先于掌握度：掌握度再高也不能排到自己的前置前面。"""
    _node(kg, "base", 100)          # 前置：已掌握
    _node(kg, "adv", 0)             # 后继：完全没学
    _prereq(kg, "base", "adv")

    assert kg.topological_sort() == ["base", "adv"]


# ── 2. 前置达标优先 ─────────────────────────────────────────────

def test_unmastered_prereq_pushes_candidate_back(kg):
    """同为根？不是 —— `x` 的前置 `px` 还没学会，优先让 `py`（前置已达标）先来。

    图：px(0) → x(0)、py(90) → y(0)。两个候选 x/y 起点分都是 0；
    差别只在"前置达标没有" → y（前置 py 已达标）必须排在 x 前面。
    """
    _node(kg, "px", 0)
    _node(kg, "py", 90)
    _node(kg, "x", 0)
    _node(kg, "y", 0)
    _prereq(kg, "px", "x")
    _prereq(kg, "py", "y")

    ordered = kg.topological_sort()

    # 根层：px(0) 与 py(90) 都无前置 → 按掌握度升序 → px 先
    assert ordered[:2] == ["px", "py"]
    # 第二层：x 的前置 px 没学会 → y 先
    assert ordered.index("y") < ordered.index("x")


# ── 3. get_learning_path 的推荐跟着同层口径走 ────────────────────

def test_learning_path_recommends_weakest_same_layer_node(kg):
    for nid, mastery in [("strong", 95), ("weak", 0), ("mid", 40)]:
        _node(kg, nid, mastery)

    path = kg.get_learning_path()

    assert path["ordered_nodes"] == ["weak", "mid", "strong"]
    assert path["current_recommendation"] == "weak"


def test_learning_path_recommendation_none_when_all_mastered(kg):
    for nid in ["a", "b"]:
        _node(kg, nid, MASTERY_NEXT_THRESHOLD + 20)

    path = kg.get_learning_path()

    assert path["current_recommendation"] is None


def test_next_to_learn_reports_unmastered_prereqs(kg):
    """`get_next_to_learn` 的"建议先巩固"列的是**还没学会的直接前置**（同一阈值口径）。"""
    _node(kg, "base", 0)
    _node(kg, "mid", 80)
    _prereq(kg, "base", "mid")

    got = kg.get_next_to_learn()

    assert got["node_id"] == "base"          # 最弱的根先补
    assert got["reason"] == "学习路径上的下一个知识点"


# ── 4. 纯函数口径（graph_order）──────────────────────────────────

def test_order_key_shape_and_semantics():
    assert order_key({"id": "a", "mastery": 10}) == (0, 10, "a")
    assert order_key({"id": "a", "mastery": 10}, prereq_unmastered=True) == (1, 10, "a")
    # 缺 mastery / 空节点 → 当作 0 分（读不到不等于已掌握）
    assert order_key({"id": "a"}) == (0, 0, "a")
    assert order_key(None) == (0, 0, "")


def test_is_unmastered_boundary():
    assert is_unmastered({"mastery": MASTERY_NEXT_THRESHOLD - 1}) is True
    assert is_unmastered({"mastery": MASTERY_NEXT_THRESHOLD}) is False
    assert is_unmastered(None) is True
    assert is_unmastered({}) is True


# ── 5. 阈值只有一处（B3-2 验收口径：静态守卫）────────────────────

def test_mastery_threshold_is_defined_once():
    """`< 50` 这种字面量不得再出现在 `app/` 里 —— 阈值唯一真值 = `graph_order`。

    官方验收口径是 `grep -n "mastery" backend/app | grep "< 50"` 只剩 1 处；
    这里更严：**一处都不许有**（唯一那处在 `graph_order.MASTERY_NEXT_THRESHOLD = 50`，
    它不带 `< 50` 字面量）。用正则扫真实代码，注释里提到历史数字也算 —— 因为它会
    诱导下一个人照抄。
    """
    hits = []
    for path in sorted(APP_DIR.rglob("*.py")):
        if path.name == "graph_order.py":
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"<\s*50\b", line):
                hits.append(f"{path.relative_to(APP_DIR)}:{lineno}: {line.strip()}")

    assert hits == [], ("阈值必须收敛到 core/graph_order.MASTERY_NEXT_THRESHOLD：\n"
                        + "\n".join(hits))


def test_graph_middleware_reexports_the_shared_threshold():
    """`graph_middleware.MASTERY_NEXT_THRESHOLD` 是同一个对象（既有 import 路径不破）。"""
    from app.core import graph_middleware

    assert graph_middleware.MASTERY_NEXT_THRESHOLD is MASTERY_NEXT_THRESHOLD
    assert graph_middleware.MASTERY_NEXT_THRESHOLD == 50
