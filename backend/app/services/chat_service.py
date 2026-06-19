"""
对话服务层：编排整个对话处理流程

新流程（两阶段分离）：
  阶段1（流式）：构建纯教学提示词 → call_llm_stream() → SSE 逐 token 推给前端
  阶段2（后台）：构建带工具说明的提示词 → call_llm_tools() → 执行工具 → 推送结果

旧流程（保留兼容）：
  process_message() 仍可用，但 /chat/stream 是推荐接口

提示词组装逻辑：
  通用模板 (system_prompt_common.j2) + 模式模板 → 完整 system prompt
  流式阶段：不含工具能力说明，AI 专注于教学引导
  后台阶段：额外注入工具能力说明，AI 可调用 function calling

用户隔离：
  所有函数接受 user_id（从 JWT 解析），内部创建 KnowledgeGraph(user_id) 实例
"""
import asyncio
import json
import logging
from typing import AsyncGenerator
from datetime import datetime
from app.core.prompt_loader import get_system_prompt
from app.core.llm_client import call_llm, call_llm_stream, call_llm_tools
from app.core.graph_analyzer import GraphAnalyzer, build_graph_context
from app.core.knowledge_graph import KnowledgeGraph
from app.core.user_profile import UserProfile
from app.core.error_codes import ErrorCode, log_error, publish_error_event
from app.core.event_bus import publish
from app.api.v1.knowledge import _apply_suggestion, _load_suggestions, _save_suggestions

logger = logging.getLogger("ai-tutor")


# ══════════════════════════════════════════════════════════════════
#  工具能力说明（仅在后台阶段注入）
# ══════════════════════════════════════════════════════════════════

TOOL_CAPABILITY_PROMPT = """
## 知识图谱编辑能力
你可以通过调用工具来管理知识图谱。当用户提到以下内容时，主动使用工具：

- **添加知识点** → 调用 `add_knowledge_node`，自动创建节点+MD文件+关联边
- **删除知识点** → 调用 `delete_node`
- **更新内容** → 调用 `update_node_content`
- **更新掌握程度** → 调用 `update_mastery`
- **创建关联** → 调用 `add_edge`
- **更新用户画像** → 调用 `update_user_profile`

例如用户说"帮我加一个汉诺塔节点"，你就调用 `add_knowledge_node` 创建节点，
然后自然回复"已添加！汉诺塔现在关联在递归定义下"。

### 你的额外能力
1. **主动优化**：当用户讨论一个知识点时，你发现节点内容不完善，主动调用 `update_node_content` 补充
2. **提升掌握度**：当用户正确回答/理解后，调用 `update_mastery` 提升掌握度
3. **关联节点**：当发现节点间有**实质性知识关系**时才调用 `add_edge`。不要仅因为两个概念在同一对话中出现就连边。必须确认它们之间存在真正的 prerequisite/related/confusion/extension 关系
4. **扩展图谱**：当用户提到知识图谱中没有的概念时，调用 `add_knowledge_node` 自动创建。`from_nodes` 只能填真正的前置知识节点（必须先学它才能理解新节点），不要随便填
5. **识别掌握度**：根据用户回复的质量，适当调整 mastery 值（0=未掌握, 1-25=入门, 26-50=熟悉, 51-75=熟练, 76-100=精通）
6. **更新用户画像**：当你在教学中观察到学生的性格特点、学习习惯、知识薄弱点等新信息时，调用 `update_user_profile` 追加到用户画像。这有助于后续更好地个性化教学。例如：发现学生害怕数学公式、喜欢图形化解释、做题容易粗心等。

### ⚠️ 权限限制（严格执行）
- **你不能修改、删除或更新人类手动创建的节点**（added_by="human"）。这些操作会被系统拒绝。
- **你不能在两个人类创建的节点之间添加 prerequisite 边**（因为前置关系影响学习路径）。
- 你可以在人类节点之间添加 related/confusion/extension 边，但这些边会标记为 AI 建议，等待审核。
- **你创建的节点和边**（added_by="ai"）可以自由修改和删除。
- 如果用户明确要求你修改某个特定节点（如"帮我改一下XX的内容"），你可以调用工具，系统会放行。

⚠️ 重要原则：知识图谱的质量远比数量重要。宁可漏掉一条关系，也不要创建错误的关系误导学习路径。

你不需要等用户说"修改"才动手。只要对话涉及某节点内容，就主动去完善它。
"""


# ══════════════════════════════════════════════════════════════════
#  Prompt 构建
# ══════════════════════════════════════════════════════════════════

