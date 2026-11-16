"""
出题 API 路由：基于 RAG 教材知识库的可靠出题 + 判分

接口：
  POST   /quiz/generate              - 生成一份测验（RAG 依据检索 + LLM 出题 + 质量过滤）
  GET    /quiz/questions             - 列出题库中的题目
  GET    /quiz/questions/{id}        - 获取单题详情
  POST   /quiz/{id}/grade            - 判分（客观题规则判分 / 简答题 LLM 判分）
  GET    /quiz/stats                 - 题库统计
  GET    /quiz/batches               - 出题历史（题集）列表
  GET    /quiz/batches/{id}/questions- 某次出题的题目

说明：
- 需要 JWT 认证，按用户隔离
- 出题依据优先从用户上传的教材知识库（含例题/真题）混合检索
"""
import logging
import re
from collections import Counter
from datetime import datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from typing import List, Literal, Optional

from app.core.auth import get_current_user
from app.core.error_codes import ErrorCode, log_error
from app.core.knowledge_graph import KnowledgeGraph
from app.core.quiz import planning
from app.core.quiz.blueprint import allocate
from app.core.quiz.budget import compute_budget
from app.core.quiz.chat_grade import (
    apply_mastery_after_answer, apply_section_after_answer, resolve_node_id,
)
from app.core.quiz.schema import (
    PlanPreviewRequest, QuizGenerateRequest, QuizGradeRequest,
)
from app.core.quiz.generator import generate_quiz
from app.core.quiz.grader import grade_question
from app.core.quiz.quiz_store import quiz_manager
from app.core.quiz.exporter import TYPE_LABELS, export_quiz as build_export

logger = logging.getLogger("ai-tutor")
router = APIRouter()


def _touch_graph(user_id: int, *, node_id: str, section_id: str,
                 question_id: int, subject: str = "",
                 correct: Optional[bool] = None) -> dict:
    """
    判分后回写掌握度；`correct=None` 时只**读**当前值、不写（幂等重复提交用）。

    为什么需要回写（2026-11-15）：对话内出题答对会 `add_mastery(+20)`
    （`chat_grade.grade_pending`），但独立出题页原先只写 `attempts`、完全不碰图谱，
    学生判完分回图谱看节点颜色一点不变 —— 反馈闭环断裂。

    口径与对话内完全一致：
      · 题带 `section_id` → 小节口径（答对置本节通过；全部小节通过才置 100）
      · 否则 → 节点口径（答对 +20，答错不变、不扣分）

    ⚠️ `node_id` 不一定真的是节点 id：独立出题页的 `knowledge_point` 是 LLM 编的
    中文知识点名（实测 44/47 命中不了节点）。解析交给 `chat_grade.resolve_node_id`
    （节点 id → 节点名 → subject），解析不到就**如实返回空 dict**，不假装成功。

    为什么 `correct=None` 必须走同一函数：幂等的语义是"这次什么都没发生"，
    重复提交不能再 `add_mastery`（实测刷新几次就把掌握度刷满），但响应里仍要
    给出与首次一致的展示字段。

    图谱写/读失败**绝不影响判分结果**（学生已经答完，分数必须返回）。
    """
    out: dict = {}
    kg = None
    try:
        kg = KnowledgeGraph(user_id=user_id)
        resolved = resolve_node_id(kg, node_id, subject)
        if not resolved:
            return out
        out["node_id"] = resolved
        if correct is None:                     # ← 只读
            if section_id:
                passed = bool((kg.get_section_learn(resolved, section_id) or {}).get("passed"))
                out.update({"section_id": section_id,
                            "next_action": "passed" if passed else "none"})
            else:
                out["mastery"] = int((kg.get_node(resolved) or {}).get("mastery") or 0)
            return out
        evidence = f"quiz:{question_id}"
        if section_id:
            r = apply_section_after_answer(kg, node_id=resolved, section_id=section_id,
                                           correct=correct, evidence=evidence)
            out.update({
                "section_id": section_id,
                "next_action": r.get("action", "none"),
                "section_note": r.get("note", ""),
            })
        else:
            note = apply_mastery_after_answer(kg, resolved, correct, evidence=evidence)
            out.update({"mastery_note": note,
                        "mastery": int((kg.get_node(resolved) or {}).get("mastery") or 0)})
    except Exception as e:                      # 图谱坏了也不该吞掉分数
        logger.warning(f"判分回写图谱失败（{node_id}）: {e}")
        out.pop("node_id", None)
        out.setdefault("mastery_note", "")
    finally:
        if kg is not None:
            try:
                kg.close()
            except Exception:
                pass
    return out


