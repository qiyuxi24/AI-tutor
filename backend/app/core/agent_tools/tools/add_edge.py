"""工具 `add_edge` —— 两个已有节点之间建关系边。

⚠️ 提示词要点：**图谱质量远比数量重要**。模型容易"因为两个概念在同一段对话里出现"就连边，
错误的前置关系会直接把学习路径带偏。所以 description 与 guidance 都要写
「关系不明确就不要调用」。
"""

from ..registry import _spec

DESCRIPTION = ("在两个已有节点之间创建关联边。⚠️ 重要：仅在确认两个节点之间存在实质性的知识关系时"
               "才调用此工具。如果只是猜测或关系不明确，不要调用。")

PARAMETERS = {
    "type": "object",
    "properties": {
        "from": {"type": "string", "description": "源节点 ID"},
        "to": {"type": "string", "description": "目标节点 ID"},
        "relation": {"type": "string", "enum": ["prerequisite", "related", "confusion", "extension"],
                     "description": "关系类型。prerequisite=必须先学from才能学to；related=共享核心概念但无严格先后；confusion=容易混淆；extension=to是from的深入/扩展"},
        "label": {"type": "string", "description": "关系标签，用简短的中文词概括，如'前置知识'、'相关概念'、'易混淆'、'扩展延伸'。不要用长句子。"},
    },
    "required": ["from", "to", "relation"],
}

GUIDANCE = """
在两个已有节点之间建立关系边。
- **何时用**：确认两节点间存在**实质性** prerequisite / related / confusion / extension 关系时才调用。
- **不要**仅因为两个概念在同一对话中出现就连边；只是猜测或关系不明确就不要调用。
- ⚠️ 知识图谱的质量远比数量重要：宁可漏掉一条关系，也不要创建错误的关系误导学习路径。
"""


def handler(args, kg) -> str:
    kg.add_edge({
        "from": args["from"], "to": args["to"],
        "relation": args["relation"], "label": args.get("label", ""),
        "added_by": "ai",
    }, caller="ai")
    return f"已创建边: {args['from']} → {args['to']} ({args['relation']})"


SPEC = _spec("add_edge", DESCRIPTION, PARAMETERS, handler, guidance=GUIDANCE)
