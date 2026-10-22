"""前置依赖成环时的防护回归（2026-10-22 线上事故）

事故链路（真实发生）：
1. `PUT /knowledge/edge/{id}` 走 `update_edge_by_id`，它**没有环检查** →
   把一条 `related` 边改成 `prerequisite` 就绕过了 `add_edge` 的防环防线；
2. 前置图因此成环，而 `get_prerequisites` 的递归 CTE 是
   `UNION ALL` + `depth + 1` + 无 visited → 环上无限展开、查询永不返回；
3. uvicorn `--workers 1` 的唯一事件循环被这条同步查询占死 →
   整个 /api 冻死约 52 小时（healthcheck 只把容器标 unhealthy，没有 autoheal）。

本文件钉两件事：
1. `update_edge_by_id` 把关系改成 prerequisite 时必须拒绝成环（堵住入口）；
2. `get_prerequisites` 遇到**已存在的**环也必须返回有限集合（存量脏数据兜底）。
"""
import pytest

from app.core.knowledge_graph import KnowledgeGraph


@pytest.fixture
def kg(tmp_path):
    """链 a → b → c（prerequisite），另有 c → a 的 related 边备用（改成前置即成环）"""
    g = KnowledgeGraph(user_id=1, data_dir=tmp_path)
    with g._conn:  # nodes.user_id 是外键，先备 users 行
        g._conn.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (1, 'cycle', 'x')")
    for nid, name in [("a", "A"), ("b", "B"), ("c", "C")]:
        g.add_node({"id": nid, "name": name, "tags": ["算法"], "mastery": 0})
    for src, dst, rel in [("a", "b", "prerequisite"), ("b", "c", "prerequisite"), ("c", "a", "related")]:
        g.add_edge({"from": src, "to": dst, "relation": rel}, caller="human")
    yield g
    g.close()


def _edge_id(kg, from_id, to_id, relation):
    row = kg._conn.execute(
        "SELECT id FROM edges WHERE from_node = ? AND to_node = ? AND relation = ? AND user_id = ?",
        (from_id, to_id, relation, 1)).fetchone()
    return row["id"]


def test_update_edge_to_prerequisite_rejects_cycle(kg):
    """c → a 由 related 改成 prerequisite 会形成 a → b → c → a，必须拒绝"""
    edge_id = _edge_id(kg, "c", "a", "related")
    with pytest.raises(ValueError, match="循环前置依赖"):
        kg.update_edge_by_id(edge_id, {"relation": "prerequisite"})
    # 抛错前不得落库
    assert kg._conn.execute(
        "SELECT relation FROM edges WHERE id = ?", (edge_id,)).fetchone()["relation"] == "related"


def test_update_edge_rejects_invalid_relation(kg):
    """update 入口也要校验关系枚举（原来可以写入任意字符串）"""
    edge_id = _edge_id(kg, "c", "a", "related")
    with pytest.raises(ValueError, match="无效的关系类型"):
        kg.update_edge_by_id(edge_id, {"relation": "bogus"})


@pytest.mark.timeout(10)
def test_get_prerequisites_terminates_on_cycle(kg):
    """存量脏数据已带环时，查询必须有限终止（曾经无限递归把整个服务占死）"""
    # 绕过 add_edge 的防环，直接写库模拟历史脏数据
    with kg._conn:
        kg._conn.execute(
            "INSERT INTO edges (from_node, to_node, relation, label, added_by, user_id, created_at, updated_at) "
            "VALUES ('c', 'a', 'prerequisite', '', 'human', 1, '2026-10-20T12:53:17', '2026-10-20T12:53:17')")
    assert set(kg.get_prerequisites("c")) == {"a", "b"}
    assert set(kg.get_prerequisites("a")) == {"b", "c"}