def _planned_by_cover_key(bp, cands, count: int) -> dict[str, int]:
    """每个考点计划出几道（预览用）。

    有蓝图（用户给了 `question_plan`）→ 直接数槽位，蓝图就是最终规格；
    没有蓝图（`count_mode=auto/manual`，题型配比还没定）→ 按 `allocate` 的轮转口径
    **估算**：`cands[i % len(cands)]`，即均摊、余数给前面的考点。

    两者一致不是巧合：`allocate` 里的 cursor 每产出一个槽位就 +1、与题型无关，
    所以「count 道题摊到候选考点上的分布」只取决于 count 与候选顺序。
    这也是为什么这里能用算术代替真的分配 —— 不必为一个估算去造一份假 plan。
    """
    if bp is not None and bp.slots:
        return dict(Counter(s.cover_key for s in bp.slots))
    n = len(cands or [])
    if n <= 0:
        return {}
    base, rest = divmod(max(0, int(count)), n)
    return {c.cover_key: base + (1 if i < rest else 0)
            for i, c in enumerate(cands)}


@router.post("/quiz/plan/preview")
async def preview_plan(req: PlanPreviewRequest,
                       user_id: int = Depends(get_current_user)):
    """题量预览：**纯计算，不落库、不调 LLM、不发通知**。

    前端「自动」模式一打开出题页就打一次，用来回答"为什么是 N 道"：
    `count` + `reason` 直接展示给用户，`sections` 逐节给出正文长度与计划题量。
    用户不信任一个不解释的数字 —— 这正是旧的 `[3,5,8,10]` 下拉的问题。

    为什么必须是纯计算：它会随"选知识点 / 改模式 / 改题量"被反复调用，
    一旦掺进 LLM 或写库就变成"点一下扣一次钱"；也因为它什么都没写，
    除了鉴权失败以外的任何情况都不该 500 —— 读不到图谱就按"无锚点"回答。

    返回:
        {
          "count": int,             # 计划题量（auto→内容预算 / manual→question_count / plan→合计）
          "reason": str,            # 一句可展示的依据
          "count_mode": "auto" | "manual" | "plan",
          "capped": bool,           # auto 是否撞了 QUIZ_MAX_QUESTIONS
          "section_count": int,     # 参与计算的考点数（无锚点 = 0）
          "content_chars": int,
          "sections": [{section_id, title, chars, planned}],
          "slot_preview": [Slot.dict(), ...]   # 无规格时为空
        }
    """
    plan = list(req.question_plan or [])
    # 一次读出候选考点 + 预算 + 蓝图。`graph_node_id` 是否属于当前用户由
    # `planning.load_candidates` 内部经 `kg.get_node()`（带 user_id 护栏）判定。
    bp, budget, cands = planning.plan_for_node(
        user_id=user_id, graph_node_id=req.graph_node_id, plan=plan or None,
    )
    anchored = cands is not None
    if budget is None:
        # 无锚点（没给节点 / 节点不存在 / 非本用户）三种情况**响应必须完全一致** ——
        # 否则这个端点就成了"别人的图谱里有没有这个节点"的探测器。
        budget = compute_budget(sections=None, content_chars=None)

    if req.count_mode == "manual":
        count = int(req.question_count or budget.count)
    elif req.count_mode == "plan":
        count = sum(r.count for r in plan) or budget.count
    else:
        count = budget.count

    sections: list[dict] = []
    if anchored:
        # 只有 count_mode=plan 才用蓝图里的槽位：另两种模式的题量由 auto/manual 决定，
        # 蓝图里那份 `question_plan` 与 count 未必对得上。用算术估算保证
        # `sum(planned) == count` 这个恒等式在三种模式下都成立（前端可直接对账）。
        planned = _planned_by_cover_key(bp if req.count_mode == "plan" else None,
                                        cands, count)
        sections = [{"section_id": c.section_id, "title": c.label,
                     "chars": c.chars, "planned": planned.get(c.cover_key, 0)}
                    for c in cands]

    return {
        "count": count,
        "reason": budget.reason,
        "count_mode": req.count_mode,
        "capped": budget.capped,
        "section_count": len(sections),
        "content_chars": budget.content_chars,
        "sections": sections,
        "slot_preview": (planning.blueprint_to_dict(bp) or {}).get("slots", []),
    }


