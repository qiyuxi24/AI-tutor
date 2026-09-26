"""AI 写图谱层（纯业务函数，2026-09-08 从 api 层下沉）。

消除 core/services → api 的反向依赖（AGENTS.md §3.6-1），供三类调用方共用：
- agent_tools.tools.add_knowledge_node（Agent 工具 add_knowledge_node）
- chat_service 图谱分析建议的自动应用 / 待审核持久化
- api 路由层按需复用

核心语义：create_node_from_ai 创建/更新一个 AI 生成的节点（写图谱 + 写 MD 文件 +
建前置边）。节点 ID 已存在时自动转"补全字段 + 追加内容"的更新模式，不报错——
避免 Agent 循环中重复创建同一概念时 ValueError 中断工具执行。

**MD 落盘不自持模板**：一律走 `KnowledgeGraph.create_node_with_content()`，
本模块只负责"建什么节点"（字段与归属），不负责"文件长什么样"。
"""

import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse

from app.core.kg_taxonomy import assign_taxonomy_sync
from app.core.knowledge_graph import KnowledgeGraph

__all__ = ["create_node_from_ai", "create_node_from_webpage",
           "apply_suggestion", "load_suggestions", "save_suggestions"]

# 网页存档节点正文上限：防超长页面把节点 MD 撑到数 MB（前端 marked 渲染也吃力）
_WEB_NODE_MAX_CHARS = 50_000
# 节点名（页面标题）长度上限，避免超长 <title> 撑坏图谱视图
_WEB_NODE_NAME_MAX_LEN = 120


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


def _find_same_name(kg: KnowledgeGraph, node_name: str, subject: str) -> str:
    """
    在本人图谱里找同名节点的 ID（同名并轨，返回空串表示没有）。

    判定逻辑（归一化 + 学科过滤）的唯一实现在 `KnowledgeGraph.find_node_by_name`，
    这里只是"取 id"的薄壳 —— 写入层（create_node_with_content）用的是同一份判定。

    ponytail: 只做字符串层同名，不做嵌入语义去重（那要在 Agent 热路径上多付一次嵌入 +
    LLM 二次确认，且当前嵌入 API 欠费）。要升级就复用 `kb/graph_generator.py` 的
    `_find_dedup_candidates` + `_confirm_synonyms` 双闸。
    """
    node = kg.find_node_by_name(node_name or "", subject)
    return node["id"] if node else ""


def create_node_from_ai(kg: KnowledgeGraph, node_id: str, node_name: str,
                        tags: list | None = None, summary: str = "",
                        difficulty: int = 3, estimated_minutes: int = 15,
                        content: str = "", from_nodes: list | None = None,
                        confidence: float | None = None,
                        subject: str = "", board: str = "") -> str:
    """
    公共函数：创建或更新一个 AI 生成的节点（写图谱 + 写 MD 文件 + 建前置边）。
    供 Agent 工具（execute_kg_tool）和图谱建议应用（apply_suggestion）共用。

    节点 ID 已存在时自动转为更新模式（追加内容/补全字段），不报错——避免
    Agent 循环中重复创建同一概念时 ValueError 中断工具执行。

    **同名并轨**：ID 不同但**中文名完全相同**（同一学科内）时也走更新模式 ——
    模型每轮都可能给同一个概念编出不同 ID（实测：`harmony_dev_intro` / `harmonyos_intro`
    同名「鸿蒙开发入门」并存，见 `docs/知识图谱/知识图谱_模块结构与封装调研.md` §7）。

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
        subject:          显式学科（Agent 工具让模型自报）；空则由 kg_taxonomy 自动判定
        board:            显式板块；空则由 kg_taxonomy 自动判定

    返回:
        操作结果描述字符串
    """
    # 显式归属优先（Agent 工具让模型自报）：subject 放 tags 首位（学科由 node_subject
    # 派生，取第一个非难度标签）、board 直接落库；缺的部分交给 kg_taxonomy 自动判定，
    # 两边都齐就不会再烧 LLM。
    subject = (subject or "").strip()
    board = (board or "").strip()
    if subject and subject not in (tags or []):
        tags = [subject, *(tags or [])]

    existing = kg.get_node(node_id)
    if existing is None:
        node_id = _find_same_name(kg, node_name, subject) or node_id
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
        # 归属补全：老节点可能既没有学科标签也没有板块（自动判定失败则原样不动）
        merged = {
            "name": node_name,
            "summary": summary or existing.get("summary", ""),
            "tags": update_data.get("tags", existing.get("tags", [])),
            "subject": subject or existing.get("subject", ""),
            "board": board or existing.get("board", ""),
        }
        assign_taxonomy_sync(kg, merged)
        if merged["tags"] != existing.get("tags"):
            update_data["tags"] = merged["tags"]
        if merged.get("subject") and merged["subject"] != existing.get("subject"):
            update_data["subject"] = merged["subject"]
        if merged.get("board") and merged["board"] != existing.get("board"):
            update_data["board"] = merged["board"]

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
        # KG-D1：学科显式落列，不再只依赖「tags 首位被 node_subject 反推」；
        # 空则 add_node 按 tags 兜底推导（老路径/老调用方无需改动）。
        "subject": subject,
        "board": board,
        "summary": summary,
        "mastery": 0,
        "difficulty": difficulty,
        "estimated_minutes": estimated_minutes,
        "added_by": "ai",
        "confidence": confidence,
    }
    # 未指定学科/板块时自动判定（规则+LLM），失败保持未分类
    assign_taxonomy_sync(kg, node_data)
    # 建库 + 写 MD 一次完成（模板与来源标注的唯一来源在 KnowledgeGraph）
    kg.create_node_with_content(node_data, content, origin="ai")

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


