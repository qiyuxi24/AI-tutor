"""GQ-17 语义归拢 + GQ-21 多源度量：判族解析 / 降级分支必须有断言。

GQ-17 的关键风险是 LLM 输出不可靠（乱给 id、代表节点是碎片名）——所以在**执行前**
必须校验并重选，本文件把校验口径锁死；执行复用 GQ-4 的 `merge_dupes`，这里做一次
端到端（两个不同名的「栈」节点 → 归成一个）确保骨架真的能跑通。

GQ-21 的数据源（`nodes.sources` / `doc_node_marks`）是后加的：缺列/缺表时必须**跳过**
而不是崩，本文件覆盖"就绪"与"未就绪"两条路径。
"""
import json
import sqlite3

from app.core.knowledge_graph import KnowledgeGraph
from scripts.inspect_graph_quality import (
    audit,
    body_of,
    build_consolidate_prompt,
    load_doc_node_marks,
    load_graph,
    merge_dupes,
    plan_consolidate,
    subject_of,
    support_counts,
)


def _node(nid: str, name: str, user_id: int = 1, subject: str = "数据结构", **kw) -> dict:
    """模拟 SQL 行：tags 是 JSON 字符串（脚本数据源 = sqlite 行）；不打 subject 走 tags 回退。"""
    node = {"id": nid, "name": name, "user_id": user_id,
            "tags": f'["{subject}"]', "summary": "", "board": "",
            "confidence": None, "mastery": 0}
    node.update(kw)
    return node


# ════════════════════════════════════════════
#  GQ-17：prompt / 判族解析
# ════════════════════════════════════════════

def test_build_consolidate_prompt_lists_ids_and_asks_for_families():
    prompt = build_consolidate_prompt("数据结构", [_node("stack", "栈"), _node("queue", "队列")])

    assert "stack" in prompt and "栈" in prompt
    assert "queue" in prompt
    assert "families" in prompt
    assert "representative" in prompt


def test_plan_consolidate_basic(tmp_path):
    """一族的代表/成员正确落到 plan（形状与 plan_merge 一致，可直接执行）"""
    nodes = [_node("a", "栈"), _node("b", "栈的基本操作"), _node("c", "队列")]
    resp = {"families": [{"representative": "a", "members": ["a", "b"],
                          "reason": "同一概念的不同侧面"}]}

    plan = plan_consolidate(resp, nodes, "数据结构", tmp_path / "nodes")

    assert len(plan) == 1
    item = plan[0]
    assert item["keep"]["id"] == "a"
    assert [x["id"] for x in item["drop"]] == ["b"]
    assert item["user_id"] == 1 and item["subject"] == "数据结构"
    assert item["reason"] == "同一概念的不同侧面"


def test_plan_consolidate_drops_unknown_and_small_families(tmp_path):
    """清单外 id 丢弃；剔除后 <2 个有效成员的族整族丢弃"""
    nodes = [_node("a", "栈")]
    resp = {"families": [
        {"representative": "a", "members": ["a", "zzz"], "reason": "只认 a"},
        {"representative": "a", "members": ["a"], "reason": "单成员"},
        "不是字典",
    ]}

    assert plan_consolidate(resp, nodes, "数据结构", tmp_path / "nodes") == []


def test_plan_consolidate_node_in_two_families_first_wins(tmp_path):
    """一个节点只能进一个族：跨族重复时先到先得，后者有效成员不足即丢弃"""
    nodes = [_node("a", "栈"), _node("b", "栈操作"), _node("c", "栈实现")]
    resp = {"families": [
        {"representative": "a", "members": ["a", "b"], "reason": "族1"},
        {"representative": "a", "members": ["a", "c"], "reason": "族2"},
    ]}

    plan = plan_consolidate(resp, nodes, "数据结构", tmp_path / "nodes")

    assert len(plan) == 1
    assert plan[0]["keep"]["id"] == "a"
    assert [x["id"] for x in plan[0]["drop"]] == ["b"]