def _plan_verdict(bp, questions: list[dict], plan: list) -> dict:
    """把「蓝图规格 vs 实际产出」对齐，给出可断言的判定。

    · `satisfied`：每个题型的数量都够 **且** 规格里的每个考点都出了题
    · `shortfall`：按题型逐行累加 `max(0, 要求-实际)` —— "还差几道"
    · `missing_cover_keys`：规格里要求、但一道题都没出的考点（可直接展示给学生）
    · `plan_shortfall`：蓝图自身的承载缺口（候选考点撑不住这个题量，来自 `allocate`）

    为什么题型与考点要**同时**判定：只比题型会漏掉"5 道全押在同一个考点"（旧病），
    只比考点会漏掉"简答题一道没出"。两个都中才算满足规格。
    """
    want: dict[str, int] = {}
    for row in plan:
        want[row.type] = want.get(row.type, 0) + row.count
    got = Counter(str(q.get("type") or "") for q in questions)

    rows = [(t, int(got.get(t, 0)), n) for t, n in want.items()]
    shortfall = sum(max(0, n - g) for _, g, n in rows)

    covered = {str(q.get("cover_key") or "") for q in questions}
    missing = [k for k in bp.cover_keys() if k not in covered]

    return {
        "satisfied": shortfall == 0 and not missing,
        "shortfall": shortfall,
        "missing_cover_keys": missing,
        "plan_shortfall": int(bp.shortfall or 0),
        # 稳定可断言的一条："单选题 3/5、判断题 2/2"（题型顺序跟随 plan）
        "detail": "、".join(f"{TYPE_LABELS.get(t, t)} {g}/{n}" for t, g, n in rows),
    }


