"""工具 `update_node_content` —— 改写/追加某个知识点的 MD 正文。

⚠️ 提示词要点：`node_id` 必须**逐字复制**图谱摘要里的 id。模型爱自己"补全"拼写
（摘要给 `binary_tree_definition`，它传 `binary_tree`），失败后还会误报"图谱里没有这个知识点"。
"""

from ..registry import _spec

DESCRIPTION = "更新一个知识节点的 MD 文件内容"

PARAMETERS = {
    "type": "object",
    "properties": {
        "node_id": {"type": "string", "description": "节点ID"},
        "content": {"type": "string", "description": "新的 Markdown 内容（全文替换）"},
        "op": {"type": "string", "enum": ["replace", "append"],
               "description": "replace=替换全文, append=追加"},
    },
    "required": ["node_id", "content"],
}

GUIDANCE = """
更新节点正文（`op=replace` 替换全文 / `append` 追加）。
- **何时用**：讲解中发现节点内容不完善，**主动**补充；或学生明确要求修改某节点内容。
- 主动优化是允许的：不需要等用户说"修改"才动手，只要对话涉及某节点内容，就去完善它。
- `node_id` 必须从图谱摘要里**逐字复制**，不要自己拼写（摘要是 `binary_tree_definition`，
  就不能传 `binary_tree`）。传错 id 工具会失败，你还会因此错误地告诉学生"图谱里没有这个知识点"。
"""


def handler(args, kg) -> str:
    op = args.get("op", "replace")
    kg.update_node_content(args["node_id"], args["content"], mode=op, caller="ai")
    return f"已更新节点 {args['node_id']} 的内容（{op}）"


SPEC = _spec("update_node_content", DESCRIPTION, PARAMETERS, handler, guidance=GUIDANCE)
