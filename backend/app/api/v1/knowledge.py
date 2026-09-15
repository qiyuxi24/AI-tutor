"""
知识图谱 API 路由

所有图谱数据通过 knowledge_graph.py 的 KnowledgeGraph 类操作。
API 层只负责：参数校验、HTTP 状态控制、调用 KnowledgeGraph 方法。
每个请求通过 JWT 识别当前用户，创建隔离的 KnowledgeGraph 实例。

接口清单：
  GET    /knowledge/graph                    - 获取完整图谱（可选 ?subject= 按学科过滤）
  GET    /knowledge/subjects                 - 获取所有学科列表
  GET    /knowledge/node/{node_id}           - 获取节点详情
  GET    /knowledge/node-ids                 - 获取所有节点 ID 列表
  POST   /knowledge/node                     - 创建节点（手动，ID 自动生成）
  PUT    /knowledge/node/{node_id}           - 更新节点（含 MD 内容）
  PUT    /knowledge/node/{node_id}/info      - 更新节点基本信息
  PUT    /knowledge/node/{node_id}/mastery   - 更新掌握程度
  DELETE /knowledge/node/{node_id}           - 删除节点
  POST   /knowledge/edge                     - 创建边
  PUT    /knowledge/edge/{edge_index}        - 更新边
  DELETE /knowledge/edge/{edge_index}        - 删除边
  POST   /knowledge/ai/edit                  - AI 编辑图谱（向后兼容）
  POST   /knowledge/decompose                - 问题拆解为知识点依赖树
  GET    /knowledge/learning-path            - 获取学习路径（拓扑排序）
  GET    /knowledge/next-to-learn            - 获取下一步学习推荐
"""

import logging
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Depends, Body, Query
from fastapi.responses import StreamingResponse
from app.core.kg_taxonomy import assign_taxonomy
from app.core.knowledge_graph import KnowledgeGraph
from app.core.prerequisite import (
    DEFAULT_MAX_PARENTS, DEFAULT_THRESHOLD, apply_candidates, infer_prerequisites,
)
from app.core import graph_middleware
from app.core.auth import get_current_user, get_current_user_from_token
from app.core.event_bus import publish, subscribe
from app.core.graph_analyzer import GraphAnalyzer
from app.models.schemas import (
    CreateNodeRequest, UpdateNodeInfoRequest,
    CreateEdgeRequest, UpdateEdgeRequest,
    UpdateMasteryRequest, UpdateNodeContentRequest,
    DecomposeRequest, LearningPathResponse, NextToLearnResponse,
)

router = APIRouter()


# ══════════════════════════════════════════════════════════════════
#  SSE 事件推送
# ══════════════════════════════════════════════════════════════════

@router.get("/knowledge/events")
async def knowledge_events(user_id: int = Depends(get_current_user_from_token)):
    """
    图谱写通道事件流（SSE）。需通过 URL query 参数传入 JWT token。
    前端通过 EventSource 连接此端点，当图谱数据变更时自动收到通知并刷新。
    事件格式：data: {"type": "graph_updated", ...}\n\n

    由于 EventSource 不支持自定义请求头，通过 URL query 参数 ?token=xxx 传递 JWT。

    2026-09-14 改为**按用户订阅**（原来是 subscribe() 全局队列）：
      - 必要：对话内出题完成的 `quiz_ready` 事件带题目内容，走 per-user 路由，
        全局队列收不到（publish 带 user_id 时只投该用户队列）；
      - 顺带修掉一个既有泄漏：全局广播的 graph_updated 会被所有在线用户都收到。
      全局广播仍能到达（publish 无 user_id 时会同时投递到每个用户队列），
      所以 graph_updated 行为不变。
    """
    return StreamingResponse(subscribe(user_id=user_id), media_type="text/event-stream")


# ══════════════════════════════════════════════════════════════════
#  读取接口
# ══════════════════════════════════════════════════════════════════