@router.post("/quiz/generate")
async def create_quiz(req: QuizGenerateRequest,
                      user_id: int = Depends(get_current_user)):
    """
    生成一份测验。

    流程：
    1. 从上传教材知识库混合检索出题依据片段（subject 作为查询词）
    2. 分批调用 LLM 基于依据出题（RAG 约束，禁止生成材料外内容），
       单批不超 QUIZ_BATCH_SIZE 道，避免思考型模型撞 max_tokens 截断
    3. 质量过滤（长度/自包含/去重）+ Pydantic 结构校验；数量不足时自动补题
    4. 题目入库（quiz.questions 表），返回题目列表

    **两条生成路径**（`question_plan` 是否为空决定）：
      · 有 `question_plan` → 先按图谱小节摊成蓝图槽位，再按槽位出题；
        出完了逐行核对，`strict=True` 且凑不齐时一道都不落库、抛 422。
      · 无 `question_plan`（老路径）→ 与改动前**逐字节等价**：
        `question_types` 原样传给生成器自由发挥，响应里也不加新字段。

    返回（老路径）:
        {
          "status": "ok",
          "question_ids": [int, ...],
          "questions": [Question.dict(), ...],
          "rejected": int,           # 首轮候选题中被过滤掉的题数
          "materials_used": int,     # 使用的参考片段数
          "requested": int,          # 请求的题数
          "generated": int           # 实际生成并入库的题数
        }
    返回（新路径，另加上面这些沿用字段）:
        {
          "satisfied": bool,             # 规格是否被完全满足
          "shortfall": int,              # 还差几道（按题型逐行累加）
          "missing_cover_keys": [...],   # 哪个考点一道题都没出
          "plan_shortfall": int          # 蓝图层面的承载缺口
        }
    """
    plan = req.effective_plan
    bp = None
    if plan:
        # 蓝图：把 question_plan 摊到该节点的小节上（考什么 → 一题一考点）
        bp, _budget, cands = planning.plan_for_node(
            user_id=user_id, graph_node_id=req.graph_node_id,
            plan=plan, difficulty=req.difficulty,
            # 「补齐缺失」：只摊到上次没铺到的考点上，不重出整卷
            only_cover_keys=req.missing_cover_keys or None,
        )
        if bp is None:
            # 无锚点（没传 graph_node_id / 该节点不属于本用户）：题型与分值规格照样要遵守，
            # 只是所有槽位落在同一个匿名考点上（cover_key = "no-anchor/#body"）。
            # 注意此处**不**泄露节点是否存在 —— 与"根本没传"走同一条路。
            bp = allocate(node_id=req.graph_node_id or "no-anchor", plan=plan,
                          candidates=[], difficulty=req.difficulty)

    kwargs = dict(
        user_id=user_id,
        subject=req.subject,
        node_id=req.node_id,
        question_count=req.question_count,
        difficulty=req.difficulty,
        question_types=req.question_types,
    )
    if bp is not None:
        kwargs["blueprint"] = bp
    try:
        result = await generate_quiz(**kwargs)
    except ValueError as e:
        log_error(ErrorCode.QUIZ_PARSE_ERROR, detail=str(e), context={"user_id": user_id})
        raise HTTPException(status_code=422, detail=ErrorCode.user_message(ErrorCode.QUIZ_PARSE_ERROR))
    except Exception as e:
        log_error(ErrorCode.QUIZ_GENERATE_FAILED, detail=str(e), context={"user_id": user_id}, exception=e)
        raise HTTPException(status_code=500, detail=ErrorCode.user_message(ErrorCode.QUIZ_GENERATE_FAILED))

    # ── 蓝图路径：先核规，再决定落不落库 ──────────────────────────
    verdict = _plan_verdict(bp, result["questions"], plan) if bp is not None else None
    if verdict is not None and req.strict and not verdict["satisfied"]:
        # strict：一道都不落库。**必须在 save_questions 之前**抛 ——
        # 先落库再删会留下"秒出秒删"的空题集，而且删不干净（attempts 可能已挂上）。
        log_error(ErrorCode.QUIZ_PLAN_SHORTFALL,
                  detail=f"{verdict['detail']}；缺考点 {len(verdict['missing_cover_keys'])} 个",
                  context={"user_id": user_id, "node": req.graph_node_id})
        raise HTTPException(
            status_code=422,
            detail=f"{ErrorCode.user_message(ErrorCode.QUIZ_PLAN_SHORTFALL)}："
                   f"{verdict['detail']}")

    # 题目入库
    store = quiz_manager._get_store(user_id)
    # 从节点详情进来时把图谱节点 id 写进 knowledge_point —— 判分要靠它回写掌握度。
    # 与对话内出题同一做法（chat_quiz.py：knowledge_point 是字符串字段，正好装图谱节点 id；
    # questions.node_id 是 INTEGER，装不下）。
    #
    # ⚠️ 这行不是可有可无的：`inspect_quiz_quality.py --user 5` 实测 20 个不同的
    # knowledge_point 里 10 个是 LLM 现编的中文短语（"二叉树中序遍历规则"），
    # `chat_grade.resolve_node_id` 两路都命中不了 → 判分不回写、答对也不涨掌握度。
    source_docs: list[dict] = []
    if req.graph_node_id:
        for q in result["questions"]:
            q["knowledge_point"] = req.graph_node_id
        # 题目血统：这份题依据的是该节点名下的哪几份**文件**。
        # 直接复用 chat_quiz 的实现（同一份口径：节点 sources），别在这里再写一遍。
        # 读不到（老节点无溯源 / 图谱写坏）→ 空列表，不影响入库。
        try:
            kg = KnowledgeGraph(user_id=user_id)
            try:
                from app.core.quiz.chat_quiz import _source_docs_for
                source_docs = _source_docs_for(kg, req.graph_node_id, "")
            finally:
                kg.close()
        except Exception as e:                  # noqa: BLE001 —— 血统是附加信息
            logger.warning(f"出题取来源文件失败（忽略）: {e}")
    elif bp is not None:
        # 有规格但没图谱锚点：**保留**模型填的知识点名（清空等于把已有的弱关联也砍掉），
        # 但要留一条日志 —— 这些题的判分回写不了掌握度，排查时要能立刻看到。
        logger.warning(
            f"用户 {user_id} 按规格出题但未传 graph_node_id："
            f"该批 {len(result['questions'])} 道题无图谱锚点，判分不会回写掌握度")
    question_ids = store.save_questions(
        result["questions"],
        subject=req.subject,
        node_id=req.node_id,
        difficulty=req.difficulty,
        source_docs=source_docs,
    )
    resp = {
        "status": "ok",
        "question_ids": question_ids,
        "questions": result["questions"],
        "rejected": result["rejected"],
        "materials_used": result["materials_used"],
        # 分批出题后可能因截断/重复凑不满，前端可据此提示"请求 N 道 / 实际 M 道"
        "requested": result.get("requested", len(result["questions"])),
        "generated": len(result["questions"]),
    }
    if verdict is not None:
        # 新路径才加这四个字段 —— 老路径的响应结构与改动前逐字节一致
        resp.update({
            "satisfied": verdict["satisfied"],
            "shortfall": verdict["shortfall"],
            "missing_cover_keys": verdict["missing_cover_keys"],
            "plan_shortfall": verdict["plan_shortfall"],
        })
    return resp


