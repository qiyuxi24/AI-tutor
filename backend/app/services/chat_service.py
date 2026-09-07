"""
对话服务层：编排整个对话处理流程

新流程（2026-09-06 起，Agent Loop）：
  run_agent_loop（app/core/agent_loop.py）= 标准 agent 循环：
  阶段2（后台）：带工具说明的提示词 → LLM↔工具多轮串联 → 图谱更新 + trace 落盘
  阶段1（流式过渡）：call_llm_stream() 仍先流式输出可见文本（Batch3 合并前保留）

提示词组装逻辑：
  通用模板 (system_prompt_common.j2) + 模式模板 → 完整 system prompt
  流式阶段：不含工具能力说明，AI 专注于教学引导
  工具阶段：额外注入工具能力说明（TOOL_CAPABILITY_PROMPT），进入 agent 循环

用户隔离：
  所有函数接受 user_id（从 JWT 解析），内部创建 KnowledgeGraph(user_id) 实例
"""
import asyncio
import json
import logging
from typing import AsyncGenerator
from datetime import datetime
from app.core.prompt_loader import get_system_prompt
from app.core.llm_client import call_llm_stream
from app.core.agent_loop import run_agent_loop, save_trace
from app.core.graph_analyzer import GraphAnalyzer, build_graph_context
from app.core.knowledge_graph import KnowledgeGraph
from app.core.user_profile import UserProfile
from app.core.error_codes import ErrorCode, log_error, publish_error_event
from app.core.event_bus import publish, subscribe, TEXT_DELTA
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
- **查询网页** → 调用 `fetch_webpage`（抓取网页正文，获取实时/外部信息）
- **检索知识** → 调用 `rag_search`（从知识图谱/上传知识库中检索与某话题最相关的内容片段，补充教学依据）

例如用户说"帮我加一个汉诺塔节点"，你就调用 `add_knowledge_node` 创建节点，
然后自然回复"已添加！汉诺塔现在关联在递归定义下"。

### 你的额外能力
1. **主动优化**：当用户讨论一个知识点时，你发现节点内容不完善，主动调用 `update_node_content` 补充
2. **提升掌握度**：当用户正确回答/理解后，调用 `update_mastery` 提升掌握度
3. **关联节点**：当发现节点间有**实质性知识关系**时才调用 `add_edge`。不要仅因为两个概念在同一对话中出现就连边。必须确认它们之间存在真正的 prerequisite/related/confusion/extension 关系
4. **扩展图谱**：当用户提到知识图谱中没有的概念时，调用 `add_knowledge_node` 自动创建。`from_nodes` 只能填真正的前置知识节点（必须先学它才能理解新节点），不要随便填
5. **识别掌握度**：根据用户回复的质量，适当调整 mastery 值（0=未掌握, 1-25=入门, 26-50=熟悉, 51-75=熟练, 76-100=精通）
6. **更新用户画像**：当你在教学中观察到学生的性格特点、学习习惯、知识薄弱点等新信息时，调用 `update_user_profile` 追加到用户画像。这有助于后续更好地个性化教学。例如：发现学生害怕数学公式、喜欢图形化解释、做题容易粗心等。
7. **查询网页**：当学生提到一个 URL、需要实时信息（新闻、最新文档、教程）或某个话题你需要外部资料来讲解时，调用 `fetch_webpage` 抓取网页正文。拿到正文后提炼要点，用通俗语言教给学生。若抓取失败，礼貌说明并提供其他学习途径。
8. **检索知识**：当学生的问题涉及某个具体知识点、需要从已学图谱或上传资料中找依据、或你想确认某个概念的资料时，调用 `rag_search` 检索相关片段。拿到片段后据此准确回答并标注出处（如"据你之前学的《数据结构》第2章…"）。若未检索到相关内容，基于已有知识回答即可，不要编造。

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


