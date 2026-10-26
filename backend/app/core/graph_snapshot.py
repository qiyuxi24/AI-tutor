"""知识图谱的**只读快照** —— 模型侧工具与本地面板共用的唯一取数实现（2026-10-26，P1/P5）。

## 为什么单独一个模块
DSH 插件里有两条出口都要同一份数据：
  · 模型侧：`agent_tools/tools/kg_snapshot.py` → 给模型看的中文文本；
  · 面板侧：`mcp_servers/local_http.py` 的 `GET /graph` → 给 `client.js` 看的 JSON。
两处各写一遍查询，就会在"学科怎么算 / 已掌握阈值是多少 / next 取哪个"上漂移
（DSH 施工计划 §P5 明确要求复用同一个函数）。

## 边界
- 只用 `KnowledgeGraph` **现成的只读方法**（`get_subjects` / `get_nodes_by_subject` /
  `get_next_to_learn`），本模块不写任何 SQL。
- "已掌握"口径走 `core/graph_order.py` 的唯一阈值（`is_unmastered`），
  不在本模块写第二个 50。
- 本模块**不兜异常**：取数失败属于调用方语境（工具要转成友好文本、HTTP 要回 JSON 500），
  各自在边界上兜（`kg_snapshot.handler` / `local_http` 的 do_GET）。

## 数据结构（`GET /graph` 的契约，改动要同步 `plugins/dsh-ai-tutor/lib/client.js`）
```json
{
  "subjects": [{"name": "数据结构", "nodeCount": 12, "masteredCount": 5,
                "nodes": [{"id": "n1", "title": "数组", "mastery": 80, "board": "线性结构"}]}],
  "nodeCount": 42,
  "next": {"node_id": "...", "name": "...", "mastery": 30, "reason": "...", "subject": "..."} | null,
  "dataRoot": "<绝对路径>"
}
```
- `nodes` 每个学科最多 `limit` 条（默认 10，夹取 1~30）；`nodeCount`/`masteredCount`
  是**该学科全量**的统计，不受 `limit` 截断影响。
- `mastery` 是图谱原始口径 0~100（`nodes.mastery`，不是 0~1 比率）。
- `next` 恒为**整张图**的推荐（`get_next_to_learn`）；`subject` 过滤只收窄 subjects 列表，
  不收窄 next —— 前端/模型都能看到"全图下一步"，过滤时由展示层标注。
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

from app.core import storage
from app.core.graph_order import is_unmastered

# 每个学科默认/最多列出的节点数（"别把整张图倒给模型"与"面板要能看全"之间取 10/30）
DEFAULT_LIMIT = 10
MAX_LIMIT = 30


def clamp_limit(limit: Any = DEFAULT_LIMIT) -> int:
    """把入参夹到 1~30：模型/面板可能传字符串、0、负数或很大 —— 一律不抛。"""
    try:
        value = int(limit)
    except (TypeError, ValueError):
        return DEFAULT_LIMIT
    return max(1, min(value, MAX_LIMIT))


def _node_entry(kg, node: dict) -> dict:
    """节点 → 对外快照条目（只暴露面板/模型需要的四个字段）。"""
    return {
        "id": str(node.get("id") or ""),
        "title": str(node.get("name") or ""),
        "mastery": int(node.get("mastery") or 0),
        "board": kg.node_board(node),
    }


def snapshot(kg, subject: str = "", limit: int = DEFAULT_LIMIT) -> dict:
    """当前用户的图谱快照（JSON 可序列化；形状见模块 docstring）。

    参数:
        kg:      `KnowledgeGraph` 实例（按 user_id 隔离，调用方负责 close）。
        subject: 只看某个学科；空串 = 全部学科。不存在的学科 → `subjects=[]`。
        limit:   每个学科最多列出的节点数（夹取 1~30）。
    返回:
        见模块 docstring 的 JSON 形状。
    """
    subject = (subject or "").strip()
    per_subject = clamp_limit(limit)

    all_subjects = kg.get_subjects()
    if subject:
        # 不存在的学科给空列表（不是抛错）：调用方/模型据此判断"是不是写错了学科名"
        names = [subject] if subject in all_subjects else []
    else:
        names = all_subjects

    subjects: list[dict] = []
    total_nodes = 0
    for name in names:
        nodes = kg.get_nodes_by_subject(name)
        total_nodes += len(nodes)
        subjects.append({
            "name": name,
            "nodeCount": len(nodes),
            "masteredCount": sum(1 for n in nodes if not is_unmastered(n)),
            "nodes": [_node_entry(kg, n) for n in nodes[:per_subject]],
        })

    return {
        "subjects": subjects,
        "nodeCount": total_nodes,
        "next": kg.get_next_to_learn(),
        "dataRoot": str(storage.DATA_ROOT),
    }


def _empty_text(subject: str, available: Iterable[str]) -> str:
    """空图谱 / 学科不存在时的友好中文（含"那就怎么办"，模型会照这句话改行为）。"""
    names = list(available)
    if subject and names:
        return (
            f"知识图谱里没有找到学科「{subject}」下的知识点。"
            f"当前图谱包含的学科：{'、'.join(names)}。"
            "可以省略 subject 参数查看全部学科。"
        )
    if subject:
        return (
            f"知识图谱还是空的，没有学科「{subject}」的知识点。"
            "学生还没建图，请基于通用知识回答，需要资料时用 rag_search 检索已上传的知识库。"
        )
    return (
        "知识图谱还是空的（当前用户没有任何知识点）。"
        "学生还没建图：请基于通用知识回答，需要资料时用 rag_search 检索已上传的知识库，"
        "不要编造学生的学习进度。"
        f"（数据根：{storage.DATA_ROOT}）"
    )


def render_text(data: dict, subject: str = "",
                available: Optional[Iterable[str]] = None) -> str:
    """把快照 dict 渲染成给模型看的中文文本（工具 handler 的唯一出口）。

    参数:
        data:      `snapshot()` 的返回值。
        subject:   本次请求的学科过滤（用于措辞：过滤时 next 要标明是"全图"推荐）。
        available: 完整学科列表（过滤后 subjects 为空时用来提示"现有学科有哪些"）。
    """
    subjects = data.get("subjects") or []
    if not subjects:
        return _empty_text(subject, available or [])

    total = int(data.get("nodeCount") or 0)
    mastered = sum(int(s.get("masteredCount") or 0) for s in subjects)
    lines = [
        f"知识图谱快照：{len(subjects)} 个学科 / {total} 个知识点，"
        f"已掌握 {mastered} 个（掌握度 ≥ 50 视为已掌握）。"
    ]

    for s in subjects:
        count = int(s.get("nodeCount") or 0)
        mastered_s = int(s.get("masteredCount") or 0)
        lines.append("")
        lines.append(f"【{s.get('name') or '未分学科'}】{count} 个知识点，已掌握 {mastered_s} 个")
        nodes = s.get("nodes") or []
        for n in nodes:
            board = f"，板块：{n['board']}" if n.get("board") else ""
            lines.append(
                f"- {n.get('title') or n.get('id')}"
                f"（掌握度 {int(n.get('mastery') or 0)}{board}；id: {n.get('id')}）"
            )
        hidden = count - len(nodes)
        if hidden > 0:
            lines.append(f"- …还有 {hidden} 个未列出（可调大 limit 或指定 subject 查看）")

    nxt = data.get("next")
    if nxt:
        scope = "（全图）" if subject else ""
        lines.append("")
        lines.append(
            f"下一步推荐{scope}：「{nxt.get('name') or nxt.get('node_id')}」"
            f"（学科：{nxt.get('subject') or '未分学科'}，掌握度 {int(nxt.get('mastery') or 0)}）"
        )
        reason = (nxt.get("reason") or "").strip()
        if reason:
            lines.append(f"  理由：{reason}")

    return "\n".join(lines)
