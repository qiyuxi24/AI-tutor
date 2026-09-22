"""
对话服务层：编排整个对话处理流程

流程（Agent Loop，2026-09-06 起，已全面收敛）：
  统一调用 run_agent_loop（app/core/agent/loop.py）标准 agent 循环：
  带工具说明的提示词 → LLM↔工具多轮串联 → 最终回答；运行记录自动落 agent_runs，
  循环内事件（thinking/tool_start/tool_result/text_delta）经 event_bus 实时推送
  （/chat/stream 消费转发 SSE，每事件带 run_id 便于前端回源）。

提示词组装逻辑：
  通用模板 (system_prompt_common.j2) + 模式模板 → 完整 system prompt
  统一注入工具能力说明（TOOL_CAPABILITY_PROMPT = 注册表生成的逐工具指南 + 跨工具策略），
  进入 agent 循环。逐工具说明的唯一来源是 core/agent_tools/tools/*.py 的 GUIDANCE
  （2026-09-15 收敛：原先这里手写一份，与 spec.description 构成双源且已实测漂移）。

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
from app.core.agent.loop import run_agent_loop
from app.core.agent_tools import TOOLS_PROMPT  # 注册表生成的「工具调用指南」段落
from app.core.config import settings
from app.core.agent.guard import trim_history_to_budget
from app.core.graph_analyzer import GraphAnalyzer, build_graph_context
from app.core.knowledge_graph import KnowledgeGraph
from app.core.profile import UserProfile
from app.core.token_counter import count_tokens
from app.core.error_codes import ErrorCode, log_error, publish_error_event
from app.core.event_bus import publish, subscribe, get_user_queue, TEXT_DELTA
from app.core.knowledge_writer import apply_suggestion, load_suggestions, save_suggestions

logger = logging.getLogger("ai-tutor")


# ══════════════════════════════════════════════════════════════════
#  工具能力说明（inject_tools=True 时注入）
#  逐工具指南 = 注册表生成（TOOLS_PROMPT）；跨工具策略 = 本文件 TOOL_POLICY_PROMPT
# ══════════════════════════════════════════════════════════════════

TOOL_POLICY_PROMPT = """
## 工具使用策略（跨工具）

逐工具说明见上一节「工具调用指南」——它由工具注册表生成，新增工具会自动出现在其中。
下面几条是**跨工具**的硬性策略，优先级高于任何单个工具的说明。

### 掌握度由谁更新（重要，别搞错）

掌握度是学生学习进度的唯一量化指标，**主信号 = 出题判分**：

- 学生答对你出的题 → 系统**自动**把该知识点掌握度 +20（**这是系统行为，你不需要调用任何工具**）
- 所以你的首要任务是**在合适时机出题**，而不是凭对话感受去改数字

**不要**因为"学生说懂了""学生回答得不错"就调 `update_mastery` —— 这类主观判断不可复核，
历史上导致掌握度长期不动。你的手动通道已收敛到 3 种硬证据（见 `update_mastery` 的说明）。

### 学生说"我懂了"时：**出题，不要再追问**（铁律，优先于其他引导策略）

这是最容易做错的地方。**当学生表示已理解某个知识点**（"懂了""明白了""我学会了""对吧？"），
或**正确回答了你的引导问题**时：

- ✅ **正确做法**：立刻调用 `quiz_generate(node_id=该知识点的 id)` 出一道题检验，
  然后用一句话说"我出一道题检验一下，稍等片刻"收尾。
- ❌ **错误做法**：继续反问（"你能用自己的话解释一下吗？""那你觉得为什么…"）。
  学生刚说懂了、你又追问，会让他觉得你不相信他；更要紧的是**掌握度永远得不到更新**——
  掌握度只由答题判分更新，不由你的主观感觉更新。

**"追问"和"出题"分工不同，不要混用**：追问用于把困惑的学生问明白；
出题用于确认学生是否真的明白了。学生一旦表态理解，就该切换到出题。

### ⚠️ 权限限制（严格执行）