@router.get("/knowledge/graph")
async def get_graph(subject: str | None = Query(None, description="可选：按学科过滤图谱，如'数据结构'"),
                    board: str | None = Query(None, description="可选：按知识板块过滤，需同时指定 subject"),
                    user_id: int = Depends(get_current_user)):
    """
    按需返回知识图谱数据（通过 graph_middleware 切片）。

    多级按需粒度：
        - 都不传        → 返回全量（兼容旧行为）
        - 只传 subject  → 返回该学科整图
        - 传 subject+board → 返回该学科下指定板块的局部子图

    推荐的前端按需流程：
        1. GET /knowledge/subjects        拿学科列表
        2. GET /knowledge/boards?subject=X 拿某学科的板块列表
        3. GET /knowledge/graph?subject=X&board=Y 按板块拉取局部子图
    """
    kg = KnowledgeGraph(user_id=user_id)
    try:
        return graph_middleware.slice_graph(kg, subject=subject, board=board)
    finally:
        kg.close()


@router.get("/knowledge/boards")
async def get_boards(subject: str = Query(..., description="学科名，如'数据结构'"),
                     user_id: int = Depends(get_current_user)):
    """
    返回指定学科下的知识板块列表（含节点数/掌握度统计），供板块侧栏按需导航。
    """
    kg = KnowledgeGraph(user_id=user_id)
    try:
        return {
            "subject": subject,
            "boards": graph_middleware.list_boards(kg, subject),
        }
    finally:
        kg.close()


@router.get("/knowledge/subjects")
async def get_subjects(user_id: int = Depends(get_current_user)):
    """返回当前用户知识图谱中已有的所有学科列表"""
    kg = KnowledgeGraph(user_id=user_id)
    try:
        return {"subjects": graph_middleware.list_subjects(kg)}
    finally:
        kg.close()


@router.get("/knowledge/node/{node_id}")
async def get_node_detail(node_id: str, user_id: int = Depends(get_current_user)):
    """返回单个节点的完整信息，包括 MD 文件内容、前置/关联节点"""
    kg = KnowledgeGraph(user_id=user_id)
    try:
        node = kg.get_node(node_id)
        if node is None:
            raise HTTPException(status_code=404, detail=f"节点不存在：{node_id}")

        # 读取 MD 文件内容
        file_path = kg.nodes_dir / f"{node_id}.md"
        content = ""
        if file_path.exists():
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

        # 前置依赖
        prerequisites = kg.get_prerequisites(node_id)

        # 关联节点（related / confusion 关系的邻居）
        related_ids = []
        for edge in kg.edges:
            if edge.get("relation") in ("related", "confusion"):
                if edge["from_node"] == node_id:
                    related_ids.append(edge["to_node"])
                elif edge["to_node"] == node_id:
                    related_ids.append(edge["from_node"])

        return {
            "id": node["id"],
            "name": node["name"],
            # 所属学科：前端据此切到对应学科再聚焦（图谱一次只渲染一个学科）
            "subject": kg.node_subject(node) or graph_middleware.SUBJECT_UNCLASSIFIED,
            "content": content,
            "tags": node.get("tags", []),
            "prerequisites": prerequisites,
            "related_nodes": related_ids,
            "mastery": node.get("mastery", 0),
            "difficulty": node.get("difficulty", 3),
            "estimated_minutes": node.get("estimated_minutes", 15),
            "summary": node.get("summary", ""),
            "file_path": node.get("file_path", ""),
        }
    finally:
        kg.close()


@router.get("/knowledge/node-ids")
async def get_node_ids(user_id: int = Depends(get_current_user)):
    """返回当前用户所有节点 ID 列表（供前端下拉选择等场景使用）"""
    kg = KnowledgeGraph(user_id=user_id)
    try:
        return {"node_ids": kg.get_node_ids()}
    finally:
        kg.close()


# ══════════════════════════════════════════════════════════════════
#  节点 CRUD
# ══════════════════════════════════════════════════════════════════

