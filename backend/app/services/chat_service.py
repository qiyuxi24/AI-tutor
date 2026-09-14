"""
对话服务层：编排整个对话处理流程

流程（Agent Loop，2026-09-06 起，已全面收敛）：
  统一调用 run_agent_loop（app/core/agent_loop.py）标准 agent 循环：
  带工具说明的提示词 → LLM↔工具多轮串联 → 最终回答；运行记录自动落 agent_runs，
  循环内事件（thinking/tool_start/tool_result/text_delta）经 event_bus 实时推送
  （/chat/stream 消费转发 SSE，每事件带 run_id 便于前端回源）。

提示词组装逻辑：
  通用模板 (system_prompt_common.j2) + 模式模板 → 完整 system prompt
  统一注入工具能力说明（TOOL_CAPABILITY_PROMPT），进入 agent 循环

图谱分析（后台异步）：
  _analyze_and_apply 分析对话 → 高置信度建议自动应用 / 其余入待审核；失败不影响回复

用户隔离：
  所有函数接受 user_id（从 JWT 解析），内部创建 KnowledgeGraph(user_id) 实例
"""
import asyncio
import json
import logging
from typing import AsyncGenerator
from datetime import datetime
from app.core.prompt_loader import get_system_prompt
from app.core.agent_loop import run_agent_loop
from app.core.context_guard import trim_history_to_budget
from app.core.graph_analyzer import GraphAnalyzer, build_graph_context
from app.core.knowledge_graph import KnowledgeGraph
from app.core.profile import UserProfile
from app.core.error_codes import ErrorCode, log_error, publish_error_event
from app.core.event_bus import publish, subscribe, get_user_queue, TEXT_DELTA
from app.core.knowledge_writer import apply_suggestion, load_suggestions, save_suggestions

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
- **下载资源** → 调用 `download_resource`（把公网上可直接下载的文档/电子书存入知识库，之后可被 `rag_search` 检索；只临时看网页正文用 `fetch_webpage`）
- **检索知识** → 调用 `rag_search`（从知识图谱/上传知识库中检索与某话题最相关的内容片段，补充教学依据）
- **联网搜索** → 调用 `mcp__websearch__web_search`（互联网搜索，返回标题/链接/摘要。知识库和图谱里都没有、或需要最新信息时才用）

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
9. **联网搜索**：当问题需要**本地资料之外的实时/外部信息**（最新新闻、新版本特性、网上教程、你知识截止后的变化），或 `rag_search` 没找到依据时，调用 `mcp__websearch__web_search`。先搜关键词拿到链接与摘要，必要时再用 `fetch_webpage` 抓正文深入。回答时**必须标注来源链接**，并提醒学生自行核查；搜索失败或没有结果时，如实说明并基于已有知识回答。**不要**为本地资料已覆盖的内容去联网搜索（浪费且拖慢回复）。

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
#  图谱为空时的行动顺序（始终注入）
# ══════════════════════════════════════════════════════════════════
# 空图谱时通用模板里的「框架约束」退化成一个节点都没有，模型容易反复检索图谱，
# 甚至反过来向学生断言"你的图谱是空的"（P1 对照实验实测到该幻觉）。
# 这里把空图谱从"无话可说"改写成明确动作：先建图谱，再调用其他工具。
EMPTY_GRAPH_PROMPT = """
## ⚠️ 当前学生图谱为空（重要，优先于上面的框架约束）
这个学生还没有任何知识点节点，上面的「框架约束」此刻无节点可依，请按下面顺序处理：

1. **先建图谱**：本次对话涉及某个知识点时，先用 `rag_search` 从已上传资料中找依据，
   再调用 `add_knowledge_node` 建立节点（能判断归属就一并带上 `subject` / `board`）。
2. **再调用其他图谱工具**：节点建好之后，才用 `add_edge` 补关系、用 `update_mastery` 记掌握度。
   图里不存在的节点调用这两个工具会失败，失败后不要反复重试同一个参数。
3. **不要向学生断言图谱状态**：不要说"你的图谱是空的 / 没有找到你的记录"这类结论，
   也不要为了确认而反复检索图谱；该建就直接建，建完照常讲解。
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

    # 加载用户画像（空画像返回 ""，不会把空模板注入提示词）
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
    # usage_mode 由上面同一个 profile 实例带下来，避免一次请求重复读画像文件
    retrieval = await _build_retrieval_context(last_user_msg, kg.user_id, kb,
                                              usage_mode=profile.get_usage_mode())
    if retrieval:
        system_prompt += retrieval

    if inject_tools:
        system_prompt += TOOL_CAPABILITY_PROMPT

    # 图谱为空：放最后（最新指令优先级最高），覆盖退化的「框架约束」
    if not kg.nodes:
        system_prompt += EMPTY_GRAPH_PROMPT

    return system_prompt, last_user_msg


async def _build_retrieval_context(student_message: str, user_id: int,
                                   kb: dict | None = None,
                                   usage_mode: str = "personal",
                                   graph_hops: int = 0) -> str:
    """
    通过 RAG 管道检索相关片段，构造注入系统提示词的检索上下文。

    参数:
        student_message: 学生当前消息
        user_id:         用户 ID
        kb:              知识库上下文范围 {node_ids, name} | None
        usage_mode:      版权/用途模式（调用方从画像读好后传入，缺省 personal）
        graph_hops:      图谱扩跳深度（0=纯语义检索，默认）。>0 时沿前置关系
                         额外补出语义不相似的前置知识片段，供 A/B 实验对比。

    返回:
        格式化的检索上下文 Markdown 文本（图谱区块 + 知识库区块）；
        无相关内容时返回空字符串。

    说明:
        检索编排统一走 rag_pipeline，一次 run 并行检索所有已注册数据源，
        按 source 分组生成两个区块，避免对同一 query 重复 embedding。
        鲁棒性：pipeline 内部已做按需开关 + 单源超时/异常隔离，绝不抛错。
    """
    from app.core.rag_pipeline import pipeline, RagContext

    hits = await pipeline.run(RagContext(
        user_id=user_id, query=student_message, top_k=5, kb=kb,
        mode=usage_mode, metadata={"graph_hops": graph_hops},
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
                    apply_result = apply_suggestion(kg, s)
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
            existing = load_suggestions(kg.nodes_dir)
            for p in pending_list:
                p["submitted_at"] = datetime.now().isoformat()
            existing.extend(pending_list)
            save_suggestions(kg.nodes_dir, existing)

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

        # 2. 发送前守卫：历史超预算时裁掉最旧轮次（裁剪统计由守卫内部记日志）
        messages, _ = trim_history_to_budget(system_prompt, messages)

        # 3. Agent 主循环：LLM ↔ 工具 多轮串联，直到自然给出最终回复
        #    ★ 传 user_id：即使无 SSE 订阅，运行记录也写入 agent_runs（默认可观测）
        result = await run_agent_loop(system_prompt, messages, kg=kg, user_id=user_id)
        reply = result.text

        # 4. 图谱分析作为后台任务执行，不阻塞对话回复
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
    # 此处的 kg 在 run_agent_loop 结束后已无后续操作，可安全关闭。
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

        # 预创建用户事件队列，确保 agent 发出的第一个事件不丢失
        # （旧实现先启动 agent 再 subscribe，agent 可能在队列创建前就发了事件 → 静默丢弃）
        get_user_queue(user_id)

        # 发送前守卫：历史超预算时裁掉最旧轮次（裁剪统计由守卫内部记日志）
        messages, _ = trim_history_to_budget(tool_prompt, messages)

        # 启动 agent loop 为后台任务（事件通过 event_bus 投递到用户队列）
        agent_task = asyncio.create_task(
            run_agent_loop(tool_prompt, messages, kg=kg, user_id=user_id)
        )

        # 消费用户事件队列 → SSE
        async for sse_data in _consume_agent_events(user_id, agent_task):
            yield sse_data

        # 获取 agent 结果（运行记录已由 run_agent_loop 内部写入 agent_runs，无需再 save_trace）
        result = await agent_task

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

    旧实现的致命缺陷：用 `async for sse in subscribe(user_id)` 消费，
    而 subscribe 内部是 `while True: await q.get()`。当 agent_task 完成
    且队列排空后，下一次 q.get() 会永久阻塞——break 检查在循环体内，
    要 q.get() 返回后才能执行到，但队列已空永远不会返回。

    新实现用 asyncio.wait 竞争 q.get() 与 agent_task 完成信号：
      - 事件先到 → 处理事件，继续循环
      - agent 先完成 → 排空剩余事件后退出
      - 同时完成 → 处理当前事件，排空剩余后退出
    """
    q = get_user_queue(user_id)

    while True:
        get_task = asyncio.ensure_future(q.get())

        done, _ = await asyncio.wait(
            {get_task, agent_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        # 事件已到达 → 格式化 yield
        if get_task in done:
            sse = _format_agent_sse(get_task.result())
            if sse is not None:
                yield sse
        else:
            get_task.cancel()
            try:
                await get_task
            except (asyncio.CancelledError, Exception):
                pass

        # agent 完成 → 让 call_soon_threadsafe 回调落地后排空剩余事件
        if agent_task in done:
            await asyncio.sleep(0)
            while not q.empty():
                sse = _format_agent_sse(q.get_nowait())
                if sse is not None:
                    yield sse
            break


def _format_agent_sse(event: dict) -> str | None:
    """把事件 dict 格式化为 SSE data 行；未知类型返回 None（静默丢弃）。"""
    evt_type = event.get("type")

    if evt_type == TEXT_DELTA:
        return f"data: {json.dumps({'token': event.get('text', '')})}\n\n"
    if evt_type in ("thinking", "tool_start", "tool_result",
                    "agent_start", "agent_done", "graph_updated", "error"):
        return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
    logger.debug(f"未知事件类型已丢弃: {evt_type}")
    return None
