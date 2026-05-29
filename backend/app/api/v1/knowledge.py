"""
知识图谱 API 路由

所有图谱数据通过 knowledge_graph.py 的 KnowledgeGraph 类操作。
API 层只负责：参数校验、HTTP 状态控制、调用 KnowledgeGraph 方法。

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
"""

import json
from pathlib import Path
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from app.core.knowledge_graph import KnowledgeGraph
from app.core.event_bus import publish, subscribe
from app.models.schemas import (
    CreateNodeRequest, UpdateNodeInfoRequest,
    CreateEdgeRequest, UpdateEdgeRequest,
    UpdateMasteryRequest, UpdateNodeContentRequest,
)

router = APIRouter()
kg = KnowledgeGraph()

# 待审核建议文件路径
SUGGESTIONS_PATH = kg.data_dir / "ai_suggestions.json"


# ══════════════════════════════════════════════════════════════════
#  SSE 事件推送
# ══════════════════════════════════════════════════════════════════

@router.get("/knowledge/events")
async def knowledge_events():
    """
    知识图谱变更事件流（SSE）。
    前端通过 EventSource 连接此端点，当图谱数据变更时自动收到通知并刷新。
    事件格式：data: {"type": "graph_updated", ...}\n\n
    """
    return StreamingResponse(subscribe(), media_type="text/event-stream")


# ══════════════════════════════════════════════════════════════════
#  读取接口
# ══════════════════════════════════════════════════════════════════

@router.get("/knowledge/graph")
async def get_graph():
    """返回完整图谱数据（所有节点 + 所有边）"""
    return {"nodes": kg.nodes, "edges": kg.edges}


@router.get("/knowledge/node/{node_id}")
async def get_node_detail(node_id: str):
    """返回单个节点的完整信息，包括 MD 文件内容、前置/关联节点"""
    node = kg.get_node(node_id)
    if node is None:
        raise HTTPException(status_code=404, detail=f"节点不存在：{node_id}")

    # 读取 MD 文件内容
    file_path = kg.data_dir / node.get("file", "")
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
            if edge["from"] == node_id:
                related_ids.append(edge["to"])
            elif edge["to"] == node_id:
                related_ids.append(edge["from"])

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
        "file_path": node.get("file", ""),
    }


@router.get("/knowledge/node-ids")
async def get_node_ids():
    """返回所有节点 ID 列表（供前端下拉选择等场景使用）"""
    return {"node_ids": kg.get_node_ids()}


# ══════════════════════════════════════════════════════════════════
#  节点 CRUD
# ══════════════════════════════════════════════════════════════════

@router.post("/knowledge/node")
async def create_node(data: dict):
    """
    创建新节点及 MD 文件

    支持两种调用方式：
    1. 手动创建（前端）：只需传 name, tags, content，ID 自动生成
       例: {"name": "二叉树", "tags": ["数据结构", "二级"], "content": "# 二叉树\n..."}
    2. AI function calling：传完整的 id, name, tags, content 等
       例: {"id": "binary_tree", "name": "二叉树", ...}

    返回: {"status": "ok", "node": {...完整节点信息...}}
    """
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
        md_path = kg.data_dir / node_data["file"]
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


@router.put("/knowledge/node/{node_id}")
async def update_node(node_id: str, data: dict):
    """
    更新节点信息或 MD 文件内容

    可同时更新节点元数据（name, mastery, difficulty, etc.）和 MD 内容。
    兼容 AI function calling 的 update_node_content 调用。
    """
    node = kg.get_node(node_id)
    if node is None:
        raise HTTPException(status_code=404, detail=f"节点不存在：{node_id}")

    # 更新元数据字段
    updatable = ["name", "mastery", "difficulty", "estimated_minutes", "summary", "tags"]
    for key in updatable:
        if key in data:
            node[key] = data[key]
    kg.save()

    # 更新 MD 内容（如果提供了）
    if "content" in data:
        md_path = kg.data_dir / node.get("file", f"nodes/{node_id}.md")
        op = data.get("op", "replace")
        if op == "replace":
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(data["content"])
        elif op == "append":
            with open(md_path, "a", encoding="utf-8") as f:
                f.write(f"\n\n{data['content']}")

    publish("graph_updated")
    return {"status": "ok"}


@router.put("/knowledge/node/{node_id}/info")
async def update_node_info(node_id: str, request: UpdateNodeInfoRequest):
    """
    更新节点的名称和标签（不改变 MD 内容）

    请求体: {"name": "新名称", "tags": ["新标签1", "新标签2"]}
    至少提供 name 或 tags 之一。
    """
    data = request.model_dump(exclude_none=True)
    if not data:
        raise HTTPException(status_code=400, detail="至少需要提供 name 或 tags")

    try:
        kg.update_node_info(node_id, data)
        publish("graph_updated")
        return {"status": "ok", "node_id": node_id, "updated": list(data.keys())}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/knowledge/node/{node_id}")
async def delete_node(node_id: str):
    """
    删除节点：移除节点、删除 MD 文件、移除所有关联边

    返回: {"deleted": true, "removed_edges": 3}
    """
    try:
        removed_edges = kg.remove_node(node_id)
        publish("graph_updated")
        return {"deleted": True, "node_id": node_id, "removed_edges": removed_edges}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.put("/knowledge/node/{node_id}/mastery")