def test_plan_consolidate_reselects_fragment_or_invalid_representative(tmp_path):
    """代表节点是碎片名 / 不在族内 → 用「非碎片名 + 正文更长」重选"""
    nodes_dir = tmp_path / "nodes" / "1"
    nodes_dir.mkdir(parents=True)
    (nodes_dir / "a.md").write_text("短正文", encoding="utf-8")
    (nodes_dir / "b.md").write_text("长正文" * 30, encoding="utf-8")
    nodes = [_node("a", "栈（复习）"), _node("b", "栈的抽象定义")]

    resp = {"families": [{"representative": "a", "members": ["a", "b"], "reason": "x"}]}
    plan = plan_consolidate(resp, nodes, "数据结构", tmp_path / "nodes")
    assert plan[0]["keep"]["id"] == "b", "碎片名代表必须重选"

    resp2 = {"families": [{"representative": "zzz", "members": ["a", "b"], "reason": "x"}]}
    plan2 = plan_consolidate(resp2, nodes, "数据结构", tmp_path / "nodes")
    assert plan2[0]["keep"]["id"] == "b", "非法代表必须重选"


def test_consolidate_plan_applied_by_merge_dupes(tmp_path):
    """端到端：两个逐字不同名的「栈」节点 → 归成一个（正文并入 + 边重定向 + 删冗余）"""
    kg = KnowledgeGraph(user_id=1, data_dir=tmp_path)
    with kg._conn:
        kg._conn.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (1, 'w', 'x')")
    kg.create_node_with_content({"id": "stack_push_pop", "name": "栈的基本操作（压栈与弹栈）",
                                 "tags": ["数据结构"]}, "正文A", origin="book")
    kg.create_node_with_content({"id": "stack_definition_and_properties",
                                 "name": "栈的定义与LIFO特性", "tags": ["数据结构"]},
                                "正文B", origin="book")
    kg.create_node_with_content({"id": "other", "name": "队列", "tags": ["数据结构"]},
                                origin="book")
    kg.add_edge({"from": "stack_push_pop", "to": "other", "relation": "related"},
                caller="human")
    kg.add_edge({"from": "stack_definition_and_properties", "to": "other",
                 "relation": "related"}, caller="human")
    kg.close()

    db_path = tmp_path / "knowledge.db"
    nodes, _ = load_graph(db_path)
    response = {"families": [{
        "representative": "stack_definition_and_properties",
        "members": ["stack_definition_and_properties", "stack_push_pop"],
        "reason": "同为栈概念的两个侧面",
    }]}

    plan = plan_consolidate(response, nodes, "数据结构", tmp_path / "nodes")
    stats = merge_dupes(db_path, plan, action="语义归拢")

    assert stats["merged"] == 1
    assert stats["content_appended"] == 1

    after = KnowledgeGraph(user_id=1, data_dir=tmp_path)
    try:
        assert sorted(n["id"] for n in after.nodes) == ["other", "stack_definition_and_properties"]
        assert {(e["from_node"], e["to_node"]) for e in after.edges} == {
            ("stack_definition_and_properties", "other")}
        assert "正文A" in after.get_node_content_preview(
            "stack_definition_and_properties", max_chars=10_000)
    finally:
        after.close()


def test_merge_dupes_appends_content_into_empty_body_keeper(tmp_path):
    """代表节点**没有正文**（无 MD 文件）时，被并节点正文也必须并入 —— 否则随删除一起丢失。

    注意触发条件是 keep 的 `body_of()` 返回 **空串**，而不是"骨架节点"：
    骨架节点（`create_node_with_content` 无正文）的 MD 正文是占位符「待完善...」（非空），
    走的是普通追加路径；真正会丢内容的是 **无 MD 的节点**（`add_node` 建的 / 老库遗留 /
    MD 缺失）—— old 代码 `and keep_body` 在这里为假，正文就永远写不进去。
    """
    kg = KnowledgeGraph(user_id=1, data_dir=tmp_path)
    with kg._conn:
        kg._conn.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (1, 'w', 'x')")
    kg.add_node({"id": "euclid", "name": "欧几里得算法", "tags": ["数据结构"]})  # 无 MD
    kg.create_node_with_content({"id": "euclid_iter", "name": "欧几里得算法的迭代实现",
                                 "tags": ["数据结构"]}, "迭代实现的正文", origin="book")
    kg.close()

    db_path = tmp_path / "knowledge.db"
    nodes, _ = load_graph(db_path)
    plan = plan_consolidate(
        {"families": [{"representative": "euclid",
                       "members": ["euclid", "euclid_iter"], "reason": "x"}]},
        nodes, "数据结构", tmp_path / "nodes")
    assert plan[0]["keep"]["id"] == "euclid"
    # 显式断言触发条件成立：keep 正文为空串（否则本用例会静默失去覆盖、不再变红）
    assert body_of(tmp_path / "nodes", 1, "euclid") == "", "前提：代表节点正文为空串"

    stats = merge_dupes(db_path, plan, action="语义归拢")
    assert stats["content_appended"] == 1, "空正文代表也必须并入被并节点正文"

    after = KnowledgeGraph(user_id=1, data_dir=tmp_path)
    try:
        assert "迭代实现的正文" in after.get_node_content_preview("euclid", max_chars=10_000)
    finally:
        after.close()


