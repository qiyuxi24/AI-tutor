"""
对话服务层：编排整个对话处理流程

新流程（两阶段分离）：
  阶段1（流式）：构建纯教学提示词 → call_llm_stream() → SSE 逐 token 推给前端
  阶段2（后台）：构建带工具说明的提示词 → call_llm_tools() → 执行工具 → 推送结果

旧流程（保留兼容）：
  process_message() 仍可用，但 /chat/stream 是推荐接口
"""

import asyncio
import logging
from typing import AsyncGenerator
from datetime import datetime
from app.core.prompt_loader import get_system_prompt
from app.core.llm_client import call_llm, call_llm_stream, call_llm_tools
from app.core.graph_analyzer import GraphAnalyzer
from app.core.error_codes import ErrorCode, log_error
from app.api.v1.knowledge import kg, _apply_suggestion, _load_suggestions, _save_suggestions

logger = logging.getLogger("ai-tutor")


def _build_chat_prompt(messages: list, mode: str) -> tuple[str, str]:
    """
    构建聊天 LLM 的完整系统提示词（含图谱上下文 + 工具能力说明）。

    返回: (system_prompt, last_user_message)
    """
    last_user_msg = next((m.content for m in reversed(messages) if m.role == 'user'), '')
    system_prompt = get_system_prompt(mode, last_user_msg)

    # 注入知识图谱上下文
    analyzer = GraphAnalyzer(kg)
    graph_context = analyzer._build_graph_context(detailed=True)

    system_prompt += f"""

## 知识图谱全量数据

你拥有一个完整的知识图谱系统，包含以下节点和关系。
每当用户谈论到相关知识时，主动查看、补充、优化这些节点内容。

{graph_context}

### 你的额外能力
1. **主动优化**：当用户讨论一个知识点时，你发现节点内容不完善，主动调用 `update_node_content` 补充
2. **提升掌握度**：当用户正确回答/理解后，调用 `update_mastery` 提升掌握度
3. **关联节点**：当发现节点间有关系但没被创建时，调用 `add_edge` 补上
4. **扩展图谱**：当用户提到知识图谱中没有的概念时，调用 `add_knowledge_node` 自动创建
5. **识别掌握度**：根据用户回复的质量，适当调整 mastery 值（0=未掌握, 1-25=入门, 26-50=熟悉, 51-75=熟练, 76-100=精通）

你不需要等用户说"修改"才动手。只要对话涉及某节点内容，就主动去完善它。
"""
    return system_prompt, last_user_msg


def _build_stream_prompt(messages: list, mode: str) -> tuple[str, str]:
    """
    构建流式阶段的系统提示词（含图谱上下文，但不含工具能力说明）。
    
    与 _build_chat_prompt 的区别：
    - 不含"你的额外能力"部分（工具调用在后台阶段处理）
    - 仅用于指导 AI 生成优质教学回复
    
    返回: (system_prompt, last_user_message)
    """
    last_user_msg = next((m.content for m in reversed(messages) if m.role == 'user'), '')
    system_prompt = get_system_prompt(mode, last_user_msg)

    # 注入知识图谱上下文（让 AI 知道有哪些知识点，但不让它调工具）
    analyzer = GraphAnalyzer(kg)
    graph_context = analyzer._build_graph_context(detailed=True)

    system_prompt += f"""

## 参考知识图谱

以下是当前知识图谱中的所有节点和关系，请根据这些内容来回答学生的问题：

{graph_context}
"""
    return system_prompt, last_user_msg


async def _analyze_and_apply(user_message: str, ai_reply: str,
                              threshold: float = 0.9) -> dict:
    """
    调用图谱分析器分析对话 → 分类建议 → 自动应用高置信度建议。

    返回: {"suggestions": [...], "applied": [...], "pending": [...]}
    """
    result = {"suggestions": [], "applied": [], "pending": []}
    try:
        analyzer = GraphAnalyzer(kg)
        analysis = await analyzer.analyze_conversation(user_message, ai_reply)
        suggestions = analysis.get("suggestions", [])
        result["suggestions"] = suggestions

        if not suggestions:
            return result

        applied_list = []
        pending_list = []
        for s in suggestions:
            confidence = s.get("confidence", 0)
            if confidence >= threshold:
                try:
                    apply_result = _apply_suggestion(s)
                    if apply_result:
                        applied_list.append({**s, "apply_result": apply_result})
                except Exception:
                    pending_list.append(s)
            else:
                pending_list.append(s)

        result["applied"] = applied_list
        result["pending"] = pending_list

        # 持久化待审核建议
        if pending_list:
            existing = _load_suggestions()
            for p in pending_list:
                p["submitted_at"] = datetime.now().isoformat()
            existing.extend(pending_list)
            _save_suggestions(existing)

    except Exception as e:
        # 图谱分析失败不影响对话流程，但记录日志 + 发布错误事件
        user_msg = log_error(
            ErrorCode.CHAT_ANALYZE_FAILED,
            detail=str(e),
            exception=e
        )
        try:
            from app.core.event_bus import publish
            publish("error", {
                "code": ErrorCode.code_only(ErrorCode.CHAT_ANALYZE_FAILED),
                "message": user_msg,
                "module": "chat_service",
                "detail": str(e)[:200],
            })
        except Exception:
            pass

    return result


