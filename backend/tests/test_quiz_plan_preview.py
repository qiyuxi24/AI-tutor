"""题量预览端点 + strict 规格验收（2026-11-16）。

两件事一起锁：`POST /quiz/plan/preview` 与 `POST /quiz/generate` 的 `question_plan` 路径。

预览端点的契约（为什么值得单独测）
--------------------------------
它会在用户"选知识点 / 切模式 / 改题量"时被反复调用 —— 一旦掺进 LLM 调用就是
"点一下扣一次钱"，一旦掺进写库就会在用户还没确认时留下垃圾题集。所以：

* **只读**：测完库里必须还是 0 行（`store.stats()`）；
* **不泄露**：节点不存在 / 不属于本用户 / 根本没传 —— 三种情况的响应必须逐字段相同，
  否则这个端点就成了"别人的图谱里有没有这个节点"的探测器；
* **对账**：`sum(sections[*].planned) == count`（前端直接展示，不能自相矛盾）。

strict 的契约
------------
`strict=True` 且凑不齐时**一道都不落库**（422，错误信息可断言到题型计数）；
`strict=False`（默认）仍交付，但 `satisfied=false` + `shortfall` + `missing_cover_keys`。
另外锁一条硬要求：**老路径（`question_plan` 为空）的响应字段集合与改动前完全一致**
—— 多一个键都可能让前端或既有测试的严格断言翻车。

全离线：LLM 用桩（`generate_quiz` 被替换），图谱与题库都落 tmp_path。
"""
import asyncio
import json
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import quiz as quiz_api
from app.core.auth import get_current_user
from app.core.knowledge_graph import KnowledgeGraph
from app.core.quiz.quiz_store import QuizStore

USER_ID = 11
OTHER_USER = 99


def _run_generate(fn, bp, *, count=None):
    """跑 `generate_quiz`：`bp=None` 时用老路径参数（只给 question_types）。"""
    n = count if count is not None else (len(bp.slots) if bp is not None else 1)
    kwargs = dict(user_id=1, subject="二叉树遍历", node_id=None, question_count=n,
                  difficulty="medium",
                  question_types=["single", "multiple", "judge", "fill", "short_answer"])
    if bp is not None:
        kwargs["blueprint"] = bp
    return asyncio.run(fn(**kwargs))


@pytest.fixture()
def kg_dir(tmp_path):
    """两个用户 + 三个节点：n1（本用户，两节）、n_other（别人的）、n_empty（本用户无小节）。"""
    d = tmp_path / "kg"
    g = KnowledgeGraph(user_id=USER_ID, data_dir=d)
    with g._conn:
        g._conn.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (?, 's', 'x')",
            (USER_ID,))
    g.create_node_with_content({"id": "n1", "name": "二叉树遍历", "subject": "数据结构"},
                               "二叉树遍历的正文。")
    g.create_section("n1", "前序遍历", content="前序正文" * 90)      # 450 字
    g.create_section("n1", "中序遍历", content="中序正文" * 90)      # 450 字
    g.create_node_with_content({"id": "n_empty", "name": "空节点", "subject": "数据结构"},
                               "只有一句话。")
    g.close()

    other = KnowledgeGraph(user_id=OTHER_USER, data_dir=d)
    with other._conn:
        other._conn.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (?, 'o', 'x')",
            (OTHER_USER,))
    other.create_node_with_content({"id": "n_other", "name": "别人的节点",
                                    "subject": "数据结构"}, "别人的正文" * 50)
    other.close()
    return d


@pytest.fixture()
def store(tmp_path):
    s = QuizStore(tmp_path / "quiz" / str(USER_ID))
    yield s
    s.close()