# ════════════════════════════════════════════
#  GQ-21：多源度量（就绪 / 未就绪降级）
# ════════════════════════════════════════════

def test_support_counts_parses_json_and_tolerates_garbage():
    nodes = [{"id": "a", "sources": json.dumps([{"doc_id": 1}, {"doc_id": 2}])},
             {"id": "b", "sources": "not-json"},
             {"id": "c", "sources": None}]

    assert support_counts(nodes) == {"a": 2, "b": 0, "c": 0}
    assert support_counts([{"id": "x"}]) == {}, "缺 sources 列 → 未就绪（空 dict）"


def test_audit_multisource_degraded(tmp_path):
    """sources 列缺失 + doc_node_marks 表缺失 → 两项都标记未就绪、不崩"""
    m = audit([_node("a", "栈")], [], tmp_path / "nodes")

    assert m["support_available"] is False
    assert m["support"] == {}
    assert m["align_available"] is False
    assert m["align_rate"] is None


def _mark(user_id: int, doc_id: int, node_id: str, kind: str | None) -> dict:
    """模拟 doc_node_marks 行：kind 写进 evidence（JSON 文本），与编排层写入形态一致。"""
    evidence = json.dumps({"doc_name": "d", "subject": "数据结构", "kind": kind}) \
        if kind is not None else None
    return {"user_id": user_id, "doc_id": doc_id, "node_id": node_id, "evidence": evidence}


def test_align_rate_two_hits_one_new(tmp_path):
    """lead 指定口径：一份资料 2 hit + 1 new → 对齐率 = 2/3"""
    nodes = [_node("a", "栈"), _node("b", "队列"), _node("c", "图")]
    doc_marks = [_mark(1, 10, "a", "hit"), _mark(1, 10, "b", "hit"), _mark(1, 10, "c", "new")]

    m = audit(nodes, [], tmp_path / "nodes", doc_marks)

    assert m["align_available"] is True
    assert m["align_hit"] == 2
    assert m["align_new"] == 1
    assert m["align_rate"] == round(2 / 3, 3)
    assert m["align_docs"] == 1, "命中/新增都算同一份资料的产出"


def test_align_rate_excludes_other_users_and_unmarked(tmp_path):
    """跨用户标记不计入；没有 kind 的旧标记不猜（不计入分子分母）"""
    nodes = [_node("a", "栈", user_id=1), _node("b", "队列", user_id=1)]
    doc_marks = [
        _mark(1, 10, "a", "hit"),
        _mark(2, 20, "b", "hit"),        # 别的用户 → 排除
        _mark(1, 30, "b", None),         # 无 kind → 不猜，不计入
    ]

    m = audit(nodes, [], tmp_path / "nodes", doc_marks)

    assert m["align_hit"] == 1
    assert m["align_new"] == 0
    assert m["align_rate"] == 1.0        # 只有 1 个有效 hit（无 new）→ 1/1
    assert m["align_docs"] == 1, "只算有标记的资料份数"