def _web_node_id(user_id: int, url: str) -> str:
    """网页节点 ID：由 URL 派生（确定性 → 天然幂等）；带 user_id 因 nodes.id 是全局主键"""
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
    return f"web_{user_id}_{digest}"


def create_node_from_webpage(kg: KnowledgeGraph, url: str, title: str = "",
                             content: str = "") -> str:
    """
    把 AI 联网抓取到的网页正文存档为一个图谱节点（origin="web"）。

    与 `create_node_from_ai` 的区别（**刻意不复用它**）：
    - **不并轨、不追加**：节点 ID 由 URL 派生，同一 URL 第二次抓取直接跳过 ——
      重复抓取不该重写节点，也不该和人类编辑打架；而 create_node_from_ai 带
      "同名并轨 + 存在即追加内容"语义，会把网页误并到同名知识点上。
    - 归属不做 LLM 判定（省一次调用）：tags = ["网页", <host>]，学科因此派生为「网页」。
    - 只负责首次落档；要"刷新正文"另开接口（避免每次抓取都重写节点）。

    参数:
        kg:      KnowledgeGraph 实例（已绑定 user_id）
        url:     网页地址（幂等键；空则直接返回，不写任何东西）
        title:   页面标题（取不到时用 URL 兜底）
        content: Markdown 正文（超 `_WEB_NODE_MAX_CHARS` 截断并注明）

    返回:
        结果描述字符串（供工具层回填给模型）
    """
    url = (url or "").strip()
    if not url:
        return "未提供网址，网页未存档。"

    node_id = _web_node_id(kg.user_id, url)
    if kg.get_node(node_id) is not None:
        return f"该网页已在图谱中（节点 ID: {node_id}），未重复写入。"

    md = (content or "").strip()
    if len(md) > _WEB_NODE_MAX_CHARS:
        md = md[:_WEB_NODE_MAX_CHARS] + f"\n\n……（网页正文过长，已截断，共 {len(md)} 字符）"

    host = urlparse(url).hostname or ""
    name = ((title or "").strip() or url)[:_WEB_NODE_NAME_MAX_LEN]
    node_data = {
        "id": node_id,
        "name": name,
        "file": f"nodes/{node_id}.md",
        "tags": [t for t in ("网页", host) if t],
        "board": "",
        "summary": f"来源：{url}",
        "mastery": 0,
        "difficulty": 3,
        "estimated_minutes": 15,
        "added_by": "ai",
    }
    # 建库 + 写 MD 一次完成（模板与来源标注的唯一来源在 KnowledgeGraph）
    kg.create_node_with_content(node_data, md, origin="web")
    return (f"已存档为图谱节点「{name}」(ID: {node_id})，可在图谱中双击查看。")


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
