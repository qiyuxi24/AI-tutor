"""P0 回归：删/清小节后必须挂 mark_node_dirty，否则索引仍保留已删正文。

根因（Lead 逐行复核）：`sections.py` 的 `create_section` / `write_section` 成功后都
调 `mark_node_dirty`，但 `delete_section` / `clear_sections` 只 `invalidate_content_cache`
→ 向量/BM25 索引仍是删除前的旧全文，已删内容仍被 S6 检索注入。
AGENTS §2「创建/改文即入索引」收口的前提正是「调用方挂了 mark_dirty」。

本文件钉住删除侧：
- `delete_section` 成功（True）/ `clear_sections` 有删除（>0）→ 该 node_id 进 dirty 集合；
- 无真实变更（False / 0）→ dirty 不变（不误标）。

隔离：独占 user_id + tmp_path 临时库；用 `node_indexer.dirty_count` / `take_dirty`
观测**真实** `_dirty` 集合（纯内存、零 IO、零网络）。建节点/建小节本身也会登记
dirty，断言前先 `take_dirty` 清掉，只测删除动作。
"""
import pytest

from app.core.knowledge_graph import KnowledgeGraph
from app.core.rag import node_indexer

UID = 991_137  # 独占：避免与其它测试的 dirty 登记撞同 user_id


@pytest.fixture
def env(tmp_path):
    kg = KnowledgeGraph(user_id=UID, data_dir=tmp_path)
    with kg._conn:  # nodes.user_id 是外键：先备 users 行
        kg._conn.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (?, ?, ?)",
            (UID, "sec_del_reindex", "x"),
        )
    node_indexer.take_dirty(UID)  # 干净基线
    yield kg
    node_indexer.take_dirty(UID)  # 不把本文件的登记留给后续用例
    kg.close()


def _seed(kg, node_id="n1"):
    """建一个带两节的小节化节点；清掉 setup 期间的登记，只留删除动作待测。"""
    kg.create_node_with_content(
        {"id": node_id, "name": "二重积分", "subject": "高等数学"}, "正文")
    kg.create_section(node_id, "定义", content="定义正文" * 20)
    kg.create_section(node_id, "例题", content="例题正文" * 20)
    node_indexer.take_dirty(UID)  # 清 create_node_with_content / create_section 的登记
    assert node_indexer.dirty_count(UID) == 0


# ── 有真实变更 → 挂 dirty ───────────────────────────────────────

def test_delete_section_marks_node_dirty(env):
    kg = env
    _seed(kg)

    assert kg.delete_section("n1", "s01") is True

    assert node_indexer.dirty_count(UID) == 1
    assert node_indexer.take_dirty(UID) == {"n1"}


def test_clear_sections_marks_node_dirty(env):
    kg = env
    _seed(kg)

    assert kg.clear_sections("n1") == 2

    assert node_indexer.dirty_count(UID) == 1
    assert node_indexer.take_dirty(UID) == {"n1"}


# ── 无真实变更 → 不误标 dirty ───────────────────────────────────

def test_delete_section_no_change_does_not_mark_dirty(env):
    kg = env
    _seed(kg)

    assert kg.delete_section("n1", "s99") is False   # 有 manifest，但无此节
    assert node_indexer.dirty_count(UID) == 0

    assert kg.delete_section("ghost", "s01") is False  # 无 manifest
    assert node_indexer.dirty_count(UID) == 0


def test_clear_sections_no_sections_does_not_mark_dirty(env):
    kg = env
    kg.create_node_with_content(
        {"id": "n1", "name": "二重积分", "subject": "高等数学"}, "正文")
    node_indexer.take_dirty(UID)  # 清掉建节点的登记（该节点从未小节化 → 无 manifest）

    assert kg.clear_sections("n1") == 0   # 无小节
    assert node_indexer.dirty_count(UID) == 0
