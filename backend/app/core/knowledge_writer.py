"""AI 写图谱层（纯业务函数，2026-09-08 从 api 层下沉）。

消除 core/services → api 的反向依赖（AGENTS.md §3.6-1），供三类调用方共用：
- agent_tools._h_add_node（Agent 工具 add_knowledge_node）
- chat_service 图谱分析建议的自动应用 / 待审核持久化
- api 路由层按需复用

核心语义：create_node_from_ai 创建/更新一个 AI 生成的节点（写图谱 + 写 MD 文件 +
建前置边）。节点 ID 已存在时自动转"补全字段 + 追加内容"的更新模式，不报错——
避免 Agent 循环中重复创建同一概念时 ValueError 中断工具执行。
"""

import json
from pathlib import Path

from app.core.knowledge_graph import KnowledgeGraph

__all__ = ["create_node_from_ai", "apply_suggestion", "load_suggestions", "save_suggestions"]


def load_suggestions(data_dir: Path) -> list:
    """加载待审核建议文件，文件不存在时返回空列表"""
    suggestions_path = data_dir / "ai_suggestions.json"
    if not suggestions_path.exists():
        return []
    try:
        with open(suggestions_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return []


def save_suggestions(data_dir: Path, suggestions: list) -> None:
    """保存待审核建议到文件"""
    suggestions_path = data_dir / "ai_suggestions.json"
    with open(suggestions_path, "w", encoding="utf-8") as f:
        json.dump(suggestions, f, ensure_ascii=False, indent=2)


def create_node_from_ai(kg: KnowledgeGraph, node_id: str, node_name: str,
                        tags: list | None = None, summary: str = "",
                        difficulty: int = 3, estimated_minutes: int = 15,
                        content: str = "", from_nodes: list | None = None,
                        confidence: float | None = None) -> str:
    """
    公共函数：创建或更新一个 AI 生成的节点（写图谱 + 写 MD 文件 + 建前置边）。
    供 Agent 工具（execute_kg_tool）和图谱建议应用（apply_suggestion）共用。

    节点 ID 已存在时自动转为更新模式（追加内容/补全字段），不报错——避免
    Agent 循环中重复创建同一概念时 ValueError 中断工具执行。

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
    existing = kg.get_node(node_id)

    if existing is not None:
        # 节点已存在 → 更新模式：补全字段 + 追加内容
        update_data: dict[str, object] = {}
        if summary and not existing.get("summary"):
            update_data["summary"] = summary
        if tags:
            existing_tags = set(existing.get("tags", []))
            new_tags = list(existing_tags | set(tags))
            if new_tags != existing.get("tags"):
                update_data["tags"] = new_tags
        if existing.get("difficulty", 3) != difficulty:
            update_data["difficulty"] = difficulty
        if existing.get("estimated_minutes", 15) != estimated_minutes:
            update_data["estimated_minutes"] = estimated_minutes
        if confidence is not None and existing.get("confidence") is None:
            update_data["confidence"] = confidence

        if update_data:
            try:
                kg.update_node_info(node_id, update_data, caller="ai")
            except PermissionError:
                # 人类创建的节点 → 跳过字段更新，但仍可追加内容
                pass

        # 追加内容到 MD 文件（有新内容时）
        md_path = kg.nodes_dir / f"{node_id}.md"
        if content.strip():
            try:
                kg.update_node_content(node_id, content, mode="append", caller="ai")
            except PermissionError:
                pass

        # 补建前置边（已存在的边会 ValueError 静默跳过）
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
                except (ValueError, PermissionError):
                    pass

        return f"节点「{node_name}」(ID: {node_id}) 已存在，已补全 {len(update_data)} 个字段，关联 {edge_count} 条新边"

    # 节点不存在 → 创建新模式
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


def apply_suggestion(kg: KnowledgeGraph, suggestion: dict) -> str:
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
