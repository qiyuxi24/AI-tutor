"""
资源采集 API 路由（B1.4）

接口（全部经 JWT 认证，按用户隔离）：
  POST /collector/search            - 学科/模式下发现候选（各适配器并行 + 授权等级过滤）
  POST /collector/tasks             - 建采集任务（勾选候选下发，后台执行 + 断点续传）
  GET  /collector/tasks/{id}        - 任务进度（供前端轮询）
  POST /collector/tasks/{id}/cancel - 协作式取消（决策 #21）
  GET  /collector/stats             - 采集覆盖率（学段→学科 已采/缺口，§4.3 可视化数据源）
  GET  /collector/stats/sources     - 来源质量统计（B3.3：按采集来源列出被 RAG 引用次数排行）

对外契约（对齐前端 api/collector.js）：
- 任务 status 领域值 pending/processing 翻译为 UI 值 queued/running，
  completed/cancelled/failed 透传
- stats 返回 { coverage: [ {stage, stage_name, total, collected,
  subjects: [{subject, collected, boards_total, boards_collected}] } ] }；
  subject.collected = 该学科已成功入库（含 content_hash 去重命中）资源份数；
  boards 粒度需目录学科带板块数据（B1.6 起）再填充，当前恒 0（前端走「份数」展示）

说明：
- 逻辑薄封装，全部委托 CollectorManager（B1.3）；manager 由 Depends(get_manager)
  注入，测试可通过 dependency_overrides 替换为 mock，全离线可测。
- 候选搜索失败由 manager 内部异常隔离静默跳过，不向上抛（无 500 路径）。
"""
import logging
from collections import Counter

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Literal

from app.core.auth import get_current_user
from app.core.collector.manager import CollectorManager, collector_manager
from app.core.collector.subjects import load_subjects
from app.core.collector.types import ALLOWED_LICENSE, CollectCandidate
from app.core.error_codes import ErrorCode, log_error

logger = logging.getLogger("ai-tutor")
router = APIRouter(tags=["资源采集"])

# 采集用途模式（见 types.ALLOWED_LICENSE）；Literal 让 FastAPI 自动 422 非法值
Mode = Literal["personal", "commercial"]

# 任务状态翻译：领域（store/manager）→ UI（api/collector.js）
_STATUS_UI = {"pending": "queued", "processing": "running"}


def get_manager() -> CollectorManager:
    """依赖注入点：默认全局单例；测试 override 为假 manager"""
    return collector_manager


# ────────────────────────────────────────────
#  请求模型
# ────────────────────────────────────────────

class SearchRequest(BaseModel):
    """搜索候选：按学科名 + 用途模式"""
    subject: str = Field(..., min_length=1, description="学科名称/ID")
    mode: Mode = "personal"


class CandidateIn(BaseModel):
    """候选入参（勾选的搜索结果；字段对齐 CollectCandidate）"""
    title: str
    source_url: str
    source: str = ""
    license_level: Literal["L0", "L1", "L2", "L3"] = "L0"
    description: str = ""
    size_bytes: int = 0


class TaskCreateRequest(BaseModel):
    """建采集任务"""
    subject: str = Field(..., min_length=1)
    mode: Mode = "personal"
    source: str = ""
    candidates: list[CandidateIn] = []


# ────────────────────────────────────────────
#  响应辅助
# ────────────────────────────────────────────

def _cand_out(c: CollectCandidate) -> dict:
    """候选 → 前端可消费 dict（meta 不下发，未落库也未参与建任务）"""
    return {
        "title": c.title,
        "source_url": c.source_url,
        "source": c.source,
        "license_level": c.license_level,
        "description": c.description,
        "size_bytes": c.size_bytes,
    }


def _task_out(task: dict) -> dict:
    """任务行 → 进度响应：DB 字段 + 状态翻译（pending→queued / processing→running）"""
    keys = ("id", "subject", "source", "status", "cursor",
            "processed_count", "total_count", "error",
            "created_at", "finished_at")
    out = {k: task[k] for k in keys if k in task}
    out["status"] = _STATUS_UI.get(task.get("status", ""), task.get("status", ""))
    return out


def _coverage_out(store) -> list[dict]:
    """覆盖率（学段→学科 已采/缺口，契约见模块 docstring / api/collector.js）

    - 骨架 = subjects.json 学段→学科 目录树（决策 §4.3，缺口一目了然）；
      无目录数据（B1.6 前）返回 []，前端显示空态。
    - 每学科 collected = 已成功入库资源数（indexed + content_hash 去重命中），
      对齐键取学科的 id 与 name 两个字符串；collected==0 即缺口。
    - boards_total/boards_collected 恒 0：板块粒度需目录学科自带板块清单（B1.6 起）。
    """
    rows = store.list_resources()
    got = Counter()
    for r in rows:
        if r.get("status") in ("indexed", "duplicate") and (r.get("subject") or "").strip():
            got[(r.get("subject") or "").strip()] += 1

    stages = []
    for stage in (load_subjects().get("stages") or []):
        subs = []
        for sub in stage.get("subjects") or []:
            keys = {k for k in (sub.get("id"), sub.get("name")) if k}
            subs.append({
                "subject": sub.get("name") or sub.get("id") or "",
                "collected": sum(got[k] for k in keys),
                "boards_total": 0,
                "boards_collected": 0,
            })
        if subs:
            stages.append({
                "stage": stage.get("id"),
                "stage_name": stage.get("name"),
                "total": len(subs),
                "collected": sum(1 for s in subs if s["collected"]),
                "subjects": subs,
            })
    return stages