def _build_graph_summary(kg: KnowledgeGraph, detailed: bool = True) -> str:
    """
    构建知识图谱摘要文本，注入到通用模板的 {knowledge_graph_summary} 占位符。

    参数:
        kg:       KnowledgeGraph 实例（已绑定 user_id）
        detailed: True=全量数据（后台阶段用），False=精简摘要（流式阶段用）
    """
    return build_graph_context(kg, detailed=detailed)


def _build_system_prompt(messages: list, mode: str, kg: KnowledgeGraph,
                        inject_tools: bool = False) -> tuple[str, str]:
    """
    构建系统提示词（合并原 _build_stream_prompt / _build_chat_prompt）。
    
    参数:
        inject_tools: True=后台阶段（详细图谱 + 工具能力说明）
                      False=流式阶段（精简图谱，纯教学引导）
    返回: (system_prompt, last_user_message)
    """
    last_user_msg = next(
        (m.content if hasattr(m, 'content') else m['content']
         for m in reversed(messages)
         if (m.role if hasattr(m, 'role') else m['role']) == 'user'),
        ''
    )
    graph_summary = _build_graph_summary(kg, detailed=inject_tools)

    # 加载用户画像（如果存在）
    profile = UserProfile(user_id=kg.user_id)
    profile_text = profile.get_summary()

    system_prompt = get_system_prompt(
        mode=mode,
        student_message=last_user_msg,
        graph_summary=graph_summary,
        user_profile=profile_text,
    )

    if inject_tools:
        system_prompt += TOOL_CAPABILITY_PROMPT

    return system_prompt, last_user_msg


# ══════════════════════════════════════════════════════════════════
#  图谱分析（后台异步）
# ══════════════════════════════════════════════════════════════════

async def _analyze_and_apply(user_message: str, ai_reply: str,
                              user_id: int,
                              threshold: float = 0.9) -> dict:
    """
    调用图谱分析器分析对话 → 分类建议 → 自动应用高置信度建议。

    ★ 自己创建并管理 KnowledgeGraph 生命周期，避免与调用方共享实例导致 use-after-close。

    参数:
        user_message: 用户最后一条消息
        ai_reply:     AI 的最新回复
        user_id:      当前用户 ID（内部创建独立的 kg 实例）
        threshold:    自动应用建议的置信度阈值（默认 0.9）

    返回: {"suggestions": [...], "applied": [...], "pending": [...]}
    """
    result = {"suggestions": [], "applied": [], "pending": []}
    kg = KnowledgeGraph(user_id=user_id)
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
                    apply_result = _apply_suggestion(kg, s)
                    if apply_result:
                        applied_list.append({**s, "apply_result": apply_result})
                except PermissionError as e:
                    logger.warning(f"AI 权限不足，建议移至待审核: {e}")
                    pending_list.append(s)
                except Exception:
                    pending_list.append(s)
            else:
                pending_list.append(s)

        result["applied"] = applied_list
        result["pending"] = pending_list

        # 持久化待审核建议
        if pending_list:
            existing = _load_suggestions(kg.nodes_dir)
            for p in pending_list:
                p["submitted_at"] = datetime.now().isoformat()
            existing.extend(pending_list)
            _save_suggestions(kg.nodes_dir, existing)

        # 如果有自动应用的变更，发布 graph_updated 事件通知前端刷新
        if applied_list:
            publish("graph_updated")

    except Exception as e:
        # 图谱分析失败不影响对话流程，但记录日志 + 发布错误事件
        user_msg = log_error(
            ErrorCode.CHAT_ANALYZE_FAILED,
            detail=str(e),
            exception=e
        )
        publish_error_event(ErrorCode.CHAT_ANALYZE_FAILED, user_msg, "chat_service", str(e)[:200])
    finally:
        kg.close()

    return result


# ══════════════════════════════════════════════════════════════════
#  对话处理入口
# ══════════════════════════════════════════════════════════════════