def test_audit_multisource_available(tmp_path):
    """sources 就绪 → 支撑度分布；doc_node_marks 就绪 → 命中/新增与率"""
    nodes = [
        _node("a", "栈", sources=json.dumps([{"doc_id": 1}, {"doc_id": 2}])),
        _node("b", "队列", sources=json.dumps([{"doc_id": 1}])),
    ]
    doc_marks = [
        _mark(1, 1, "a", "hit"),
        _mark(1, 1, "b", "new"),
        _mark(1, 2, "a", "hit"),
    ]

    m = audit(nodes, [], tmp_path / "nodes", doc_marks)

    assert m["support_available"] is True
    assert m["support_max"] == 2
    assert m["support_multi"] == 1
    assert m["support_avg"] == 1.5
    assert m["align_available"] is True
    assert m["align_hit"] == 2
    assert m["align_new"] == 1
    assert m["align_rate"] == round(2 / 3, 3)
    assert m["align_docs"] == 2


def test_load_doc_node_marks_missing_table_returns_none(tmp_path):
    db = tmp_path / "knowledge.db"
    sqlite3.connect(str(db)).close()          # 存在但没建表
    assert load_doc_node_marks(db) is None


def test_audit_reads_real_sources_and_doc_marks(tmp_path):
    """契约贯通：用 storage-layer 的真实接口写入 → 体检的 GQ-21 指标能正确读出。

    这条锁的是"磁盘 schema ↔ 体检口径"的一致性（GQ-18 的 `nodes.sources` 列 +
    GQ-19 的 `doc_node_marks` 表），而不是我自己的合成 dict。用真实的
    `mark_doc_nodes(..., evidence={"kind": ...})` 形态（编排层就是这么写的）。
    """
    kg = KnowledgeGraph(user_id=1, data_dir=tmp_path)
    with kg._conn:
        kg._conn.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (1, 'w', 'x')")
    kg.create_node_with_content({"id": "stack", "name": "栈", "tags": ["数据结构"]},
                                "正文", origin="book")
    kg.create_node_with_content({"id": "queue", "name": "队列", "tags": ["数据结构"]},
                                "正文", origin="book")
    kg.add_sources("stack", [{"doc_id": 1}, {"doc_id": 2}])   # 栈被 2 份资料支撑
    kg.add_sources("queue", [{"doc_id": 1}])
    # 编排层形态：命中与新增**分两次**写，kind 落在 evidence
    kg.mark_doc_nodes(1, ["stack"], evidence={"doc_name": "d", "kind": "hit"})
    kg.mark_doc_nodes(1, ["queue"], evidence={"doc_name": "d", "kind": "new"})
    kg.close()

    db_path = tmp_path / "knowledge.db"
    nodes, edges = load_graph(db_path)
    marks = load_doc_node_marks(db_path)
    assert marks is not None, "doc_node_marks 表应由 KnowledgeGraph 建好"

    m = audit(nodes, edges, tmp_path / "nodes", marks)

    assert m["support_available"] is True
    assert m["support"]["stack"] == 2
    assert m["support"]["queue"] == 1
    assert m["support_max"] == 2
    assert m["support_multi"] == 1
    assert m["align_available"] is True
    assert m["align_hit"] == 1
    assert m["align_new"] == 1
    assert m["align_rate"] == 0.5, "同一份资料 1 hit + 1 new → 50%"


def test_print_report_with_multisource_available(tmp_path, capsys):
    """就绪路径也要能打印（当前磁盘库还未迁移，跑不到这条）—— 防报告里 KeyError。"""
    from scripts.inspect_graph_quality import print_report

    nodes = [_node("a", "栈", sources=json.dumps([{"doc_id": 1}])),
             _node("b", "队列", sources=json.dumps([{"doc_id": 1}]))]
    marks = [_mark(1, 1, "a", "hit"), _mark(1, 1, "b", "new")]
    m = audit(nodes, [], tmp_path / "nodes", marks)

    print_report("t", m)                       # 不抛异常
    out = capsys.readouterr().out
    assert "对齐率" in out and "50.0%" in out
    assert "支撑度" in out


def test_subject_of_prefers_subject_column_then_tags():
    assert subject_of({"subject": "数据结构", "tags": '["强化学习"]'}) == "数据结构"
    assert subject_of({"tags": '["强化学习"]'}) == "强化学习"
    assert subject_of({"subject": "  ", "tags": '["数据结构"]'}) == "数据结构"
