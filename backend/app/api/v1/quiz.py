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
from datetime import datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from typing import List, Literal, Optional

from app.core.auth import get_current_user
from app.core.error_codes import ErrorCode, log_error
from app.core.knowledge_graph import KnowledgeGraph
from app.core.quiz.chat_grade import (
    apply_mastery_after_answer, apply_section_after_answer, resolve_node_id,
)
from app.core.quiz.schema import QuizGenerateRequest, QuizGradeRequest
from app.core.quiz.generator import generate_quiz
from app.core.quiz.grader import grade_question
from app.core.quiz.quiz_store import quiz_manager
from app.core.quiz.exporter import export_quiz as build_export

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

    返回:
        {
          "status": "ok",
          "question_ids": [int, ...],
          "questions": [Question.dict(), ...],
          "rejected": int,           # 首轮候选题中被过滤掉的题数
          "materials_used": int,     # 使用的参考片段数
          "requested": int,          # 请求的题数
          "generated": int           # 实际生成并入库的题数
        }
    """
    try:
        result = await generate_quiz(
            user_id=user_id,
            subject=req.subject,
            node_id=req.node_id,
            question_count=req.question_count,
            difficulty=req.difficulty,
            question_types=req.question_types,
        )
    except ValueError as e:
        log_error(ErrorCode.QUIZ_PARSE_ERROR, detail=str(e), context={"user_id": user_id})
        raise HTTPException(status_code=422, detail=ErrorCode.user_message(ErrorCode.QUIZ_PARSE_ERROR))
    except Exception as e:
        log_error(ErrorCode.QUIZ_GENERATE_FAILED, detail=str(e), context={"user_id": user_id}, exception=e)
        raise HTTPException(status_code=500, detail=ErrorCode.user_message(ErrorCode.QUIZ_GENERATE_FAILED))

    # 题目入库
    store = quiz_manager._get_store(user_id)
    # 从节点详情进来时把图谱节点 id 写进 knowledge_point —— 判分要靠它回写掌握度。
    # 与对话内出题同一做法（chat_quiz.py：knowledge_point 是字符串字段，正好装图谱节点 id；
    # questions.node_id 是 INTEGER，装不下）。
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
    question_ids = store.save_questions(
        result["questions"],
        subject=req.subject,
        node_id=req.node_id,
        difficulty=req.difficulty,
        source_docs=source_docs,
    )
    return {
        "status": "ok",
        "question_ids": question_ids,
        "questions": result["questions"],
        "rejected": result["rejected"],
        "materials_used": result["materials_used"],
        # 分批出题后可能因截断/重复凑不满，前端可据此提示"请求 N 道 / 实际 M 道"
        "requested": result.get("requested", len(result["questions"])),
        "generated": len(result["questions"]),
    }


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
