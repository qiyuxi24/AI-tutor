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
from fastapi import APIRouter, Depends, HTTPException, Query
from typing import List, Literal

from app.core.auth import get_current_user
from app.core.error_codes import ErrorCode, log_error
from app.core.quiz.schema import QuizGenerateRequest, QuizGradeRequest
from app.core.quiz.generator import generate_quiz
from app.core.quiz.grader import grade_question
from app.core.quiz.quiz_store import quiz_manager

logger = logging.getLogger("ai-tutor")
router = APIRouter()


@router.post("/quiz/generate")
async def create_quiz(req: QuizGenerateRequest,
                      user_id: int = Depends(get_current_user)):
    """
    生成一份测验。

    流程：
    1. 从上传教材知识库混合检索出题依据片段（subject 作为查询词）
    2. 调用 LLM 基于依据出题（RAG 约束，禁止生成材料外内容）
    3. 质量过滤（长度/自包含/去重）+ Pydantic 结构校验
    4. 题目入库（quiz.questions 表），返回题目列表

    返回:
        {
          "status": "ok",
          "question_ids": [int, ...],
          "questions": [Question.dict(), ...],
          "rejected": int,           # 被过滤掉的题目数
          "materials_used": int      # 使用的参考片段数
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
