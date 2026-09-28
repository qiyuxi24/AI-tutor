"""LLM 用量与 prompt 缓存命中统计（只读）。

数据源 = 主系统 `llm_usage` 表（`backend/data/agent_runs/agent_runs.db`），一次真实
LLM 调用一行，token 明细取自**供应商响应里的 `usage`**（含
`prompt_tokens_details.cached_tokens`）——计费真值，不是本地估算。

为什么需要这页：缓存命中价约为输入价的 1/5，命中率低 = 白烧钱；而命中率是
提示词"前缀顺序"改造的**唯一验收口径**（见
`docs/上下文工程/上下文工程_Prompt缓存命中率_调研与优化方案.md`）。
与主系统 `GET /agent/usage` 的区别：这里看**全站**，不按登录用户隔离。
"""
from fastapi import APIRouter, Depends, Query

from app.core.admin_auth import CurrentAdmin, get_current_admin
from app.core.db import llm_usage_summary

router = APIRouter()


@router.get("/usage")
def get_llm_usage(
    since_hours: float = Query(168, gt=0, le=8760),
    group_by: str = Query("kind"),
    admin: CurrentAdmin = Depends(get_current_admin),
):
    """全站 LLM 用量 + 缓存命中率 + 金额换算（默认近 7 天，按功能分组）。

    group_by 取 kind（功能）/ model（含备用模型降级）/ day / user，白名单外退回 kind。
    """
    return llm_usage_summary(since_hours=since_hours, group_by=group_by)