async def _build_system_prompt(messages: list, mode: str, kg: KnowledgeGraph,
                              inject_tools: bool = False, current_node: str = "",
                              kb: dict | None = None) -> tuple[str, str]:
    """
    构建系统提示词（合并原 _build_stream_prompt / _build_chat_prompt）。
    
    参数:
        inject_tools: True=后台阶段（详细图谱 + 工具能力说明）
                      False=流式阶段（精简图谱，纯教学引导）
        current_node: 递归模式：当前正在教学的知识点 ID
        kb: 知识库上下文范围 {node_ids: [...], name: str}，可选；
            传入后在所选目录范围内检索文档片段注入提示词
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

    # 递归模式额外参数
    extra_kwargs = {}
    if mode == "recursive":
        # 构建框架摘要（所有节点 ID + 名称 + 依赖关系，不含内容）
        framework_lines = []
        for n in kg.nodes:
            tags = ", ".join(n.get("tags", []))
            mastery = n.get("mastery", 0)
            framework_lines.append(f"  [{n['id']}] {n['name']} (掌握度:{mastery}, 标签:{tags})")
        node_list = "\n".join(framework_lines) if framework_lines else "  (暂无节点)"

        edge_lines = []
        for e in kg.edges:
            if e.get("relation") == "prerequisite":
                edge_lines.append(f"  {e['from_node']} → {e['to_node']} (前置依赖)")
        edge_list = "\n".join(edge_lines) if edge_lines else "  (暂无依赖关系)"

        extra_kwargs["knowledge_graph_framework"] = (
            f"### 框架节点\n{node_list}\n\n### 依赖关系\n{edge_list}"
        )
        extra_kwargs["current_node"] = current_node or "未知节点"

    system_prompt = get_system_prompt(
        mode=mode,
        student_message=last_user_msg,
        graph_summary=graph_summary,
        user_profile=profile_text,
        **extra_kwargs,
    )

    # 注入检索上下文（RAG 是增强而非必需：检索失败或为空时不影响主提示词）
    # 去耦合：检索编排统一走 rag_pipeline，一次 run 按数据源分组生成图谱/知识库两个区块
    retrieval = await _build_retrieval_context(last_user_msg, kg.user_id, kb)
    if retrieval:
        system_prompt += retrieval

    if inject_tools:
        system_prompt += TOOL_CAPABILITY_PROMPT

    return system_prompt, last_user_msg


async def _build_retrieval_context(student_message: str, user_id: int,
                                   kb: dict | None = None) -> str:
    """
    通过 RAG 管道检索相关片段，构造注入系统提示词的检索上下文。

    参数:
        student_message: 学生当前消息
        user_id:         用户 ID
        kb:              知识库上下文范围 {node_ids, name} | None

    返回:
        格式化的检索上下文 Markdown 文本（图谱区块 + 知识库区块）；
        无相关内容时返回空字符串。

    说明:
        检索编排统一走 rag_pipeline，一次 run 并行检索所有已注册数据源，
        按 source 分组生成两个区块，避免对同一 query 重复 embedding。
        鲁棒性：pipeline 内部已做按需开关 + 单源超时/异常隔离，绝不抛错。
    """
    from app.core.rag_pipeline import pipeline, RagContext

    # 商用/个人用途模式（读 user_profile.preferences.usage_mode，缺省 personal）
    usage_mode = "personal"
    try:
        usage_mode = UserProfile(user_id=user_id).get_usage_mode()
    except Exception as e:
        logger.warning(f"读取用途模式失败，按 personal 处理: {e}")

    hits = await pipeline.run(RagContext(
        user_id=user_id, query=student_message, top_k=5, kb=kb,
        mode=usage_mode,
    ))

    graph_hits = [h for h in hits if h.source == "graph"]
    kb_hits = [h for h in hits if h.source == "kb"]

    blocks: list[str] = []

    # 图谱区块
    if graph_hits:
        lines = [
            "## 相关知识点参考（RAG 检索）",
            "以下是从你的知识图谱中检索到的与当前话题最相关的学习内容片段，可帮助回答时更准确、贴合你的已学知识：",
        ]
        for i, r in enumerate(graph_hits, 1):
            node_name = r.metadata.get("node_name") or r.path or "知识点"
            lines.append(f"### 片段 {i}：{node_name}"
                         + (f"（{r.heading}）" if r.heading else ""))
            lines.append(r.content)
        blocks.append("\n\n".join(lines))

    # 知识库区块
    if kb_hits:
        kb_name = (kb or {}).get("name") or "我的知识库"
        lines = [
            f"## 知识库参考（来自「{kb_name}」）",
            "以下是从你选择的知识库文档中检索到的与当前话题相关的内容片段，回答时可结合这些资料：",
        ]
        for i, r in enumerate(kb_hits, 1):
            lines.append(f"### 片段 {i}"
                         + (f"：{r.path}" if r.path else ""))
            lines.append(r.content)
        blocks.append("\n\n".join(lines))

    return "\n\n".join(blocks)


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

        # RAG 增量索引：对本次实际变更的节点重新建立语义索引
        if applied_list:
            await _index_applied_nodes(applied_list, user_id, kg)

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


async def _index_applied_nodes(applied_list: list[dict], user_id: int, kg) -> None:
    """
    对图谱分析后实际变更的节点增量更新 RAG 索引。

    参数:
        applied_list: 已应用的建议列表
        user_id:      当前用户 ID
        kg:           KnowledgeGraph 实例

    说明:
        - add_node        → 索引新节点
        - update_content  → 重新索引该节点
        - add_edge        → 不涉及内容检索，跳过
        任一节点索引失败不影响整体流程。
    """
    from app.core.rag.manager import rag_manager

    # 收集需要（重新）索引的节点 ID
    node_ids_to_index: set[str] = set()
    for s in applied_list:
        action = s.get("action")
        if action == "add_node":
            node = s.get("node", {})
            nid = node.get("id")
            if nid:
                node_ids_to_index.add(nid)
        elif action == "update_content":
            nid = s.get("node_id")
            if nid:
                node_ids_to_index.add(nid)

    for nid in node_ids_to_index:
        node = kg.get_node(nid)
        if node is None:
            continue
        try:
            await rag_manager.index_node(user_id, node, kg)
        except Exception as e:
            logger.warning(f"RAG 索引节点 {nid} 失败: {e}")


# ══════════════════════════════════════════════════════════════════
#  对话处理入口
# ══════════════════════════════════════════════════════════════════

async def process_message(user_id: int, messages: list, mode: str,
                        current_node: str = "",
                        kb: dict | None = None) -> tuple[str, str, dict]:
    """
    处理一条学生消息。

    参数:
        user_id:      数据库用户 ID（从 JWT 解析）
        messages:     完整对话历史（Pydantic ChatMessage 列表）
        mode:         引导模式（adaptive / free_talk / recursive）
        current_node: 递归模式：当前正在教学的知识点 ID

    返回:
        (AI回复文本, 使用的模式, 图谱分析结果)

    注意：
        图谱分析（_analyze_and_apply）作为后台任务异步执行，
        不阻塞对话回复的返回，避免前端等待超时。
    """
    kg = KnowledgeGraph(user_id=user_id)

    try:
        # 1. 构建系统提示词（含图谱上下文 + RAG + 工具能力）
        system_prompt, last_user_msg = await _build_system_prompt(
            messages, mode, kg, inject_tools=True, current_node=current_node, kb=kb
        )

        # 2. Agent 主循环：LLM ↔ 工具 多轮串联，直到自然给出最终回复
        result = await run_agent_loop(system_prompt, messages, kg=kg)
        reply = result.text

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
    current_node: str = "",
    kb: dict | None = None,
) -> AsyncGenerator[str, None]:
    """
    统一 SSE 流（方案 B）：run_agent_loop 为主答案生成器，
    循环内的事件（thinking/tool_start/tool_result/text_delta）实时推到前端。

    架构：
      1. 启动 agent_loop 作为 asyncio.Task（带 user_id → 事件投递到用户队列）
      2. 同时 subscribe(user_id) 消费事件 → 转 SSE yield
      3. agent_loop 完成后做图谱分析 + trace 落盘 + graph_updated 事件
      4. yield [DONE]

    向后兼容：旧前端收到 {"token": "..."} 仍正常（text_delta 事件转 token 格式）。
    """
    kg = KnowledgeGraph(user_id=user_id)
    try:
        tool_prompt, _ = await _build_system_prompt(
            messages, mode, kg, inject_tools=True,
            current_node=current_node, kb=kb
        )

        # 启动 agent loop 为后台任务（事件通过 event_bus 投递到用户队列）
        agent_task = asyncio.create_task(
            run_agent_loop(tool_prompt, messages, kg=kg, user_id=user_id)
        )

        # 消费用户事件队列 → SSE
        async for sse_data in _consume_agent_events(user_id, agent_task):
            yield sse_data

        # 获取 agent 结果
        result = await agent_task
        save_trace(result, user_id)

        # 图谱分析（复用现有逻辑）
        user_msgs = [m for m in messages if (m.role if hasattr(m, 'role') else m['role']) == 'user']
        if user_msgs and result.text:
            last_user = user_msgs[-1].content if hasattr(user_msgs[-1], 'content') else user_msgs[-1]['content']
            await _analyze_and_apply(last_user, result.text, user_id)

        publish("graph_updated")

        yield "data: [DONE]\n\n"
    except Exception as e:
        error_msg = str(e)
        if not error_msg.startswith("[E-"):
            error_msg = log_error(ErrorCode.CHAT_PROCESS_FAILED, detail=str(e), exception=e)
        yield f"data: {json.dumps({'error': error_msg})}\n\n"
    finally:
        kg.close()


async def _consume_agent_events(user_id: int, agent_task: asyncio.Task) -> AsyncGenerator[str, None]:
    """
    消费 agent 事件队列，转为 SSE 格式 yield。
    agent_task 完成后停止消费。
    """
    async for sse in subscribe(user_id):
        if agent_task.done() and _user_queue_empty(user_id):
            break

        payload = json.loads(sse.replace("data: ", "").strip())
        evt_type = payload.get("type")

        if evt_type == TEXT_DELTA:
            # 向后兼容：text_delta → token 格式
            yield f"data: {json.dumps({'token': payload.get('text', '')})}\n\n"
        elif evt_type in ("thinking", "tool_start", "tool_result", "agent_start", "agent_done"):
            # 新事件类型：透传给前端
            yield sse
        elif evt_type == "graph_updated":
            # 图谱更新事件：透传
            yield sse
        elif evt_type == "error":
            yield sse

        # agent 完成且队列已空 → 退出
        if agent_task.done() and _user_queue_empty(user_id):
            break


def _user_queue_empty(user_id: int) -> bool:
    """检查用户事件队列是否为空。"""
    from app.core.event_bus import _user_queues
    q = _user_queues.get(user_id)
    return q is None or q.empty()


async def process_background_tools(
    messages: list,
    mode: str,
    user_id: int,
    current_node: str = "",
    kb: dict | None = None,
) -> None:
    """
    后台任务：调用 LLM 判断是否需要执行工具 + 图谱分析。

    此函数在流式回复完成后由 BackgroundTasks 触发，
    失败不影响主对话流程。

    参数:
        messages:     完整对话历史
        mode:         引导模式
        user_id:      数据库用户 ID（从 JWT 解析）
        current_node: 递归模式：当前正在教学的知识点 ID
    """
    kg = KnowledgeGraph(user_id=user_id)
    try:
        # 1. Agent 主循环：工具判断 + 执行 + 自然收尾（替代旧 call_llm_tools 一轮脚本）
        tool_prompt, last_user_msg = await _build_system_prompt(
            messages, mode, kg, inject_tools=True, current_node=current_node, kb=kb
        )
        result = await run_agent_loop(tool_prompt, messages, kg=kg)
        save_trace(result, user_id)

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
