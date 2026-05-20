"""知识图谱 API 路由"""
from fastapi import APIRouter, HTTPException
from app.core.knowledge_graph import KnowledgeGraph

router = APIRouter()
kg = KnowledgeGraph()


# ── 读取 ──

@router.get("/knowledge/graph")
async def get_graph():
    """返回完整图谱数据"""
    return {"nodes": kg.nodes, "edges": kg.edges}


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
        "file_path": node.get("file", ""),
    }


# ── 写入（供 AI function calling 使用）───

@router.post("/knowledge/node")
async def create_node(data: dict):
    """创建新节点及 MD 文件"""
    try:
        node_data = {
            "id": data["id"],
            "name": data["name"],
            "file": f"nodes/{data['id']}.md",
            "tags": data.get("tags", []),
            "summary": data.get("summary", ""),
            "mastery": 0,
            "difficulty": data.get("difficulty", 3),
            "estimated_minutes": data.get("estimated_minutes", 15),
            "added_by": data.get("added_by", "ai"),
            "created_at": "",
        }
        kg.add_node(node_data)
        md_path = kg.data_dir / node_data["file"]
        md_content = data.get("content", f"# {data['name']}\n\n待完善...\n")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_content)
        return {"status": "ok", "node_id": data["id"]}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except KeyError as e:
        raise HTTPException(status_code=400, detail=f"缺少必填字段：{e}")


@router.put("/knowledge/node/{node_id}")
async def update_node(node_id: str, data: dict):
    """更新节点信息或 MD 文件内容"""
    node = kg.get_node(node_id)
    if node is None:
        raise HTTPException(status_code=404, detail=f"节点不存在：{node_id}")

    for key in ["name", "mastery", "difficulty", "estimated_minutes", "summary", "tags"]:
        if key in data:
            node[key] = data[key]
    kg.save()

    if "content" in data:
        md_path = kg.data_dir / node.get("file", f"nodes/{node_id}.md")
        op = data.get("op", "replace")
        if op == "replace":
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(data["content"])
        elif op == "append":
            with open(md_path, "a", encoding="utf-8") as f:
                f.write(f"\n\n{data['content']}")

    return {"status": "ok"}


@router.delete("/knowledge/node/{node_id}")
async def delete_node(node_id: str):
    """删除节点和对应 MD 文件"""
    node = kg.get_node(node_id)
    if node is None:
        raise HTTPException(status_code=404, detail=f"节点不存在：{node_id}")

    md_path = kg.data_dir / node.get("file", f"nodes/{node_id}.md")
    if md_path.exists():
        md_path.unlink()

    kg.nodes = [n for n in kg.nodes if n["id"] != node_id]
    kg.edges = [e for e in kg.edges if e["from"] != node_id and e["to"] != node_id]
    kg.save()
    return {"status": "ok"}


@router.post("/knowledge/edge")
async def create_edge(data: dict):
    """创建关联边"""
    try:
        kg.add_edge(data)
        return {"status": "ok"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/knowledge/node/{node_id}/mastery")
async def update_mastery(node_id: str, data: dict):
    """更新节点的掌握程度"""
    node = kg.get_node(node_id)
    if node is None:
        raise HTTPException(status_code=404, detail=f"节点不存在：{node_id}")

    mastery = data.get("mastery")
    if mastery is None or not (0 <= mastery <= 100):
        raise HTTPException(status_code=400, detail="mastery 必须在 0-100 之间")

    node["mastery"] = mastery
    node["added_by"] = data.get("added_by", "ai")
    kg.save()
    return {"status": "ok", "node_id": node_id, "mastery": mastery}


@router.post("/knowledge/ai/edit")
async def ai_edit_graph(data: dict):
    """AI 助手编辑知识图谱（向后兼容）"""
    action = data.get("action")
    try:
        if action == "add_node":
            kg.add_node(data["node"])
            return {"status": "ok"}
        elif action == "add_edge":
            kg.add_edge(data["edge"])
            return {"status": "ok"}
        elif action == "update_node":
            node = kg.get_node(data["node_id"])
            if node is None:
                raise HTTPException(status_code=404, detail=f"节点不存在：{data['node_id']}")
            for key in ["mastery", "difficulty", "estimated_minutes", "summary", "tags"]:
                if key in data:
                    node[key] = data[key]
            kg.save()
            return {"status": "ok"}
        else:
            raise HTTPException(status_code=400, detail=f"未知操作：{action}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))