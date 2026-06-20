"""
知识图谱 API 路由

所有图谱数据通过 knowledge_graph.py 的 KnowledgeGraph 类操作。
API 层只负责：参数校验、HTTP 状态控制、调用 KnowledgeGraph 方法。
每个请求通过 JWT 识别当前用户，创建隔离的 KnowledgeGraph 实例。

接口清单：
  GET    /knowledge/graph                    - 获取完整图谱
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

import json
from pathlib import Path
from fastapi import APIRouter, HTTPException, Depends, Body, Query
from fastapi.responses import StreamingResponse
from app.core.knowledge_graph import KnowledgeGraph
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
    知识图谱变更事件流（SSE）。需通过 URL query 参数传入 JWT token。
    前端通过 EventSource 连接此端点，当图谱数据变更时自动收到通知并刷新。
    事件格式：data: {"type": "graph_updated", ...}\n\n
    
    由于 EventSource 不支持自定义请求头，通过 URL query 参数 ?token=xxx 传递 JWT。
    """
    return StreamingResponse(subscribe(), media_type="text/event-stream")


# ══════════════════════════════════════════════════════════════════
#  读取接口
# ══════════════════════════════════════════════════════════════════

@router.get("/knowledge/graph")
async def get_graph(user_id: int = Depends(get_current_user)):
    """返回当前用户的完整图谱数据（所有节点 + 所有边）"""
    kg = KnowledgeGraph(user_id=user_id)
    try:
        return {"nodes": kg.nodes, "edges": kg.edges}
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
            "summary": data.get("summary", ""),
            "mastery": data.get("mastery", 0),
            "difficulty": data.get("difficulty", 3),
            "estimated_minutes": data.get("estimated_minutes", 15),
            "added_by": data.get("added_by", "human"),
            "confidence": data.get("confidence"),
        }

        kg.add_node(node_data)

        # 创建对应的 MD 文件
        md_path = kg.nodes_dir / f"{data['id']}.md"
        md_content = data.get("content")
        if not md_content:
            md_content = f"# {data['name']}\n\n> 手动创建\n\n## 概述\n\n待完善...\n"
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_content)

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
#  AI 建议执行（公共函数，供 llm_client 和 chat_service 共用）
# ══════════════════════════════════════════════════════════════════

def _load_suggestions(data_dir: Path) -> list:
    """加载待审核建议文件，文件不存在时返回空列表"""
    suggestions_path = data_dir / "ai_suggestions.json"
    if not suggestions_path.exists():
        return []
    try:
        with open(suggestions_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return []


def _save_suggestions(data_dir: Path, suggestions: list) -> None:
    """保存待审核建议到文件"""
    suggestions_path = data_dir / "ai_suggestions.json"
    with open(suggestions_path, "w", encoding="utf-8") as f:
        json.dump(suggestions, f, ensure_ascii=False, indent=2)


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
            kg.add_node(node_data)

            # 创建空白 MD 文件
            md_path = kg.nodes_dir / f"{node_id}.md"
            md_content = f"# {node_name}\n\n> 由 AI 通过问题拆解自动创建（学习路径框架节点）\n\n## 概述\n\n待完善...\n"
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(md_content)

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


def create_node_from_ai(kg: KnowledgeGraph, node_id: str, node_name: str,
                        tags: list | None = None, summary: str = "",
                        difficulty: int = 3, estimated_minutes: int = 15,
                        content: str = "", from_nodes: list | None = None,
                        confidence: float | None = None) -> str:
    """
    公共函数：创建一个 AI 生成的节点（写图谱 + 写 MD 文件 + 建前置边）。
    供 execute_kg_tool() 和 _apply_suggestion() 共用，消除重复代码。

    参数:
        kg:               KnowledgeGraph 实例（已绑定 user_id）
        node_id:          节点英文 ID
        node_name:        节点中文名
        tags:             标签列表
        summary:          一句话摘要
        difficulty:       难度 1-5
        estimated_minutes: 预估学习分钟数
        content:          Markdown 正文（空则生成默认模板）
        from_nodes:       前置节点 ID 列表，自动创建 prerequisite 边
        confidence:       AI 置信度

    返回:
        操作结果描述字符串
    """
    node_data = {
        "id": node_id,
        "name": node_name,
        "file": f"nodes/{node_id}.md",
        "tags": tags or [],
        "summary": summary,
        "mastery": 0,
        "difficulty": difficulty,
        "estimated_minutes": estimated_minutes,
        "added_by": "ai",
        "confidence": confidence,
        # 不传 created_at，让 add_node() 使用默认值 datetime.now().isoformat()
    }
    kg.add_node(node_data)

    # 写 MD 文件
    md_path = kg.nodes_dir / f"{node_id}.md"
    if content.strip():
        md_content = content if content.strip().startswith("#") else \
                     f"# {node_name}\n\n> 由 AI 自动创建\n\n{content}"
    else:
        summary_line = f"\n> {summary}" if summary else ""
        md_content = f"# {node_name}\n> 由 AI 自动创建{summary_line}\n\n## 概述\n\n待完善...\n"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)

    # 创建前置边
    edge_count = 0
    for pid in (from_nodes or []):
        if kg.get_node(pid):
            try:
                kg.add_edge({
                    "from": pid, "to": node_id,
                    "relation": "prerequisite",
                    "label": f"是学习 {node_name} 的前置知识",
                    "added_by": "ai",
                }, caller="ai")
                edge_count += 1
            except ValueError:
                pass
            except PermissionError:
                pass

    return f"已创建节点「{node_name}」(ID: {node_id})，关联 {edge_count} 条边"


