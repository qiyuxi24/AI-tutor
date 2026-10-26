"""工具 `kg_snapshot` —— 只读返回当前用户知识图谱的摘要（学科 / 节点 / 掌握度 / 下一步）。

为什么需要它：外部宿主（DSH）里的模型看不到主系统的图谱界面，只有一份文本；
没有这个工具，模型只能靠 `rag_search` 猜"学生学过什么"，会重复讲已掌握的内容。
只读、无副作用 → 免确认（`TIER_FREE`）。

实现全在 `core/graph_snapshot.py`（与 `local_http` 的 `GET /graph` 复用同一份取数），
本模块只是"参数夹取 + 异常兜底 + 中文渲染"的薄壳（`tools/README.md` 三条硬约定）。
"""

from app.core import graph_snapshot

from ..registry import TIER_FREE, _spec

DESCRIPTION = (
    "读取学生知识图谱的只读快照：有哪些学科、每个学科的知识点与掌握度、"
    "以及推荐的下一步学习内容。当你想知道「学生学过什么 / 哪里薄弱 / 接下来该学什么」时"
    "先调用它，再决定讲什么、讲多深、是否出题。只读，不修改图谱。"
)

PARAMETERS = {
    "type": "object",
    "properties": {
        "subject": {"type": "string",
                    "description": "只看某个学科（如「数据结构」）；省略则汇总全部学科"},
        "limit": {"type": "integer", "minimum": 1, "maximum": 30,
                  "description": "每个学科最多列出的知识点数（1~30，默认 10）"},
    },
}

GUIDANCE = """
读取学生知识图谱的只读快照（学科 / 知识点 / 掌握度 / 下一步）。
- **何时用**：开始教学前确认"学生有什么基础、哪些已掌握、哪些薄弱"；
  学生问"我接下来该学什么"；回答要联系学生已学内容时。别猜学生的学习进度。
- **何时别用**：需要某个话题的正文资料 → 用 `rag_search`；需要联网 → 用 `fetch_webpage`。
- **免确认**：只读取数据、不修改任何东西，无需先问学生，直接调用。
- 返回空图谱（学生还没建图）时按"没有已学记录"处理：基于通用知识回答，不要编造进度。
"""


def handler(args, kg) -> str:
    """工具执行体：永不抛异常（失败也给模型能看懂、能改行为的中文）。"""
    args = args if isinstance(args, dict) else {}
    subject = str(args.get("subject") or "").strip()
    limit = graph_snapshot.clamp_limit(args.get("limit", graph_snapshot.DEFAULT_LIMIT))
    try:
        data = graph_snapshot.snapshot(kg, subject=subject, limit=limit)
        # available 只用于"学科名写错/空图"时的提示；kg 已连上，这次读取零额外 IO
        return graph_snapshot.render_text(data, subject=subject,
                                          available=kg.get_subjects())
    except Exception as exc:  # 读库失败不该把整个 agent 循环炸掉
        return (
            f"读取知识图谱失败：{exc}。可以先基于通用知识回答学生，"
            "或改用 rag_search 检索已上传的资料。"
        )


SPEC = _spec("kg_snapshot", DESCRIPTION, PARAMETERS, handler, guidance=GUIDANCE,
             tier=TIER_FREE)
