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
- **何时用**：学生明确要求修改某节点内容，或你**提议**补充/完善并获得学生同意之后。
- ⚠️ **属于「须先问」操作**：发现节点内容不完善可以先提议
  （"这个知识点的内容我可以帮你补充完整，要吗？"），学生答应后再调用，不要直接改。
- 学生主动要求（"帮我补充一下 XX 的内容"）＝ 已授权，直接调用。
- `node_id` 必须从图谱摘要里**逐字复制**，不要自己拼写（摘要是 `binary_tree_definition`，
  就不能传 `binary_tree`）。传错 id 工具会失败，你还会因此错误地告诉学生"图谱里没有这个知识点"。
"""


def handler(args, kg) -> str:
    node_id = args["node_id"]
    # 小节化节点这里写的是主 MD，而读侧只认小节 → 写了等于没写。
    # 与其静默哑火（模型会据此对学生说"已更新"，是明确的错话），不如直接指回正确的工具。
    if kg.has_sections(node_id):
        return (f"操作失败：{node_id} 的正文已按「小节」组织，本工具写的主 MD 不会被读到。"
                "请改用 update_node_sections 重写该知识点的讲解。")
    op = args.get("op", "replace")
    kg.update_node_content(node_id, args["content"], mode=op, caller="ai")
    return f"已更新节点 {node_id} 的内容（{op}）"


SPEC = _spec("update_node_content", DESCRIPTION, PARAMETERS, handler, guidance=GUIDANCE)
