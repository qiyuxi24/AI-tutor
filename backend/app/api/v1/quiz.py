"""
出题 API 路由：基于 RAG 教材知识库的可靠出题 + 判分

接口：
  POST   /quiz/generate              - 生成一份测验（RAG 依据检索 + LLM 出题 + 质量过滤）
  GET    /quiz/questions             - 列出题库中的题目
  GET    /quiz/questions/{id}        - 获取单题详情
  POST   /quiz/{id}/grade            - 判分（客观题规则判分 / 简答题 LLM 判分）
  GET    /quiz/stats                 - 题库统计

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
from typing import List, Literal

from app.core.auth import get_current_user
from app.core.error_codes import ErrorCode, log_error
from app.core.quiz.schema import QuizGenerateRequest, QuizGradeRequest
from app.core.quiz.generator import generate_quiz
from app.core.quiz.grader import grade_question
from app.core.quiz.quiz_store import quiz_manager
from app.core.quiz.exporter import export_quiz as build_export

logger = logging.getLogger("ai-tutor")
router = APIRouter()


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
    question_ids = store.save_questions(
        result["questions"],
        subject=req.subject,
        node_id=req.node_id,
        difficulty=req.difficulty,
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
    判分结果写入 attempts 表。

    返回:
        {"status": "ok", "score": int, "max_score": int, "correct": bool,
         "comment": str, "detail": str, "analysis": str}
    """
    store = quiz_manager._get_store(user_id)
    q = store.get_question(question_id)
    if not q:
        raise HTTPException(status_code=404, detail=ErrorCode.user_message(ErrorCode.QUIZ_NOT_FOUND))

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
    return {
        "status": "ok",
        **result,
        "analysis": q["analysis"],
    }


@router.get("/quiz/stats")
async def quiz_stats(user_id: int = Depends(get_current_user)):
    """题库统计"""
    store = quiz_manager._get_store(user_id)
    return store.stats()


@router.get("/quiz/export")
async def export_quiz(
    format: str = Query("docx", pattern="^(docx|pdf|md)$",
                       description="导出格式：docx（Word）/ pdf / md"),
    subject: str = Query("", description="按主题导出；空=导出全部题库"),
    ids: str = Query("", description="指定题目 id（逗号分隔），优先于 subject"),
    limit: int = Query(100, ge=1, le=500, description="最多导出题目数"),
    with_answer: bool = Query(True, description="是否包含答案与解析（false=学生卷）"),
    user_id: int = Depends(get_current_user),
):
    """
    导出试卷为 Word / PDF / Markdown，直接浏览器下载。

    - ids 非空：按给定题目 id 顺序导出（刚生成的那套题传它的 question_ids）
    - 否则：按 subject 筛选题库导出（subject 空=全部题库，按出题先后排序）
    - with_answer=false 可导出不带答案的「学生卷」
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
    suffix = "_试题" if with_answer else "_学生卷"
    filename = f"{stem}{suffix}_{datetime.now().strftime('%Y%m%d_%H%M')}.{ext}"

    return Response(
        content=payload,
        media_type=media_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
            "Access-Control-Expose-Headers": "Content-Disposition",
        },
    )
