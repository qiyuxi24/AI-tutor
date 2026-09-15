"""
对话内出题（P0）测试 —— 全部离线，不调 LLM。

覆盖：
- 工具分发：协程 handler 被 await（不再走线程池）、同步 handler 行为不变、单工具超时按 spec 覆盖
- quiz_generate 工具：立即返回 + 起后台任务；节点不存在 / 重复触发 的友好降级
- 判分链路：待作答题目反查 → 规则判分 → 记录作答 → 答对确定性抬升掌握度
- 后台出题任务：成功推 quiz_ready（且**不含答案**）、失败推 ok=False
"""
import asyncio
import json
from types import SimpleNamespace

import pytest

from app.core import agent_tools
from app.core.agent_tools.tools import grade_answer, quiz_generate
from app.core.quiz import chat_quiz
from app.core.quiz.quiz_store import QuizManager


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _clear_inflight():
    """每个用例前后清掉"出题中"占位，避免用例间互相污染。"""
    chat_quiz._INFLIGHT.clear()
    yield
    chat_quiz._INFLIGHT.clear()


def _tc(name: str, args: dict, tc_id: str = "call_1") -> SimpleNamespace:
    """构造一个 OpenAI 风格的 tool_call"""
    return SimpleNamespace(
        id=tc_id,
        function=SimpleNamespace(name=name, arguments=json.dumps(args)),
    )


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


# ══════════════════════════════════════════════════════════════════
#  工具分发（async handler / per-tool timeout）
# ══════════════════════════════════════════════════════════════════

def test_async_dispatch_awaits_coroutine_handler(monkeypatch):
    """协程 handler 由 execute_kg_tool_async 直接 await（不是丢线程池）。"""
    async def _handler(args, kg):
        assert kg == "KG"
        return f"async-ok:{args['x']}"

    monkeypatch.setitem(agent_tools._TOOL_BY_NAME, "probe_async", {
        "name": "probe_async", "description": "", "parameters": {},
        "handler": _handler, "timeout_secs": None,
    })
    out = _run(agent_tools.execute_kg_tool_async(_tc("probe_async", {"x": 1}), "KG"))
    assert out == "async-ok:1"


def test_async_dispatch_still_supports_sync_handler(monkeypatch):
    """同步 handler 行为不变（仍在线程池执行）。"""
    def _handler(args, kg):
        return f"sync-ok:{args['x']}"

    monkeypatch.setitem(agent_tools._TOOL_BY_NAME, "probe_sync", {
        "name": "probe_sync", "description": "", "parameters": {},
        "handler": _handler, "timeout_secs": None,
    })
    out = _run(agent_tools.execute_kg_tool_async(_tc("probe_sync", {"x": 2}), None))
    assert out == "sync-ok:2"


def test_async_dispatch_isolates_handler_exception(monkeypatch):
    """协程 handler 抛业务错 → 隔离成友好文案，不向上炸。"""
    async def _handler(args, kg):
        raise ValueError("节点不存在")

    monkeypatch.setitem(agent_tools._TOOL_BY_NAME, "probe_err", {
        "name": "probe_err", "description": "", "parameters": {},
        "handler": _handler, "timeout_secs": None,
    })
    out = _run(agent_tools.execute_kg_tool_async(_tc("probe_err", {}), None))
    assert out.startswith("操作失败:") and "节点不存在" in out


def test_tool_timeout_secs_prefers_spec(monkeypatch):
    """单工具超时可以按 spec 覆盖默认值。"""
    assert agent_tools.tool_timeout_secs(_tc("quiz_generate", {}), 60) == 90
    assert agent_tools.tool_timeout_secs(_tc("rag_search", {}), 60) == 60
    assert agent_tools.tool_timeout_secs(_tc("不存在的工具", {}), 60) == 60


def test_quiz_tools_registered():
    """两个新工具已导出给模型（KG_TOOLS）且 schema 完整。"""
    names = {t["function"]["name"] for t in agent_tools.KG_TOOLS}
    assert {"quiz_generate", "grade_answer"} <= names
    for t in agent_tools.KG_TOOLS:
        if t["function"]["name"] in ("quiz_generate", "grade_answer"):
            assert t["function"]["parameters"]["required"]
            # spec 上的 timeout_secs 不应泄漏进模型侧 schema
            assert "timeout_secs" not in t["function"]


