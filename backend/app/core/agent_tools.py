"""Agent 工具系统（自 llm_client.py 拆分，2026-09-08）—— 工具注册的唯一入口。

每条 = {name, description, parameters(JSON Schema), handler(args, kg)->str}
- parameters 结构与 MCP tools/list 的 inputSchema 同构（JSON Schema）
- KG_TOOLS（喂给模型的 tools 参数）与 execute_kg_tool（工具分发）均由注册表驱动
- 新增工具：在 _TOOL_SPECS 注册一条 spec + 写一个 handler 即可，
  不再需要改 if/elif 分发或另抄 schema。
  （ponytail: 暂不引入 MCP server 运行时——MiniMax/OpenAI 模型 API 原生消费
   function calling；将来需要把工具导出给外部 agent 用时，本表可直接映射。）

重型实现已拆到领域模块（本文件仅薄壳 handler + 注册表）：
- fetch_webpage      → web_tool.py（SSRF 防护 + HTML→文本）
- rag_search         → rag_tool.py（RAG 检索 + 同步/异步适配）
- add_knowledge_node → knowledge_writer.py（AI 建节点/更新节点，纯业务，2026-09-08 下沉）
"""

import json

from app.core.error_codes import ErrorCode, log_error
from app.core.knowledge_writer import create_node_from_ai
from app.core.rag_tool import rag_search
from app.core.web_tool import fetch_webpage


def _spec(name, description, parameters, handler):
    return {"name": name, "description": description,
            "parameters": parameters, "handler": handler}


# ── handler：工具执行体（(args: dict, kg: KnowledgeGraph) -> 给模型的文本）──

def _h_add_node(args, kg) -> str:
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
    )


def _h_update_content(args, kg) -> str:
    op = args.get("op", "replace")
    kg.update_node_content(args["node_id"], args["content"], mode=op, caller="ai")
    return f"已更新节点 {args['node_id']} 的内容（{op}）"


def _h_update_mastery(args, kg) -> str:
    kg.update_node_info(args["node_id"], {"mastery": args["mastery"], "added_by": "ai"}, caller="ai")
    return f"已将 {args['node_id']} 的掌握程度更新为 {args['mastery']}/100"


def _h_add_edge(args, kg) -> str:
    kg.add_edge({
        "from": args["from"], "to": args["to"],
        "relation": args["relation"], "label": args.get("label", ""),
        "added_by": "ai",
    }, caller="ai")
    return f"已创建边: {args['from']} → {args['to']} ({args['relation']})"


def _h_delete_node(args, kg) -> str:
    removed = kg.remove_node(args["node_id"], caller="ai")
    return f"已删除节点 {args['node_id']}，同时移除 {removed} 条关联边"


def _h_update_profile(args, kg) -> str:
    from app.core.user_profile import UserProfile
    note_id = UserProfile(user_id=kg.user_id).add_note(args["content"], source="ai")
    return f"已为用户画像新增观察笔记（{note_id}）"


def _h_fetch_webpage(args, kg) -> str:
    return fetch_webpage(args["url"], max_chars=int(args.get("max_chars", 3000)))


def _h_rag_search(args, kg) -> str:
    return rag_search(args.get("query", ""), source=args.get("source", "all"),
                      top_k=int(args.get("top_k", 3)), user_id=kg.user_id)


