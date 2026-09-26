"""KG-T1：主题层级（themes / node_themes）—— schema 与 CRUD 行为断言。

全离线：不碰 LLM，全部使用 tmp_path 临时库，不污染 data/knowledge/。

覆盖实现计划的 Task 1（schema）/ Task 2（主题 CRUD）/ Task 3（归属 CRUD），
并额外锁定决策补充 D4 的语义：
- `clear_ai_themes` 必须**按学科限定**，不得跨学科误删其他课的 AI 归属；
- `node_themes` 是"路径"——清主题/归属**只删路径，不删 nodes 行、不删 MD 正文**。
"""
import sqlite3

import pytest

from app.core.knowledge_graph import KnowledgeGraph


@pytest.fixture
def kg(tmp_path):
    g = KnowledgeGraph(user_id=1, data_dir=tmp_path)
    with g._conn:
        g._conn.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (1, 't', 'x')")
    yield g
    g.close()


def _tables(g) -> set:
    return {r[0] for r in g._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}


def _indexes(g) -> set:
    return {r[0] for r in g._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'").fetchall()}


def test_theme_tables_created(kg):
    assert {"themes", "node_themes"} <= _tables(kg)


def test_theme_indexes_created(kg):
    assert {"idx_themes_user_subject",
            "idx_node_themes_theme",
            "idx_node_themes_node"} <= _indexes(kg)


def test_legacy_db_gets_theme_tables_idempotently(tmp_path):
    """老库（只有 nodes/edges）实例化后自动补两张表；重复打开幂等。"""
    data_dir = tmp_path / "kg"
    data_dir.mkdir(parents=True)
    db = sqlite3.connect(str(data_dir / "knowledge.db"))
    db.executescript("""
        CREATE TABLE nodes (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, file_path TEXT NOT NULL,
            tags TEXT DEFAULT '[]', board TEXT DEFAULT '', subject TEXT DEFAULT '',
            summary TEXT DEFAULT '', mastery INTEGER DEFAULT 0, difficulty INTEGER DEFAULT 3,
            estimated_minutes INTEGER DEFAULT 15, added_by TEXT DEFAULT 'human',
            created_at TEXT, confidence REAL, user_id INTEGER
        );
        CREATE TABLE edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_node TEXT NOT NULL, to_node TEXT NOT NULL, relation TEXT NOT NULL,
            label TEXT DEFAULT '', added_by TEXT DEFAULT 'human', confidence REAL,
            user_id INTEGER
        );
    """)
    db.commit()
    db.close()

    for _ in range(2):          # 两次打开 = 幂等
        g = KnowledgeGraph(user_id=1, data_dir=data_dir)
        try:
            assert {"themes", "node_themes"} <= _tables(g)
        finally:
            g.close()


# ── Task 2：主题 CRUD ────────────────────────────────

def _seed_tree(kg, subject="数据结构"):
    """建一棵最小主题树：省(线性结构) → 市(数组/链表)。"""
    t1 = kg.create_theme(subject, "线性结构", level=1, order_index=0)
    c1 = kg.create_theme(subject, "数组", level=2, parent_id=t1, order_index=0)
    c2 = kg.create_theme(subject, "链表", level=2, parent_id=t1, order_index=1)
    return t1, c1, c2


def test_create_theme_rejects_bad_level(kg):
    with pytest.raises(ValueError, match="层级"):
        kg.create_theme("数据结构", "三层", level=3)


def test_create_theme_requires_parent_for_level2(kg):
    with pytest.raises(ValueError, match="parent_id"):
        kg.create_theme("数据结构", "数组", level=2)


def test_create_level1_rejects_parent(kg):
    t1 = kg.create_theme("数据结构", "线性结构", level=1)
    with pytest.raises(ValueError, match="省级"):
        kg.create_theme("数据结构", "非法", level=1, parent_id=t1)


def test_create_theme_rejects_empty_name(kg):
    with pytest.raises(ValueError, match="不能为空"):
        kg.create_theme("数据结构", "   ", level=1)


def test_child_must_match_parent_subject(kg):
    t1 = kg.create_theme("数据结构", "线性结构", level=1)
    with pytest.raises(ValueError, match="同一门课"):
        kg.create_theme("算法", "数组", level=2, parent_id=t1)


def test_list_themes_filters_by_subject_and_orders(kg):
    _seed_tree(kg, "数据结构")
    kg.create_theme("操作系统", "进程管理", level=1)

    themes = kg.list_themes("数据结构")

    assert [t["name"] for t in themes] == ["线性结构", "数组", "链表"], \
        "按 level 再 order_index 排序，且只含指定课"


def test_update_theme_marks_human(kg):
    t1 = kg.create_theme("数据结构", "线性结构", level=1)

    kg.update_theme(t1, {"name": "线性表"})

    row = kg._conn.execute("SELECT * FROM themes WHERE id = ?", (t1,)).fetchone()
    assert row["name"] == "线性表"
    assert row["source"] == "human", "人工改名后必须置 human，重算才不会覆盖"


def test_update_theme_rejects_unknown_field_only(kg):
    t1 = kg.create_theme("数据结构", "线性结构", level=1)
    with pytest.raises(ValueError, match="可更新"):
        kg.update_theme(t1, {"level": 2})


def test_delete_theme_cascades_children(kg):
    t1, c1, c2 = _seed_tree(kg)

    kg.delete_theme(t1)

    assert kg.list_themes("数据结构") == [], "删父主题级联删子主题"


def test_themes_are_isolated_per_user(tmp_path):
    g1 = KnowledgeGraph(user_id=1, data_dir=tmp_path)
    with g1._conn:
        g1._conn.execute("INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (1,'u1','x')")
    g1.create_theme("数据结构", "线性结构", level=1)
    g1.close()

    g2 = KnowledgeGraph(user_id=2, data_dir=tmp_path)
    with g2._conn:
        g2._conn.execute("INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (2,'u2','x')")
    try:
        assert g2.list_themes("数据结构") == [], "不得读到其他用户的主题"
    finally:
        g2.close()


# ── Task 3：归属 CRUD ────────────────────────────────

def _seed_nodes(kg):
    kg.add_node({"id": "a", "name": "递归", "tags": ["数据结构"]})
    kg.add_node({"id": "b", "name": "动态规划", "tags": ["数据结构"]})


def test_set_node_themes_multi_membership(kg):
    """一个节点可属多个主题（通用性的核心断言）。"""
    _seed_nodes(kg)
    t1 = kg.create_theme("数据结构", "函数", level=1)
    t2 = kg.create_theme("数据结构", "算法思想", level=1)

    kg.set_node_themes("a", [
        {"theme_id": t1, "weight": 1.0, "is_primary": True},
        {"theme_id": t2, "weight": 0.6},
    ])

    assert {t["theme_id"] for t in kg.get_node_themes("a")} == {t1, t2}


def test_only_one_primary_per_node(kg):
    _seed_nodes(kg)
    t1 = kg.create_theme("数据结构", "函数", level=1)
    t2 = kg.create_theme("数据结构", "算法思想", level=1)

    kg.set_node_themes("a", [
        {"theme_id": t1, "weight": 0.5, "is_primary": True},
        {"theme_id": t2, "weight": 0.9, "is_primary": True},
    ])

    primaries = [t for t in kg.get_node_themes("a") if t["is_primary"]]
    assert len(primaries) == 1, "每节点至多一个主归属"


def test_set_node_themes_overwrites_previous_ai_assignment(kg):
    _seed_nodes(kg)
    t1 = kg.create_theme("数据结构", "函数", level=1)
    t2 = kg.create_theme("数据结构", "算法思想", level=1)

    kg.set_node_themes("a", [{"theme_id": t1, "is_primary": True}])
    kg.set_node_themes("a", [{"theme_id": t2, "is_primary": True}])

    assert [t["theme_id"] for t in kg.get_node_themes("a")] == [t2], \
        "覆盖式写入：旧的 ai 归属被替换"


def test_human_assignment_survives_rebuild(kg):
    """人工设定的归属不被 AI 重算覆盖 —— 稳定性的关键。"""
    _seed_nodes(kg)
    t1 = kg.create_theme("数据结构", "函数", level=1)
    t2 = kg.create_theme("数据结构", "算法思想", level=1)

    kg.set_node_themes("a", [{"theme_id": t1, "is_primary": True}], source="human")
    kg.set_node_themes("a", [{"theme_id": t2, "is_primary": True}], source="ai")

    ids = {t["theme_id"] for t in kg.get_node_themes("a")}
    assert t1 in ids, "human 归属不得被重算删掉"
    assert t2 in ids, "新 ai 归属正常写入"


def test_human_primary_not_overridden_by_ai_primary(kg):
    """human 已是主归属时，AI 再写主归属不得产生第二个 is_primary（AC-T3）。"""
    _seed_nodes(kg)
    t1 = kg.create_theme("数据结构", "函数", level=1)
    t2 = kg.create_theme("数据结构", "算法思想", level=1)

    kg.set_node_themes("a", [{"theme_id": t1, "is_primary": True}], source="human")
    kg.set_node_themes("a", [{"theme_id": t2, "weight": 9.0, "is_primary": True}], source="ai")

    primaries = [t for t in kg.get_node_themes("a") if t["is_primary"]]
    assert len(primaries) == 1, "至多一个主归属"
    assert primaries[0]["theme_id"] == t1, "尊重人工主归属"


def test_set_node_themes_drops_foreign_theme_ids(kg):
    """聚类输出可能含脏数据（他人主题/不存在的 id）→ 静默丢弃，不报错。"""
    _seed_nodes(kg)
    kg.set_node_themes("a", [{"theme_id": "th_not_exist", "is_primary": True}])

    assert kg.get_node_themes("a") == []


def test_unknown_node_raises(kg):
    with pytest.raises(ValueError, match="节点不存在"):
        kg.set_node_themes("nope", [{"theme_id": "th_x"}])


def test_get_theme_nodes_includes_descendants(kg):
    _seed_nodes(kg)
    t1, c1, c2 = _seed_tree(kg)

    kg.set_node_themes("a", [{"theme_id": c1, "is_primary": True}])
    kg.set_node_themes("b", [{"theme_id": c2, "is_primary": True}])

    assert set(kg.get_theme_nodes(t1)) == {"a", "b"}, "父主题含全部后代节点"
    assert kg.get_theme_nodes(c1) == ["a"]
    assert kg.get_theme_nodes(t1, include_descendants=False) == []


def test_get_primary_theme_map_batch(kg):
    """批量主归属：只回 is_primary、按课过滤、无归属节点不出现（KG-T4 数据源）。"""
    _seed_nodes(kg)                       # a=递归 / b=动态规划（数据结构）
    t1 = kg.create_theme("数据结构", "线性结构", level=1)
    c1 = kg.create_theme("数据结构", "数组", level=2, parent_id=t1)
    t_os = kg.create_theme("操作系统", "进程管理", level=1)

    kg.set_node_themes("a", [
        {"theme_id": c1, "is_primary": True},
        {"theme_id": t1, "weight": 0.4},          # 非主归属 → 不进 map
    ])
    kg.set_node_themes("b", [{"theme_id": t_os, "is_primary": True}])

    assert kg.get_primary_theme_map("数据结构") == {"a": c1}, \
        "只回主归属，且按主题所属学科过滤"
    assert kg.get_primary_theme_map() == {"a": c1, "b": t_os}, "不传学科 = 全量"


def test_clear_ai_themes_keeps_human(kg):
    _seed_nodes(kg)
    t_human = kg.create_theme("数据结构", "人工主题", level=1, source="human")
    kg.create_theme("数据结构", "AI主题", level=1)
    kg.set_node_themes("a", [{"theme_id": t_human, "is_primary": True}], source="human")

    kg.clear_ai_themes("数据结构")

    names = [t["name"] for t in kg.list_themes("数据结构")]
    assert names == ["人工主题"], "只清 AI 主题，human 主题保留"
    assert [t["theme_id"] for t in kg.get_node_themes("a")] == [t_human]


def test_clear_ai_themes_keeps_nodes_and_md_content(kg):
    """D4：`node_themes` 是"路径"——清主题/归属只删路径，不删节点行、不删 MD 正文。"""
    kg.create_node_with_content(
        {"id": "a", "name": "递归", "subject": "数据结构"}, "## 递归\n\n递归是把大问题拆成同类子问题。")
    md = kg.nodes_dir / "a.md"
    assert md.exists(), "前置：节点 MD 已落盘"
    before = md.read_text(encoding="utf-8")

    t1 = kg.create_theme("数据结构", "函数", level=1)
    kg.set_node_themes("a", [{"theme_id": t1, "is_primary": True}])

    kg.clear_ai_themes("数据结构")

    assert kg.get_node("a") is not None, "节点行必须仍在"
    assert md.exists() and md.read_text(encoding="utf-8") == before, "MD 正文必须一字不动"
    assert kg.get_node_themes("a") == [], "归属（路径）被清"


def test_clear_ai_themes_does_not_touch_other_subject(kg):
    """D4：清本课的 AI 归属时，不得跨学科误删其他课的 AI 归属与主题。"""
    kg.add_node({"id": "a", "name": "递归", "subject": "数据结构"})
    kg.add_node({"id": "b", "name": "进程", "subject": "操作系统"})
    t_ds = kg.create_theme("数据结构", "函数", level=1)
    t_os = kg.create_theme("操作系统", "进程管理", level=1)
    kg.set_node_themes("a", [{"theme_id": t_ds, "is_primary": True}])
    kg.set_node_themes("b", [{"theme_id": t_os, "is_primary": True}])

    kg.clear_ai_themes("数据结构")

    assert kg.get_node_themes("a") == [], "本课的 AI 归属被清"
    assert [t["theme_id"] for t in kg.get_node_themes("b")] == [t_os], \
        "其他课的 AI 归属必须活下来（跨学科不误删）"
    assert [t["name"] for t in kg.list_themes("操作系统")] == ["进程管理"], \
        "其他课的主题必须活下来"


def test_node_themes_cascade_with_node_delete(kg):
    _seed_nodes(kg)
    t1 = kg.create_theme("数据结构", "函数", level=1)
    kg.set_node_themes("a", [{"theme_id": t1, "is_primary": True}])

    kg.remove_node("a")

    assert kg._conn.execute(
        "SELECT COUNT(*) FROM node_themes WHERE node_id = 'a'").fetchone()[0] == 0


# ── Task 4：聚类模块（mock LLM，全离线）─────────────

import asyncio
import json

from app.core import kg_themes


def _seed_course(kg, n=4):
    for i in range(n):
        kg.add_node({"id": f"n{i}", "name": f"知识点{i}", "tags": ["数据结构"],
                     "subject": "数据结构"})


def _mock_llm(payload):
    async def _fake(system_prompt, messages, **kwargs):
        return json.dumps(payload, ensure_ascii=False)
    return _fake


def test_generate_writes_two_level_tree(kg, monkeypatch):
    _seed_course(kg)
    payload = {"themes": [
        {"name": "线性结构", "children": [
            {"name": "数组", "nodes": ["n0", "n1"]},
            {"name": "链表", "nodes": ["n2"]},
        ]},
        {"name": "树结构", "children": [
            {"name": "二叉树", "nodes": ["n3"]},
        ]},
    ]}
    monkeypatch.setattr(kg_themes, "call_llm", _mock_llm(payload))

    result = asyncio.run(kg_themes.generate_subject_themes(kg, "数据结构"))

    assert result["status"] == "ok"
    themes = kg.list_themes("数据结构")
    # list_themes 按 level → order_index 排序（Task 2 契约）：两级全在前、市级在后
    assert [t["level"] for t in themes] == [1, 1, 2, 2, 2]
    assert {t["name"] for t in themes if t["level"] == 1} == {"线性结构", "树结构"}
    assert {t["name"] for t in themes if t["level"] == 2} == {"数组", "链表", "二叉树"}
    assert result["assigned"] == 4
    assert result["unassigned"] == 0


def test_generate_records_multi_membership(kg, monkeypatch):
    """同一知识点出现在两个主题下 → 多归属落库；且首次出现者为主归属。"""
    _seed_course(kg)
    payload = {"themes": [
        {"name": "函数", "children": [{"name": "递归", "nodes": ["n0"]}]},
        {"name": "算法思想", "children": [{"name": "分治", "nodes": ["n0"]}]},
    ]}
    monkeypatch.setattr(kg_themes, "call_llm", _mock_llm(payload))

    asyncio.run(kg_themes.generate_subject_themes(kg, "数据结构"))

    assigns = kg.get_node_themes("n0")
    assert len(assigns) == 2
    primaries = [a for a in assigns if a["is_primary"]]
    assert len(primaries) == 1
    assert primaries[0]["theme_name"] == "递归", "首次出现的主题 = 主归属"


def test_generate_rejects_too_broad_theme_name(kg, monkeypatch):
    """C3 守卫：比课更泛 / 与课同名的主题被丢弃。"""
    _seed_course(kg)
    payload = {"themes": [
        {"name": "数据结构", "children": [{"name": "数组", "nodes": ["n0"]}]},
        {"name": "计算机基础", "children": [{"name": "二进制", "nodes": ["n1"]}]},
        {"name": "线性结构", "children": [{"name": "链表", "nodes": ["n2"]}]},
    ]}
    monkeypatch.setattr(kg_themes, "call_llm", _mock_llm(payload))

    asyncio.run(kg_themes.generate_subject_themes(kg, "数据结构"))

    names = {t["name"] for t in kg.list_themes("数据结构")}
    assert "数据结构" not in names, "与课同名 → 拒绝"
    assert "计算机基础" not in names, "比课更泛 → 拒绝"
    assert "线性结构" in names


def test_generate_preserves_human_theme(kg, monkeypatch):
    """重算不覆盖人工主题与人工归属（稳定性纪律）。"""
    _seed_course(kg)
    t_human = kg.create_theme("数据结构", "人工主题", level=1, source="human")
    kg.set_node_themes("n0", [{"theme_id": t_human, "is_primary": True}], source="human")
    monkeypatch.setattr(kg_themes, "call_llm",
                        _mock_llm({"themes": [
                            {"name": "线性结构", "children": [{"name": "数组", "nodes": ["n1"]}]}]}))

    asyncio.run(kg_themes.generate_subject_themes(kg, "数据结构"))

    names = {t["name"] for t in kg.list_themes("数据结构")}
    assert "人工主题" in names and "线性结构" in names
    assert [a["theme_id"] for a in kg.get_node_themes("n0")] == [t_human]


def test_generate_reports_unassigned(kg, monkeypatch):
    _seed_course(kg)
    monkeypatch.setattr(kg_themes, "call_llm",
                        _mock_llm({"themes": [
                            {"name": "线性结构", "children": [{"name": "数组", "nodes": ["n0"]}]}]}))

    result = asyncio.run(kg_themes.generate_subject_themes(kg, "数据结构"))

    assert result["assigned"] == 1
    assert result["unassigned"] == 3


def test_generate_empty_course_is_noop(kg):
    assert asyncio.run(kg_themes.generate_subject_themes(kg, "空课"))["status"] == "empty"


def test_generate_llm_failure_never_raises(kg, monkeypatch):
    _seed_course(kg)

    async def _boom(*args, **kwargs):
        raise RuntimeError("LLM 挂")

    monkeypatch.setattr(kg_themes, "call_llm", _boom)

    result = asyncio.run(kg_themes.generate_subject_themes(kg, "数据结构"))

    assert result["status"] == "failed", "聚类失败不得抛异常（不能毁掉建图）"
    assert kg.list_themes("数据结构") == [], "失败时库里不得留半截主题"


def test_prompt_d3_includes_existing_theme_tree_only_when_present(kg, monkeypatch):
    """D3：重算前把该课现有主题树喂进 prompt 并要求"沿用"；首次（无主题）不含该小节。"""
    _seed_course(kg)
    prompts: list[str] = []

    def _capture(payload):
        async def _fake(system_prompt, messages, **kwargs):
            prompts.append(messages[0]["content"])
            return json.dumps(payload, ensure_ascii=False)
        return _fake

    # 第一次：库里没有主题 → prompt 不含「沿用」小节
    monkeypatch.setattr(kg_themes, "call_llm", _capture(
        {"themes": [{"name": "线性结构", "children": [{"name": "数组", "nodes": ["n0"]}]}]}))
    asyncio.run(kg_themes.generate_subject_themes(kg, "数据结构"))
    assert "尽量沿用" not in prompts[0], "无既有主题时「沿用」小节不得出现"

    # 造一棵既有主题（模拟上一轮聚类的产物）
    t1 = kg.create_theme("数据结构", "树结构", level=1)
    kg.create_theme("数据结构", "二叉树", level=2, parent_id=t1)

    # 第二次：prompt 含既有主题名，且含「沿用」那句话
    monkeypatch.setattr(kg_themes, "call_llm", _capture(
        {"themes": [{"name": "树结构", "children": [{"name": "二叉树", "nodes": ["n1"]}]}]}))
    asyncio.run(kg_themes.generate_subject_themes(kg, "数据结构"))
    assert "树结构" in prompts[1] and "二叉树" in prompts[1], "既有主题应渲染进 prompt"
    assert "尽量沿用" in prompts[1], "应要求 LLM 沿用既有命名与结构"


def test_prompt_requires_every_node_assigned(kg, monkeypatch):
    """防回归：prompt 必须显式要求「每个知识点都要分配」，并把容量写成参考值而非上限。

    真机证据（2026-09-26）：仅 128/302 节点入树，根因是「每个二级主题应能容纳约 10 个
    知识点」被读成「只放 10 个」→ 模型主动停止分配。此处只断言 prompt 文本，不测 LLM 行为。
    """
    _seed_course(kg)
    prompts: list[str] = []

    def _capture(payload):
        async def _fake(system_prompt, messages, **kwargs):
            prompts.append(messages[0]["content"])
            return json.dumps(payload, ensure_ascii=False)
        return _fake

    monkeypatch.setattr(kg_themes, "call_llm", _capture(
        {"themes": [{"name": "线性结构", "children": [{"name": "数组", "nodes": ["n0"]}]}]}))
    asyncio.run(kg_themes.generate_subject_themes(kg, "数据结构"))

    assert "每一个知识点" in prompts[0], "prompt 必须显式要求把每个知识点都分配（防措辞歧义回归）"
    assert "不是上限" in prompts[0], "容量表述必须注明是参考值、不是上限"


def test_prompt_enforces_tree_structure_discipline(kg, monkeypatch):
    """防回归：真机暴露的三种树形缺陷必须各有一条 prompt 纪律对应。

    真机证据（2026-09-26，uid5 第 2 轮，覆盖 300/302）：① 「最优行为价值的计算(48)」
    错挂到「多臂老虎机与探索利用」下；② 同一课内近义 L2 并存（「策略评估与求解」/
    「策略评估与迭代求解」）；③ L2 大小 3~57 极不均；④ 出现「课程综合与未识别片段」
    这类兜底命名。D3 会沿用既有命名 → 不修 prompt 就会把这棵歪树继承下去。
    此处只断言 prompt 文本，不测 LLM 行为。
    """
    _seed_course(kg)
    prompts: list[str] = []

    def _capture(payload):
        async def _fake(system_prompt, messages, **kwargs):
            prompts.append(messages[0]["content"])
            return json.dumps(payload, ensure_ascii=False)
        return _fake

    monkeypatch.setattr(kg_themes, "call_llm", _capture(
        {"themes": [{"name": "线性结构", "children": [{"name": "数组", "nodes": ["n0"]}]}]}))
    asyncio.run(kg_themes.generate_subject_themes(kg, "数据结构"))

    assert "语义一致" in prompts[0], "须禁止把子主题挂到语义无关的一级主题下"
    assert "重复的二级主题" in prompts[0], "须禁止同一课内近义 L2 并存"
    assert "均衡" in prompts[0], "须要求 L2 大小大致均衡（过大则拆同级兄弟）"
    assert "兜底命名" in prompts[0], "须禁止「…综合/其他/未识别片段」这类兜底主题名"


# ── Task 5：API 契约（直接测路由函数，不起 HTTP 服务）──

def test_router_exposes_theme_endpoints():
    """新端点已注册（路径与谓词）。"""
    from app.api.v1.knowledge import router
    paths = {(r.path, tuple(sorted(r.methods))) for r in router.routes}
    assert ("/knowledge/themes", ("GET",)) in paths
    assert ("/knowledge/themes/rebuild", ("POST",)) in paths


def test_node_detail_payload_includes_themes(kg):
    """节点详情的数据源（kg.get_node_themes）可用且带主题名 —— 端点只是转发。"""
    _seed_nodes(kg)
    t1 = kg.create_theme("数据结构", "函数", level=1)
    kg.set_node_themes("a", [{"theme_id": t1, "is_primary": True}])

    themes = kg.get_node_themes("a")

    assert themes and themes[0]["theme_name"] == "函数"


# ── P0：改 subject → 清理 AI 主题归属（human 保留，§6.2 稳定性纪律）──
# 缺陷：节点换课后，它的 node_themes 仍指向旧课的主题（旧树里还在、新树里永远没有）。
# 唯一真实触发路径 = knowledge_writer.create_node_from_ai 的更新分支，但它最终落到
# 本层 update_node_info —— 在这一层收口即可覆盖所有调用方。

def _seed_dual_theme(kg):
    """节点 a 属『数据结构』，带一条 ai 归属 + 一条 human 归属。"""
    kg.add_node({"id": "a", "name": "递归", "subject": "数据结构"})
    t_ai = kg.create_theme("数据结构", "函数", level=1)
    t_human = kg.create_theme("数据结构", "人工主题", level=1, source="human")
    kg.set_node_themes("a", [{"theme_id": t_human, "is_primary": True}], source="human")
    kg.set_node_themes("a", [{"theme_id": t_ai, "is_primary": True}], source="ai")
    return t_ai, t_human


def test_change_subject_clears_ai_themes_keeps_human(kg):
    t_ai, t_human = _seed_dual_theme(kg)

    kg.update_node_info("a", {"subject": "操作系统"})

    remaining = {t["theme_id"] for t in kg.get_node_themes("a")}
    assert t_ai not in remaining, "换课后旧课的 AI 归属必须清掉（旧树里还在、新树里永没有）"
    assert t_human in remaining, "human 归属（§6.2）不得被清理"
    assert kg.get_node("a")["subject"] == "操作系统", "subject 列已更新"


def test_same_subject_does_not_touch_themes(kg):
    t_ai, t_human = _seed_dual_theme(kg)

    kg.update_node_info("a", {"subject": "数据结构"})   # 传相同值

    assert {t["theme_id"] for t in kg.get_node_themes("a")} == {t_ai, t_human}, \
        "学科没变（同值）→ 归属一字不动"


def test_empty_subject_treated_as_unchanged(kg):
    t_ai, t_human = _seed_dual_theme(kg)

    kg.update_node_info("a", {"subject": "   "})        # 空值

    assert {t["theme_id"] for t in kg.get_node_themes("a")} == {t_ai, t_human}, \
        "空 subject 视为未变化，不误清归属"


# ── P1：导出带上主归属主题名（只做加法，不改既有字段/结构）──

def test_export_includes_primary_theme_name(kg, monkeypatch):
    import app.api.v1.knowledge as kapi

    kg.add_node({"id": "a", "name": "递归", "subject": "数据结构"})
    t1 = kg.create_theme("数据结构", "函数", level=1)
    kg.set_node_themes("a", [{"theme_id": t1, "is_primary": True}])

    monkeypatch.setattr(kapi, "KnowledgeGraph", lambda user_id: kg)

    async def _call():
        resp = await kapi.export_knowledge(subject="数据结构", user_id=1)
        return b"".join([c async for c in resp.body_iterator]).decode("utf-8")

    text = asyncio.run(_call())

    assert "### 递归" in text, "既有节点行结构保留"
    assert "函数" in text, "主归属主题名应出现在导出文本里"


# ── 合并去重支撑：唯一主归属归一化 + 归属/别名搬移 ──────────────
# 这三个公开方法原先是散在 scripts/inspect_graph_quality.py 里的裸 SQL，规则与
# set_node_themes 各写一份（必漂）。现收敛进 KnowledgeGraph，脚本只调用它们 ——
# 这里锁定语义，防止将来又出现第二份实现。

def _raw_assign(kg, node_id, theme_id, weight, is_primary, source="ai"):
    """直接写一行 node_themes，绕过 set_node_themes 的归一化，造"脏"状态给 renormalize 用。"""
    with kg._conn:
        kg._conn.execute(
            "INSERT OR REPLACE INTO node_themes"
            " (node_id, theme_id, user_id, weight, is_primary, source, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (node_id, theme_id, kg.user_id, weight, int(is_primary), source,
             "2020-01-01T00:00:00"))