@router.get("/quiz/questions")
async def list_questions(
    subject: str = Query("", description="按主题筛选"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user_id: int = Depends(get_current_user),
):
    """列出题库中的题目"""
    store = quiz_manager._get_store(user_id)
    questions = store.list_questions(subject, limit, offset)
    return {"questions": questions}


@router.get("/quiz/questions/{question_id}")
async def get_question(question_id: int,
                       user_id: int = Depends(get_current_user)):
    """获取单题详情"""
    store = quiz_manager._get_store(user_id)
    q = store.get_question(question_id)
    if not q:
        raise HTTPException(status_code=404, detail=ErrorCode.user_message(ErrorCode.QUIZ_NOT_FOUND))
    return q


@router.post("/quiz/{question_id}/grade")
async def grade(question_id: int,
                user_answer: str = Query(..., min_length=1, description="用户作答"),
                user_id: int = Depends(get_current_user)):
    """
    对库中已保存的题目判分。

    user_answer 通过 query 传入。客观题规则判分，简答题 LLM 判分。
    判分结果写入 attempts 表；题目关联了图谱节点时**顺带回写掌握度**
    （响应里的 `node_id` / `mastery` / `mastery_note` 或 `section_id` / `next_action`）。

    **幂等**（2026-11-15）：同一道题 + 同一个答案重复提交 → 返回上次结果并置
    `idempotent=true`，**不再记一次 attempt、也不再加掌握度**。前端刷新 / 学生手滑
    重复点 / 重放都会触发，不拦的话掌握度会被白送上去（实测 q2 被判对两次 = +40）。

    返回:
        {"status": "ok", "score": int, "max_score": int, "correct": bool,
         "comment": str, "detail": str, "analysis": str,
         "node_id": str, "mastery": int, "mastery_note": str,   # 仅关联图谱时
         "idempotent": true}                                    # 仅重复提交时
    """
    store = quiz_manager._get_store(user_id)
    q = store.get_question(question_id)
    if not q:
        raise HTTPException(status_code=404, detail=ErrorCode.user_message(ErrorCode.QUIZ_NOT_FOUND))

    # 幂等：同题同答案已判过 → 直接回放，不重复记账
    previous = store.find_attempt(question_id, user_answer)
    if previous is not None:
        resp = {
            "status": "ok",
            "score": previous["score"],
            "max_score": previous["max_score"],
            "correct": bool(previous["correct"]),
            "comment": previous["comment"],
            "detail": "",
            "analysis": q["analysis"],
            "idempotent": True,
        }
        node_id = q.get("knowledge_point") or ""
        if node_id:
            # 只读当前值回报，**不再回写**（幂等的意义就是"这次什么都没发生"）
            resp.update(_touch_graph(
                user_id, node_id=node_id, section_id=q.get("section_id") or "",
                question_id=question_id, subject=q.get("subject") or ""))
        return resp

    req = QuizGradeRequest(
        question_id=question_id,
        question=q["question"],
        type=q["type"],
        user_answer=user_answer,
        answer=q["answer"],
        points=q["points"],
        analysis=q["analysis"],
        comment_prompt=q["comment_prompt"],
    )
    try:
        result = await grade_question(req)
    except Exception as e:
        log_error(ErrorCode.QUIZ_GRADE_FAILED, detail=str(e), context={"question_id": question_id}, exception=e)
        raise HTTPException(status_code=500, detail=ErrorCode.user_message(ErrorCode.QUIZ_GRADE_FAILED))

    # 记录作答
    store.record_attempt(
        question_id=question_id,
        user_answer=user_answer,
        score=result["score"],
        max_score=result["max_score"],
        correct=result["correct"],
        comment=result["comment"],
    )

    resp = {
        "status": "ok",
        **result,
        "analysis": q["analysis"],
    }
    # 回写图谱（老题没有 knowledge_point → 跳过，行为与改动前一致）
    node_id = q.get("knowledge_point") or ""
    if node_id:
        resp.update(_touch_graph(
            user_id, node_id=node_id, section_id=q.get("section_id") or "",
            question_id=question_id, subject=q.get("subject") or "",
            correct=result["correct"]))
    return resp


@router.get("/quiz/batches")
async def list_batches(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user_id: int = Depends(get_current_user),
):
    """
    列出出题历史（题集），最新出的在前。

    一次出题 = 一个题集（`save_questions` 一次调用）。老库里没有批次标记的历史题
    各成一集，仍在列表中可见。返回:
        {"batches": [{"batch_id","subject","question_count","created_at",...}],
         "total": int}
    """
    store = quiz_manager._get_store(user_id)
    return {
        "batches": store.list_batches(limit=limit, offset=offset),
        "total": store.count_batches(),
    }


@router.get("/quiz/batches/{batch_id}/questions")
async def get_batch_questions(
    batch_id: str,
    user_id: int = Depends(get_current_user),
):
    """取某次出题（题集）的全部题目，供历史记录回看 / 判分 / 导出。找不到 → 404。"""
    store = quiz_manager._get_store(user_id)
    questions = store.list_by_batch(batch_id)
    if not questions:
        raise HTTPException(status_code=404,
                            detail=ErrorCode.user_message(ErrorCode.QUIZ_NOT_FOUND))
    return {"batch_id": batch_id, "questions": questions}


@router.get("/quiz/stats")
async def quiz_stats(user_id: int = Depends(get_current_user)):
    """题库统计"""
    store = quiz_manager._get_store(user_id)
    return store.stats()


@router.get("/quiz/export")
async def export_quiz(
    format: str = Query("docx", pattern="^(docx|pdf|md|tsv)$",
                       description="导出格式：docx（Word）/ pdf / md / tsv（Anki 卡片）"),
    subject: str = Query("", description="按主题导出；空=导出全部题库"),
    ids: str = Query("", description="指定题目 id（逗号分隔），优先于 subject"),
    limit: int = Query(100, ge=1, le=500, description="最多导出题目数"),
    with_answer: bool = Query(True, description="是否包含答案与解析（false=学生卷）"),
    user_id: int = Depends(get_current_user),
):
    """
    导出试卷为 Word / PDF / Markdown / Anki 卡片，直接浏览器下载。

    - ids 非空：按给定题目 id 顺序导出（刚生成的那套题传它的 question_ids）
    - 否则：按 subject 筛选题库导出（subject 空=全部题库，按出题先后排序）
    - with_answer=false 可导出不带答案的「学生卷」
    - format=tsv 导出 Anki 卡片：**始终包含答案**（卡片无答案即无复习价值）
    """
    store = quiz_manager._get_store(user_id)

    questions: list[dict] = []
    if ids.strip():
        for raw in ids.split(","):
            raw = raw.strip()
            if not raw.lstrip("-").isdigit():
                continue
            q = store.get_question(int(raw))
            if q:
                questions.append(q)
    else:
        # 题库按 id DESC 返回（最新在前）→ 反转为出题先后顺序
        questions = list(reversed(store.list_questions(subject, limit, 0)))

    if not questions:
        raise HTTPException(status_code=404, detail="没有可导出的题目")

    difficulty = questions[0].get("difficulty", "") or ""
    try:
        payload, media_type, ext = build_export(
            questions, fmt=format, subject=subject,
            difficulty=difficulty, with_answer=with_answer,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if isinstance(payload, str):
        payload = payload.encode("utf-8")

    # 文件名：去非法字符 + 截断。学科名是中文 → HTTP 头只能 latin-1，
    # 必须用 RFC 5987 的 filename* 百分号编码，否则构造响应时就抛 UnicodeEncodeError
    stem = re.sub(r'[\\/:*?"<>|\s]+', "_", (subject or "AI试题").strip())[:40] or "AI试题"
    # Anki 卡片强制带答案 → 文件名也不能标「学生卷」，否则与实际内容不符
    suffix = "_试题" if (with_answer or format == "tsv") else "_学生卷"
    filename = f"{stem}{suffix}_{datetime.now().strftime('%Y%m%d_%H%M')}.{ext}"

    return Response(
        content=payload,
        media_type=media_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
            "Access-Control-Expose-Headers": "Content-Disposition",
        },
    )
