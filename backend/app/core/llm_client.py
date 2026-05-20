"""大模型API客户端 —— 接入阿里云千问 + 知识图谱编辑工具"""
import os
import json
from urllib.request import Request, urlopen
from urllib.error import URLError
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

client = OpenAI(
    api_key=os.getenv("DASHSCOPE_API_KEY"),
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
)

MODEL_NAME = os.getenv("MODEL_NAME", "qwen-plus")
KG_API_BASE = "http://localhost:8000/api/v1/knowledge"

# ─── 知识图谱编辑工具定义（千问 function calling）───
KG_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "add_knowledge_node",
            "description": "添加一个新知识点节点及其 MD 文件",
            "parameters": {
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
                                   "description": "前置节点 ID 列表，会自动创建边"},
                },
                "required": ["id", "name", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_node_content",
            "description": "更新一个知识节点的 MD 文件内容",
            "parameters": {
                "type": "object",
                "properties": {
                    "node_id": {"type": "string", "description": "节点ID"},
                    "content": {"type": "string", "description": "新的 Markdown 内容（全文替换）"},
                    "op": {"type": "string", "enum": ["replace", "append"],
                           "description": "replace=替换全文, append=追加"},
                },
                "required": ["node_id", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_mastery",
            "description": "更新用户对某个知识点的掌握程度",
            "parameters": {
                "type": "object",
                "properties": {
                    "node_id": {"type": "string", "description": "节点ID"},
                    "mastery": {"type": "integer", "description": "掌握度 0-100",
                                "minimum": 0, "maximum": 100},
                },
                "required": ["node_id", "mastery"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "add_edge",
            "description": "在两个已有节点之间创建关联边",
            "parameters": {
                "type": "object",
                "properties": {
                    "from": {"type": "string", "description": "源节点 ID"},
                    "to": {"type": "string", "description": "目标节点 ID"},
                    "relation": {"type": "string", "enum": ["prerequisite", "related", "confusion", "extension"],
                                 "description": "关系类型"},
                    "label": {"type": "string", "description": "关系描述"},
                },
                "required": ["from", "to", "relation"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_node",
            "description": "删除一个知识点节点及其 MD 文件",
            "parameters": {
                "type": "object",
                "properties": {
                    "node_id": {"type": "string", "description": "要删除的节点ID"},
                },
                "required": ["node_id"]
            }
        }
    },
]


def execute_kg_tool(tool_call) -> str:
    """执行千问请求的知识图谱操作，返回结果描述"""
    try:
        args = json.loads(tool_call.function.arguments)
        name = tool_call.function.name

        if name == "add_knowledge_node":
            node_payload = {
                "id": args["id"], "name": args["name"],
                "tags": args.get("tags", []),
                "summary": args.get("summary", ""),
                "difficulty": args.get("difficulty", 3),
                "estimated_minutes": args.get("estimated_minutes", 15),
                "content": args["content"],
                "added_by": "ai",
            }
            body = json.dumps(node_payload, ensure_ascii=False).encode()
            req = Request(f"{KG_API_BASE}/node", data=body, method='POST',
                          headers={'Content-Type': 'application/json'})
            with urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read().decode())
            if resp.status != 200:
                return f"创建节点失败: {result.get('detail', str(result))}"

            for pid in args.get("from_nodes", []):
                edge_payload = {
                    "from": pid, "to": args["id"],
                    "relation": "prerequisite",
                    "label": f"是学习 {args['name']} 的前置知识",
                    "added_by": "ai",
                }
                ebody = json.dumps(edge_payload, ensure_ascii=False).encode()
                ereq = Request(f"{KG_API_BASE}/edge", data=ebody, method='POST',
                               headers={'Content-Type': 'application/json'})
                urlopen(ereq, timeout=5)

            return f"已创建节点「{args['name']}」(ID: {args['id']})，关联了 {len(args.get('from_nodes', []))} 条前置边"

        elif name == "update_node_content":
            payload = {"content": args["content"], "op": args.get("op", "replace")}
            body = json.dumps(payload, ensure_ascii=False).encode()
            req = Request(f"{KG_API_BASE}/node/{args['node_id']}", data=body, method='PUT',
                          headers={'Content-Type': 'application/json'})
            with urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read().decode())
            if resp.status != 200:
                return f"更新失败: {result.get('detail', str(result))}"
            return f"已更新节点 {args['node_id']} 的内容（{args.get('op', 'replace')}）"

        elif name == "update_mastery":
            payload = json.dumps({"mastery": args["mastery"], "added_by": "ai"}).encode()
            req = Request(f"{KG_API_BASE}/node/{args['node_id']}/mastery", data=payload,
                          method='PUT', headers={'Content-Type': 'application/json'})
            urlopen(req, timeout=10)
            return f"已将 {args['node_id']} 的掌握程度更新为 {args['mastery']}/100"

        elif name == "add_edge":
            payload = json.dumps(args, ensure_ascii=False).encode()
            req = Request(f"{KG_API_BASE}/edge", data=payload, method='POST',
                          headers={'Content-Type': 'application/json'})
            urlopen(req, timeout=10)
            return f"已创建边: {args['from']} → {args['to']} ({args['relation']})"

        elif name == "delete_node":
            req = Request(f"{KG_API_BASE}/node/{args['node_id']}", method='DELETE')
            urlopen(req, timeout=10)
            return f"已删除节点 {args['node_id']}"

        else:
            return f"未知工具: {name}"

    except Exception as e:
        return f"工具执行出错: {str(e)}"


async def call_llm(system_prompt: str, messages: list) -> str:
    """
    调用千问API，支持 function calling 编辑知识图谱
    
    参数:
        system_prompt: 系统提示词
        messages: 完整对话历史 [{role, content}, ...]
    
    返回:
        AI的回复文本
    """
    try:
        api_messages = [{"role": "system", "content": system_prompt}]
        for msg in messages:
            api_messages.append({
                "role": msg["role"] if isinstance(msg, dict) else msg.role,
                "content": msg["content"] if isinstance(msg, dict) else msg.content
            })

        # 带 tools 调用千问
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=api_messages,
            tools=KG_TOOLS,
            temperature=0.7,
            max_tokens=2000,
        )

        message = response.choices[0].message

        # 如果千问请求了工具调用
        if message.tool_calls:
            # 先把 assistant 的消息加入历史（含 tool_calls）
            api_messages.append({
                "role": "assistant",
                "content": message.content or "",
                "tool_calls": [
                    {"id": tc.id, "type": "function",
                     "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                    for tc in message.tool_calls
                ]
            })

            # 执行每一个工具，把结果加回消息
            for tc in message.tool_calls:
                result = execute_kg_tool(tc)
                api_messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result
                })

            # 调用千问第二次，让它基于工具结果生成回复
            response2 = client.chat.completions.create(
                model=MODEL_NAME,
                messages=api_messages,
                temperature=0.7,
                max_tokens=2000,
            )
            return response2.choices[0].message.content

        # 没有工具调用，直接返回
        return message.content

    except Exception as e:
        return f"AI调用失败: {str(e)}"