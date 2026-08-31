"""
知识图谱按需请求中间件（Graph Middleware）

职责：
    统一处理"学科 → 知识板块 → 节点/边"的多级按需切片逻辑。
    前端只需声明要看哪个学科（subject）下的哪个板块（board），
    本中间件负责切片并返回该板块的局部子图，避免一次性拉取全量数据。

设计：
    纯函数式切片，不持有状态、不直接连数据库，只依赖 KnowledgeGraph 的查询方法，
    因此可独立测试、可复用（图谱页/学习路径/对话上下文均可调用）。

支持粒度（按需返回，粒度逐级细化）：
    scope="subjects"   → 全部学科列表（轻量，替代原 /knowledge/subjects）
    scope="boards"     → 指定学科下的板块列表（含节点数/掌握度统计）
    scope="graph"      → 指定板块的局部子图（nodes + edges）
    scope="subject"    → 整学科的图（当未指定板块，或板块为空时的兜底）
"""

from __future__ import annotations

from typing import Optional


def slice_graph(kg, subject: Optional[str] = None,
                board: Optional[str] = None) -> dict:
    """
    按需切片图谱数据。

    参数:
        kg: KnowledgeGraph 实例（已绑定当前用户）
        subject: 学科名；None 表示不限学科（此时 board 会被忽略）
        board: 板块名；指定后返回该板块局部子图，否则返回整学科图

    返回:
        {
            "nodes": [...],
            "edges": [...],
            "subject": str | None,
            "board": str | None,
            "node_count": int,
            "edge_count": int,
        }
    """
    subject = (subject or "").strip() or None
    board = (board or "").strip() or None

    # 未指定学科 → 返回全量（兼容旧行为，向前端显式标注 subject=None）
    if not subject:
        nodes = list(kg.nodes)
        edges = list(kg.edges)
        return {
            "nodes": nodes, "edges": edges,
            "subject": None, "board": None,
            "node_count": len(nodes), "edge_count": len(edges),
        }

    # 指定学科 + 板块 → 返回板块局部子图
    if board:
        nodes = kg.get_nodes_by_board(subject, board)
        edges = kg.get_edges_by_board(subject, board)
    else:
        # 只指定学科 → 返回整学科图
        nodes = kg.get_nodes_by_subject(subject)
        edges = kg.get_edges_by_subject(subject)

    return {
        "nodes": nodes, "edges": edges,
        "subject": subject, "board": board or None,
        "node_count": len(nodes), "edge_count": len(edges),
    }


def list_subjects(kg) -> list[str]:
    """返回当前用户所有学科（轻量接口，供学科选择器/板块导航使用）"""
    return kg.get_subjects()


def list_boards(kg, subject: str) -> list[dict]:
    """
    返回某学科下的知识板块列表（含统计），供板块侧栏渲染。
    仅返回有节点的板块；未分组节点以 {"board": ""} 形式出现。
    """
    return kg.get_boards_by_subject(subject)
