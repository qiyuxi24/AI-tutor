"""大模型API客户端 —— 接入阿里云千问 + 知识图谱编辑工具

调用策略（两阶段分离）：
  阶段1 call_llm_stream():  流式输出文本给用户 → 不等待工具调用
  阶段2 call_llm_tools():   后台判断是否需要调用工具 → 执行工具 → 可选的流式补充输出
"""
import os
import json
import logging
from typing import AsyncGenerator, Optional
from openai import AsyncOpenAI, APIStatusError, APITimeoutError, APIConnectionError, AuthenticationError, RateLimitError
from dotenv import load_dotenv
from app.core.error_codes import ErrorCode, log_error

load_dotenv()

logger = logging.getLogger("ai-tutor")

# 使用 AsyncOpenAI 实现真正的异步 I/O，不阻塞 FastAPI 事件循环
# timeout 设为 120s：阿里云百炼 qwen-plus 模型在 function calling 多轮调用场景下可能需要较长时间
client = AsyncOpenAI(
    api_key=os.getenv("DASHSCOPE_API_KEY"),
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    timeout=120.0,
)

MODEL_NAME = os.getenv("MODEL_NAME", "qwen-plus")

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
    """
    执行千问请求的知识图谱操作，返回结果描述
    
    直接调用 KnowledgeGraph 对象的方法，不走 HTTP 自调用。
    这样做的好处：
    - 无网络开销（避免 localhost HTTP 往返）
    - 异常类型更清晰（ValueError vs URLError）
    - 与 _apply_suggestion() 使用同一套 kg 操作方式
    """
    # 延迟 import，避免模块加载时的循环依赖
    from app.api.v1.knowledge import kg

    try:
        args = json.loads(tool_call.function.arguments)
        name = tool_call.function.name

        if name == "add_knowledge_node":
            from app.api.v1.knowledge import create_node_from_ai
            return create_node_from_ai(
                node_id=args["id"],
                node_name=args["name"],
                tags=args.get("tags"),
                summary=args.get("summary", ""),
                difficulty=int(args.get("difficulty", 3)),
                estimated_minutes=int(args.get("estimated_minutes", 15)),
                content=args.get("content", ""),
                from_nodes=args.get("from_nodes"),
            )

        elif name == "update_node_content":
            node_id = args["node_id"]
            content = args["content"]
            op = args.get("op", "replace")
            kg.update_node_content(node_id, content, mode=op)
            return f"已更新节点 {node_id} 的内容（{op}）"

        elif name == "update_mastery":
            node_id = args["node_id"]
            mastery = args["mastery"]
            node = kg.get_node(node_id)
            if node is None:
                return f"更新失败：节点 {node_id} 不存在"
            node["mastery"] = mastery
            kg.save()
            return f"已将 {node_id} 的掌握程度更新为 {mastery}/100"

        elif name == "add_edge":
            kg.add_edge({
                "from": args["from"],
                "to": args["to"],
                "relation": args["relation"],
                "label": args.get("label", ""),
                "added_by": "ai",
            })
            return f"已创建边: {args['from']} → {args['to']} ({args['relation']})"

        elif name == "delete_node":
            node_id = args["node_id"]
            removed_edges = kg.remove_node(node_id)
            return f"已删除节点 {node_id}，同时移除 {removed_edges} 条关联边"

        else:
            return f"未知工具: {name}"

    except ValueError as e:
        # 业务逻辑错误（重复节点、不存在的节点等）——这是 AI 的错，返回友好提示
        log_error(ErrorCode.LLM_TOOL_EXEC_FAILED, detail=str(e), context={"tool": name})
        return f"操作失败: {str(e)}"
    except Exception as e:
        # 其他意外错误（文件写入失败等）
        log_error(ErrorCode.LLM_TOOL_EXEC_FAILED, detail=str(e), exception=e, context={"tool": name})
        return f"工具执行出错: {str(e)}"