- **你不能修改、删除或更新人类手动创建的节点**（added_by="human"）。这些操作会被系统拒绝。
- **你不能在两个人类创建的节点之间添加 prerequisite 边**（因为前置关系影响学习路径）。
- 你可以在人类节点之间添加 related/confusion/extension 边，但这些边会标记为 AI 建议，等待审核。
- **你创建的节点和边**（added_by="ai"）可以自由修改和删除。
- 如果用户明确要求你修改某个特定节点（如"帮我改一下XX的内容"），你可以调用工具，系统会放行。
"""

# 完整工具能力说明 = 注册表生成的逐工具指南（含 MCP 工具，随开关自动增减）+ 跨工具策略
TOOL_CAPABILITY_PROMPT = TOOLS_PROMPT + TOOL_POLICY_PROMPT


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

# 固定段（S2–S7 = 整个 system prompt）预算线，见 docs/上下文工程/上下文工程_预算框架.md §3.2 / 不变量 B-3
FIXED_SEGMENT_WARN_RATIO = 0.40      # 超此线记 warning：固定成本开始挤压 S8 历史
FIXED_SEGMENT_DEGRADE_RATIO = 0.45   # 超此线强制重建图谱注入（唯一还能压的可还原段）

# ── S6 / S7 独立配额（框架 §1.2 表 / 不变量 B-6）──
# 两源各自截断、互不挤占：图谱正文与 S4 结构注入天然重复（压它风险最低），
# 知识库正文是全新内容但噪声代价高（Chroma 实测 1 个干扰项即有害）。
RETRIEVAL_SEGMENT_MAX_TOKENS = 3_000   # 每段上限（目标 2K / 上限 3K）
RETRIEVAL_HIT_OVERHEAD = 20            # 每条片段的标题行等固定开销（token）
# query 双端放置（框架 §4.2，Lost in the Middle：KV 检索 45.6%→100%，代价 ~50 token）
RETRIEVAL_QUERY_ECHO_MAX_CHARS = 300


def _trim_hits(hits: list, max_tokens: int) -> tuple[list, int]:
    """按相关性降序（pipeline 已排序）保留放得下的片段，至少留 1 条。

    返回 (kept, dropped)；dropped > 0 时调用方须在区块尾部标注截断
    （对齐 S4 的"展示范围说明"风格：不标注会让模型以为检索只有这么多依据）。
    """
    kept: list = []
    used = 0
    for hit in hits:
        cost = count_tokens(hit.content) + RETRIEVAL_HIT_OVERHEAD
        if kept and used + cost > max_tokens:
            break
        kept.append(hit)
        used += cost
    return kept, len(hits) - len(kept)


def _truncation_note(shown: int, dropped: int) -> str:
    """检索区块的截断说明（未截断时返回空串）。"""
    if dropped <= 0:
        return ""
    return (f"\n\n> 说明：本区块为控制上下文体量已截断，仅展示最相关的 {shown} 条"
            f"（共 {shown + dropped} 条）；需要更多依据时可再次检索。")


def _build_graph_summary(kg: KnowledgeGraph, detailed: bool = True,
                         focus_node_id: str = "", max_chars: int | None = None) -> str:
    """
    构建知识图谱摘要文本，注入到通用模板的 {knowledge_graph_summary} 占位符。

    参数:
        kg:            KnowledgeGraph 实例（已绑定 user_id）
        detailed:      True=全量数据（后台阶段用），False=精简摘要（流式阶段用）
        focus_node_id: 当前教学节点；注入体量超上限时优先保留其邻域（见 graph_analyzer）
        max_chars:     本次注入的字符上限；None=settings.graph_inject_max_chars。
                       固定段越线强制降级时会传一个更小的值重建（见 _build_system_prompt）
    """
    return build_graph_context(kg, detailed=detailed, focus_node_id=focus_node_id,
                               max_chars=max_chars)


async def _build_system_prompt(messages: list, mode: str, kg: KnowledgeGraph,
                              inject_tools: bool = False, current_node: str = "",
                              kb: dict | None = None) -> tuple[str, str]:
    """
    构建系统提示词（合并原 _build_stream_prompt / _build_chat_prompt）。
    
    参数:
        inject_tools: True=注入详细图谱 + 工具能力说明（生产唯一用法：/chat 与 /chat/stream 都传它）
                      False=精简图谱、不注入工具说明（旧两段式架构"流式阶段"的遗留开关，
                      已无生产调用点，保留给"纯教学、不给工具"的实验）
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
    graph_summary = _build_graph_summary(kg, detailed=inject_tools, focus_node_id=current_node)

    # 加载用户画像（空画像返回 ""，不会把空模板注入提示词）
    profile = UserProfile(user_id=kg.user_id)
    profile_text = profile.get_summary()

    # 递归模式额外参数。
    # 图谱统一由上面的 graph_summary 注入：模板不再自拼第二份「框架节点 + 仅 prerequisite 边」——
    # 那份与 graph_summary 信息重叠（节点 + 全量带 relation 标签的边），且不走注入体量控制。
    extra_kwargs = {}
    if mode == "recursive":
        extra_kwargs["current_node"] = current_node or "未知节点"

    # 注入检索上下文（RAG 是增强而非必需：检索失败或为空时不影响主提示词）
    # 去耦合：检索编排统一走 rag_pipeline，一次 run 按数据源分组生成图谱/知识库两个区块
    # usage_mode 由上面同一个 profile 实例带下来，避免一次请求重复读画像文件
    # 检索块先取一次：下面拼装要用，且降级重建时不重复检索
    retrieval = await _build_retrieval_context(last_user_msg, kg.user_id, kb,
                                              usage_mode=profile.get_usage_mode())

    def _assemble(graph_text: str) -> str:
        """按固定顺序拼装 system prompt（= 固定段 S2–S7 全集）。"""
        prompt = get_system_prompt(
            mode=mode,
            student_message=last_user_msg,
            graph_summary=graph_text,
            user_profile=profile_text,
            **extra_kwargs,
        )
        if retrieval:
            prompt += retrieval
        if inject_tools:
            prompt += TOOL_CAPABILITY_PROMPT
        # 图谱为空：放最后（最新指令优先级最高），覆盖退化的「框架约束」
        if not kg.nodes:
            prompt += EMPTY_GRAPH_PROMPT
        return prompt

    system_prompt = _assemble(graph_summary)
    fixed_tokens = count_tokens(system_prompt)
    budget = max(1, settings.llm_ctx_budget)

    # 固定段越线强制降级（框架 §3.2「不得照发」）：能压的只有图谱结构注入（S4，
    # 有外部还原源）；S1/S2/S9 不可压，S3 的 schema 每轮必发。guard 裁不动 system_prompt，
    # 所以这一步必须在组装侧做。上限减半重建一次，仍越线记 error 照发（已无手段）。
    degrade_line = int(budget * FIXED_SEGMENT_DEGRADE_RATIO)
    if fixed_tokens > degrade_line and graph_summary:
        graph_summary = _build_graph_summary(
            kg, detailed=inject_tools, focus_node_id=current_node,
            max_chars=max(1, len(graph_summary) // 2),
        )
        shrunk_prompt = _assemble(graph_summary)
        shrunk_tokens = count_tokens(shrunk_prompt)
        if shrunk_tokens > degrade_line:
            logger.error(
                f"固定段（S2–S7）{shrunk_tokens} tokens 超 {FIXED_SEGMENT_DEGRADE_RATIO:.0%}×预算 "
                f"{budget}（图谱减半重建前 {fixed_tokens}）→ 照发：S1/S2/S3/S9 不可压，已无手段"
            )
        else:
            logger.warning(
                f"固定段越线（{fixed_tokens} > {degrade_line}）→ 图谱注入降级重建："
                f"{fixed_tokens}→{shrunk_tokens} tokens（图谱区块 {len(graph_summary)} 字符）"
            )
        system_prompt, fixed_tokens = shrunk_prompt, shrunk_tokens

    # 预算可观测（不变量 B-3）：system prompt = 固定段全集，是每轮重发的固定成本
    logger.info(
        f"系统提示词 {fixed_tokens} tokens"
        f"（固定段占预算 {fixed_tokens / budget:.1%}；"
        f"图谱区块 {len(graph_summary)} 字符，检索区块 {len(retrieval)} 字符）"
    )
    warn_line = int(budget * FIXED_SEGMENT_WARN_RATIO)
    if fixed_tokens > warn_line:
        logger.warning(
            f"固定段（S2–S7）{fixed_tokens} tokens 超告警线 {warn_line}"
            f"（{FIXED_SEGMENT_WARN_RATIO:.0%}×预算 {budget}）→ 正在挤压 S8 历史；"
            f"优先降 GRAPH_INJECT_MAX_CHARS 与画像体量"
        )

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
        体量（2026-09-15，不变量 B-6）：S6 图谱 / S7 知识库**各自**截断到
        `RETRIEVAL_SEGMENT_MAX_TOKENS`，一源超长不挤占另一源；截断时尾部标注展示范围。
        位置（框架 §4.2）：当前问题同时出现在检索块**前后**（query-aware contextualization）。
    """
    from app.core.rag_pipeline import pipeline, RagContext

    hits = await pipeline.run(RagContext(
        user_id=user_id, query=student_message, top_k=5, kb=kb,
        mode=usage_mode, metadata={"graph_hops": graph_hops},
    ))

    graph_hits = [h for h in hits if h.source == "graph"]
    kb_hits = [h for h in hits if h.source == "kb"]

    # 两源独立截断（B-6）：图谱先截、知识库后截，互不挤占预算
    graph_hits, graph_dropped = _trim_hits(graph_hits, RETRIEVAL_SEGMENT_MAX_TOKENS)
    kb_hits, kb_dropped = _trim_hits(kb_hits, RETRIEVAL_SEGMENT_MAX_TOKENS)

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
        blocks.append("\n\n".join(lines) + _truncation_note(len(graph_hits), graph_dropped))

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
        blocks.append("\n\n".join(lines) + _truncation_note(len(kb_hits), kb_dropped))

    if not blocks:
        return ""

    # query-aware 双端放置（框架 §4.2 唯一允许的注入顺序改动）：查询同时置于数据前后
    echo = ("## 当前问题（检索片段以此为准）\n"
            f"{student_message[:RETRIEVAL_QUERY_ECHO_MAX_CHARS]}\n")
    return "\n\n".join([echo, *blocks, echo])


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
    kg = None
    try:
        # 构造也必须在 try 内：本函数由 asyncio.create_task 火忘式调用，
        # 在 try 外抛异常会变成 "Task exception was never retrieved"（只进 stderr，
        # 不进 tutor.log、不进任何库），是排查时最难看见的一类失败。
        kg = KnowledgeGraph(user_id=user_id)
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
        if kg is not None:
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

        # 2. 发送前守卫：清理较早工具结果 + 历史超预算时分层压缩（统计由守卫内部记日志）
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

        # 发送前守卫：清理较早工具结果 + 历史超预算时分层压缩（统计由守卫内部记日志）
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
                    "agent_start", "agent_done", "graph_updated", "quiz_ready", "error"):
        return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
    logger.debug(f"未知事件类型已丢弃: {evt_type}")
    return None