# ══════════════════════════════════════════════════════════════════
#  quiz_generate 工具
# ══════════════════════════════════════════════════════════════════

def test_quiz_generate_schedules_background_and_returns_immediately(monkeypatch):
    """工具立即返回（不等待出题），并真的起了后台任务。"""
    started = []

    async def fake_generate(user_id, *, node_id, count=1):
        started.append((user_id, node_id))

    monkeypatch.setattr(chat_quiz, "generate_and_publish", fake_generate)
    chat_quiz._INFLIGHT.clear()

    kg = _FakeKg({"bt": {"id": "bt", "name": "二叉树"}})

    async def _scenario():
        out = await quiz_generate.handler({"node_id": "bt"}, kg)
        await asyncio.sleep(0)      # 让 create_task 落地
        return out

    out = _run(_scenario())
    assert "后台出题" in out
    assert "不要等待" in out
    assert started == [(7, "bt")]


def test_quiz_generate_rejects_unknown_node():
    """节点不存在 → 友好文案，不起后台任务。"""
    kg = _FakeKg({})
    out = _run(quiz_generate.handler({"node_id": "nope"}, kg))
    assert "不在当前知识图谱" in out


def test_quiz_generate_rejects_missing_node_id():
    out = _run(quiz_generate.handler({}, _FakeKg()))
    assert "需要指定 node_id" in out


def test_quiz_generate_dedupes_concurrent_trigger(monkeypatch):
    """已有出题任务在跑时，第二次触发被拒（模型可能连调两次）。"""
    async def fake_generate(user_id, *, node_id, count=1):
        await asyncio.sleep(1)

    monkeypatch.setattr(chat_quiz, "generate_and_publish", fake_generate)
    chat_quiz._INFLIGHT.clear()
    kg = _FakeKg({"bt": {"id": "bt", "name": "二叉树"}})

    async def _scenario():
        first = await quiz_generate.handler({"node_id": "bt"}, kg)
        second = await quiz_generate.handler({"node_id": "bt"}, kg)
        return first, second

    first, second = _run(_scenario())
    assert "后台出题" in first
    assert "已经有一道题在生成中" in second


# ══════════════════════════════════════════════════════════════════
#  后台出题任务 → quiz_ready 事件
# ══════════════════════════════════════════════════════════════════

def _patch_store(monkeypatch, tmp_path):
    mgr = QuizManager(tmp_path)
    monkeypatch.setattr(chat_quiz, "quiz_manager", mgr)
    return mgr


def test_generate_and_publish_pushes_questions_without_answers(
        tmp_path, monkeypatch):
    """出题成功 → 推 quiz_ready；推送载荷**不含答案/解析**（防作弊）。"""
    _patch_store(monkeypatch, tmp_path)
    published = []
    monkeypatch.setattr(chat_quiz, "publish",
                        lambda etype, data=None, user_id=None: published.append(
                            (etype, data, user_id)))

    class _FakeKgForGen:
        def __init__(self, user_id):
            self.user_id = user_id
            self.closed = False

        def get_node(self, node_id):
            return {"id": node_id, "name": "二叉树的性质", "difficulty": 2,
                    "mastery": 0, "summary": "完全二叉树编号性质"}

        def get_node_content_preview(self, node_id, **kw):
            return "若 i=1 则无双亲；若 2i<=n 则左孩子为 2i。"

        def close(self):
            self.closed = True

    monkeypatch.setattr(chat_quiz, "KnowledgeGraph", _FakeKgForGen)

    # 该节点已考过一道题 → 必须传给 generator 做跨调用去重（否则可刷掌握度）
    prior = "这是一道之前已经考过的题目，内容长度足够长了哦"
    chat_quiz.quiz_manager._get_store(7).save_questions(
        [{"id": "q1", "type": "single", "question": prior,
          "options": [{"label": "甲", "value": "A"}, {"label": "乙", "value": "B"},
                      {"label": "丙", "value": "C"}, {"label": "丁", "value": "D"}],
          "answer": ["A"], "analysis": "解析", "points": 10,
          "knowledge_point": "bt"}],
        subject="二叉树的性质", source="chat",
    )

    async def fake_generate_quiz(**kwargs):
        assert kwargs["question_types"] == chat_quiz.CHAT_QUIZ_TYPES
        assert kwargs["subject"] == "二叉树的性质"
        assert kwargs["difficulty"] == "easy"      # difficulty=2 → easy
        assert kwargs["seed_materials"]            # 节点正文已作为第一依据注入
        assert kwargs["avoid_questions"] == [prior]  # 跨调用去重已生效
        return {
            "questions": [{
                "id": "q1", "type": "single", "question": "编号 53 的双亲编号是多少？",
                "options": [{"label": "25", "value": "A"}, {"label": "26", "value": "B"},
                            {"label": "27", "value": "C"}, {"label": "52", "value": "D"}],
                "answer": ["B"], "analysis": "⌊53/2⌋ = 26", "points": 10,
            }],
            "rejected": 0, "materials_used": 1,
        }

    import app.core.quiz.generator as gen_mod
    monkeypatch.setattr(gen_mod, "generate_quiz", fake_generate_quiz)

    chat_quiz._INFLIGHT.clear()
    _run(chat_quiz.generate_and_publish(7, node_id="bt"))

    assert len(published) == 1
    etype, data, uid = published[0]
    assert etype == "quiz_ready" and uid == 7 and data["ok"] is True
    assert data["node_id"] == "bt" and data["subject"] == "二叉树的性质"
    q = data["questions"][0]
    assert q["question"].startswith("编号 53")
    assert q["id"] > 0                      # 已入库，带题库 id
    assert "answer" not in q and "analysis" not in q   # 不泄漏答案