# ────────────────────────────────────────────
#  路由
# ────────────────────────────────────────────

@router.post("/collector/search")
async def search_candidates(req: SearchRequest,
                            user_id: int = Depends(get_current_user),
                            manager: CollectorManager = Depends(get_manager)):
    """搜索候选：遍历已注册适配器（如 wikipedia/wikibooks），按 mode 过滤授权等级并去重"""
    candidates = await manager.search(req.subject, req.mode)
    return {
        "status": "ok",
        "subject": req.subject,
        "mode": req.mode,
        "candidates": [_cand_out(c) for c in candidates],
    }


@router.post("/collector/tasks")
async def create_task(req: TaskCreateRequest,
                      user_id: int = Depends(get_current_user),
                      manager: CollectorManager = Depends(get_manager)):
    """建采集任务并后台执行；同 URL/同内容已入库的候选自动跳过（决策 #19）"""
    # 商用模式仅 L0 兜底校验（L3 各模式禁采）：防绕过前端勾选直接 curl 灌入（比赛合规边界）
    allowed = ALLOWED_LICENSE.get(req.mode, ALLOWED_LICENSE["personal"])
    banned = [c.source_url for c in req.candidates if c.license_level not in allowed]
    if banned:
        raise HTTPException(
            status_code=422,
            detail=f"候选 {banned[0]} 的授权等级（{req.mode} 模式）不可采集",
        )
    cands = [CollectCandidate(**c.model_dump()) for c in req.candidates]
    try:
        task = await manager.start_task(
            user_id=user_id, subject=req.subject,
            candidates=cands, mode=req.mode, source=req.source,
        )
    except Exception as e:
        log_error(ErrorCode.COLL_TASK_FAILED, detail=str(e),
                  context={"user_id": user_id, "subject": req.subject}, exception=e)
        raise HTTPException(status_code=500,
                            detail=ErrorCode.user_message(ErrorCode.COLL_TASK_FAILED))
    return {"status": "ok", "task": _task_out(task)}


@router.get("/collector/tasks/{task_id}")
async def get_task(task_id: int,
                   user_id: int = Depends(get_current_user),
                   manager: CollectorManager = Depends(get_manager)):
    """任务进度（含 cursor/total_count，前端轮询采集进度）"""
    task = manager.get_task(user_id=user_id, task_id=task_id)
    if task is None:
        raise HTTPException(status_code=404,
                            detail=ErrorCode.user_message(ErrorCode.COLL_TASK_NOT_FOUND))
    return {"task": _task_out(task)}


@router.post("/collector/tasks/{task_id}/cancel")
async def cancel_task(task_id: int,
                      user_id: int = Depends(get_current_user),
                      manager: CollectorManager = Depends(get_manager)):
    """协作式取消：执行循环在下一候选边界收尾为 cancelled（决策 #21）"""
    task = manager.cancel(user_id=user_id, task_id=task_id)
    if task is None:
        raise HTTPException(status_code=404,
                            detail=ErrorCode.user_message(ErrorCode.COLL_TASK_NOT_FOUND))
    return {"status": "ok", "task": _task_out(task)}


@router.get("/collector/stats")
async def stats(user_id: int = Depends(get_current_user),
                manager: CollectorManager = Depends(get_manager)):
    """采集覆盖率：学段→学科 已采/缺口（§4.3，前端 api/collector.js 契约）"""
    store = manager.store_mgr._get_store(user_id)
    return {"coverage": _coverage_out(store)}


@router.get("/collector/stats/sources")
async def source_stats(user_id: int = Depends(get_current_user),
                       manager: CollectorManager = Depends(get_manager)):
    """来源质量统计（B3.3）：按采集来源列出被 RAG 引用次数排行（降序）

    口径（见 CollectorStore.source_reference_stats）：
    - 只统计**文档类**资源（已入库 KB 文档，file_node_id 非空）；
      每行带 resource_type="document" 让调用方知道当前口径。
    - dataset_quiz 题目源入库走 quiz_id、无 file_node_id，**暂不计入**（后续另开项）。
    - 未命中过的来源也在榜内（times_referenced=0），便于看出"采了却没被用过"的源。
    """
    store = manager.store_mgr._get_store(user_id)
    return {"sources": store.source_reference_stats()}
