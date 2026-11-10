"""空图谱时工具层的**可自纠正**提示（TODO §1 P1，2026-11-10 修）。

现场（P1 对照实验）：模型拿「知识点名字」当 node_id 去 `add_edge`（`from="二叉搜索树"`），
或者凭空连一条它以为已存在的边。旧文案只有 `起始节点不存在：二叉搜索树` ——
模型看到这句话的解读是"参数没传对"，于是**用同样的参数重试**，直到触发
`loop._LoopGuard` 的"同参数重复 ≤2"熔断，用户看到的是"AI 说在整理图谱，然后什么都没发生"。

修法是把异常文案写成**下一步动作**（不是断言）：点名 `add_knowledge_node` 与
"要传节点 ID 不是名字"。断言锁的是"文案里有没有可执行动作"，不是具体措辞 ——
措辞以后可以再改，但**不能退回纯断言**。
"""
import pytest

from app.core.knowledge_graph import KnowledgeGraph


@pytest.fixture
def kg(tmp_path):
    g = KnowledgeGraph(user_id=1, data_dir=tmp_path)
    with g._conn:
        g._conn.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (1, 'p1', 'x')")
    yield g
    g.close()


def test_missing_from_node_tells_model_what_to_do(kg):
    kg.add_node({"id": "b", "name": "动态规划", "tags": ["算法"]})

    with pytest.raises(ValueError) as ei:
        kg.add_edge({"from": "二叉搜索树", "to": "b", "relation": "related"})

    msg = str(ei.value)
    assert "二叉搜索树" in msg, "必须保留出错的标识本身（模型要能对上它传的参数）"
    assert "add_knowledge_node" in msg, "必须点出该调哪个工具，否则模型只会重试同样参数"
    assert "节点 ID" in msg, "实测病因就是拿名字当 ID，必须点明"


def test_missing_to_node_also_self_correcting(kg):
    kg.add_node({"id": "a", "name": "递归", "tags": ["算法"]})

    with pytest.raises(ValueError) as ei:
        kg.add_edge({"from": "a", "to": "不存在", "relation": "related"})

    msg = str(ei.value)
    assert "目标节点不存在" in msg
    assert "add_knowledge_node" in msg


def test_existing_both_nodes_still_works(kg):
    """文案改动不得影响正常路径（两个节点都在 → 照常建边）。"""
    kg.add_node({"id": "a", "name": "递归", "tags": ["算法"]})
    kg.add_node({"id": "b", "name": "动态规划", "tags": ["算法"]})

    kg.add_edge({"from": "a", "to": "b", "relation": "related"})
    assert len(kg.edges) == 1