def _primaries(kg, node_id):
    return [t for t in kg.get_node_themes(node_id) if t["is_primary"]]


def test_renormalize_primary_prefers_human(kg):
    """human 主归属优先保留，即使 AI 归属 weight 更高。"""
    _seed_nodes(kg)
    t_human = kg.create_theme("数据结构", "人工主题", level=1, source="human")
    t_ai = kg.create_theme("数据结构", "AI主题", level=1)
    _raw_assign(kg, "a", t_human, 1.0, True, source="human")
    _raw_assign(kg, "a", t_ai, 9.0, True, source="ai")

    kg.renormalize_primary("a")

    ps = _primaries(kg, "a")
    assert len(ps) == 1, "至多一个主归属（AC-T3）"
    assert ps[0]["theme_id"] == t_human, "human 优先，不被高权重 AI 归属取代"


def test_renormalize_primary_picks_max_weight_when_no_human(kg):
    _seed_nodes(kg)
    t_low = kg.create_theme("数据结构", "低权重", level=1)
    t_high = kg.create_theme("数据结构", "高权重", level=1)
    _raw_assign(kg, "a", t_low, 0.5, True)
    _raw_assign(kg, "a", t_high, 0.9, True)

    kg.renormalize_primary("a")

    ps = _primaries(kg, "a")
    assert len(ps) == 1
    assert ps[0]["theme_id"] == t_high, "无 human → 取 weight 最大者"