def test_generate_and_publish_reports_failure(tmp_path, monkeypatch):
    """出题失败 → 推 ok=False（学生不会永远等下去）。"""
    _patch_store(monkeypatch, tmp_path)
    published = []
    monkeypatch.setattr(chat_quiz, "publish",
                        lambda etype, data=None, user_id=None: published.append(
                            (etype, data, user_id)))

    class _KgNoNode:
        def __init__(self, user_id):
            self.user_id = user_id

        def get_node(self, node_id):
            return None

        def close(self):
            pass

    monkeypatch.setattr(chat_quiz, "KnowledgeGraph", _KgNoNode)
    chat_quiz._INFLIGHT.clear()
    _run(chat_quiz.generate_and_publish(7, node_id="ghost"))

    assert published[0][1]["ok"] is False
    assert "不存在" in published[0][1]["message"]


# ══════════════════════════════════════════════════════════════════
#  待作答题目反查
# ══════════════════════════════════════════════════════════════════

def _seed_question(store, *, source, question_text, knowledge_point=""):
    return store.save_questions(
        [{"id": "q1", "type": "single", "question": question_text,
          "options": [{"label": "甲", "value": "A"}, {"label": "乙", "value": "B"},
                      {"label": "丙", "value": "C"}, {"label": "丁", "value": "D"}],
          "answer": ["A"], "analysis": "解析", "points": 10,
          "knowledge_point": knowledge_point}],
        subject="测试", source=source,
    )[0]


def test_get_pending_question_only_returns_chat_source(tmp_path):
    """只认对话内出的题（source='chat'），不误取题库页未作答的历史题。"""
    store = QuizManager(tmp_path)._get_store(1)
    _seed_question(store, source="", question_text="题库页的历史题（未作答）")
    assert store.get_pending_question() is None

    qid = _seed_question(store, source="chat", question_text="对话内出的题")
    pending = store.get_pending_question()
    assert pending and pending["id"] == qid


def test_get_pending_question_skips_answered(tmp_path):
    """作答过的题不再算待作答。"""
    store = QuizManager(tmp_path)._get_store(1)
    qid = _seed_question(store, source="chat", question_text="对话内出的题")
    store.record_attempt(qid, "A", 10, 10, True, "回答正确")
    assert store.get_pending_question() is None


# ── 跨调用去重：答对已作为掌握度主信号，不排除旧题就能刷分 ──

def test_asked_questions_scoped_by_knowledge_point(tmp_path):
    store = QuizManager(tmp_path)._get_store(1)
    _seed_question(store, source="chat", question_text="节点 bt 的第一道题",
                   knowledge_point="bt")
    _seed_question(store, source="chat", question_text="节点 bt 的第二道题",
                   knowledge_point="bt")
    _seed_question(store, source="chat", question_text="别的节点的题",
                   knowledge_point="other")

    assert set(store.asked_questions("bt")) == {"节点 bt 的第一道题", "节点 bt 的第二道题"}
    assert store.asked_questions("other") == ["别的节点的题"]
    assert store.asked_questions("") == []       # 无归属时不筛（避免误伤）


