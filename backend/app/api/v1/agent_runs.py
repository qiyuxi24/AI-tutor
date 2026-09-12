"""Agent 运行记录展示/治理 API —— agent_runs 表的只读 + 管理出口

数据来源：agent_run_store.agent_runs（唯一持久化事实源，run_agent_loop 内部写入）。
列表/详情/统计/删除都按 user_id 隔离；分层保留清理（prune）由启动 + 每日任务执行，
无需经此 API。stats 路由需声明在 /agent/runs/{run_id} 之前，避免被路径参数吞掉。
"""
from fastapi import APIRouter, HTTPException, Query, Depends
from app.core.auth import get_current_user
from app.core import agent_run_store as store

router = APIRouter()


@router.get("/agent/runs")
async def list_agent_runs(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user_id: int = Depends(get_current_user),
):
    """
    当前用户的 agent 运行列表（按时间倒序，不含 evidence 大字段），带 total 分页。

    GET /api/v1/agent/runs?limit=50&offset=0
    """
    return {
        "runs": store.list_runs(user_id, limit, offset=offset),
        "total": store.count_runs(user_id),
        "limit": limit,
        "offset": offset,
    }


@router.get("/agent/runs/stats")
async def agent_runs_stats(user_id: int = Depends(get_current_user)):
    """
    当前用户的运行概览：条数 / 成功率 / LLM 调用 / 累计 token / 记录体积。

    GET /api/v1/agent/runs/stats
    """
    return {"stats": store.run_stats(user_id)}


@router.get("/agent/runs/{run_id}")
async def get_agent_run(run_id: str, user_id: int = Depends(get_current_user)):
    """
    单次 agent 运行详情（含 evidence 证据序列，thinking 全文/完整工具参数与返回）。

    GET /api/v1/agent/runs/{run_id}
    """
    run = store.get_run(user_id, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    return run


@router.delete("/agent/runs/{run_id}")
async def delete_agent_run(run_id: str, user_id: int = Depends(get_current_user)):
    """
    删除本人的一条运行记录（含 evidence）。不存在或非本人返回 404。

    DELETE /api/v1/agent/runs/{run_id}
    """
    if not store.delete_run(user_id, run_id):
        raise HTTPException(status_code=404, detail="运行记录不存在")
    return {"status": "ok"}


@router.delete("/agent/runs")
async def clear_agent_runs(user_id: int = Depends(get_current_user)):
    """
    清空当前用户的全部 agent 运行记录（演示/合规清理用）。

    DELETE /api/v1/agent/runs
    """
    return {"status": "ok", "deleted": store.delete_user_runs(user_id)}
