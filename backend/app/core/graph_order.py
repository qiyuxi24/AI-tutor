"""图谱学习顺序的**同层排序口径**（唯一实现）＋「还没学会」阈值（唯一定义）。

为什么单独一个模块（B3-1 / B3-2）
--------------------------------
图谱里有**两套排序**，它们不是同一个东西，**没有合并成一个函数**：

| | `KnowledgeGraph.topological_sort()` | `graph_middleware._layers()` |
|---|---|---|
| 输入 | 全量 prerequisite 边 | 一个切片内的 prerequisite 边 |
| 输出 | **全序**（所有知识点的学习顺序） | **层号**（每个节点第几层） |
| 破环 | 排不进的节点按 id 追加到末尾 | DFS 丢 back-edge，再算最长前置链 |
| 附带 | 无 | `_lift_related_only_levels`（相关概念并入同层） |

合并任一方都会破坏另一方的契约（看板要层号、学习路径要全序；破环策略与"相关概念并入同层"
都不是学习顺序的语义）。判定与证据见 `docs/架构/待拍板问题_2026-10-10.md` §A。

但它们对"**同一层里谁该先学**"必须用同一个口径 —— 那份口径住在这里：

    前置未达标排后 → 掌握度升序 → id 升序（稳定可复现）

调用方：
* `KnowledgeGraph.topological_sort()` —— Kahn 每次从"当前可学集合"里挑这个键最小的；
* `graph_middleware.path_board()` 的 `recommended` —— 同一个键（前面加层号）。

`MASTERY_NEXT_THRESHOLD` 也住在这里：它同时是"还没学会"（推荐 / 主路径 / 前置是否达标）的
唯一阈值。以前 `50` 写在三个地方（`knowledge_graph.py` 两处 + `graph_middleware.py` 一处），
改一处忘一处就会让"推荐下一步"和"前置达标判定"给出互相矛盾的答案。

⚠️ 与另外两个掌握度常量**不是**一回事，别混：
    `MASTERY_WEAK = 30` / `MASTERY_MASTERED = 70`（`graph_middleware.mastery_bucket` 的四档分档）。
"""

from __future__ import annotations

from typing import Iterable, Mapping, Optional

# 「还没学会」阈值：推荐下一步 / 主路径起点 / 前置是否达标 共用（唯一真值）。
MASTERY_NEXT_THRESHOLD = 50

__all__ = ["MASTERY_NEXT_THRESHOLD", "is_unmastered", "order_key", "order_candidates"]


def is_unmastered(node: Optional[dict]) -> bool:
    """该节点是否"还没学会"（`None` / 缺字段 = 没学会 —— 读不到的节点不该被当成已掌握）。"""
    return int((node or {}).get("mastery") or 0) < MASTERY_NEXT_THRESHOLD


def order_key(node: Optional[dict], *, prereq_unmastered: bool = False) -> tuple:
    """同层排序键：`(前置未达标?, 掌握度, id)`。

    · **前置达标优先**：前置还没学会的节点哪怕自己分数低，也排在"前置已达标"的后面 ——
      否则会把人推到"地基没打好的楼上"（B3-1 的用户口径）。
    · 掌握度**升序**：同层里先补最弱的那一节。
    · `id` 兜底：同样分数时结果必须确定、可复现（前端高亮/看板位置会跟着它）。
    """
    nid = str((node or {}).get("id") or "")
    return (1 if prereq_unmastered else 0, int((node or {}).get("mastery") or 0), nid)


def order_candidates(candidates: Iterable[str], nodes_by_id: Mapping[str, dict],
                     prereqs_of: Mapping[str, Iterable[str]]) -> list[str]:
    """把"当前可学集合"按同层口径排成有序列表（纯函数，不改动入参）。

    `prereqs_of[nid]` 是该节点的直接前置 id 集合；缺失的前置节点按"没学会"处理。
    """
    return sorted(candidates, key=lambda nid: order_key(
        nodes_by_id.get(nid) or {"id": nid},
        prereq_unmastered=any(is_unmastered(nodes_by_id.get(p))
                              for p in prereqs_of.get(nid, ())),
    ))