def _apply_suggestion(kg: KnowledgeGraph, suggestion: dict) -> str:
    """
    执行单条图谱分析建议（来自 GraphAnalyzer），返回结果描述字符串。
    注意：与 execute_kg_tool() 不同，suggestion 数据结构来自分析 LLM 的 JSON。
    """
    action = suggestion.get("action")

    if action == "add_node":
        node = suggestion.get("node", {})
        new_node_id = node.get("id", "")
        # recommended_edges 可能包含多种关系类型：
        # - prerequisite（已有节点→新节点）：加入 from_nodes
        # - related/confusion/extension（已有节点↔新节点）：创建独立边
        from_nodes = []
        extra_edges = []
        for e in suggestion.get("recommended_edges", []):
            relation = e.get("relation", "related")
            if e.get("to") == new_node_id:
                if relation == "prerequisite":
                    # 已有节点 → 新节点：已有节点是前置
                    from_nodes.append(e["from"])
                else:
                    # 非 prerequisite 边，稍后单独创建
                    extra_edges.append(e)
            elif e.get("from") == new_node_id:
                # 新节点 → 已有节点：独立边
                extra_edges.append(e)

        result = create_node_from_ai(
            kg=kg,
            node_id=new_node_id,
            node_name=node.get("name", ""),
            tags=node.get("tags"),
            summary=node.get("summary", ""),
            difficulty=int(node.get("difficulty", 3)),
            estimated_minutes=int(node.get("estimated_minutes", 15)),
            content=node.get("content", ""),
            from_nodes=from_nodes,
            confidence=suggestion.get("confidence"),
        )

        # 创建非 prerequisite 的额外边
        for e in extra_edges:
            try:
                kg.add_edge({
                    "from": e["from"], "to": e["to"],
                    "relation": e.get("relation", "related"),
                    "label": e.get("label", ""),
                    "added_by": "ai",
                    "confidence": e.get("confidence", suggestion.get("confidence")),
                }, caller="ai")
            except (ValueError, PermissionError):
                pass

        return result

    elif action == "add_edge":
        edge = suggestion["edge"]
        kg.add_edge({
            "from": edge["from"], "to": edge["to"],
            "relation": edge.get("relation", "related"),
            "label": edge.get("label", ""),
            "added_by": "ai", "confidence": suggestion.get("confidence"),
        }, caller="ai")
        return f"已创建边: {edge['from']} → {edge['to']} ({edge.get('relation', 'related')})"

    elif action == "update_content":
        kg.update_node_content(suggestion["node_id"],
                               suggestion.get("content_snippet", ""), mode="append",
                               caller="ai")
        return f"已更新节点 {suggestion['node_id']} 的内容"

    else:
        raise ValueError(f"不支持的操作：{action}")
