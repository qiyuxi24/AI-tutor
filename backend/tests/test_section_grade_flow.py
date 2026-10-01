"""按**小节**的判分闭环：答对→小节通过（全通过→节点 100）；答错→先重出题、第二次降级。

用户口径（2026-09-29）：
  · 「已懂」不等于通过 —— 要出题验证，答对才算该小节 `passed`；
  · 答错第一次：讲清错在哪 + 再出一道同类题（**不降级**）；
  · 答错第二次：把该小节标记为「不懂」，转入教学；
  · 某节点**全部小节** passed → 节点掌握度**直接置 100**（不是每答对一题 +20 累加）。

同时锁住反向兼容：**没有 `section_id` 的题**（对话内常规出题）仍走旧的节点级口径（答对 +20），
老行为不能被改坏。

全离线：真实 KnowledgeGraph / QuizStore 落在 tmp_path，不碰 LLM。

注：pytest-asyncio 未安装（见 test_chat_empty_graph.py 的约定），异步用例统一 `asyncio.run`。
"""
import asyncio

import pytest

from app.core.knowledge_graph import KnowledgeGraph
from app.core.quiz.chat_grade import auto_grade_pending, grade_pending
from app.core.quiz.quiz_store import QuizStore

USER_ID = 11


@pytest.fixture
def kg(tmp_path):
    g = KnowledgeGraph(user_id=USER_ID, data_dir=tmp_path / "kg")
    with g._conn:
        g._conn.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (?, 's', 'x')",
            (USER_ID,))
    g.create_node_with_content({"id": "n1", "name": "二重积分", "subject": "高等数学"}, "正文")
    g.create_section("n1", "定义与几何意义", content="定义正文" * 60)
    g.create_section("n1", "计算例题", content="例题正文" * 60)
    yield g
    g.close()


@pytest.fixture
def store(tmp_path):
    return QuizStore(tmp_path / "quiz" / str(USER_ID))


def _add(store, *, section_id="s01", answer="A", node_id="n1", qid="q1"):
    """落一道单选题，返回题库 id。"""
    ids = store.save_questions([{
        "id": qid, "type": "single", "question": "下面哪个说法正确？",
        "options": [{"value": "A", "label": "甲"}, {"value": "B", "label": "乙"}],
        "answer": [answer], "analysis": "解析文字", "knowledge_point": node_id,
    }], subject="二重积分", source="chat", section_id=section_id)
    return ids[0]


# ── 答对 ────────────────────────────────────────────────────────

def test_correct_marks_section_passed_without_node_gain(kg, store):
    """答对只把**本节**置为通过，不给节点加 20 分（全通过才置 100）。"""
    _add(store, section_id="s01")
    r = asyncio.run(grade_pending("A", kg=kg, store=store))

    assert r["ok"] and r["correct"]
    assert r["section_id"] == "s01"
    assert r["next_action"] == "passed"
    assert "已通过" in r["section_note"]
    assert kg.get_section_learn("n1", "s01")["passed"] is True
    assert kg.get_node("n1")["mastery"] == 0


def test_all_sections_passed_sets_mastery_100(kg, store):
    """全部小节通过 → 节点掌握度直接 100；只通过一半时不动。"""
    _add(store, section_id="s01", qid="q1")
    _add(store, section_id="s02", qid="q2")

    asyncio.run(grade_pending("A", kg=kg, store=store))          # 先判最新那道（s02）
    assert kg.get_section_learn("n1", "s02")["passed"] is True
    assert kg.get_node("n1")["mastery"] == 0

    r = asyncio.run(grade_pending("A", kg=kg, store=store))      # 再判剩下那道（s01）
    assert kg.get_node("n1")["mastery"] == 100
    assert "全部小节已通过" in r["section_note"]


# ── 答错 ────────────────────────────────────────────────────────

def test_first_wrong_answer_retries_without_downgrade(kg, store):
    """第一次答错：标记不动、attempts+1，动作是 retry（由调用方再出一题）。"""
    _add(store, section_id="s01")
    r = asyncio.run(grade_pending("B", kg=kg, store=store))

    assert r["correct"] is False
    assert r["next_action"] == "retry"
    st = kg.get_section_learn("n1", "s01")
    assert st["attempts"] == 1
    assert st["mark"] == "unknown"          # 不降级
    assert st["passed"] is False