def test_renormalize_primary_tie_breaks_by_theme_id(kg):
    """并列时按 theme_id 稳定选择（保证可复现）。"""
    _seed_nodes(kg)
    t1 = kg.create_theme("数据结构", "主题A", level=1)
    t2 = kg.create_theme("数据结构", "主题B", level=1)
    low, high = sorted([t1, t2])
    _raw_assign(kg, "a", high, 1.0, True)
    _raw_assign(kg, "a", low, 1.0, True)

    kg.renormalize_primary("a")

    ps = _primaries(kg, "a")
    assert len(ps) == 1
    assert ps[0]["theme_id"] == low, "并列 → theme_id 升序"


def test_renormalize_primary_no_themes_is_noop(kg):
    """无归属 / 节点不存在时为空操作，不抛异常（合并脚本可能对任意节点调用）。"""
    _seed_nodes(kg)
    kg.renormalize_primary("a")       # 节点存在但无归属
    kg.renormalize_primary("nope")    # 节点不存在
    assert kg.get_node_themes("a") == []


def test_reassign_node_themes_moves_all_and_keeps_one_primary(kg):
    _seed_nodes(kg)
    t1 = kg.create_theme("数据结构", "函数", level=1)
    t2 = kg.create_theme("数据结构", "算法思想", level=1)
    kg.set_node_themes("a", [{"theme_id": t1, "weight": 1.0, "is_primary": True},
                             {"theme_id": t2, "weight": 0.6}])

    result = kg.reassign_node_themes("a", "b")

    assert result == {"moved": 2, "deduped": 0}
    assert {t["theme_id"] for t in kg.get_node_themes("b")} == {t1, t2}, "归属全部搬到目标"
    assert len(_primaries(kg, "b")) == 1, "搬完目标主归属仍唯一"