async def update_mastery(node_id: str, data: dict):
    """更新节点的掌握程度（0-100）"""
    node = kg.get_node(node_id)
    if node is None:
        raise HTTPException(status_code=404, detail=f"节点不存在：{node_id}")

    mastery = data.get("mastery")
    if mastery is None or not (0 <= mastery <= 100):
        raise HTTPException(status_code=400, detail="mastery 必须在 0-100 之间")

    node["mastery"] = mastery
    node["added_by"] = data.get("added_by", "ai")
    kg.save()
    publish("graph_updated")
    return {"status": "ok", "node_id": node_id, "mastery": mastery}


# ════════════════════════
#  边 CRUD
# ════════════════════════

@router.post("/knowledge/edge")
async def create_edge(data: dict):
    """
    创建关联边

    请求体: {"from": "节点A", "to": "节点B", "relation": "prerequisite", "label": "说明"}
    重复创建相同的 from+to+relation 边会返回 409。
    """
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


@router.put("/knowledge/edge/{edge_index}")
async def update_edge(edge_index: int, request: UpdateEdgeRequest):
    """
    更新边的 relation 和/或 label

    参数:
        edge_index: 边在 edges 数组中的索引（0-based）

    请求体: {"relation": "related", "label": "新的关系说明"}
    """
    data = request.model_dump(exclude_none=True)
    if not data:
        raise HTTPException(status_code=400, detail="至少需要提供 relation 或 label")

    try:
        kg.update_edge(edge_index, data)
        updated_edge = kg.edges[edge_index]
        publish("graph_updated")
        return {"status": "ok", "edge_index": edge_index, "edge": updated_edge}
    except IndexError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/knowledge/edge/{edge_index}")
async def delete_edge(edge_index: int):
    """
    删除指定索引的边

    返回: {"deleted": true, "edge_index": 0}
    """
    try:
        kg.remove_edge(edge_index)
        publish("graph_updated")
        return {"deleted": True, "edge_index": edge_index}
    except IndexError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ══════════════════════════════════════════════════════════════════
#  AI 编辑（向后兼容）
# ══════════════════════════════════════════════════════════════════

@router.post("/knowledge/ai/edit")
async def ai_edit_graph(data: dict):
    """AI 助手编辑知识图谱（向后兼容的快捷接口）"""
    action = data.get("action")
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
            for key in ["mastery", "difficulty", "estimated_minutes", "summary", "tags"]:
                if key in data:
                    node[key] = data[key]
            kg.save()
            publish("graph_updated")
            return {"status": "ok"}
        else:
            raise HTTPException(status_code=400, detail=f"未知操作：{action}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ══════════════════════════════════════════════════════════════════
#  AI 建议执行（公共函数，供 llm_client 和 chat_service 共用）
# ══════════════════════════════════════════════════════════════════

def _load_suggestions() -> list:
    """加载待审核建议文件，文件不存在时返回空列表"""
    if not SUGGESTIONS_PATH.exists():
        return []
    try:
        with open(SUGGESTIONS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return []


def _save_suggestions(suggestions: list) -> None:
    """保存待审核建议到文件"""
    with open(SUGGESTIONS_PATH, "w", encoding="utf-8") as f:
        json.dump(suggestions, f, ensure_ascii=False, indent=2)


def create_node_from_ai(node_id: str, node_name: str,
                        tags: list | None = None, summary: str = "",
                        difficulty: int = 3, estimated_minutes: int = 15,
                        content: str = "", from_nodes: list | None = None,
                        confidence: float | None = None) -> str:
    """
    公共函数：创建一个 AI 生成的节点（写图谱 + 写 MD 文件 + 建前置边）。
    供 execute_kg_tool() 和 _apply_suggestion() 共用，消除重复代码。

    参数:
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
        "created_at": "",
    }
    kg.add_node(node_data)

    # 写 MD 文件
    md_path = kg.data_dir / node_data["file"]
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
                })
                edge_count += 1
            except ValueError:
                pass

    return f"已创建节点「{node_name}」(ID: {node_id})，关联 {edge_count} 条边"


def _apply_suggestion(suggestion: dict) -> str:
    """
    执行单条图谱分析建议（来自 GraphAnalyzer），返回结果描述字符串。
    注意：与 execute_kg_tool() 不同，suggestion 数据结构来自分析 LLM 的 JSON。
    """
    action = suggestion.get("action")

    if action == "add_node":
        node = suggestion.get("node", {})
        return create_node_from_ai(
            node_id=node.get("id", ""),
            node_name=node.get("name", ""),
            tags=node.get("tags"),
            summary=node.get("summary", ""),
            difficulty=int(node.get("difficulty", 3)),
            estimated_minutes=int(node.get("estimated_minutes", 15)),
            content=node.get("content", ""),
            from_nodes=[e["from"] for e in suggestion.get("recommended_edges", [])
                        if e.get("to") == node.get("id", "")],
            confidence=suggestion.get("confidence"),
        )

    elif action == "add_edge":
        edge = suggestion["edge"]
        kg.add_edge({
            "from": edge["from"], "to": edge["to"],
            "relation": edge.get("relation", "related"),
            "label": edge.get("label", ""),
            "added_by": "ai", "confidence": suggestion.get("confidence"),
        })
        return f"已创建边: {edge['from']} → {edge['to']} ({edge.get('relation', 'related')})"

    elif action == "update_content":
        kg.update_node_content(suggestion["node_id"],
                               suggestion.get("content_snippet", ""), mode="append")
        return f"已更新节点 {suggestion['node_id']} 的内容"

    else:
        raise ValueError(f"不支持的操作：{action}")