async def process_message(user_id: int, messages: list, mode: str) -> tuple[str, str, dict]:
    """
    处理一条学生消息。

    参数:
        user_id:  数据库用户 ID（从 JWT 解析）
        messages: 完整对话历史（Pydantic ChatMessage 列表）
        mode:     引导模式（adaptive / free_talk）

    返回:
        (AI回复文本, 使用的模式, 图谱分析结果)

    注意：
        图谱分析（_analyze_and_apply）作为后台任务异步执行，
        不阻塞对话回复的返回，避免前端等待超时。
    """
    kg = KnowledgeGraph(user_id=user_id)

    try:
        # 1. 构建系统提示词（含图谱上下文 + 工具能力）
        system_prompt, last_user_msg = _build_system_prompt(messages, mode, kg, inject_tools=True)

        # 2. 调用 AI 获取回复
        reply = await call_llm(system_prompt, messages, enable_tools=True, kg=kg)

        # 3. 图谱分析作为后台任务执行，不阻塞对话回复
        #    ★ 传入 user_id 而非 kg 实例，让 _analyze_and_apply 自己管理 kg 生命周期
        asyncio.create_task(_analyze_and_apply(last_user_msg, reply, user_id))

    except Exception as e:
        error_msg = str(e)
        if not error_msg.startswith("[E-"):
            error_msg = log_error(ErrorCode.CHAT_PROCESS_FAILED, detail=str(e), exception=e)
        publish_error_event(ErrorCode.CHAT_PROCESS_FAILED, error_msg, "chat_service", str(e)[:200])
        kg.close()
        return error_msg, mode, {"suggestions": [], "applied": [], "pending": []}

    # ★ 修复：_analyze_and_apply 传入 user_id，让它自己管理 kg 生命周期。
    # 此处的 kg 在 call_llm 工具调用完成后已无后续操作，可安全关闭。
    kg.close()
    return reply, mode, {"suggestions": [], "applied": [], "pending": []}


# ══════════════════════════════════════════════════════════════════
#  流式对话处理（新接口 /chat/stream 使用）
# ══════════════════════════════════════════════════════════════════

async def process_message_stream(
    messages: list,
    mode: str,
    user_id: int,
) -> AsyncGenerator[str, None]:
    """
    流式处理一条学生消息。

    阶段1（流式）：逐 token yield 给前端，让用户立刻看到回复。
    阶段2（后台）：在生成器结束后，通过 BackgroundTasks 触发工具调用和图谱分析。

    参数:
        messages: 完整对话历史（Pydantic ChatMessage 列表）
        mode:     引导模式
        user_id:  数据库用户 ID（从 JWT 解析）

    Yields:
        SSE 格式的字符串（"data: {...}\n\n"）
    """
    kg = KnowledgeGraph(user_id=user_id)
    try:
        # 阶段1：流式生成回复（纯教学，不带 tools）
        stream_prompt, _ = _build_system_prompt(messages, mode, kg, inject_tools=False)

        try:
            async for token in call_llm_stream(stream_prompt, messages):
                yield f"data: {json.dumps({'token': token})}\n\n"
        except Exception as e:
            error_msg = str(e)
            if not error_msg.startswith("[E-"):
                error_msg = log_error(ErrorCode.CHAT_PROCESS_FAILED, detail=str(e), exception=e)
            yield f"data: {json.dumps({'error': error_msg})}\n\n"
            return

        # 流式结束，发送完成信号
        yield "data: [DONE]\n\n"
    finally:
        kg.close()


async def process_background_tools(
    messages: list,
    mode: str,
    user_id: int,
) -> None:
    """
    后台任务：调用 LLM 判断是否需要执行工具 + 图谱分析。

    此函数在流式回复完成后由 BackgroundTasks 触发，
    失败不影响主对话流程。

    参数:
        messages: 完整对话历史
        mode:     引导模式
        user_id:  数据库用户 ID（从 JWT 解析）
    """
    kg = KnowledgeGraph(user_id=user_id)
    try:
        # 1. 工具调用（function calling）—— 使用含工具说明的 prompt
        tool_prompt, last_user_msg = _build_system_prompt(messages, mode, kg, inject_tools=True)
        tool_result = await call_llm_tools(tool_prompt, messages, kg=kg)

        # 2. 图谱分析（GraphAnalyzer 独立判断）
        user_msgs = [m for m in messages if (m.role if hasattr(m, 'role') else m['role']) == 'user']
        assistant_msgs = [m for m in messages if (m.role if hasattr(m, 'role') else m['role']) == 'assistant']
        last_user = user_msgs[-1].content if hasattr(user_msgs[-1], 'content') else user_msgs[-1]['content'] if user_msgs else ''
        last_ai = assistant_msgs[-1].content if hasattr(assistant_msgs[-1], 'content') else assistant_msgs[-1]['content'] if assistant_msgs else ''

        if last_user and last_ai:
            await _analyze_and_apply(last_user, last_ai, user_id)

        # 3. 如果工具有结果或图谱有更新，通过 SSE 推送通知
        publish("graph_updated")

    except Exception as e:
        # 后台任务静默失败，只记日志
        log_error(ErrorCode.CHAT_PROCESS_FAILED, detail=f"后台工具分析失败: {str(e)}", exception=e)
    finally:
        kg.close()