def test_reassign_node_themes_dedupes_and_takes_max_weight(kg):
    _seed_nodes(kg)
    t1 = kg.create_theme("数据结构", "函数", level=1)
    kg.set_node_themes("a", [{"theme_id": t1, "weight": 0.3}])
    kg.set_node_themes("b", [{"theme_id": t1, "weight": 0.9}])

    result = kg.reassign_node_themes("a", "b")

    assert result == {"moved": 0, "deduped": 1}, "同主题已在目标 → 计入去重、不新增行"
    weights = [t["weight"] for t in kg.get_node_themes("b") if t["theme_id"] == t1]
    assert weights == [0.9], "weight 取两者较大值"


def test_reassign_node_themes_upgrades_weight_when_source_higher(kg):
    _seed_nodes(kg)
    t1 = kg.create_theme("数据结构", "函数", level=1)
    kg.set_node_themes("a", [{"theme_id": t1, "weight": 0.9}])
    kg.set_node_themes("b", [{"theme_id": t1, "weight": 0.3}])

    kg.reassign_node_themes("a", "b")

    weights = [t["weight"] for t in kg.get_node_themes("b") if t["theme_id"] == t1]
    assert weights == [0.9], "源更高 → 抬到源权重"


def test_reassign_node_themes_is_idempotent(kg):
    _seed_nodes(kg)
    t1 = kg.create_theme("数据结构", "函数", level=1)
    kg.set_node_themes("a", [{"theme_id": t1, "weight": 1.0, "is_primary": True}])

    first = kg.reassign_node_themes("a", "b")
    second = kg.reassign_node_themes("a", "b")

    assert first == {"moved": 1, "deduped": 0}
    assert second == {"moved": 0, "deduped": 1}, "再搬一次不新增行（幂等）"
    assert len([t for t in kg.get_node_themes("b") if t["theme_id"] == t1]) == 1


