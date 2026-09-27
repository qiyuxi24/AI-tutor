"""工具 `add_knowledge_node` —— 新增知识点节点（节点 + MD 文件 + prerequisite 边）。

重型实现在领域模块 `core/knowledge_writer.py`（AI 写图谱层，`chat_service` 也在用），
本模块只做 spec + 薄壳 handler。

⚠️ 提示词要点：`from_nodes` 只填**真正的前置知识节点**。模型很爱上把所有相关节点都塞进去，
实测会把"相关"当"前置"，污染学习路径。
"""

from app.core.knowledge_writer import create_node_from_ai

from ..registry import _spec

DESCRIPTION = ("添加一个新知识点节点及其 MD 文件。⚠️ from_nodes 只应包含真正的前置知识节点"
               "（即必须先学会它们才能理解新节点），不要随便填已有的节点 ID。")

PARAMETERS = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "description": "节点英文ID，如 'hanoi_tower'"},
        "name": {"type": "string", "description": "节点中文名称"},
        "tags": {"type": "array", "items": {"type": "string"},
                 "description": "自由标签（如 ['递归', '分治']）。⚠️ 不要写难度级别（难度用 difficulty 字段）、不要重复填学科（学科用 subject 字段，别混进 tags）"},
        "subject": {"type": "string",
                    "description": "该知识点所属学科（如 '数据结构'）。⚠️ 对话上下文能明确判断时填写，且必须复用已有学科名；不确定就省略，系统会自动判定。"},
        "board": {"type": "string",
                  "description": "该学科下的知识板块（如 '线性表'）。⚠️ 必须与 subject 匹配、优先复用该学科已有板块名；不确定就省略，系统会自动判定。"},
        "summary": {"type": "string", "description": "一句话摘要"},
        "difficulty": {"type": "integer", "description": "难度 1-5", "minimum": 1, "maximum": 5},
        "estimated_minutes": {"type": "integer", "description": "预估学习分钟数"},
        "content": {"type": "string", "description": "完整的 Markdown 内容"},
        "from_nodes": {"type": "array", "items": {"type": "string"},
                       "description": "前置节点 ID 列表（会自动创建 prerequisite 边）。⚠️ 只能包含真正必须先学的前置知识节点。如果新节点不需要任何已有节点作为前置，传空数组或不传。"},
    },
    "required": ["id", "name", "content"],
}

GUIDANCE = """
添加知识点节点（自动创建节点 + MD 文件 + prerequisite 关联边）。
- **何时用**：学生讨论到图谱里还没有的概念时，或明确要求"帮我加一个 XX 节点"——
  不必等学生说"修改"才动手，对话涉及的知识点就该建起来。
- `from_nodes` 只填**真正的前置知识节点**（必须先学会它才能理解新节点）；没有前置就不传。
- 能判断学科/板块就一并带上 `subject` / `board`，且**必须复用已有名称**；不确定就省略，系统会自动判定。
- `tags` 只填自由标签：**不要**写"一级/二级/三级"这类难度（难度有 `difficulty` 字段承载），也不要重复填学科。
- 例：学生说"帮我加一个汉诺塔节点"，建完后自然回复"已添加！汉诺塔现在关联在递归定义下"。
"""


def handler(args, kg) -> str:
    return create_node_from_ai(
        kg=kg,
        node_id=args["id"],
        node_name=args["name"],
        tags=args.get("tags"),
        summary=args.get("summary", ""),
        difficulty=int(args.get("difficulty", 3)),
        estimated_minutes=int(args.get("estimated_minutes", 15)),
        content=args.get("content", ""),
        from_nodes=args.get("from_nodes"),
        subject=args.get("subject", ""),
        board=args.get("board", ""),
    )


SPEC = _spec("add_knowledge_node", DESCRIPTION, PARAMETERS, handler, guidance=GUIDANCE)
