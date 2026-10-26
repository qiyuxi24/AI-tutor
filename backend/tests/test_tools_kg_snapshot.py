"""`kg_snapshot` 工具 + `core/graph_snapshot.py` 的契约测试（2026-10-26，P1）。

为什么单独锁这一块：
1. **同源**：模型侧工具与插件面板（`local_http GET /graph`）必须共用
   `graph_snapshot.snapshot()` —— 这里断言"工具输出 == 同一份数据的渲染"，
   另在 `test_local_http.py` 断言 HTTP 端点的 JSON 也是这个形状。
2. **永不抛异常**：工具在外部宿主（DSH）里跑，异常会把整个 loop 挂掉；
   失败必须是模型能看懂、能改行为的中文。
3. **掌握度口径唯一**：已掌握 = `graph_order.is_unmastered` 的反面（阈值 50），
   本文件用 49/50 边界锁住，防止有人在这里写第二个阈值。

全部零真实 `data/`：`KnowledgeGraph` 一律指向 `tmp_path`。
"""
import pytest

from app.core import graph_snapshot, storage
from app.core.agent_tools import registry
from app.core.agent_tools.tools import kg_snapshot
from app.core.knowledge_graph import KnowledgeGraph


@pytest.fixture
def kg(tmp_path):
    """带 users 行的空图谱（`nodes.user_id` 是外键）"""
    g = KnowledgeGraph(user_id=1, data_dir=tmp_path)
    with g._conn:
        g._conn.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (1, 'snap', 'x')")
    yield g
    g.close()


def _add(kg, node_id, name, subject="数据结构", board="", mastery=0):
    kg.add_node({"id": node_id, "name": name, "subject": subject,
                 "board": board, "mastery": mastery})


# ── 模块五段式 / 注册表接线 ──────────────────────────────────────────────

def test_tool_module_is_five_section_and_free_tier():
    """`tools/README.md` 五段式 + 免确认：外部模型不该为只读工具先问学生。"""
    for attr in ("DESCRIPTION", "PARAMETERS", "GUIDANCE", "handler", "SPEC"):
        assert hasattr(kg_snapshot, attr), attr
    assert kg_snapshot.SPEC["name"] == "kg_snapshot"
    assert kg_snapshot.SPEC["tier"] == registry.TIER_FREE
    assert kg_snapshot.SPEC["handler"] is kg_snapshot.handler
    assert kg_snapshot.PARAMETERS["type"] == "object"
    assert set(kg_snapshot.PARAMETERS["properties"]) == {"subject", "limit"}
    # guidance 必须自述"免确认"（authorization block 由 tier 派生，模型靠这句话决定不问）
    assert "免确认" in kg_snapshot.GUIDANCE


def test_registered_once_after_delete_node_in_graph_group():
    names = [s["name"] for s in registry.all_specs()]
    assert names.count("kg_snapshot") == 1
    assert names.index("kg_snapshot") == names.index("delete_node") + 1


# ── 空图谱 / 坏图谱：友好中文、永不抛 ───────────────────────────────────

def test_empty_graph_returns_friendly_chinese(kg):
    text = kg_snapshot.handler({}, kg)
    assert "知识图谱还是空的" in text
    assert "rag_search" in text          # 告诉模型"那就去检索资料"
    assert "不要编造" in text
    assert isinstance(text, str) and text.strip()


def test_broken_graph_returns_text_not_exception():
    class _BrokenKg:
        def get_subjects(self):
            raise RuntimeError("数据库打不开")

    text = kg_snapshot.handler({}, _BrokenKg())
    assert text.startswith("读取知识图谱失败：")
    assert "数据库打不开" in text
    assert "rag_search" in text


def test_non_dict_args_are_tolerated(kg):
    """宿主/模型可能传 null 或字符串 —— 按默认参数处理，不是报错。"""
    assert isinstance(kg_snapshot.handler(None, kg), str)
    assert isinstance(kg_snapshot.handler("oops", kg), str)


# ── 有图：学科 / 节点 / 掌握度 / 下一步 ────────────────────────────────

