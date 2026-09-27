"""学科 / 板块的重命名与解散：`rename_subject` / `rename_board` / `remove_board`。

三条语义（本测试逐条锁定）：
- **学科改名口径与读取侧同源**：改的是 `get_nodes_by_subject` 认得的那批（subject 列 +
  tags 回退），且 tags 里的旧学科名一并替换 —— 否则 `subject_from_tags` 仍推导出旧名，
  迁移回填会把老节点写回旧课（同一门课裂成两个）。主题树的 `subject` 同步改。
- **板块是 `nodes.board` 上的分组标签**（无独立表）：改名只动本学科该板块的节点；
  `remove_board` 是**解散**（board 置空、知识点保留），不删节点。
- **按学科隔离**：别学科的同名板块一律不动。
- 全离线：不碰 LLM，tmp_path 临时库，不污染 `data/knowledge/`。
"""
import pytest

from app.core.knowledge_graph import KnowledgeGraph


@pytest.fixture
def kg(tmp_path):
    g = KnowledgeGraph(user_id=1, data_dir=tmp_path)
    with g._conn:  # nodes.user_id 是外键，先备 users 行
        g._conn.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (1, 't', 'x')")
    yield g
    g.close()


def _seed(kg):
    """数据结构：线性表(d1,d2) + 树结构(d3)；操作系统：同名板块「线性表」(o1)。"""
    kg.create_node_with_content(
        {"id": "d1", "name": "数组", "tags": ["数据结构", "线性表"],
         "subject": "数据结构", "board": "线性表"}, "正文")
    kg.create_node_with_content(
        {"id": "d2", "name": "链表", "tags": ["数据结构"],
         "subject": "数据结构", "board": "线性表"}, "正文")
    kg.create_node_with_content(
        {"id": "d3", "name": "二叉树", "tags": ["数据结构"],
         "subject": "数据结构", "board": "树结构"}, "正文")
    kg.create_node_with_content(
        {"id": "o1", "name": "进程调度", "tags": ["操作系统", "线性表"],
         "subject": "操作系统", "board": "线性表"}, "正文")


def _ids(nodes) -> set[str]:
    """节点 id 集合。用集合而非列表：`kg.nodes` 的 SELECT 没有 ORDER BY，
    UPDATE 之后行序会变，断言顺序 = 定时炸弹。"""
    return {n["id"] for n in nodes}


# ── 学科改名 ──────────────────────────────────────────

def test_rename_subject_moves_nodes_and_tags(kg):
    _seed(kg)

    r = kg.rename_subject("数据结构", "数据结构与算法")

    assert r["renamed_nodes"] == 3
    assert _ids(kg.get_nodes_by_subject("数据结构与算法")) == {"d1", "d2", "d3"}
    assert kg.get_nodes_by_subject("数据结构") == []
    assert kg.get_node("d1")["tags"] == ["数据结构与算法", "线性表"], \
        "tags 里的旧学科名必须一起换，否则 tags 回退仍指向旧课"


def test_rename_subject_includes_legacy_tag_only_nodes(kg):
    """subject 列为空、只靠 tags 认课的老节点也要跟着改名（与读取侧同一口径）。"""
    _seed(kg)
    with kg._conn:
        kg._conn.execute("UPDATE nodes SET subject = '' WHERE id = 'd3'")
    kg._invalidate_cache()
    assert _ids(kg.get_nodes_by_subject("数据结构")) == {"d1", "d2", "d3"}

    kg.rename_subject("数据结构", "数据结构与算法")

    assert kg.get_node("d3")["subject"] == "数据结构与算法"


def test_rename_subject_leaves_other_subject_untouched(kg):
    _seed(kg)

    kg.rename_subject("数据结构", "数据结构与算法")

    assert _ids(kg.get_nodes_by_subject("操作系统")) == {"o1"}
    assert kg.get_node("o1")["tags"] == ["操作系统", "线性表"]


def test_rename_subject_keeps_board_and_mastery(kg):
    """改名只改"课名"，板块归属与学习进度不能丢。"""
    _seed(kg)
    kg.update_node_info("d1", {"mastery": 60})

    kg.rename_subject("数据结构", "数据结构与算法")

    d1 = kg.get_node("d1")
    assert d1["board"] == "线性表"
    assert d1["mastery"] == 60


def test_rename_subject_rejects_taken_name(kg):
    """改到已存在的学科 = 静默合并两门课（边/主题/掌握度混在一起）→ 必须拒绝。"""
    _seed(kg)
    with pytest.raises(ValueError, match="已存在"):
        kg.rename_subject("数据结构", "操作系统")


def test_rename_subject_rejects_blank_and_same(kg):
    _seed(kg)
    with pytest.raises(ValueError):
        kg.rename_subject("数据结构", "   ")
    with pytest.raises(ValueError):
        kg.rename_subject("   ", "新学科")
    with pytest.raises(ValueError, match="相同"):
        kg.rename_subject("数据结构", "数据结构")


# ── 板块改名 ──────────────────────────────────────────

def test_rename_board_only_touches_that_subject(kg):
    _seed(kg)

    r = kg.rename_board("数据结构", "线性表", "线性结构")

    assert r["renamed_nodes"] == 2
    assert _ids(kg.get_nodes_by_board("数据结构", "线性结构")) == {"d1", "d2"}
    assert kg.get_nodes_by_board("数据结构", "线性表") == []
    assert _ids(kg.get_nodes_by_board("操作系统", "线性表")) == {"o1"}, \
        "别课的同名板块不能动"


def test_rename_board_rejects_taken_and_missing(kg):
    _seed(kg)
    with pytest.raises(ValueError, match="已存在"):
        kg.rename_board("数据结构", "线性表", "树结构")
    with pytest.raises(ValueError, match="没有板块"):
        kg.rename_board("数据结构", "不存在的板块", "新板块")


def test_board_ops_reject_blank_names(kg):
    _seed(kg)
    for call in (lambda: kg.rename_board("", "线性表", "新板块"),
                 lambda: kg.rename_board("数据结构", "  ", "新板块"),
                 lambda: kg.rename_board("数据结构", "线性表", "  "),
                 lambda: kg.rename_board("数据结构", "线性表", "线性表"),
                 lambda: kg.remove_board("数据结构", ""),
                 lambda: kg.remove_board("数据结构", "   "),
                 lambda: kg.remove_board("", "线性表")):
        with pytest.raises(ValueError):
            call()


# ── 板块解散 ──────────────────────────────────────────

def test_remove_board_keeps_nodes(kg):
    """解散板块 = board 置空（回到未分组），**不删节点、不删正文**。"""
    _seed(kg)

    r = kg.remove_board("数据结构", "线性表")

    assert r["moved_nodes"] == 2
    d1 = kg.get_node("d1")
    assert d1 is not None and d1["board"] == ""
    assert _ids(kg.get_nodes_by_subject("数据结构")) == {"d1", "d2", "d3"}
    assert (kg.nodes_dir / "d1.md").exists()
    assert _ids(kg.get_nodes_by_board("操作系统", "线性表")) == {"o1"}, \
        "别课的同名板块不能动"


def test_remove_board_rejects_missing_board(kg):
    _seed(kg)
    with pytest.raises(ValueError, match="没有板块"):
        kg.remove_board("数据结构", "不存在的板块")
