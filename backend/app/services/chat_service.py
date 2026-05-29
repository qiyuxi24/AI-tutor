"""
对话服务层：编排整个对话处理流程

流程：
1. 构建系统提示词（含知识图谱上下文）
2. 调用大模型获取 AI 回复（带 function calling）
3. 对话后异步分析图谱更新建议 → 自动应用高置信度建议（fire-and-forget）
4. 返回 (AI回复, 模式, 图谱分析结果)
"""

import asyncio
import logging
from datetime import datetime
from app.core.prompt_loader import get_system_prompt
from app.core.llm_client import call_llm
from app.core.graph_analyzer import GraphAnalyzer
from app.core.error_codes import ErrorCode, log_error
from app.api.v1.knowledge import kg, _apply_suggestion, _load_suggestions, _save_suggestions

logger = logging.getLogger("ai-tutor")


def _build_chat_prompt(messages: list, mode: str) -> tuple[str, str]:
    """
    构建聊天 LLM 的完整系统提示词（含图谱上下文）。

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
