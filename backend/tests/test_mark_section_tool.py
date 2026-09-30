"""工具 `mark_section_understood`：讲完一节就把它记成「已懂」（AI 讲完即记）。

锁定四件事：
  1. 正常路径 → 写 `mark=understood` + `mark_by='ai'`，并推 `graph_updated`（前端跟着刷）；
  2. 模型编造 / 不属于该节点的小节 id → 友好文案 + **不写盘**（教学现场不能报错打断）；
  3. 缺参数 / 节点无小节 → 给可操作文案，**永不抛异常**；
  4. 返回值必须点明「已懂 ≠ 通过」并引导出题 —— 否则模型会以为讲完就算过，状态栏永远停在已懂。

全离线：tmp_path 真实 KnowledgeGraph，不碰 LLM。
"""
import pytest

from app.core.agent_tools.tools import mark_section_understood as tool
from app.core.knowledge_graph import (
    LEARN_MARK_BY_AI,
    LEARN_MARK_BY_USER,
    LEARN_MARK_UNDERSTOOD,
    KnowledgeGraph,
)

USER_ID = 21


@pytest.fixture
def kg(tmp_path):
    g = KnowledgeGraph(user_id=USER_ID, data_dir=tmp_path / "kg")
    with g._conn:
        g._conn.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (?, 's', 'x')",
            (USER_ID,))
    g.create_node_with_content({"id": "n1", "name": "二重积分", "subject": "高等数学"}, "正文")
    yield g
    g.close()


@pytest.fixture
def events(monkeypatch):
    """捕获 `publish` 的事件类型（工具内部延迟导入，所以打 event_bus 模块上的名字）。"""
    seen = []
    import app.core.event_bus as bus
    monkeypatch.setattr(bus, "publish", lambda ev, *a, **kw: seen.append(ev))
    return seen


def _two_sections(kg):
    kg.create_section("n1", "定义", content="定义正文" * 50)
    kg.create_section("n1", "例题", content="例题正文" * 50)


# ── 正常路径 ────────────────────────────────────────────────────

def test_marks_section_understood_with_ai_origin(kg, events):
    """讲完 → 已懂，且来源是 ai（不是 user）—— 前端据此显示“AI 讲完自动记”。"""
    _two_sections(kg)
    out = tool.handler({"node_id": "n1", "section_id": "s02"}, kg)

    st = kg.get_section_learn("n1", "s02")
    assert st["mark"] == LEARN_MARK_UNDERSTOOD
    assert st["mark_by"] == LEARN_MARK_BY_AI
    # 另一节不受影响（局部更新）
    assert kg.get_section_learn("n1", "s01")["mark"] == "unknown"
    assert "graph_updated" in events


def test_result_text_says_understood_is_not_passed(kg):
    """返回值必须点破"已懂 ≠ 通过"并引导出题，否则模型会以为讲完就过关了。"""
    _two_sections(kg)
    out = tool.handler({"node_id": "n1", "section_id": "s01"}, kg)

    assert "第 1 节" in out and "定义" in out
    assert "≠" in out and "通过" in out
    assert "quiz_generate" in out and "section_id=s01" in out


def test_ai_marking_does_not_touch_passed_or_user_mark(kg):
    """AI 只补「已懂」这一笔：不写 passed（那要出题答对），也不动学生自评的另一些节。"""
    _two_sections(kg)
    kg.set_section_learn("n1", "s01", mark=LEARN_MARK_UNDERSTOOD, mark_by=LEARN_MARK_BY_USER)
    tool.handler({"node_id": "n1", "section_id": "s02"}, kg)

    assert kg.get_section_learn("n1", "s02")["passed"] is False
    assert kg.get_section_learn("n1", "s01")["mark_by"] == LEARN_MARK_BY_USER


# ── 降级路径（永不抛异常）────────────────────────────────────────

def test_unknown_section_id_is_rejected_without_writing(kg, events):
    """模型编造的小节 id → 明确指回清单，且**一个字节都不写**。"""
    _two_sections(kg)
    out = tool.handler({"node_id": "n1", "section_id": "s99"}, kg)

    assert "s99" in out and "s01" in out      # 文案里给出真实存在的 id
    assert kg.get_section_learn("n1", "s01")["mark"] == "unknown"
    assert events == []                       # 没写盘就不该推 graph_updated


def test_missing_args_returns_hint(kg):
    assert "node_id" in tool.handler({}, kg)
    assert "node_id" in tool.handler({"node_id": "n1"}, kg)


def test_node_without_sections(kg):
    out = tool.handler({"node_id": "n1", "section_id": "s01"}, kg)
    assert "没有小节" in out