async def process_message(user_id: str, messages: list, mode: str) -> tuple[str, str, dict]:
    """
    处理一条学生消息。

    参数:
        user_id:  用户唯一标识
        messages: 完整对话历史（Pydantic ChatMessage 列表）
        mode:     引导模式（scaffolding / think_first / reverse_teaching）

    返回:
        (AI回复文本, 使用的模式, 图谱分析结果)

    注意：
        图谱分析（_analyze_and_apply）作为后台任务异步执行，
        不阻塞对话回复的返回，避免前端等待超时。
    """
    # 1. 构建系统提示词（含图谱上下文）
    system_prompt, last_user_msg = _build_chat_prompt(messages, mode)

    # 2. 调用 AI 获取回复
    try:
        reply = await call_llm(system_prompt, messages, enable_tools=True)
    except Exception as e:
        # call_llm 内部已通过 log_error 记录日志 + 附加错误码
        # 这里直接使用异常消息（已包含 [E-XXX-XXX] 格式）返回给前端
        error_msg = str(e)
        # 兜底：如果消息中没有错误码前缀，手动附加
        if not error_msg.startswith("[E-"):
            error_msg = log_error(ErrorCode.CHAT_PROCESS_FAILED, detail=str(e), exception=e)
        # 发布错误事件到 SSE
        try:
            from app.core.event_bus import publish
            code = error_msg[1:10] if error_msg.startswith("[E-") else ErrorCode.code_only(ErrorCode.CHAT_PROCESS_FAILED)
            publish("error", {
                "code": code,
                "message": error_msg,
                "module": "chat_service",
                "detail": str(e)[:200],
            })
        except Exception:
            pass
        return error_msg, mode, {"suggestions": [], "applied": [], "pending": []}

    # 3. 图谱分析作为后台任务执行，不阻塞对话回复
    #    使用 asyncio.create_task 实现 fire-and-forget
    asyncio.create_task(_analyze_and_apply(last_user_msg, reply))

    return reply, mode, {"suggestions": [], "applied": [], "pending": []}


# ══════════════════════════════════════════════════════════════════
#  流式对话处理（新接口 /chat/stream 使用）
# ══════════════════════════════════════════════════════════════════

async def process_message_stream(
    messages: list,
    mode: str,
) -> AsyncGenerator[str, None]:
    """
    流式处理一条学生消息。
    
    阶段1（流式）：逐 token yield 给前端，让用户立刻看到回复。
    阶段2（后台）：在生成器结束后，通过 BackgroundTasks 触发工具调用和图谱分析。
    
    参数:
        messages: 完整对话历史（Pydantic ChatMessage 列表）
        mode:     引导模式
    
    Yields:
        SSE 格式的字符串（"data: {...}\n\n"）
    """
    # 阶段1：流式生成回复（纯文本，不带 tools）
    stream_prompt, _ = _build_stream_prompt(messages, mode)
    
    try:
        async for token in call_llm_stream(stream_prompt, messages):
            # 按 SSE 格式 yield 每个 token
            import json as _json
            yield f"data: {_json.dumps({'token': token})}\n\n"
    except Exception as e:
        # 流式调用失败，发送错误事件
        error_msg = str(e)
        if not error_msg.startswith("[E-"):
            error_msg = log_error(ErrorCode.CHAT_PROCESS_FAILED, detail=str(e), exception=e)
        import json as _json
        yield f"data: {_json.dumps({'error': error_msg})}\n\n"
        return
    
    # 流式结束，发送完成信号
    yield "data: [DONE]\n\n"


async def process_background_tools(
    messages: list,
    mode: str,
) -> None:
    """
    后台任务：调用 LLM 判断是否需要执行工具 + 图谱分析。
    
    此函数在流式回复完成后由 BackgroundTasks 触发，
    失败不影响主对话流程。
    
    参数:
        messages: 完整对话历史
        mode:     引导模式
    """
    try:
        # 1. 工具调用（function calling）
        tool_prompt, last_user_msg = _build_chat_prompt(messages, mode)
        tool_result = await call_llm_tools(tool_prompt, messages)
        
        # 2. 图谱分析（GraphAnalyzer 独立判断）
        #    注意：需要从 messages 中提取最后一轮的用户消息和 AI 回复
        user_msgs = [m for m in messages if (m.role if hasattr(m, 'role') else m['role']) == 'user']
        assistant_msgs = [m for m in messages if (m.role if hasattr(m, 'role') else m['role']) == 'assistant']
        last_user = user_msgs[-1].content if hasattr(user_msgs[-1], 'content') else user_msgs[-1]['content'] if user_msgs else ''
        last_ai = assistant_msgs[-1].content if hasattr(assistant_msgs[-1], 'content') else assistant_msgs[-1]['content'] if assistant_msgs else ''
        
        if last_user and last_ai:
            await _analyze_and_apply(last_user, last_ai)
        
        # 3. 如果工具有结果或图谱有更新，通过 SSE 推送通知
        from app.core.event_bus import publish
        publish("graph_updated")
        
    except Exception as e:
        # 后台任务静默失败，只记日志
        log_error(ErrorCode.CHAT_PROCESS_FAILED, detail=f"后台工具分析失败: {str(e)}", exception=e)