def test_snapshot_shape_and_mastery_threshold(kg):
    _add(kg, "n1", "数组", board="线性结构", mastery=80)
    _add(kg, "n2", "链表", board="线性结构", mastery=49)     # 49 = 未掌握（阈值 50）
    _add(kg, "n3", "栈", mastery=50)                          # 50 = 已掌握（边界含等号）
    _add(kg, "n4", "递归", subject="算法", mastery=0)

    data = graph_snapshot.snapshot(kg)

    assert data["nodeCount"] == 4
    assert [s["name"] for s in data["subjects"]] == ["数据结构", "算法"]
    ds = data["subjects"][0]
    assert ds["nodeCount"] == 3
    assert ds["masteredCount"] == 2          # 80 与 50，49 不算
    assert data["subjects"][1]["masteredCount"] == 0
    node = {n["id"]: n for n in ds["nodes"]}
    assert node["n1"] == {"id": "n1", "title": "数组", "mastery": 80, "board": "线性结构"}
    assert set(node["n3"]) == {"id", "title", "mastery", "board"}
    assert data["dataRoot"] == str(storage.DATA_ROOT)
    # 还有未掌握节点 → 一定给"下一步"
    assert data["next"] and data["next"]["node_id"]


def test_render_text_mentions_counts_nodes_board_and_next(kg):
    _add(kg, "n1", "数组", board="线性结构", mastery=80)
    _add(kg, "n2", "链表", board="线性结构", mastery=10)

    text = kg_snapshot.handler({"subject": "数据结构"}, kg)

    assert "知识图谱快照：1 个学科 / 2 个知识点，已掌握 1 个" in text
    assert "【数据结构】2 个知识点，已掌握 1 个" in text
    assert "- 数组（掌握度 80，板块：线性结构；id: n1）" in text
    assert "- 链表（掌握度 10，板块：线性结构；id: n2）" in text
    assert "下一步推荐（全图）：" in text
    assert "理由：" in text


def test_next_is_none_when_everything_mastered(kg):
    _add(kg, "n1", "数组", mastery=100)
    _add(kg, "n2", "链表", mastery=60)
    data = graph_snapshot.snapshot(kg)
    assert data["next"] is None
    assert "下一步推荐" not in graph_snapshot.render_text(data)


def test_subject_filter_narrows_list_but_keeps_global_next(kg):
    _add(kg, "n1", "数组", mastery=80)
    _add(kg, "n2", "递归", subject="算法", mastery=0)

    data = graph_snapshot.snapshot(kg, subject="算法")
    assert [s["name"] for s in data["subjects"]] == ["算法"]
    assert data["nodeCount"] == 1
    # next 是"全图"推荐，不随 subject 收窄（面板/模型都要能给出全局下一步）
    assert data["next"] is not None


def test_unknown_subject_returns_empty_and_hint(kg):
    _add(kg, "n1", "数组", mastery=80)

    data = graph_snapshot.snapshot(kg, subject="不存在的学科")
    assert data["subjects"] == []
    assert data["nodeCount"] == 0

    text = kg_snapshot.handler({"subject": "不存在的学科"}, kg)
    assert "没有找到学科「不存在的学科」" in text
    assert "数据结构" in text          # 告诉你现有哪些学科
    assert "省略 subject" in text


def test_limit_clamps_nodes_but_counts_stay_whole(kg):
    for i in range(5):
        _add(kg, f"n{i}", f"节点{i}", mastery=0)

    data = graph_snapshot.snapshot(kg, limit=2)
    ds = data["subjects"][0]
    assert len(ds["nodes"]) == 2
    assert ds["nodeCount"] == 5
    assert data["nodeCount"] == 5

    text = kg_snapshot.handler({"limit": 2}, kg)
    assert "还有 3 个未列出" in text


def test_limit_and_defaults_are_safe(kg):
    """恶意/模型给的畸形 limit 一律夹取，不抛异常。"""
    assert graph_snapshot.clamp_limit(None) == graph_snapshot.DEFAULT_LIMIT
    assert graph_snapshot.clamp_limit("abc") == graph_snapshot.DEFAULT_LIMIT
    assert graph_snapshot.clamp_limit(0) == 1
    assert graph_snapshot.clamp_limit(-5) == 1
    assert graph_snapshot.clamp_limit("3") == 3
    assert graph_snapshot.clamp_limit(10_000) == graph_snapshot.MAX_LIMIT
