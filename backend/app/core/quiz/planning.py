"""出题规划的接线层：图谱 → 候选考点 → 蓝图 → 可序列化 dict。

为什么单独一层
-------------
`blueprint.py` 是纯函数（不读库），`generator.py` 不碰图谱（它只收材料与规格），
所以"从图谱读出小节与正文"这一步必须有地方落 —— 就是这里。
三个调用方（`POST /quiz/generate`、`POST /quiz/plan/preview`、未来的批量出题）
共用同一份取径，避免每个端点各写一遍 `list_sections` + `node_content_text`。

安全
----
`graph_node_id` 来自**客户端**，必须经 `kg.get_node()` 校验归属（该查询带 user_id 护栏）。
节点不存在 / 不属于当前用户 → 返回 `None`（调用方按"没有锚点"处理），
**不抛异常也不回显别人的规模** —— 否则这个端点就成了图谱规模探测器。
"""

from __future__ import annotations

import logging

from app.core.quiz.blueprint import (
    Blueprint,
    CandidatePoint,
    allocate,
    build_candidates,
)
from app.core.quiz.budget import CountBudget, compute_budget
from app.core.quiz.schema import PlanRow

logger = logging.getLogger("ai-tutor")

__all__ = [
    "load_candidates",
    "plan_for_node",
    "blueprint_to_dict",
    "budget_to_dict",
]


def load_candidates(user_id: int, graph_node_id: str) -> list[CandidatePoint] | None:
    """读图谱节点的小节与正文，构造候选考点。

    返回:
        候选考点列表；节点不存在 / 未指定 / 读取失败 → **None**
        （None 与"空列表"语义不同：None = 没有锚点，空列表 = 有锚点但没内容）
    """
    if not (graph_node_id or "").strip():
        return None
    try:
        from app.core.knowledge_graph import KnowledgeGraph

        kg = KnowledgeGraph(user_id=user_id)
        try:
            node = kg.get_node(graph_node_id)
            if node is None:
                logger.info(f"出题规划：图谱节点不存在或非本用户（{graph_node_id}），按无锚点处理")
                return None
            sections = kg.list_sections(graph_node_id)
            body = kg.node_content_text(graph_node_id) or ""
            # 每节的正文长度：小节化节点逐节读；未小节化时整篇算一个考点
            if sections:
                enriched = []
                for sec in sections:
                    sid = str(sec.get("id") or "")
                    text = kg.read_section(graph_node_id, sid) if sid else ""
                    enriched.append({**sec, "chars": len(text or "")})
                sections = enriched
            return build_candidates(node_id=graph_node_id, sections=sections, body=body)
        finally:
            kg.close()
    except Exception as e:                       # noqa: BLE001 —— 规划失败不阻断出题
        logger.warning(f"出题规划读图谱失败（按无锚点处理）：{type(e).__name__}: {e}")
        return None


def plan_for_node(*, user_id: int, graph_node_id: str,
                  plan: list[PlanRow] | None, difficulty: str = "medium",
                  only_cover_keys: list[str] | None = None,
                  ) -> tuple[Blueprint | None, CountBudget | None, list[CandidatePoint] | None]:
    """一次读出：候选考点 + 题量预算 + 蓝图。

    三者的关系：
        candidates → budget（按小节数与正文长度算题量）
        candidates + plan → blueprint（把题摊到考点上）

    返回 `(blueprint, budget, candidates)`；没有锚点时前两项为 None，
    调用方据此走老路径（`question_types` + 自由出题）。

    `only_cover_keys`（2026-11-16，「补齐缺失」）
    --------------------------------------------
    只把题摊到这几个考点上 —— 前端上次出题没铺满时带着 `missing_cover_keys` 回来，
    语义是"把漏的那几个补上"，**不是**"重出整卷"。所以候选集先按 key 过滤再摊。

    两个边界：
      · key 全部失效（图谱改过小节 / 换了节点）→ 不过滤，退回全量候选。
        宁可多出几道，也别让用户的「补齐」按钮一点就报错。
      · 过滤后只剩一个考点而 plan 要好几道 → `allocate` 会如实报 `shortfall`，
        调用方照常按 strict 决定交付与否。**不在这里偷偷放宽**。
    """
    cands = load_candidates(user_id, graph_node_id)
    if cands is None:
        return None, None, None

    if only_cover_keys:
        wanted = set(only_cover_keys)
        picked = [c for c in cands if c.cover_key in wanted]
        if picked:
            logger.info(f"出题规划：按缺失考点补齐 {len(picked)}/{len(cands)} 个"
                        f"（{graph_node_id}）")
            cands = picked
        else:
            logger.warning(f"出题规划：missing_cover_keys 与候选考点全不匹配"
                           f"（{graph_node_id}，给了 {len(only_cover_keys)} 个），退回全量候选")

    budget = compute_budget(
        sections=[c.as_dict() for c in cands],
        # 未小节化时 `#body` 这一个候选的 chars 就是正文全长
        content_chars=sum(c.chars for c in cands) or None,
    )
    bp = allocate(node_id=graph_node_id, plan=list(plan or []),
                  candidates=cands, difficulty=difficulty)
    return bp, budget, cands


def blueprint_to_dict(bp: Blueprint | None) -> dict | None:
    """蓝图 → JSON 可序列化（进任务上下文、进响应、进 `quiz_batches.spec_json`）。"""
    if bp is None:
        return None
    return {
        "slots": [s.as_dict() for s in bp.slots],
        "candidates": [c.as_dict() for c in bp.candidates],
        "shortfall": bp.shortfall,
        "reason": bp.reason,
        "total": bp.total,
        "by_type": bp.by_type(),
        "cover_keys": bp.cover_keys(),
    }


def budget_to_dict(budget: CountBudget | None) -> dict | None:
    if budget is None:
        return None
    return {
        "count": budget.count,
        "content_cap": budget.content_cap,
        "section_floor": budget.section_floor,
        "content_chars": budget.content_chars,
        "section_count": budget.section_count,
        "reason": budget.reason,
        "capped": budget.capped,
        "sections": budget.sections,
    }