_TOOL_SPECS = [
    _spec(
        "add_knowledge_node",
        "添加一个新知识点节点及其 MD 文件。⚠️ from_nodes 只应包含真正的前置知识节点（即必须先学会它们才能理解新节点），不要随便填已有的节点 ID。",
        {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "节点英文ID，如 'hanoi_tower'"},
                "name": {"type": "string", "description": "节点中文名称"},
                "tags": {"type": "array", "items": {"type": "string"},
                         "description": "标签，含难度级别如 ['算法', '三级']"},
                "summary": {"type": "string", "description": "一句话摘要"},
                "difficulty": {"type": "integer", "description": "难度 1-5", "minimum": 1, "maximum": 5},
                "estimated_minutes": {"type": "integer", "description": "预估学习分钟数"},
                "content": {"type": "string", "description": "完整的 Markdown 内容"},
                "from_nodes": {"type": "array", "items": {"type": "string"},
                               "description": "前置节点 ID 列表（会自动创建 prerequisite 边）。⚠️ 只能包含真正必须先学的前置知识节点。如果新节点不需要任何已有节点作为前置，传空数组或不传。"},
            },
            "required": ["id", "name", "content"],
        },
        _h_add_node,
    ),
    _spec(
        "update_node_content",
        "更新一个知识节点的 MD 文件内容",
        {
            "type": "object",
            "properties": {
                "node_id": {"type": "string", "description": "节点ID"},
                "content": {"type": "string", "description": "新的 Markdown 内容（全文替换）"},
                "op": {"type": "string", "enum": ["replace", "append"],
                       "description": "replace=替换全文, append=追加"},
            },
            "required": ["node_id", "content"],
        },
        _h_update_content,
    ),
    _spec(
        "update_mastery",
        "更新用户对某个知识点的掌握程度",
        {
            "type": "object",
            "properties": {
                "node_id": {"type": "string", "description": "节点ID"},
                "mastery": {"type": "integer", "description": "掌握度 0-100",
                            "minimum": 0, "maximum": 100},
            },
            "required": ["node_id", "mastery"],
        },
        _h_update_mastery,
    ),
    _spec(
        "add_edge",
        "在两个已有节点之间创建关联边。⚠️ 重要：仅在确认两个节点之间存在实质性的知识关系时才调用此工具。如果只是猜测或关系不明确，不要调用。",
        {
            "type": "object",
            "properties": {
                "from": {"type": "string", "description": "源节点 ID"},
                "to": {"type": "string", "description": "目标节点 ID"},
                "relation": {"type": "string", "enum": ["prerequisite", "related", "confusion", "extension"],
                             "description": "关系类型。prerequisite=必须先学from才能学to；related=共享核心概念但无严格先后；confusion=容易混淆；extension=to是from的深入/扩展"},
                "label": {"type": "string", "description": "关系标签，用简短的中文词概括，如'前置知识'、'相关概念'、'易混淆'、'扩展延伸'。不要用长句子。"},
            },
            "required": ["from", "to", "relation"],
        },
        _h_add_edge,
    ),
    _spec(
        "delete_node",
        "删除一个知识点节点及其 MD 文件",
        {"type": "object", "properties": {"node_id": {"type": "string", "description": "要删除的节点ID"}},
         "required": ["node_id"]},
        _h_delete_node,
    ),
    _spec(
        "update_user_profile",
        "更新学生用户画像。当你在教学中观察到学生的性格特点、学习习惯、知识薄弱点等新信息时，应主动更新用户画像，以便后续更好地个性化教学。每次调用会作为一条带时间戳的观察笔记记录到画像的「AI 教学笔记」部分；若本次观察与已有笔记内容重复，则无需再次记录。",
        {"type": "object",
         "properties": {"content": {"type": "string", "description": "本次观察到的学生信息（一句话到一小段均可），如：学习风格、知识薄弱点、性格特点、偏好等。避免重复记录已有内容。"}},
         "required": ["content"]},
        _h_update_profile,
    ),
    _spec(
        "fetch_webpage",
        "抓取并返回一个网页的可读文本内容（会自动剥离 HTML 标签、脚本、样式）。当学生提到某个 URL、网上资料、或需要实时信息（新闻、文档、教程）时，可以用此工具获取网页正文。返回内容会截断到 max_chars 限制内。",
        {"type": "object",
         "properties": {
             "url": {"type": "string", "description": "要查询的网页完整 URL，需以 http:// 或 https:// 开头"},
             "max_chars": {"type": "integer", "description": "返回文本的最大字符数（默认 3000，最大 20000）。超出部分会被截断。"},
         },
         "required": ["url"]},
        _h_fetch_webpage,
    ),
    _spec(
        "rag_search",
        "检索知识库/知识图谱中与某话题最相关的内容片段。当学生的问题涉及某个具体知识点、需要从已有的学习资料或图谱节点中找依据时，可调用此工具获取相关片段。返回内容带来源标注，可据此更准确地回答或引用。",
        {"type": "object",
         "properties": {
             "query": {"type": "string", "description": "要检索的内容，用学生当前话题或你想深挖的子问题表述"},
             "source": {"type": "string", "enum": ["graph", "kb", "all"],
                        "description": "检索来源：graph=知识图谱，kb=上传知识库，all=全部（默认）"},
             "top_k": {"type": "integer", "description": "返回条数 1~5（默认 3）"},
         },
         "required": ["query"]},
        _h_rag_search,
    ),
]

# 工具名索引 + OpenAI function-calling 导出（喂给模型；结构保持不变，仅改数据源）
_TOOL_BY_NAME = {s["name"]: s for s in _TOOL_SPECS}
KG_TOOLS = [
    {"type": "function", "function": {"name": s["name"], "description": s["description"],
                                      "parameters": s["parameters"]}}
    for s in _TOOL_SPECS
]


def execute_kg_tool(tool_call, kg) -> str:
    """
    按工具注册表执行模型请求的工具调用，返回给模型的结果描述。

    数据驱动分发（查 _TOOL_SPECS，不再手写 if/elif）：新增工具只需注册
    spec + handler，模型侧的 KG_TOOLS 与执行侧自动生效。

    直接调用 KnowledgeGraph / 领域函数，不走 HTTP 自调用：
    - 无网络开销（避免 localhost HTTP 往返）
    - 异常类型清晰（ValueError=业务错 / PermissionError=权限不足）
    - 与图谱建议应用（knowledge_writer.apply_suggestion）使用同一套 kg 操作方式

    参数:
        tool_call: 模型返回的工具调用对象（.function.name / .function.arguments）
        kg:        KnowledgeGraph 实例（已绑定当前 user_id）
    """
    name = getattr(getattr(tool_call, "function", None), "name", None)
    spec = _TOOL_BY_NAME.get(name)
    if spec is None:
        return f"未知工具: {name}"

    try:
        args = json.loads(getattr(tool_call.function, "arguments", "{}"))
        if not isinstance(args, dict):
            raise ValueError("工具参数必须是 JSON 对象")
    except ValueError as e:
        log_error(ErrorCode.LLM_TOOL_EXEC_FAILED, detail=f"参数解析失败: {e}", context={"tool": name})
        return f"操作失败: {str(e)}"

    try:
        return spec["handler"](args, kg)
    except ValueError as e:
        # 业务逻辑错误（重复节点、不存在的节点等）——这是 AI 的错，返回友好提示
        log_error(ErrorCode.LLM_TOOL_EXEC_FAILED, detail=str(e), context={"tool": name})
        return f"操作失败: {str(e)}"
    except PermissionError as e:
        # AI 权限不足（试图修改人类创建的节点）——友好提示 AI 不要这样做
        log_error(ErrorCode.LLM_TOOL_EXEC_FAILED, detail=str(e), context={"tool": name})
        return f"权限不足: {str(e)}。如需修改，请让用户手动操作。"
    except Exception as e:
        # 其他意外错误（文件写入失败等）
        log_error(ErrorCode.LLM_TOOL_EXEC_FAILED, detail=str(e), exception=e, context={"tool": name})
        return f"工具执行出错: {str(e)}"