async def call_llm(system_prompt: str, messages: list, enable_tools: bool = True) -> str:
    """
    调用千问API
    
    参数:
        system_prompt: 系统提示词
        messages: 完整对话历史 [{role, content}, ...] 或 Pydantic ChatMessage 列表
        enable_tools: 是否启用 function calling 编辑知识图谱。
                      True  → 带 KG_TOOLS，支持工具调用（对话场景）
                      False → 不带 tools，纯文本返回（分析/生成场景）
    
    返回:
        AI的回复文本
    
    异常:
        所有异常都会附加错误码信息后向上抛出:
        - APITimeoutError      → E-LLM-001
        - RateLimitError       → E-LLM-002
        - AuthenticationError  → E-LLM-003
        - APIConnectionError   → E-LLM-004
        - APIStatusError (5xx) → E-LLM-005
        - 其他未知异常          → E-SYS-002
    """
    # 1. 构造 API 消息列表
    api_messages = [{"role": "system", "content": system_prompt}]
    for msg in messages:
        api_messages.append({
            "role": msg["role"] if isinstance(msg, dict) else msg.role,
            "content": msg["content"] if isinstance(msg, dict) else msg.content
        })

    # 2. 构建请求参数
    create_kwargs = {
        "model": MODEL_NAME,
        "messages": api_messages,
        "temperature": 0.7,
        "max_tokens": 2000,
    }
    # 根据 enable_tools 决定是否注入 function calling 工具定义
    if enable_tools:
        create_kwargs["tools"] = KG_TOOLS

    # 3. 异步调用千问 API（真正的非阻塞 I/O）
    try:
        response = await client.chat.completions.create(**create_kwargs)
    except APITimeoutError as e:
        user_msg = log_error(ErrorCode.LLM_API_TIMEOUT, detail=str(e), exception=e)
        raise RuntimeError(user_msg) from e
    except RateLimitError as e:
        user_msg = log_error(ErrorCode.LLM_API_RATE_LIMIT, detail=str(e), exception=e)
        raise RuntimeError(user_msg) from e
    except AuthenticationError as e:
        user_msg = log_error(ErrorCode.LLM_API_AUTH_ERROR, detail=str(e), exception=e)
        raise RuntimeError(user_msg) from e
    except APIConnectionError as e:
        user_msg = log_error(ErrorCode.LLM_API_NETWORK, detail=str(e), exception=e)
        raise RuntimeError(user_msg) from e
    except APIStatusError as e:
        if e.status_code >= 500:
            user_msg = log_error(ErrorCode.LLM_API_SERVER_ERROR, detail=f"HTTP {e.status_code}: {str(e)}", exception=e)
        else:
            user_msg = log_error(ErrorCode.LLM_API_SERVER_ERROR, detail=f"HTTP {e.status_code}: {str(e)}", exception=e)
        raise RuntimeError(user_msg) from e
    except Exception as e:
        user_msg = log_error(ErrorCode.SYS_UNKNOWN_ERROR, detail=f"LLM调用未知错误: {str(e)}", exception=e)
        raise RuntimeError(user_msg) from e

    message = response.choices[0].message

    # 空回复检查
    if not message.content and not message.tool_calls:
        user_msg = log_error(ErrorCode.LLM_RESPONSE_EMPTY, detail="AI返回空内容")
        raise RuntimeError(user_msg)

    # 4. 如果启用了工具且千问请求了工具调用
    if enable_tools and message.tool_calls:
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
        # 注意：execute_kg_tool 是同步函数（本地文件 I/O），不阻塞事件循环太久
        for tc in message.tool_calls:
            result = execute_kg_tool(tc)
            api_messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result
            })

        # 异步调用千问第二次，让它基于工具结果生成回复
        try:
            response2 = await client.chat.completions.create(
                model=MODEL_NAME,
                messages=api_messages,
                temperature=0.7,
                max_tokens=2000,
            )
        except APITimeoutError as e:
            user_msg = log_error(ErrorCode.LLM_API_TIMEOUT, detail=f"第二次调用超时: {str(e)}", exception=e)
            raise RuntimeError(user_msg) from e
        except APIConnectionError as e:
            user_msg = log_error(ErrorCode.LLM_API_NETWORK, detail=f"第二次调用网络错误: {str(e)}", exception=e)
            raise RuntimeError(user_msg) from e
        except Exception as e:
            user_msg = log_error(ErrorCode.SYS_UNKNOWN_ERROR, detail=f"第二次调用失败: {str(e)}", exception=e)
            raise RuntimeError(user_msg) from e

        return response2.choices[0].message.content

    # 5. 没有工具调用，直接返回
    return message.content


# ══════════════════════════════════════════════════════════════════
#  阶段1：流式输出（纯文本，不带 tools）
# ══════════════════════════════════════════════════════════════════