@router.post("/knowledge/node")
async def create_node(data: dict = Body(...), user_id: int = Depends(get_current_user)):
    """
    创建新节点及 MD 文件

    支持两种调用方式：
    1. 手动创建（前端）：只需传 name, tags, content，ID 自动生成
       例: {"name": "二叉树", "tags": ["数据结构", "二级"], "content": "# 二叉树\n..."}
    2. AI function calling：传完整的 id, name, tags, content 等
       例: {"id": "binary_tree", "name": "二叉树", ...}

    返回: {"status": "ok", "node": {...完整节点信息...}}

    未指定学科（tags 里的学科标签）或板块（board）时，按"规则+LLM"自动判定并写入
    （见 core/kg_taxonomy.py）；判定失败落「未分类」，不影响建节点。

    注意：使用 dict + Body(...) 而非 Pydantic 模型，因为需要兼容 AI function calling
    传来的额外字段（id, from_nodes, difficulty 等），这些字段不固定。
    """
    kg = KnowledgeGraph(user_id=user_id)
    try:
        # 如果调用方没有提供 ID → 手动创建模式，自动生成
        if "id" not in data or not data.get("id"):
            data["id"] = kg.generate_node_id(data.get("name", ""))

        node_data = {
            "id": data["id"],
            "name": data["name"],
            "file": f"nodes/{data['id']}.md",
            "tags": data.get("tags", []),
            "board": data.get("board", ""),
            "summary": data.get("summary", ""),
            "mastery": data.get("mastery", 0),
            "difficulty": data.get("difficulty", 3),
            "estimated_minutes": data.get("estimated_minutes", 15),
            "added_by": data.get("added_by", "human"),
            "confidence": data.get("confidence"),
        }

        await assign_taxonomy(kg, node_data)

        # 建库 + 写 MD 一次完成（模板收口在 KnowledgeGraph）
        kg.create_node_with_content(node_data, data.get("content") or "", origin="manual")

        publish("graph_updated")
        return {"status": "ok", "node": node_data}

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except KeyError as e:
        raise HTTPException(status_code=400, detail=f"缺少必填字段：{e}")
    finally:
        kg.close()


@router.put("/knowledge/node/{node_id}")
async def update_node(node_id: str, data: dict = Body(...), user_id: int = Depends(get_current_user)):
    """
    更新节点信息或 MD 文件内容

    可同时更新节点元数据（name, mastery, difficulty, etc.）和 MD 内容。
    兼容 AI function calling 的 update_node_content 调用。
    """
    kg = KnowledgeGraph(user_id=user_id)
    try:
        node = kg.get_node(node_id)
        if node is None:
            raise HTTPException(status_code=404, detail=f"节点不存在：{node_id}")

        # 更新元数据字段
        update_data = {}
        updatable = ["name", "mastery", "difficulty", "estimated_minutes", "summary", "tags"]
        for key in updatable:
            if key in data:
                update_data[key] = data[key]
        if update_data:
            kg.update_node_info(node_id, update_data)

        # 更新 MD 内容（如果提供了）—— 统一走 kg.update_node_content() 权限保护
        if "content" in data:
            kg.update_node_content(
                node_id,
                data["content"],
                mode=data.get("op", "replace"),
                caller="human",
            )

        publish("graph_updated")
        return {"status": "ok"}
    finally:
        kg.close()


@router.put("/knowledge/node/{node_id}/info")
async def update_node_info(node_id: str, request: UpdateNodeInfoRequest,
                           user_id: int = Depends(get_current_user)):
    """
    更新节点的名称和标签（不改变 MD 内容）

    请求体: {"name": "新名称", "tags": ["新标签1", "新标签2"]}
    至少提供 name 或 tags 之一。
    """
    data = request.model_dump(exclude_none=True)
    if not data:
        raise HTTPException(status_code=400, detail="至少需要提供 name 或 tags")

    kg = KnowledgeGraph(user_id=user_id)
    try:
        kg.update_node_info(node_id, data)
        publish("graph_updated")
        return {"status": "ok", "node_id": node_id, "updated": list(data.keys())}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    finally:
        kg.close()


