"""
对话内判分测试 —— 全部离线，不调 LLM。

覆盖（`core/quiz/chat_grade.py`，与出题侧 `test_chat_quiz.py` 分开）：
- 待作答题目反查 → 规则判分 → 记录作答 → **答对确定性抬升掌握度**
- 答错不扣分（不做惩罚性扣分）
- 掌握度封顶 100、人类创建的节点无权限时静默降级
- 没有待作答题目 / 空作答 的友好降级
- **可复用性契约**：题库可注入、不传 kg 时纯判分（不碰图谱）、结构化返回、source 可选
"""
import asyncio

import pytest

from app.core.agent_tools.tools import grade_answer
from app.core.quiz import chat_grade
from app.core.quiz.quiz_store import QuizManager


def _run(coro):
    return asyncio.run(coro)


def _store(tmp_path, user_id: int = 7):
    """判分支持 store 注入 → 直接给题库实例，无需 monkeypatch 全局单例。"""
    return QuizManager(tmp_path)._get_store(user_id)


class _FakeKg:
    """最小 KnowledgeGraph 替身（只用到 user_id / get_node / update_node_info）"""

    def __init__(self, nodes=None, mastery_fails=False):
        self.user_id = 7
        self._nodes = nodes or {}
        self._mastery_fails = mastery_fails
        self.mastery_updates = []   # [(node_id, mastery)]

    def get_node(self, node_id):
        return self._nodes.get(node_id)

    def update_node_info(self, node_id, data, caller="human"):
        if self._mastery_fails:
            raise PermissionError("人类创建的节点")
        self.mastery_updates.append((node_id, data.get("mastery")))
        self._nodes.setdefault(node_id, {})["mastery"] = data.get("mastery")


def _seed_question(store, *, source, question_text, knowledge_point=""):
    return store.save_questions(
        [{"id": "q1", "type": "single", "question": question_text,
          "options": [{"label": "甲", "value": "A"}, {"label": "乙", "value": "B"},
                      {"label": "丙", "value": "C"}, {"label": "丁", "value": "D"}],
          "answer": ["A"], "analysis": "解析", "points": 10,
          "knowledge_point": knowledge_point}],
        subject="测试", source=source,
    )[0]


# ══════════════════════════════════════════════════════════════════
#  判分 → 更新掌握度
# ══════════════════════════════════════════════════════════════════

def test_grade_answer_correct_grades_and_raises_mastery(tmp_path):
    """答对 → 判满分、记作答、掌握度 +20，并把解析交给模型。"""
    store = _store(tmp_path)
    _seed_question(store, source="chat", question_text="栈的特点是什么？",
                   knowledge_point="stack")

    kg = _FakeKg({"stack": {"id": "stack", "name": "栈", "mastery": 20}})
    out = _run(chat_grade.grade_pending_answer(kg, "A", store=store))

    assert "答对" in out and "10/10" in out
    assert "解析" in out
    assert "20 提升到 40" in out
    assert kg.mastery_updates == [("stack", 40)]
    assert store.get_pending_question() is None      # 已作答，不再待答


def test_grade_answer_wrong_keeps_mastery(tmp_path):
    """答错 → 0 分、掌握度**不变**（不做惩罚性扣分），并要求模型继续追问。"""
    store = _store(tmp_path)
    _seed_question(store, source="chat", question_text="栈的特点是什么？",
                   knowledge_point="stack")

    kg = _FakeKg({"stack": {"id": "stack", "name": "栈", "mastery": 40}})
    out = _run(chat_grade.grade_pending_answer(kg, "D", store=store))

    assert "答错" in out
    assert kg.mastery_updates == []                  # 不扣分
    assert "不要直接给出答案" in out


def test_grade_answer_mastery_capped_at_100():
    """掌握度封顶 100。"""
    kg = _FakeKg({"stack": {"id": "stack", "name": "栈", "mastery": 95}})
    note = chat_grade.apply_mastery_after_answer(kg, "stack", correct=True)
    assert kg.mastery_updates == [("stack", 100)]
    assert "100" in note


def test_grade_answer_mastery_permission_error_is_silent():
    """人类创建的节点 AI 无权改 → 判分照常，只是不改进度、不报错。"""
    kg = _FakeKg({"stack": {"id": "stack", "name": "栈", "mastery": 10}},
                 mastery_fails=True)
    assert chat_grade.apply_mastery_after_answer(kg, "stack", correct=True) == ""


def test_grade_answer_without_pending_question(tmp_path):
    """没有待作答题目 → 友好文案（并提示先出题）。"""
    out = _run(chat_grade.grade_pending_answer(_FakeKg(), "A", store=_store(tmp_path)))
    assert "没有等待作答的题目" in out


def test_grade_answer_requires_answer_text():
    """空作答不发判分。"""
    out = _run(grade_answer.handler({"user_answer": "  "}, _FakeKg()))
    assert "缺少 user_answer" in out


def test_grade_module_does_not_import_quiz_generation():
    """判分模块不得反向依赖出题模块（拆分后的架构约束）。"""
    assert not hasattr(chat_grade, "chat_quiz")
    assert not hasattr(chat_grade, "start_background_generation")


def test_grade_module_has_no_runtime_heavy_deps():
    """运行时零依赖：图谱类只在类型标注里，题库模块按需延迟导入。"""
    assert not hasattr(chat_grade, "KnowledgeGraph")   # 仅在 TYPE_CHECKING 下导入
    assert not hasattr(chat_grade, "quiz_manager")     # 注入 store 时根本不拉起题库模块


# ══════════════════════════════════════════════════════════════════
#  可复用性契约（任何模块拎起来就能用）
# ══════════════════════════════════════════════════════════════════

def test_grade_pending_works_without_kg(tmp_path):
    """不传 kg → 纯判分：拿结构化结果，完全不碰知识图谱。"""
    store = _store(tmp_path)
    _seed_question(store, source="chat", question_text="栈的特点是什么？",
                   knowledge_point="stack")

    r = _run(chat_grade.grade_pending("A", store=store))

    assert r["ok"] is True and r["correct"] is True
    assert (r["score"], r["max_score"]) == (10, 10)
    assert r["question"] == "栈的特点是什么？"
    assert r["knowledge_point"] == "stack"
    assert r["mastery_note"] == ""            # 没传 kg → 不写掌握度
    assert store.get_pending_question() is None   # 仍照常记录作答


def test_grade_pending_resolves_store_by_user_id(tmp_path, monkeypatch):
    """没有 kg 时可用 user_id 走默认题库单例（延迟导入，可被替换）。"""
    import app.core.quiz.quiz_store as quiz_store_mod
    mgr = QuizManager(tmp_path)
    monkeypatch.setattr(quiz_store_mod, "quiz_manager", mgr)
    _seed_question(mgr._get_store(7), source="chat", question_text="栈的特点是什么？")

    r = _run(chat_grade.grade_pending("A", user_id=7))
    assert r["ok"] is True and r["score"] == 10


def test_grade_pending_without_store_or_user_id_raises():
    """定位不到题库时明确报错，而不是静默用错库。"""
    with pytest.raises(ValueError, match="无法定位题库"):
        _run(chat_grade.grade_pending("A"))


def test_grade_pending_source_can_include_quiz_page_questions(tmp_path):
    """默认只认对话内出的题；source="" 时才认题库页的历史题。"""
    store = _store(tmp_path)
    _seed_question(store, source="", question_text="题库页的历史题，未作答")

    assert _run(chat_grade.grade_pending("A", store=store))["ok"] is False
    r = _run(chat_grade.grade_pending("A", store=store, source=""))
    assert r["ok"] is True and r["score"] == 10
