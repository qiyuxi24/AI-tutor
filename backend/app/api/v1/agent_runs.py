"""Agent 运行记录展示/治理 API —— agent_runs 表的只读 + 管理出口

数据来源：agent_run_store.agent_runs（唯一持久化事实源，run_agent_loop 内部写入）。
列表/详情/统计/删除都按 user_id 隔离；分层保留清理（prune）由启动 + 每日任务执行，
无需经此 API。stats 路由需声明在 /agent/runs/{run_id} 之前，避免被路径参数吞掉。

另附两个排查口径的出口：
- `/agent/usage`：调用级 token 明细（core/llm/usage.py 的 llm_usage 表，与 agent_runs 同库）。
  run 级看 agent_runs，调用级（按功能 / 模型 / 天）看这里。
- `/agent/logs`：agent 调试流水（core/agent/debug_log.py，14 天保留）。按 run_id 串联
  一次运行的全部步骤，比 grep tutor.log 强；但它是**开发口径**，`AGENT_DEBUG_LOG=0` 可关闭。
"""
from fastapi import APIRouter, HTTPException, Query, Depends
from app.core.auth import get_current_user
from app.core.agent import debug_log, store as store
from app.core.llm import usage as llm_usage

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


@router.get("/agent/usage")
async def agent_usage(
    since_hours: float = Query(168, gt=0, le=8760),
    group_by: str = Query("kind"),
    user_id: int = Depends(get_current_user),
):
    """
    当前用户的 LLM 用量聚合（调用级明细，来自 llm_usage 表，见 core/llm/usage.py）。

    group_by 取 kind（功能）/ model / day / user，白名单外退回 kind；默认近 7 天。
    GET /api/v1/agent/usage?since_hours=168&group_by=kind
    """
    return llm_usage.summary(since_hours=since_hours, group_by=group_by, user_id=user_id)


@router.get("/agent/logs")
async def agent_debug_logs(
    run_id: str | None = Query(None),
    scope: str | None = Query(None),
    level: str | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    user_id: int = Depends(get_current_user),
):
    """
    当前用户的 agent 调试流水（按 id 升序，读起来像一次流水）。

    排查时用它替代 grep tutor.log：每条带 run_id / scope / event / level / data，
    按 run_id 一拉就是一次运行的完整步骤（每轮 LLM token、工具结果清理、预算耗尽、
    强制收尾…）。**开发口径，非审计口径**——DEBUG_LOG 可整体关闭，且只留 14 天。

    GET /api/v1/agent/logs?run_id=xxx&scope=loop&level=WARNING&limit=200
    """
    return {"logs": debug_log.recent(run_id=run_id, user_id=user_id,
                                     scope=scope, level=level, limit=limit)}


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
