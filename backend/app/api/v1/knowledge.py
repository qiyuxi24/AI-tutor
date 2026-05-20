"""知识图谱 API 路由"""
from fastapi import APIRouter, HTTPException
from app.core.knowledge_graph import KnowledgeGraph

router = APIRouter()
kg = KnowledgeGraph()


@router.get("/knowledge/graph")
async def get_graph():
    """返回完整图谱数据"""
    return {
        "nodes": kg.nodes,
        "edges": kg.edges
    }


@router.get("/knowledge/node/{node_id}")
async def get_node_detail(node_id: str):
    """返回单个节点的完整信息"""
    node = kg.get_node(node_id)
    if node is None:
        raise HTTPException(status_code=404, detail=f"节点不存在：{node_id}")

    file_path = kg.data_dir / node.get("file", "")
    content = ""
    if file_path.exists():
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
    else:
        content = f"（文件未找到：{file_path}）"

    prerequisites = kg.get_prerequisites(node_id)
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
    }


@router.put("/knowledge/node/{node_id}/mastery")
async def update_mastery(node_id: str, data: dict):
    """更新节点的掌握程度（AI 或用户设置）"""
    node = kg.get_node(node_id)
    if node is None:
        raise HTTPException(status_code=404, detail=f"节点不存在：{node_id}")

    mastery = data.get("mastery")
    if mastery is None or not (0 <= mastery <= 100):
        raise HTTPException(status_code=400, detail="mastery 必须在 0-100 之间")

    node["mastery"] = mastery
    node["added_by"] = data.get("added_by", "ai")
    kg.save()  # 持久化到文件
    return {"status": "ok", "node_id": node_id, "mastery": mastery}


@router.post("/knowledge/ai/edit")
async def ai_edit_graph(data: dict):
    """AI 助手编辑知识图谱（添加/修改节点或边）"""
    action = data.get("action")  # "add_node" | "add_edge" | "update_node"
    try:
        if action == "add_node":
            kg.add_node(data["node"])
            return {"status": "ok", "message": f"节点 {data['node']['id']} 已添加"}
        elif action == "add_edge":
            kg.add_edge(data["edge"])
            return {"status": "ok", "message": "边已添加"}
        elif action == "update_node":
            node = kg.get_node(data["node_id"])
            if node is None:
                raise HTTPException(status_code=404, detail=f"节点不存在：{data['node_id']}")
            for key in ["mastery", "difficulty", "estimated_minutes", "summary", "tags"]:
                if key in data:
                    node[key] = data[key]
            kg.save()
            return {"status": "ok", "message": f"节点 {data['node_id']} 已更新"}
        else:
            raise HTTPException(status_code=400, detail=f"未知操作：{action}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))