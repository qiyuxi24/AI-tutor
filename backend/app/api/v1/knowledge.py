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
    """返回单个节点的完整信息，包括 MD 文件内容和前置/关联节点"""
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
        "related_nodes": related_ids
    }