@pytest.fixture()
def client(store, kg_dir, monkeypatch):
    """端点内自建 `KnowledgeGraph` 的两条来源都要指向 tmp：
    一条是 `api/v1/quiz.py` 顶层的名字，另一条是 `planning.load_candidates` 里
    函数内的 `from app.core.knowledge_graph import KnowledgeGraph`。
    """
    factory = lambda user_id: KnowledgeGraph(user_id=user_id, data_dir=kg_dir)  # noqa: E731
    app = FastAPI()
    app.include_router(quiz_api.router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: USER_ID
    monkeypatch.setattr(quiz_api.quiz_manager, "_get_store", lambda user_id: store)
    monkeypatch.setattr(quiz_api, "KnowledgeGraph", factory)
    monkeypatch.setattr("app.core.knowledge_graph.KnowledgeGraph", factory)
    monkeypatch.setattr("app.core.quiz.planning.KnowledgeGraph", factory, raising=False)
    return TestClient(app)


def _preview(client, **payload):
    resp = client.post("/api/v1/quiz/plan/preview", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


# ── 预览：无锚点分支 ────────────────────────────────────────────

def test_preview_without_node_falls_back_to_default_count(client):
    """没给节点 → count 走默认下限，且明确说明"按默认 N 道"（不假装算过内容）。"""
    data = _preview(client, subject="栈", count_mode="auto")

    assert data["count"] == 3
    assert data["section_count"] == 0
    assert data["sections"] == []
    assert data["slot_preview"] == []
    assert "未指定知识点" in data["reason"]
    assert data["content_chars"] == 0


def test_preview_unknown_node_is_indistinguishable_from_no_node(client):
    """节点不存在 / 不属于本用户 → 与"根本没传"**逐字段相同**（不泄露存在性）。"""
    baseline = _preview(client, subject="数据结构", count_mode="auto")
    missing = _preview(client, subject="数据结构", count_mode="auto",
                       graph_node_id="does_not_exist")
    others = _preview(client, subject="数据结构", count_mode="auto",
                      graph_node_id="n_other")

    assert missing == baseline, "不存在的节点不能有可观测差异"
    assert others == baseline, "别人的节点不能有可观测差异"


# ── 预览：有锚点分支 ────────────────────────────────────────────

def test_preview_with_node_reports_sections_and_budget(client):
    """有锚点 → 逐节给出正文长度与计划题量，且各节之和等于 count。"""
    data = _preview(client, subject="数据结构", count_mode="auto",
                    graph_node_id="n1")

    assert data["section_count"] == 2
    assert [s["section_id"] for s in data["sections"]] == ["s01", "s02"]
    assert [s["title"] for s in data["sections"]] == ["前序遍历", "中序遍历"]
    assert all(s["chars"] > 0 for s in data["sections"])
    assert data["content_chars"] == sum(s["chars"] for s in data["sections"])
    # 900 字 → cap=2、floor=2 → 2 道；但下限是 3 道
    assert data["count"] == 3
    assert sum(s["planned"] for s in data["sections"]) == data["count"]
    assert data["slot_preview"] == [], "auto 模式没有规格，不该凭空造槽位"


def test_preview_manual_mode_uses_requested_count(client):
    """manual → count 就是用户要的数（不被内容预算改写），但仍逐节摊开。"""
    data = _preview(client, subject="数据结构", count_mode="manual",
                    question_count=7, graph_node_id="n1")

    assert data["count"] == 7
    assert sum(s["planned"] for s in data["sections"]) == 7
    assert data["capped"] is False


def test_preview_plan_mode_mirrors_blueprint_slots(client):
    """plan → count = 合计，`slot_preview` 与 `sections[*].planned` 都取自同一份蓝图。"""
    data = _preview(client, subject="数据结构", count_mode="plan",
                    question_count=5, graph_node_id="n1",
                    question_plan=[{"type": "single", "count": 3},
                                   {"type": "judge", "count": 2}])

    assert data["count"] == 5
    assert len(data["slot_preview"]) == 5
    assert sum(s["planned"] for s in data["sections"]) == 5
    planned_by_key: dict[str, int] = {}
    for slot in data["slot_preview"]:
        planned_by_key[slot["cover_key"]] = planned_by_key.get(slot["cover_key"], 0) + 1
    got = {s["section_id"]: s["planned"] for s in data["sections"] if s["planned"]}
    assert len(got) == len(planned_by_key), "每个被规划的考点都要出现在 sections 里"
    assert sum(got.values()) == 5
    # 槽位里必须带齐前端要展示的规格字段
    first = data["slot_preview"][0]
    assert {"index", "cover_key", "cover_label", "type", "points",
            "bloom", "option_count", "difficulty", "section_id"} <= set(first)


def test_preview_is_read_only(client, store):
    """预览不落库、不调 LLM（LLM 已全局禁真调，调了会抛）、不发通知。"""
    _preview(client, subject="数据结构", count_mode="plan", question_count=4,
             graph_node_id="n1",
             question_plan=[{"type": "single", "count": 4}])

    assert store.stats()["total_questions"] == 0


def test_preview_node_without_sections_still_anchored(client):
    """有节点但没小节 → 一个"整体"考点（不是"无锚点"），前端仍能显示计划题量。"""
    data = _preview(client, subject="数据结构", count_mode="auto",
                    graph_node_id="n_empty")

    assert data["section_count"] == 1
    assert data["sections"][0]["section_id"] == ""
    assert data["sections"][0]["planned"] == data["count"]


# ── 出题：老路径等价 ────────────────────────────────────────────

def _fake_blueprint_generate(**kwargs):
    """按 kwargs 里的蓝图槽位产出"一槽一题"（题型跟着槽位走，选项数也跟着题型）。"""
    bp = kwargs.get("blueprint")

    async def _run():
        qs = []
        for slot in bp.slots:
            option_types = slot.type in ("single", "multiple")
            opts = ([{"value": v, "label": f"选项{v}"} for v in ("A", "B", "C", "D")]
                    if option_types else [])
            qs.append({
                "id": f"q{slot.index}", "type": slot.type,
                "question": f"关于{slot.cover_label}的第 {slot.index} 道题（题干足够长）。",
                "options": opts,
                "answer": ["A"] if option_types else (["对"] if slot.type == "judge"
                                                      else ["答案"]),
                "knowledge_point": "",
                "cover_key": slot.cover_key, "section_id": slot.section_id,
                "bloom_level": slot.bloom, "option_count": len(opts),
                "plan_json": "{}",
            })
        return {"questions": qs, "rejected": 0, "materials_used": 0,
                "requested": len(bp.slots)}

    return _run()


def test_old_path_response_shape_unchanged(client, monkeypatch):
    """老路径（无 question_plan）→ 响应字段集合与改动前**完全一致**，且 question_types 照旧传。"""
    seen: dict = {}

    async def fake_generate(**kwargs):
        seen.update(kwargs)
        return {"questions": [{
            "id": "q1", "type": "single", "question": "旧路径出的题，题干足够长。",
            "options": [{"value": "A", "label": "甲"}, {"value": "B", "label": "乙"}],
            "answer": ["A"], "knowledge_point": "栈",
        }], "rejected": 1, "materials_used": 2, "requested": 1}

    monkeypatch.setattr(quiz_api, "generate_quiz", fake_generate)

    resp = client.post("/api/v1/quiz/generate",
                       json={"subject": "栈", "question_count": 1})

    assert resp.status_code == 200
    data = resp.json()
    assert set(data) == {"status", "question_ids", "questions", "rejected",
                         "materials_used", "requested", "generated"}
    assert "blueprint" not in seen, "老路径不能带蓝图参数"
    assert seen["question_types"] == ["single", "multiple", "judge", "fill", "short_answer"]
    assert seen["question_count"] == 1


# ── 出题：规格路径 satisfied / strict ────────────────────────────

def _plan_payload(strict: bool, node: str = "n1"):
    """3 型 5 道、摊在 2 个小节上且**没有**承载缺口（每个 考点×题型 组合只用一次）。

    刻意不用 `[single×3, judge×2]`：那个规格会让 (s01, single) 被排两次，
    `allocate` 如实报 `shortfall=1`（同一考点同一题型问两遍 = 内容承载不足），
    于是"规格被满足"这个用例会挂在一个与它无关的字段上。
    """
    return {
        "subject": "二叉树遍历", "question_count": 5, "count_mode": "plan",
        "graph_node_id": node, "strict": strict,
        "question_plan": [{"type": "single", "count": 2},
                          {"type": "judge", "count": 2},
                          {"type": "fill", "count": 1}],
    }


def test_plan_path_satisfied_annotates_questions(client, store, monkeypatch):
    """规格被满足 → satisfied=true，且入库行带上考点/小节/认知层次/选项数。"""
    async def fake_generate(**kwargs):
        return await _fake_blueprint_generate(**kwargs)

    monkeypatch.setattr(quiz_api, "generate_quiz", fake_generate)

    resp = client.post("/api/v1/quiz/generate", json=_plan_payload(strict=True))

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["satisfied"] is True
    assert data["shortfall"] == 0
    assert data["missing_cover_keys"] == []
    assert data["plan_shortfall"] == 0
    assert data["generated"] == 5

    rows = store.list_questions()
    assert len(rows) == 5
    # 有 graph_node_id → knowledge_point 必须被改写成节点 id（否则判分回写不了掌握度）
    assert {r["knowledge_point"] for r in rows} == {"n1"}
    assert all(r["cover_key"].startswith("n1/") for r in rows)
    assert {r["section_id"] for r in rows} == {"s01", "s02"}
    assert all(r["bloom_level"] for r in rows)
    # 选项数跟随槽位：选择题 4，非选择题 0（不适用）
    assert {r["type"]: r["option_count"] for r in rows} == {
        "single": 4, "judge": 0, "fill": 0}


def test_plan_path_reports_plan_shortfall_when_plan_over_extends_content(client, monkeypatch):
    """规格超出内容承载（同考点同题型排两次）→ `plan_shortfall` 如实报，但仍可交付。"""
    async def fake_generate(**kwargs):
        return await _fake_blueprint_generate(**kwargs)

    monkeypatch.setattr(quiz_api, "generate_quiz", fake_generate)

    resp = client.post("/api/v1/quiz/generate", json={
        "subject": "二叉树遍历", "question_count": 5, "count_mode": "plan",
        "graph_node_id": "n1",
        "question_plan": [{"type": "single", "count": 3},
                          {"type": "judge", "count": 2}],
    })

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["satisfied"] is True, "题目数量与考点都够 —— 承载缺口不是交付缺口"
    assert data["plan_shortfall"] == 1, "2 个小节撑不住 3 道单选，如实报"


def test_plan_path_strict_failure_persists_nothing(client, store, monkeypatch):
    """strict=True + 凑不齐 → 422，错误信息可断言，**库里一行都没有**。"""
    async def fake_generate(**kwargs):
        bp = kwargs["blueprint"]
        # 规格要 2 单选 + 2 判断 + 1 填空，只出得来 1 道单选
        qs = [{
            "id": "q1", "type": "single",
            "question": "只出得来这一道单选题，题干足够长以便通过质量过滤。",
            "options": [{"value": v, "label": f"选项{v}"} for v in ("A", "B", "C", "D")],
            "answer": ["A"], "cover_key": bp.slots[0].cover_key,
        }]
        return {"questions": qs, "rejected": 0, "materials_used": 0,
                "requested": len(bp.slots)}

    monkeypatch.setattr(quiz_api, "generate_quiz", fake_generate)

    resp = client.post("/api/v1/quiz/generate", json=_plan_payload(strict=True))

    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail.startswith("[E-QUIZ-007]")
    assert "出题未满足规格" in detail
    # 逐型列出"实际/要求"（题型顺序跟随 plan，可稳定断言）
    assert "单选题 1/2" in detail
    assert "判断题 0/2" in detail
    assert "填空题 0/1" in detail
    assert store.stats()["total_questions"] == 0, "strict 失败必须一道都不落库"


def test_plan_path_non_strict_delivers_with_gaps(client, store, monkeypatch):
    """strict=False（默认）→ 仍交付，但如实报告缺口：**题型**差几道 + **哪个考点**没出到。"""
    async def fake_generate(**kwargs):
        bp = kwargs["blueprint"]
        # 只出得动第一道（考点 s01）—— 于是 (a) 三个题型都缺，(b) 考点 s02 一道没有
        qs = [{
            "id": "q1", "type": "single",
            "question": "只出得来这一道单选题，题干足够长以便通过质量过滤。",
            "options": [{"value": v, "label": f"选项{v}"} for v in ("A", "B", "C", "D")],
            "answer": ["A"], "cover_key": bp.slots[0].cover_key,
        }]
        return {"questions": qs, "rejected": 0, "materials_used": 0,
                "requested": len(bp.slots)}

    monkeypatch.setattr(quiz_api, "generate_quiz", fake_generate)

    resp = client.post("/api/v1/quiz/generate", json=_plan_payload(strict=False))

    assert resp.status_code == 200
    data = resp.json()
    assert data["satisfied"] is False
    # 单选要 2 出 1（缺 1）+ 判断 0/2（缺 2）+ 填空 0/1（缺 1）
    assert data["shortfall"] == 4
    assert data["missing_cover_keys"] == ["n1/s02"], "s01 出了题，缺的是 s02"
    assert data["generated"] == 1
    assert store.stats()["total_questions"] == 1, "非 strict 要照常入库"


def test_plan_path_without_graph_node_keeps_llm_knowledge_point(client, store, monkeypatch):
    """有规格但没 graph_node_id → 保留模型填的知识点名（不清空），仍按规格验收。"""
    async def fake_generate(**kwargs):
        bp = kwargs["blueprint"]
        qs = [{
            "id": f"q{i}", "type": "single",
            "question": f"第 {i} 道单选题，题干足够长以便通过质量过滤。",
            "options": [{"value": v, "label": f"选项{v}"} for v in ("A", "B", "C", "D")],
            "answer": ["A"], "knowledge_point": "二叉树遍历",
            "cover_key": bp.slots[i - 1].cover_key,
        } for i in (1, 2)]
        return {"questions": qs, "rejected": 0, "materials_used": 0, "requested": 2}

    monkeypatch.setattr(quiz_api, "generate_quiz", fake_generate)

    resp = client.post("/api/v1/quiz/generate", json={
        "subject": "二叉树遍历", "question_count": 2, "count_mode": "plan",
        "question_plan": [{"type": "single", "count": 2}],
    })

    assert resp.status_code == 200, resp.text
    rows = store.list_questions()
    assert [r["knowledge_point"] for r in rows] == ["二叉树遍历", "二叉树遍历"]
    assert resp.json()["satisfied"] is True


# ── 出题：「补齐缺失」只补缺口，不重出整卷 ──────────────────────

def test_fill_missing_restricts_candidates_to_missing_keys(client, monkeypatch):
    """带 `missing_cover_keys` → 槽位只落在这些考点上（不是把整卷重出一遍）。"""
    seen = {}

    async def fake_generate(**kwargs):
        seen["bp"] = kwargs["blueprint"]
        return await _fake_blueprint_generate(**kwargs)

    monkeypatch.setattr(quiz_api, "generate_quiz", fake_generate)

    resp = client.post("/api/v1/quiz/generate", json={
        "subject": "二叉树遍历", "question_count": 2, "count_mode": "plan",
        "graph_node_id": "n1", "missing_cover_keys": ["n1/s02"],
        "question_plan": [{"type": "single", "count": 2}],
    })

    assert resp.status_code == 200, resp.text
    bp = seen["bp"]
    assert {s.cover_key for s in bp.slots} == {"n1/s02"}, "只补缺的那一个考点"
    assert {s.section_id for s in bp.slots} == {"s02"}
    # 同一考点出两道单选 → 承载缺口如实报，但仍按非 strict 交付
    assert bp.shortfall == 1


def test_fill_missing_falls_back_when_keys_all_stale(client, monkeypatch):
    """key 全失效（图谱改过小节）→ 退回全量候选，不报错。

    用户的「补齐」按钮不该因为前端存了旧 key 就一点即失败 —— 宁可多出几道。
    """
    seen = {}

    async def fake_generate(**kwargs):
        seen["bp"] = kwargs["blueprint"]
        return await _fake_blueprint_generate(**kwargs)

    monkeypatch.setattr(quiz_api, "generate_quiz", fake_generate)

    resp = client.post("/api/v1/quiz/generate", json={
        "subject": "二叉树遍历", "question_count": 1, "count_mode": "plan",
        "graph_node_id": "n1", "missing_cover_keys": ["n1/s99"],
        "question_plan": [{"type": "single", "count": 1}],
    })

    assert resp.status_code == 200, resp.text
    bp = seen["bp"]
    assert bp.slots[0].cover_key.startswith("n1/"), "退回全量候选，仍是本节点的考点"
    assert bp.slots[0].cover_key != "n1/s99"


# ── 生成器：蓝图路径（提示词写死槽位 + 补题只补空槽位）────────────

def _slot_bp(slots, node="n1"):
    from app.core.quiz.blueprint import Blueprint

    return Blueprint(slots=list(slots), candidates=[], shortfall=0, reason="test")


def _slot(index, qtype="single", cover_key="n1/s01", label="前序遍历"):
    from app.core.quiz.blueprint import Slot

    return Slot(index=index, cover_key=cover_key, cover_label=label, type=qtype,
                points=10, bloom="remember", option_count=4 if qtype == "single" else 0,
                difficulty="medium", section_id=cover_key.split("/")[-1])


def _llm_item(index, qtype="single", **kw):
    """一道能过全部校验的候选题（判断题不带选项，单选带 4 个）。"""
    item = {
        "id": f"q{index}", "type": qtype,
        "question": f"第 {index} 道题：关于遍历顺序的说法哪一项足够严谨？",
        "answer": ["A"] if qtype == "single" else ["对"],
    }
    if qtype in ("single", "multiple"):
        item["options"] = [{"label": v, "value": v} for v in ("A", "B", "C", "D")]
    if qtype == "fill":
        item["answer"] = ["前序"]
    if qtype == "short_answer":
        item["answer"] = []
        item["analysis"] = "参考要点：根左右。"
    item.update(kw)
    return item


@pytest.fixture()
def llm_stub(monkeypatch):
    """记录每次出题提示词，并按 `responder(prompt, call_index)` 返回候选 JSON。"""
    from app.core.quiz import generator

    calls: list[str] = []
    responder = {"fn": lambda prompt, i: "[]"}

    class _FakeKb:
        def collect_files(self, uid, nid):
            return []

        async def search(self, uid, query, node_ids=None, top_k=8):
            return [{"content": "前序遍历：根 → 左 → 右。"}]

    async def fake_call_llm(system, messages, max_tokens=None, **kw):
        prompt = messages[0]["content"]
        calls.append(prompt)
        return responder["fn"](prompt, len(calls) - 1)

    monkeypatch.setattr(generator, "kb_manager", _FakeKb())
    monkeypatch.setattr(generator, "call_llm", fake_call_llm)
    return {"calls": calls, "responder": responder}


def test_generator_blueprint_prompt_names_every_slot(llm_stub):
    """提示词必须逐槽写死考点/题型/选项数/认知层次/分值 —— 这是"模型不知道考什么"的解药。"""
    from app.core.quiz.generator import generate_quiz

    bp = _slot_bp([_slot(1), _slot(2, cover_key="n1/s02", label="中序遍历")])
    llm_stub["responder"]["fn"] = lambda prompt, i: json.dumps(
        [_llm_item(1, cover_key="n1/s01"), _llm_item(2, cover_key="n1/s02")])

    result = _run_generate(generate_quiz, bp)

    prompt = llm_stub["calls"][0]
    assert "n1/s01" in prompt and "n1/s02" in prompt, "考点键要原样进提示词"
    assert "前序遍历" in prompt and "中序遍历" in prompt, "考点名称要让模型看懂"
    assert "单选题" in prompt and "识记" in prompt, "题型与认知层次都要写成人话"
    assert "| 4 |" in prompt or "|4|" in prompt, "选项数要进表格"
    assert "cover_key" in prompt, "必须要求模型带回 cover_key"
    assert len(result["questions"]) == 2


def test_generator_blueprint_attaches_slot_fields(llm_stub):
    """产出题带齐槽位信息（`Question` 不动，额外键追加在 model_dump 之后）。"""
    from app.core.quiz.generator import generate_quiz

    bp = _slot_bp([_slot(1)])
    llm_stub["responder"]["fn"] = lambda prompt, i: json.dumps(
        [_llm_item(1, cover_key="n1/s01")])

    got = _run_generate(generate_quiz, bp)["questions"][0]

    assert got["cover_key"] == "n1/s01"
    assert got["section_id"] == "s01"
    assert got["bloom_level"] == "remember"
    assert got["option_count"] == 4
    assert json.loads(got["plan_json"])["cover_key"] == "n1/s01"
    assert got["points"] == 10, "分值以槽位为准"


def test_generator_blueprint_topup_retries_only_empty_slots(llm_stub):
    """首轮漏掉一个槽位 → 补题提示词只针对那个槽位，且带"补题"字样与禁止重复。"""
    from app.core.quiz.generator import generate_quiz

    bp = _slot_bp([_slot(1), _slot(2, cover_key="n1/s02", label="中序遍历")])
    llm_stub["responder"]["fn"] = lambda prompt, i: json.dumps(
        [_llm_item(1, cover_key="n1/s01")] if i == 0
        else [_llm_item(2, cover_key="n1/s02")])

    result = _run_generate(generate_quiz, bp)

    assert len(llm_stub["calls"]) == 2, "首轮 1 批 + 补题 1 批"
    topup_prompt = llm_stub["calls"][1]
    assert "**补题**" in topup_prompt
    assert "禁止重复" in topup_prompt
    assert "n1/s02" in topup_prompt, "补题只补没产出的那个槽位"
    assert "n1/s01" not in topup_prompt, "已产出的槽位不该再要一次"
    assert len(result["questions"]) == 2


def test_generator_blueprint_stops_when_no_progress(llm_stub):
    """补题一轮没有任何推进 → 立刻停（不死循环、不无限烧钱）。"""
    from app.core.quiz.generator import generate_quiz

    bp = _slot_bp([_slot(1), _slot(2, cover_key="n1/s02", label="中序遍历")])
    llm_stub["responder"]["fn"] = lambda prompt, i: json.dumps(
        [_llm_item(1, cover_key="n1/s01")])

    result = _run_generate(generate_quiz, bp)

    assert len(llm_stub["calls"]) == 2, "首轮 1 批 + 一轮无进展的补题，然后停"
    assert len(result["questions"]) == 1
    assert result["requested"] == 2


def test_generator_blueprint_no_topup_round_cap(llm_stub):
    """补题**不受** `QUIZ_MAX_TOPUP_ROUNDS` 限制：要 3 轮才补齐也能成功。

    4 个槽位、每轮只补出 1 个 → 首轮 + 3 轮补题。老路径的常量是 2 轮，
    它挡不住这条路径（那正是删掉固定轮数的理由）。
    """
    from app.core.quiz import generator
    from app.core.quiz.generator import generate_quiz

    assert generator.QUIZ_MAX_TOPUP_ROUNDS == 2        # 老路径的常量没被改
    bp = _slot_bp([_slot(i, cover_key=f"n1/s0{i}") for i in (1, 2, 3, 4)])
    # 每次调用只出「第 call+1 个」槽位，于是每轮只推进一个。
    # 题干里带上 cover_key：`_deduplicate` 按归一化题干去重，各轮题干必须互不相同，
    # 否则补题产出的题会被当成"与已有题目重复"而白补一轮。
    llm_stub["responder"]["fn"] = lambda prompt, i: json.dumps(
        [_llm_item(i + 1, cover_key=f"n1/s0{i + 1}",
                   question=f"关于考点 n1/s0{i + 1} 的说法哪一项足够严谨？")])

    result = _run_generate(generate_quiz, bp)

    assert len(result["questions"]) == 4, "3 轮补题后凑齐（老路径 2 轮上限挡不住它）"
    assert len(llm_stub["calls"]) == 4, "首轮 1 批 + 3 轮补题"


_ROW_RE = re.compile(r"\| \d+ \| (n1/s\d+) \| [^|]+ \| (\w+)（")


def test_generator_blueprint_groups_batches_by_type(llm_stub):
    """槽位按题型分组、每组再按 QUIZ_BATCH_SIZE 切 —— 提示词里题型不会自相矛盾。"""
    from app.core.quiz.generator import generate_quiz

    # 5 个单选（→ 2 批：4+1）+ 2 个判断（→ 1 批）= 3 批
    slots = [_slot(i + 1, cover_key=f"n1/s0{i + 1}") for i in range(5)]
    slots += [_slot(6, qtype="judge", cover_key="n1/s01"),
              _slot(7, qtype="judge", cover_key="n1/s02")]

    def responder(prompt, i):
        # 把提示词表格里的槽位**全部**产出：首轮就该补齐，不会进补题循环，
        # 于是 `calls` 恰好等于批数，可以拿它反推切批结果。
        # 题干带上 cover_key + 题型：`_deduplicate` 按归一化题干去重，跨批同名会被当重复。
        rows = _ROW_RE.findall(prompt)
        return json.dumps([_llm_item(j + 1, qtype=t, cover_key=k,
                                     question=f"考点 {k} 的{t}题：下列说法哪一项严谨？")
                           for j, (k, t) in enumerate(rows)])

    llm_stub["responder"]["fn"] = responder

    _run_generate(generate_quiz, _slot_bp(slots))

    assert len(llm_stub["calls"]) == 3, "5 单选 → 2 批，2 判断 → 1 批"
    assert "本批只需出 4 道" in llm_stub["calls"][0]
    assert "本批只需出 1 道" in llm_stub["calls"][1]
    assert "本批只需出 2 道" in llm_stub["calls"][2]
    # 每批题型单一（这是分组的全部意义：提示词里选项数/答案形态不自相矛盾）
    for call in llm_stub["calls"]:
        types = {t for _k, t in _ROW_RE.findall(call)}
        assert len(types) == 1, f"同一批混了多种题型: {types}"
    for prompt in llm_stub["calls"]:
        types = {"单选题" in prompt, "判断题" in prompt}
        assert len(types) == 1 or not all(types), "同一批不该混两种题型说明"


def test_generator_without_blueprint_unchanged(llm_stub):
    """`blueprint=None` → 提示词还是老样子（"请聚焦不同考点"），产出不带槽位字段。"""
    from app.core.quiz.generator import generate_quiz

    llm_stub["responder"]["fn"] = lambda prompt, i: json.dumps([_llm_item(1)])

    result = _run_generate(generate_quiz, None, count=1)

    prompt = llm_stub["calls"][0]
    assert "请聚焦不同考点" in prompt
    assert "cover_key" not in prompt
    assert "槽位" not in prompt
    assert result["questions"][0]["id"] == "q1"
    assert "cover_key" not in result["questions"][0], "老路径不能多出字段（落库行为会变）"


def test_generator_blueprint_validates_against_slot_option_count(llm_stub):
    """槽位要 3 个选项 → 4 选项的题被判不合格（退回未产出），不被静默接受。"""
    from app.core.quiz.blueprint import Slot
    from app.core.quiz.generator import generate_quiz

    slot = Slot(index=1, cover_key="n1/s01", cover_label="前序遍历", type="single",
                points=10, bloom="remember", option_count=3, difficulty="medium",
                section_id="s01")
    # 3 选项才是合格品；补题时给合格的
    llm_stub["responder"]["fn"] = lambda prompt, i: json.dumps(
        [_llm_item(1, cover_key="n1/s01")] if i == 0
        else [{"id": "q1", "type": "single",
               "question": "三个选项的单选题：前序遍历的第一个节点是？",
               "options": [{"label": v, "value": v} for v in ("A", "B", "C")],
               "answer": ["A"], "cover_key": "n1/s01"}])

    result = _run_generate(generate_quiz, _slot_bp([slot]))

    assert len(result["questions"]) == 1
    assert len(result["questions"][0]["options"]) == 3
    assert result["questions"][0]["option_count"] == 3
    assert result["rejected"] >= 1, "4 选项那份要被记成不合格"