async def call_llm_stream(
    system_prompt: str,
    messages: list,
) -> AsyncGenerator[str, None]:
    """
    流式调用千问 API，逐 token yield 给前端。
    
    关键设计：不带 tools，只做纯文本回复。
    工具调用在 call_llm_tools() 中单独处理（后台）。
    
    参数:
        system_prompt: 系统提示词（含图谱上下文，但不含工具能力说明）
        messages:      完整对话历史 [{role, content}, ...]
    
    Yields:
        每个 chunk 的文本 token（str）
    
    异常:
        所有异常都会附加错误码后向上抛出
    """
    # 构造 API 消息列表
    api_messages = [{"role": "system", "content": system_prompt}]
    for msg in messages:
        api_messages.append({
            "role": msg["role"] if isinstance(msg, dict) else msg.role,
            "content": msg["content"] if isinstance(msg, dict) else msg.content
        })

    try:
        # 流式调用，不带 tools
        response = await client.chat.completions.create(
            model=MODEL_NAME,
            messages=api_messages,
            temperature=0.7,
            max_tokens=2000,
            stream=True,
        )
    except APITimeoutError as e:
        user_msg = log_error(ErrorCode.LLM_API_TIMEOUT, detail=str(e), exception=e)
        raise RuntimeError(user_msg) from e
    except RateLimitError as e:
        user_msg = log_error(ErrorCode.LLM_API_RATE_LIMIT, detail=str(e), exception=e)
        raise RuntimeError(user_msg) from e
    except AuthenticationError as e:
        user_msg = log_error(ErrorCode.LLM_API_AUTH_ERROR, detail=str(e), exception=e)
        raise RuntimeError(user_msg) from e
    except APIConnectionError as e:
        user_msg = log_error(ErrorCode.LLM_API_NETWORK, detail=str(e), exception=e)
        raise RuntimeError(user_msg) from e
    except APIStatusError as e:
        user_msg = log_error(ErrorCode.LLM_API_SERVER_ERROR, detail=f"HTTP {e.status_code}: {str(e)}", exception=e)
        raise RuntimeError(user_msg) from e
    except Exception as e:
        user_msg = log_error(ErrorCode.SYS_UNKNOWN_ERROR, detail=f"流式调用未知错误: {str(e)}", exception=e)
        raise RuntimeError(user_msg) from e

    # 逐 chunk yield 文本内容
    async for chunk in response:
        delta = chunk.choices[0].delta if chunk.choices else None
        if delta and delta.content:
            yield delta.content


# ══════════════════════════════════════════════════════════════════
#  阶段2：后台工具调用（带 function calling）
# ══════════════════════════════════════════════════════════════════

async def call_llm_tools(
    system_prompt: str,
    messages: list,
) -> Optional[str]:
    """
    后台调用千问 API，判断是否需要执行知识图谱工具。
    
    关键设计：
    - 带 KG_TOOLS，允许 function calling
    - 如果有 tool_calls → 执行工具 → 再次调用 LLM 生成确认回复
    - 返回的确认回复通过 SSE 的 graph_update 事件推送给前端（而非流式输出）
    
    参数:
        system_prompt: 系统提示词（含工具能力说明）
        messages:      完整对话历史
    
    返回:
        工具执行后的确认消息（如"已创建节点 xxx"），无 tool_calls 时返回 None
    
    异常:
        不向上抛出，所有错误内部消化（后台任务不应影响主流程）
    """
    # 构造 API 消息列表
    api_messages = [{"role": "system", "content": system_prompt}]
    for msg in messages:
        api_messages.append({
            "role": msg["role"] if isinstance(msg, dict) else msg.role,
            "content": msg["content"] if isinstance(msg, dict) else msg.content
        })

    try:
        response = await client.chat.completions.create(
            model=MODEL_NAME,
            messages=api_messages,
            temperature=0.3,  # 工具调用用低温度，更确定性
            max_tokens=2000,
            tools=KG_TOOLS,
        )
    except Exception as e:
        # 后台任务失败不影响主流程，只记日志
        log_error(ErrorCode.LLM_API_SERVER_ERROR, detail=f"后台工具调用失败: {str(e)}", exception=e)
        return None

    message = response.choices[0].message

    # 没有工具调用 → 无需处理
    if not message.tool_calls:
        return None

    # 有工具调用 → 执行工具
    api_messages.append({
        "role": "assistant",
        "content": message.content or "",
        "tool_calls": [
            {"id": tc.id, "type": "function",
             "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
            for tc in message.tool_calls
        ]
    })

    tool_results = []
    for tc in message.tool_calls:
        try:
            result = execute_kg_tool(tc)
            tool_results.append(result)
        except Exception as e:
            tool_results.append(f"工具 {tc.function.name} 执行失败: {str(e)}")
        api_messages.append({
            "role": "tool",
            "tool_call_id": tc.id,
            "content": tool_results[-1]
        })

    # 第二次调用 LLM，让 AI 基于工具结果生成确认回复
    try:
        response2 = await client.chat.completions.create(
            model=MODEL_NAME,
            messages=api_messages,
            temperature=0.7,
            max_tokens=500,
        )
        return response2.choices[0].message.content
    except Exception as e:
        log_error(ErrorCode.LLM_API_SERVER_ERROR, detail=f"工具确认回复失败: {str(e)}", exception=e)
        # 即使第二次调用失败，工具已执行，返回简单汇总
        return "已自动完成图谱更新：" + "; ".join(tool_results)