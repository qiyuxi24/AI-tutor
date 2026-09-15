"""工具 `delete_node` —— 删除知识点节点（连同 MD 文件与关联边）。

⚠️ 权限限制：人类手动创建的节点删不掉（`KnowledgeGraph.remove_node(caller="ai")` 会抛
`PermissionError`，由 `dispatch` 转成"权限不足: …"文案）。这种情况要让用户自己操作，
**不要反复重试** —— 所以 guidance 里显式写了这一条。
"""

from ..registry import _spec

DESCRIPTION = "删除一个知识点节点及其 MD 文件"

PARAMETERS = {
    "type": "object",
    "properties": {"node_id": {"type": "string", "description": "要删除的节点ID"}},
    "required": ["node_id"],
}

GUIDANCE = """
删除节点（连同它的 MD 文件与关联边）。
- **何时用**：学生明确要求删除某个知识点，或确认某节点是误建。
- 人类手动创建的节点删不掉（系统会拒绝）——这种情况让学生自己操作，不要反复重试。
"""


def handler(args, kg) -> str:
    removed = kg.remove_node(args["node_id"], caller="ai")
    return f"已删除节点 {args['node_id']}，同时移除 {removed} 条关联边"


SPEC = _spec("delete_node", DESCRIPTION, PARAMETERS, handler, guidance=GUIDANCE)