@router.delete("/knowledge/node/{node_id}")
async def delete_node(node_id: str, user_id: int = Depends(get_current_user)):
    """
    删除节点：移除节点、删除 MD 文件、移除所有关联边

    返回: {"deleted": true, "removed_edges": 3}
    """
    kg = KnowledgeGraph(user_id=user_id)
    try:
        removed_edges = kg.remove_node(node_id)
        # 同步清理该节点的 RAG 索引
        try:
            from app.core.rag.manager import rag_manager
            rag_manager.delete_node_index(user_id, node_id)
        except Exception as e:
            logging.getLogger("ai-tutor").warning(f"清理 RAG 索引失败（节点 {node_id}）: {e}")
        publish("graph_updated")
        return {"deleted": True, "node_id": node_id, "removed_edges": removed_edges}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    finally:
        kg.close()


@router.put("/knowledge/node/{node_id}/mastery")
async def update_mastery(node_id: str, data: dict = Body(...),
                         user_id: int = Depends(get_current_user)):
    """更新节点的掌握程度（0-100）"""
    kg = KnowledgeGraph(user_id=user_id)
    try:
        node = kg.get_node(node_id)
        if node is None:
            raise HTTPException(status_code=404, detail=f"节点不存在：{node_id}")

        mastery = data.get("mastery")
        if mastery is None or not (0 <= mastery <= 100):
            raise HTTPException(status_code=400, detail="mastery 必须在 0-100 之间")

        kg.update_node_info(node_id, {
            "mastery": mastery,
            "added_by": data.get("added_by", "ai")
        })
        publish("graph_updated")
        return {"status": "ok", "node_id": node_id, "mastery": mastery}
    finally:
        kg.close()


# ════════════════════════
#  边 CRUD
# ════════════════════════

@router.post("/knowledge/edge")
async def create_edge(data: dict = Body(...), user_id: int = Depends(get_current_user)):
    """
    创建关联边

    请求体: {"from": "节点A", "to": "节点B", "relation": "prerequisite", "label": "说明"}
    重复创建相同的 from+to+relation 边会返回 409。
    """
    kg = KnowledgeGraph(user_id=user_id)
    try:
        kg.add_edge(data)
        publish("graph_updated")
        return {"status": "ok", "edge": data}
    except ValueError as e:
        # 判断是否是重复边错误
        msg = str(e)
        if "已存在" in msg:
            raise HTTPException(status_code=409, detail=msg)
        raise HTTPException(status_code=400, detail=msg)
    finally:
        kg.close()


@router.put("/knowledge/edge/{edge_id}")
async def update_edge(edge_id: int, request: UpdateEdgeRequest,
                      user_id: int = Depends(get_current_user)):
    """
    更新边的 relation 和/或 label

    参数:
        edge_id: 边的数据库主键 ID（非索引！）

    请求体: {"relation": "related", "label": "新的关系说明"}
    """
    data = request.model_dump(exclude_none=True)
    if not data:
        raise HTTPException(status_code=400, detail="至少需要提供 relation 或 label")

    kg = KnowledgeGraph(user_id=user_id)
    try:
        kg.update_edge_by_id(edge_id, data)
        # 查询更新后的边以返回完整信息
        updated_edge = None
        for e in kg.edges:
            if e["id"] == edge_id:
                updated_edge = e
                break
        publish("graph_updated")
        return {"status": "ok", "edge_id": edge_id, "edge": updated_edge}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    finally:
        kg.close()


@router.delete("/knowledge/edge/{edge_id}")
async def delete_edge(edge_id: int, user_id: int = Depends(get_current_user)):
    """
    删除指定 ID 的边

    返回: {"deleted": true, "edge_id": 123}
    """
    kg = KnowledgeGraph(user_id=user_id)
    try:
        kg.remove_edge_by_id(edge_id)
        publish("graph_updated")
        return {"deleted": True, "edge_id": edge_id}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    finally:
        kg.close()


# ══════════════════════════════════════════════════════════════════
#  AI 编辑（向后兼容）
# ══════════════════════════════════════════════════════════════════