def test_second_wrong_answer_downgrades_to_confused(kg, store):
    """第二次仍错 → 把该小节标记为「不懂」，转入教学。"""
    _add(store, section_id="s01", qid="q1")
    asyncio.run(grade_pending("B", kg=kg, store=store))          # 第一次 → retry
    _add(store, section_id="s01", qid="q2")               # 系统重出的新题
    r = asyncio.run(grade_pending("B", kg=kg, store=store))

    assert r["next_action"] == "downgrade"
    st = kg.get_section_learn("n1", "s01")
    assert st["mark"] == "confused"
    assert st["attempts"] == 2


# ── 答错后**不再自动出题**：改成"先讲错点 + 问一句要不要再来一道"（2026-09-30）──
# 用户口径：出题前必须先问学生。旧版在判分那一刻就排期并写"系统已自动再推一道本节题"
# —— 学生没点过头就被考；而且出题是异步的（可能失败 / 被 _INFLIGHT 挡住），承诺也未必兑现。

def test_retry_note_asks_before_quizzing(kg, store):
    """第一次答错 → 文案要求"讲错点 + 问一句"，**不许**说"已经再推一道"。"""
    _add(store, section_id="s01")
    r = asyncio.run(grade_pending("B", kg=kg, store=store))

    assert r["next_action"] == "retry"
    assert "先讲清错在哪" in r["section_note"]
    assert "问一句" in r["section_note"]
    assert "已自动再推" not in r["section_note"]


def test_auto_grade_schedules_no_quiz_at_all(kg, store, monkeypatch):
    """判分路径**一次都不排期**（谁都不能在学生点头前把题推出去），但要求模型去问。"""
    from app.core.quiz import chat_quiz
    calls = []

    def fake(*args, **kwargs):
        calls.append((args, kwargs))
        return True

    monkeypatch.setattr(chat_quiz, "start_background_generation", fake)
    _add(store, section_id="s01")

    note = asyncio.run(auto_grade_pending(kg, "B", store=store))

    assert calls == []                  # 判分不再触发任何出题
    assert "问一句" in note              # 但明确要求模型去征求同意


def test_auto_grade_downgrade_note_switches_to_teaching(kg, store, monkeypatch):
    """第二次答错 → 降级为「不懂」→ 文案转入讲解（全程不出题）。"""
    from app.core.quiz import chat_quiz
    calls = []
    monkeypatch.setattr(chat_quiz, "start_background_generation",
                        lambda *a, **kw: calls.append(1) or True)
    _add(store, section_id="s01", qid="q1")
    asyncio.run(auto_grade_pending(kg, "B", store=store))        # 第 1 次
    _add(store, section_id="s01", qid="q2")

    note = asyncio.run(auto_grade_pending(kg, "B", store=store))  # 第 2 次

    assert calls == []
    assert "降级" in note and "按不懂来讲" in note


def test_wrong_then_right_still_passes(kg, store):
    """错一次、第二次答对：照样通过（降级只在连续错两次时发生）。"""
    _add(store, section_id="s01", qid="q1")
    asyncio.run(grade_pending("B", kg=kg, store=store))
    _add(store, section_id="s01", qid="q2")
    r = asyncio.run(grade_pending("A", kg=kg, store=store))

    assert r["next_action"] == "passed"
    assert kg.get_section_learn("n1", "s01")["passed"] is True


# ── 反向兼容 ────────────────────────────────────────────────────

def test_node_level_question_keeps_gain_20(kg, store):
    """没有 section_id 的题仍走旧口径（答对 +20），且不碰任何小节状态。"""
    _add(store, section_id="")
    r = asyncio.run(grade_pending("A", kg=kg, store=store))

    assert r["section_id"] == ""
    assert r["next_action"] == ""
    assert r["section_note"] == ""
    assert kg.get_node("n1")["mastery"] == 20
    assert kg.get_section_learn("n1", "s01")["passed"] is False


def test_quiz_store_persists_section_id(store):
    """题库新列：存得进、读得出（判分要靠它定位是哪一节的题）。"""
    _add(store, section_id="s02")
    q = store.get_pending_question(source="chat")
    assert q["section_id"] == "s02"
    assert store.get_question(q["id"])["section_id"] == "s02"
    assert store.list_by_knowledge_point("n1")[0]["section_id"] == "s02"