def test_filter_questions_excludes_already_asked():
    """avoid_texts 里的旧题干必须被排除（归一化后比较，忽略标点/空白）。"""
    from app.core.quiz.quality import filter_questions
    from app.core.quiz.schema import Question, QuestionOption

    def _mk(text):
        return Question(
            id="q1", type="single", question=text,
            options=[QuestionOption(label=x, value=v)
                     for x, v in (("甲", "A"), ("乙", "B"), ("丙", "C"), ("丁", "D"))],
            answer=["A"], analysis="解析",
        )

    old = "这是一道之前已经考过的题目，题干内容足够长了吧"
    fresh = "这是一道全新的题目，之前完全没有出现过，足够长了吧"

    kept, rejected = filter_questions([_mk(fresh)], avoid_texts=[old])
    assert len(kept) == 1 and rejected == 0

    kept, rejected = filter_questions([_mk(old)], avoid_texts=[old])
    assert len(kept) == 0 and rejected == 1

    # 标点差异不影响判定（归一化比较）
    kept, _ = filter_questions([_mk(old.replace("，", ""))], avoid_texts=[old])
    assert len(kept) == 0


# ══════════════════════════════════════════════════════════════════
#  判分 → 更新掌握度
# ══════════════════════════════════════════════════════════════════

def test_grade_answer_correct_grades_and_raises_mastery(tmp_path, monkeypatch):
    """答对 → 判满分、记作答、掌握度 +20，并把解析交给模型。"""
    _patch_store(monkeypatch, tmp_path)
    store = chat_quiz.quiz_manager._get_store(7)
    _seed_question(store, source="chat", question_text="栈的特点是什么？",
                   knowledge_point="stack")

    kg = _FakeKg({"stack": {"id": "stack", "name": "栈", "mastery": 20}})
    out = _run(chat_quiz.grade_pending_answer(kg, "A"))

    assert "答对" in out and "10/10" in out
    assert "解析" in out
    assert "20 提升到 40" in out
    assert kg.mastery_updates == [("stack", 40)]
    assert store.get_pending_question() is None      # 已作答，不再待答


def test_grade_answer_wrong_keeps_mastery(tmp_path, monkeypatch):
    """答错 → 0 分、掌握度**不变**（不做惩罚性扣分），并要求模型继续追问。"""
    _patch_store(monkeypatch, tmp_path)
    store = chat_quiz.quiz_manager._get_store(7)
    _seed_question(store, source="chat", question_text="栈的特点是什么？",
                   knowledge_point="stack")

    kg = _FakeKg({"stack": {"id": "stack", "name": "栈", "mastery": 40}})
    out = _run(chat_quiz.grade_pending_answer(kg, "D"))

    assert "答错" in out
    assert kg.mastery_updates == []                  # 不扣分
    assert "不要直接给出答案" in out


def test_grade_answer_mastery_capped_at_100():
    """掌握度封顶 100。"""
    kg = _FakeKg({"stack": {"id": "stack", "name": "栈", "mastery": 95}})
    note = chat_quiz.apply_mastery_after_answer(kg, "stack", correct=True)
    assert kg.mastery_updates == [("stack", 100)]
    assert "100" in note


def test_grade_answer_mastery_permission_error_is_silent():
    """人类创建的节点 AI 无权改 → 判分照常，只是不改进度、不报错。"""
    kg = _FakeKg({"stack": {"id": "stack", "name": "栈", "mastery": 10}},
                 mastery_fails=True)
    assert chat_quiz.apply_mastery_after_answer(kg, "stack", correct=True) == ""


def test_grade_answer_without_pending_question(tmp_path, monkeypatch):
    """没有待作答题目 → 友好文案（并提示先出题）。"""
    _patch_store(monkeypatch, tmp_path)
    out = _run(chat_quiz.grade_pending_answer(_FakeKg(), "A"))
    assert "没有等待作答的题目" in out


def test_grade_answer_requires_answer_text():
    """空作答不发判分。"""
    out = _run(grade_answer.handler({"user_answer": "  "}, _FakeKg()))
    assert "缺少 user_answer" in out