@router.post("/knowledge/ai/edit")
async def ai_edit_graph(data: dict = Body(...), user_id: int = Depends(get_current_user)):
    """AI 助手编辑知识图谱（向后兼容的快捷接口）"""
    action = data.get("action")
    kg = KnowledgeGraph(user_id=user_id)
    try:
        if action == "add_node":
            kg.add_node(data["node"])
            publish("graph_updated")
            return {"status": "ok"}
        elif action == "add_edge":
            kg.add_edge(data["edge"])
            publish("graph_updated")
            return {"status": "ok"}
        elif action == "update_node":
            node = kg.get_node(data["node_id"])
            if node is None:
                raise HTTPException(status_code=404, detail=f"节点不存在：{data['node_id']}")
            update_data = {}
            for key in ["mastery", "difficulty", "estimated_minutes", "summary", "tags"]:
                if key in data:
                    update_data[key] = data[key]
            if update_data:
                kg.update_node_info(data["node_id"], update_data)
            publish("graph_updated")
            return {"status": "ok"}
        else:
            raise HTTPException(status_code=400, detail=f"未知操作：{action}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        kg.close()


# ══════════════════════════════════════════════════════════════════
#  学习路径推荐
# ══════════════════════════════════════════════════════════════════

@router.post("/knowledge/decompose")
async def decompose_question(request: DecomposeRequest,
                              user_id: int = Depends(get_current_user)):
    """
    将用户问题拆解为知识点依赖树，并在图谱中创建骨架节点（无内容）。

    流程：
    1. LLM 分析问题 → 输出依赖树 JSON（节点 + prerequisite 边）
    2. 检查哪些节点已存在（同名跳过），创建不存在的节点
    3. 创建 prerequisite 边
    4. 返回完整依赖树结构

    返回: DecomposeResponse（含 target, nodes, edges, created_nodes, created_edges）
    """
    kg = KnowledgeGraph(user_id=user_id)
    try:
        analyzer = GraphAnalyzer(kg)
        result = await analyzer.decompose_question(request.question)

        if result["target"] is None:
            raise HTTPException(status_code=422, detail="无法解析问题，请尝试更具体地描述")

        # 创建节点（只建骨架，不带内容）
        created_nodes = []
        for node in result["nodes"]:
            node_id = node.get("id", "")
            node_name = node.get("name", "")
            if not node_id or not node_name:
                continue

            # 跳过已存在的节点
            if kg.get_node(node_id) is not None:
                continue

            node_data = {
                "id": node_id,
                "name": node_name,
                "file": f"nodes/{node_id}.md",
                "tags": node.get("tags", []),
                "summary": node.get("summary", f"学习路径节点：{node_name}"),
                "mastery": 0,
                "difficulty": node.get("difficulty", 3),
                "estimated_minutes": node.get("estimated_minutes", 15),
                "added_by": "ai",
                "confidence": node.get("confidence"),
            }
            await assign_taxonomy(kg, node_data)  # 未指定学科/板块时自动判定
            # 建库 + 写空白骨架 MD（模板收口在 KnowledgeGraph）
            kg.create_node_with_content(node_data, origin="decompose")

            created_nodes.append(node_id)

        # 创建边
        created_edges = 0
        for edge in result["edges"]:
            from_id = edge.get("from", "")
            to_id = edge.get("to", "")
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
            publish("graph_updated")

        return {
            "target": result["target"],
            "nodes": result["nodes"],
            "edges": result["edges"],
            "created_nodes": created_nodes,
            "created_edges": created_edges,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"问题拆解失败：{str(e)}")
    finally:
        kg.close()


@router.get("/knowledge/learning-path")
async def get_learning_path(
    target_node_id: str = Query(None, description="可选：指定目标节点，只返回到该节点的路径"),
    user_id: int = Depends(get_current_user),
):
    """
    获取学习路径：对所有 prerequisite 边做拓扑排序，返回推荐学习顺序。

    可选参数:
        target_node_id: 如果指定，只返回从根节点到该目标节点的路径

    返回: LearningPathResponse
    """
    kg = KnowledgeGraph(user_id=user_id)
    try:
        result = kg.get_learning_path(target_node_id)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取学习路径失败：{str(e)}")
    finally:
        kg.close()


@router.get("/knowledge/next-to-learn")
async def get_next_to_learn(user_id: int = Depends(get_current_user)):
    """
    获取"下一步该学什么"的推荐。

    返回拓扑排序中第一个 mastery < 50 的节点。
    如果全部已掌握，返回 node_id=None。

    返回: NextToLearnResponse
    """
    kg = KnowledgeGraph(user_id=user_id)
    try:
        result = kg.get_next_to_learn()
        if result is None:
            return {"node_id": None, "name": "", "mastery": 100, "reason": "所有知识点已掌握！"}
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取学习推荐失败：{str(e)}")
    finally:
        kg.close()


@router.get("/knowledge/stats")
async def get_stats(subject: str | None = Query(None, description="可选：只统计指定学科，如'数据结构'"),
                    user_id: int = Depends(get_current_user)):
    """
    学习进度聚合统计（仪表盘数据源）。

    数据全部从图谱 mastery 聚合而来（单一数据源），不建独立进度表：
        - overall:        全局（或指定学科）聚合
        - by_subject:     按学科分组（subject=None 时返回）
        - weak_points:    薄弱点 Top3（mastery=0，按难度降序）
        - next_to_learn:  下一步学习推荐（复用已有逻辑）
    """
    kg = KnowledgeGraph(user_id=user_id)
    try:
        return graph_middleware.compute_stats(kg, subject=subject or None)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取学习进度统计失败：{str(e)}")
    finally:
        kg.close()


# ══════════════════════════════════════════════════════════════════
#  知识图谱导出（合并 Markdown 下载）
# ══════════════════════════════════════════════════════════════════

@router.get("/knowledge/export")
async def export_knowledge(subject: str | None = Query(None, description="可选：只导出指定学科"),
                           user_id: int = Depends(get_current_user)):
    """
    导出知识图谱为合并的 Markdown 文件（供前端下载）。

    格式：
      # {学科名} 知识图谱
      > 导出时间 / 节点数 / 边数
      ## 节点列表（按难度排序）
      ### 节点名 (ID, 掌握度, 难度)
      节点 MD 正文…
      ## 依赖关系
      A → B (前置)
    """
    kg = KnowledgeGraph(user_id=user_id)
    try:
        nodes = kg.nodes
        edges = kg.edges

        if subject:
            # 学科归属只能由 node_subject 从 tags 推导：nodes 表没有 subject 列，
            # 节点 dict 里也不存在 "subject" 键（旧实现用 n.get("subject") 过滤，恒空）
            nodes = [n for n in nodes if kg.node_subject(n) == subject]
            node_ids = {n["id"] for n in nodes}
            edges = [e for e in edges
                     if e["from_node"] in node_ids or e["to_node"] in node_ids]

        nodes.sort(key=lambda n: (n.get("difficulty", 3), n.get("mastery", 0)))

        lines: list[str] = []
        title = f"{subject} 知识图谱" if subject else "知识图谱导出"
        lines.append(f"# {title}")
        lines.append(f"> 导出时间：{__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')}")
        lines.append(f"> 节点数：{len(nodes)} | 依赖关系：{len(edges)}")
        lines.append("")

        lines.append("## 节点列表")
        for n in nodes:
            mastery_label = {0: "未学", 1: "入门", 26: "熟悉", 51: "熟练", 76: "精通"}
            ml = next((v for k, v in sorted(mastery_label.items(), reverse=True)
                       if n.get("mastery", 0) >= k), "未学")
            lines.append(f"### {n['name']} (ID: {n['id']}, 掌握度: {n.get('mastery', 0)}/{ml}, 难度: {n.get('difficulty', 3)})")
            if n.get("summary"):
                lines.append(f"> {n['summary']}")
            lines.append("")

            md_path = kg.nodes_dir / f"{n['id']}.md"
            if md_path.exists():
                md_content = md_path.read_text(encoding="utf-8").strip()
                if md_content.startswith("#"):
                    md_content = "\n".join(md_content.split("\n")[1:]).strip()
                lines.append(md_content)
            else:
                lines.append("（无内容）")
            lines.append("")

        prereq_edges = [e for e in edges if e.get("relation") == "prerequisite"]
        if prereq_edges:
            lines.append("## 依赖关系")
            for e in prereq_edges:
                from_name = next((n["name"] for n in nodes if n["id"] == e["from_node"]), e["from_node"])
                to_name = next((n["name"] for n in nodes if n["id"] == e["to_node"]), e["to_node"])
                lines.append(f"- {from_name} → {to_name} (前置)")
            lines.append("")

        content = "\n".join(lines)
        filename = f"{subject or 'knowledge'}-export.md"

        return StreamingResponse(
            iter([content.encode("utf-8")]),
            media_type="text/markdown; charset=utf-8",
            # 学科名是中文 → HTTP 头只能 latin-1，必须用 RFC 5987 的 filename* 百分号编码，
            # 否则 StreamingResponse 构造时就抛 UnicodeEncodeError（带学科导出 500）
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
        )
    finally:
        kg.close()


# ══════════════════════════════════════════════════════════════════
#  先修关系推断（P0-3：多准则无监督投票）
# ══════════════════════════════════════════════════════════════════

@router.post("/knowledge/prerequisite/infer")
async def infer_prerequisites_api(payload: dict = Body(...),
                                  user_id: int = Depends(get_current_user)):
    """
    推断学科内的先修关系（算法与准则说明见 core/prerequisite.py）。

    与「LLM 直接给 prerequisite 边」的区别：本接口的每条候选边都带**逐准则证据**，
    结果可复现、可解释、可量化（配套评测脚本 backend/scripts/eval_prerequisite.py）。

    请求体:
        subject:     必填，学科名（如"数据结构"）
        threshold:   可选，认定阈值，默认 0.30（越大越保守，直接控制召回/精度）
        max_parents: 可选，单节点最大入边数，默认 5
        apply:       可选，默认 false。true = 把候选写库（AI 权限护栏仍生效：
                     两端都是人类创建的节点之间的先修边会被跳过并记录原因）

    返回:
        {subject, node_count, candidate_count, candidates: [{from, to, from_name,
         to_name, score, votes}], applied?}
    """
    subject = (payload.get("subject") or "").strip()
    if not subject:
        raise HTTPException(status_code=400, detail="缺少 subject 参数")

    kg = KnowledgeGraph(user_id=user_id)
    try:
        nodes = kg.get_nodes_by_subject(subject)
        if len(nodes) < 2:
            return {"subject": subject, "node_count": len(nodes),
                    "candidate_count": 0, "candidates": [],
                    "message": "该学科节点不足 2 个，无法推断先修关系"}

        edges = kg.get_edges_by_subject(subject)
        # C1 正文引用准则需要节点正文；走 KG 的带缓存读取，不要绕过它直读 MD
        content = {n["id"]: kg.get_node_content_preview(n["id"], max_lines=200, max_chars=4000)
                   for n in nodes}

        from app.core.kb.embedder import get_embedder  # 延迟导入：避免拖慢 API 启动
        embedder = get_embedder()

        candidates = infer_prerequisites(
            nodes, edges, content=content, embedder=embedder,
            threshold=float(payload.get("threshold", DEFAULT_THRESHOLD)),
            max_parents_per_node=int(payload.get("max_parents", DEFAULT_MAX_PARENTS)),
        )

        names = {n["id"]: n.get("name", "") for n in nodes}
        result = {
            "subject": subject,
            "node_count": len(nodes),
            "candidate_count": len(candidates),
            "candidates": [c.to_dict(names) for c in candidates],
        }
        if payload.get("apply"):
            result["applied"] = apply_candidates(kg, candidates)
        return result
    finally:
        kg.close()
