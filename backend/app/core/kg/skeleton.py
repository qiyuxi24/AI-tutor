"""把「问题拆解」的依赖树计划落成图谱骨架（**唯一实现**：HTTP 端点与 Agent 工具共用）。

P0-3（2026-10-11）为什么抽出来：`POST /knowledge/decompose` 早已实现"问题 → 知识点依赖树
→ 骨架节点 + prerequisite 边"（`GraphAnalyzer.decompose_question`），但它**只挂在 HTTP
端点上、不在 `NATIVE_SPECS` 里**，前端也无人调用 → 孤儿端点。结果是模型只能一个节点一个
节点地调 `add_knowledge_node`，而单次 run 的工具调用上限是 12（`loop_guard`），
一次对话里**建不出超过 12 个节点的图**（想建 15 节点的图只能跨多轮慢慢攒）。

抽成领域函数后：端点和新的 `plan_knowledge_nodes` 工具**共用同一份**落库逻辑
（不抄第二份），也让它与 P0-1 的写范围解耦。

放 `core/kg/` 而不是 `api/`：调用方（工具 handler）不该依赖 API 层。
"""

import logging
from typing import Awaitable, Callable, Optional

logger = logging.getLogger("ai-tutor")

# 依赖树里的节点归属判定函数签名（`kg_taxonomy.assign_taxonomy` 一族）
TaxonomyFn = Callable[[object, dict], Awaitable[object]]


async def create_skeleton_from_plan(kg, plan: dict,
                                    taxonomy_fn: Optional[TaxonomyFn] = None) -> dict:
    """把依赖树计划落成图谱骨架（只建节点与 prerequisite 边，不带正文）。

    参数:
        kg:           KnowledgeGraph 实例（已绑定 user_id）
        plan:         `GraphAnalyzer.decompose_question` 的产物
                      `{"target": {...}, "nodes": [{id,name,tags,...}], "edges": [{from,to}]}`
        taxonomy_fn:  归属判定（未指定学科/板块时自动判定）。None → 惰性取
                      `kg_taxonomy.assign_taxonomy`；显式传入是为了让调用方（及测试）
                      能注入自己的实现，避免这里藏着第二处导入缝。

    返回:
        `{"created_nodes": [实际落点 id...], "created_edges": n, "skipped_nodes": [已存在的 id...]}`
        created_nodes 用的是 `create_node_with_content` 的**返回值**：同名并轨命中已有
        节点时是已有节点的 id（回执不能报一个没建出来的 id）。
    """
    if taxonomy_fn is None:
        from app.core.kg_taxonomy import assign_taxonomy
        taxonomy_fn = assign_taxonomy

    created_nodes: list[str] = []
    skipped_nodes: list[str] = []

    for node in plan.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id") or "").strip()
        node_name = str(node.get("name") or "").strip()
        if not node_id or not node_name:
            continue
        # 跳过已存在的节点（同名并轨由 create_node_with_content 兜底）
        if kg.get_node(node_id) is not None:
            skipped_nodes.append(node_id)
            continue

        node_data = {
            "id": node_id,
            "name": node_name,
            "file": f"nodes/{node_id}.md",
            "tags": node.get("tags", []),
            # 显式学科/板块优先（调用方可预先写进 plan 的节点里，省一次归属判定）
            "subject": node.get("subject", ""),
            "board": node.get("board", ""),
            "summary": node.get("summary", f"学习路径节点：{node_name}"),
            "mastery": 0,
            "difficulty": node.get("difficulty", 3),
            "estimated_minutes": node.get("estimated_minutes", 15),
            "added_by": "ai",
            "confidence": node.get("confidence"),
        }
        await taxonomy_fn(kg, node_data)  # 未指定学科/板块时自动判定
        # 建库 + 写空白骨架 MD（模板收口在 KnowledgeGraph）。
        created_nodes.append(kg.create_node_with_content(node_data, origin="decompose"))

    created_edges = 0
    for edge in plan.get("edges") or []:
        if not isinstance(edge, dict):
            continue
        from_id = str(edge.get("from") or "").strip()
        to_id = str(edge.get("to") or "").strip()
        if not from_id or not to_id:
            continue
        if not kg.get_node(from_id) or not kg.get_node(to_id):
            continue
        try:
            kg.add_edge({
                "from": from_id,
                "to": to_id,
                "relation": "prerequisite",
                "label": f"学习「{to_id}」前需掌握「{from_id}」",
                "added_by": "ai",
            }, caller="ai")
            created_edges += 1
        except (ValueError, PermissionError):
            pass

    if created_nodes or created_edges:
        logger.info(
            f"骨架落库：新建节点 {len(created_nodes)} 个 / 新边 {created_edges} 条"
            f"（跳过已存在 {len(skipped_nodes)} 个）")
    return {"created_nodes": created_nodes, "created_edges": created_edges,
            "skipped_nodes": skipped_nodes}