def test_reassign_node_themes_rejects_unknown_node(kg):
    _seed_nodes(kg)
    t1 = kg.create_theme("数据结构", "函数", level=1)
    kg.set_node_themes("a", [{"theme_id": t1, "is_primary": True}])

    with pytest.raises(ValueError):
        kg.reassign_node_themes("a", "nope")
    with pytest.raises(ValueError):
        kg.reassign_node_themes("nope", "a")
    with pytest.raises(ValueError, match="自身"):
        kg.reassign_node_themes("a", "a")


def test_reassign_node_themes_isolated_per_user(tmp_path):
    """他人节点视同不存在 → ValueError（用户隔离）。"""
    g1 = KnowledgeGraph(user_id=1, data_dir=tmp_path)
    g2 = KnowledgeGraph(user_id=2, data_dir=tmp_path)
    try:
        for g, uid in ((g1, 1), (g2, 2)):
            with g._conn:
                g._conn.execute(
                    "INSERT OR IGNORE INTO users (id, username, password_hash)"
                    " VALUES (?, ?, 'x')", (uid, f"u{uid}"))
        g1.add_node({"id": "a", "name": "递归", "tags": ["数据结构"]})
        g2.add_node({"id": "z", "name": "别人的", "tags": ["数据结构"]})

        with pytest.raises(ValueError):
            g1.reassign_node_themes("z", "a")   # z 属 user 2
    finally:
        g1.close()
        g2.close()


def test_reassign_node_aliases_redirects_keys(kg):
    _seed_nodes(kg)
    kg.register_alias("排队别名", "a", source="ai")

    moved = kg.reassign_node_aliases("a", "b")

    assert moved == 1
    keys = {r["alias_key"] for r in kg._conn.execute(
        "SELECT alias_key FROM node_aliases WHERE node_id = ? AND user_id = ?",
        ("b", kg.user_id)).fetchall()}
    assert "排队别名" in keys, "别名改指保留者"
    assert kg._conn.execute(
        "SELECT COUNT(*) FROM node_aliases WHERE node_id = 'a'").fetchone()[0] == 0


def test_reassign_node_aliases_rejects_unknown(kg):
    _seed_nodes(kg)
    with pytest.raises(ValueError):
        kg.reassign_node_aliases("a", "nope")
    with pytest.raises(ValueError, match="自身"):
        kg.reassign_node_aliases("a", "a")
