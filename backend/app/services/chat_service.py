"""
对话服务层：编排整个对话处理流程

流程（Agent Loop，2026-09-06 起，已全面收敛）：
  统一调用 run_agent_loop（app/core/agent/loop.py）标准 agent 循环：
  带工具说明的提示词 → LLM↔工具多轮串联 → 最终回答；运行记录自动落 agent_runs，
  循环内事件（thinking/tool_start/tool_result/text_delta）经 event_bus 实时推送
  （/chat/stream 消费转发 SSE，每事件带 run_id 便于前端回源）。

提示词组装逻辑：
  唯一模板 (system_prompt_common.j2，2026-09-26 由三种引导模式合并) → 完整 system prompt
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
from app.core.agent_tools.registry import build_authorization_block
from app.core.config import settings
from app.core.agent.guard import trim_history_to_budget
from app.core.graph_analyzer import GraphAnalyzer
from app.core.knowledge_graph import KnowledgeGraph
from app.core.profile import UserProfile
from app.core.token_counter import count_tokens
from app.core.error_codes import ErrorCode, log_error, publish_error_event
from app.core.event_bus import publish, TEXT_DELTA
from app.core.knowledge_writer import apply_suggestion, load_suggestions, save_suggestions
from app.core.quiz.chat_grade import auto_grade_pending

logger = logging.getLogger("ai-tutor")


# ══════════════════════════════════════════════════════════════════
#  工具能力说明（inject_tools=True 时注入）
#  逐工具指南 = 注册表生成（TOOLS_PROMPT）；跨工具策略 = 本文件 TOOL_POLICY_PROMPT
# ══════════════════════════════════════════════════════════════════

_TOOL_POLICY_TEMPLATE = """
## 工具使用策略（跨工具）

逐工具说明见上一节「工具调用指南」——它由工具注册表生成，新增工具会自动出现在其中。
下面几条是**跨工具**的硬性策略，优先级高于任何单个工具的说明。

### 工具授权分级（先看清哪些要问，再动手）

你的工具分两类，**用错类别会让学生觉得你擅自动了他的东西**：

**① 免确认 —— 直接调用，不要问**
只读检索，或只记录学生本人的信息，不改变他的知识图谱结构：
{free_tools}
（学生说"帮我查一下""搜搜看"，直接搜，别先问"要不要我搜"。）

**② 须先问 —— 先在对话里问过学生、他答应了，下一轮再调用**
会改变知识图谱结构，或把资料下载留存进他的库：
{ask_tools}

铁律（违反会被学生视为"擅自操作"）：
1. **这一类操作，本轮只输出询问文本，不要同时调用工具。**
   正确：只说"我把它整理成一个知识点加进你的图谱，方便以后复习，要吗？"
   错误：一边问"要吗？"一边已经调用了 `add_knowledge_node`。
2. **学生明确要求 = 已授权**（"帮我加个节点""把这个删掉""考考我""把这篇存下来"）→
   直接调用，不要再问一遍。
3. **学生拒绝或没回应就不做**，也不要换个说法反复劝。
4. 同一件事只问一次；已在同一轮获得同意，就直接调用，不要重复确认。

### 掌握度由谁更新（重要，别搞错）

掌握度是学生学习进度的唯一量化指标，**主信号 = 出题判分**：

- 学生答对你出的题 → 系统**自动**把该知识点掌握度 +20（**这是系统行为，你不需要调用任何工具**）
- 所以你的首要任务是**在合适时机出题**，而不是凭对话感受去改数字

**不要**因为"学生说懂了""学生回答得不错"就调 `update_mastery` —— 这类主观判断不可复核，
历史上导致掌握度长期不动。你的手动通道已收敛到 3 种硬证据（见 `update_mastery` 的说明）。

### 学生说"我懂了"时：**提议出题，不要再追问**（铁律，优先于其他引导策略）

这是最容易做错的地方。**当学生表示已理解某个知识点**（"懂了""明白了""我学会了""对吧？"），
或**正确回答了你的引导问题**时：

- ✅ **正确做法**：用一句话**提议**出题——"要不要我出一道题检验一下？"
  学生答应后（"好""可以""来"），**下一轮**立刻调用 `quiz_generate(node_id=该知识点的 id)`。
- ❌ **错误做法（两种）**：① 继续反问（"你能用自己的话解释一下吗？""那你觉得为什么…"）——
  学生刚说懂了、你又追问，会让他觉得你不相信他，而且**掌握度永远得不到更新**
  （掌握度只由答题判分更新，不由你的主观感觉更新）；
  ② 边问"要吗"边已经把题出了 —— 那属于"擅自出题"。
- ⚡ **学生主动要求练习**（"考考我""练一道"）＝ 已授权，直接出题，不用再问。

**"追问"和"出题"分工不同，不要混用**：追问用于把困惑的学生问明白；
出题用于确认学生是否真的明白了。学生一旦表态理解，就该切换到"提议出题"。

### ⚠️ 权限限制（严格执行）

- **你不能修改、删除或更新人类手动创建的节点**（added_by="human"）。这些操作会被系统拒绝。
- **你不能在两个人类创建的节点之间添加 prerequisite 边**（因为前置关系影响学习路径）。
- 你可以在人类节点之间添加 related/confusion/extension 边，但这些边会标记为 AI 建议，等待审核。
- **你创建的节点和边**（added_by="ai"）可以自由修改和删除。
- 如果用户明确要求你修改某个特定节点（如"帮我改一下XX的内容"），你可以调用工具，系统会放行。
"""

# 授权分级两份名单 = **注册表派生**（唯一声明处是各工具的 `_spec(tier=...)`）。
# 原先这里是手写名单、测试再硬编码一份，实测已漏项：`update_mastery` / `grade_answer`
# 谁都没列 → 模型对它们没有任何分级指引，且加新工具必须记得改三处。
_AUTH_FREE, _AUTH_ASK = build_authorization_block()
TOOL_POLICY_PROMPT = (_TOOL_POLICY_TEMPLATE
                      .replace("{free_tools}", _AUTH_FREE)
                      .replace("{ask_tools}", _AUTH_ASK))

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


def _last_user_text(messages: list) -> str:
    """取最后一条用户消息的文本（messages 元素可能是 dict 或 pydantic 对象）"""
    for m in reversed(messages or []):
        role = m.get("role") if isinstance(m, dict) else getattr(m, "role", "")
        if role == "user":
            content = m.get("content") if isinstance(m, dict) else getattr(m, "content", "")
            return (content or "").strip()
    return ""


async def _build_graph_summary(kg: KnowledgeGraph, detailed: bool = True,
                               focus_node_id: str = "",
                               max_chars: int | None = None) -> str:
    """
    构建知识图谱摘要文本，注入到通用模板的 {knowledge_graph_summary} 占位符。

    **经 RAG 中间件取回**：图谱结构是 pipeline 的 required 源 `graph_structure`，
    与图谱片段 / 知识库片段同级暴露，消费层只依赖 RagHit（依赖倒置）。

    参数:
        kg:            KnowledgeGraph 实例（已绑定 user_id）
        detailed:      True=附节点 MD 摘要；False=仅名称+标签
        focus_node_id: 当前教学节点；注入体量超上限时优先保留其邻域
        max_chars:     本次注入字符上限；None=settings.graph_inject_max_chars
    """
    from app.core.rag_pipeline import pipeline, RagContext

    hits = await pipeline.run(RagContext(
        user_id=kg.user_id, query="", sources={"graph_structure"},
        metadata={"kg": kg, "graph_detailed": detailed,
                  "focus_node_id": focus_node_id, "graph_max_chars": max_chars},
    ))
    return "".join(h.content for h in hits if h.source == "graph_structure")


def _is_learn_opener(messages: list) -> bool:
    """本次请求是不是「去学习」的**开场轮**（对话里还没有任何 assistant 消息）。

    开场轮的判定不能靠模型自述，得看会话结构：学生点「去学习」→ 前端新开对话 + 发一条
    "开始学习「X」"，所以第一轮 messages = [user]，此后每轮都会多一条 assistant。
    """
    for m in messages or []:
        role = m.get("role") if isinstance(m, dict) else getattr(m, "role", "")
        if role == "assistant":
            return False
    return True


def learn_mode_kind(kg: KnowledgeGraph, focus_node: dict | None, messages: list) -> str:
    """学习模式的轮次类型：`"opener"` 开场轮 / `"normal"` 后续轮 / `""` 不适用。

    判定 = 有 focus 节点 + 该节点已小节化 + 会话里还没有 assistant 消息（`_is_learn_opener`）。

    **调用方用它决定开场轮要不要禁工具**（`run_agent_loop(no_tools=...)`）——
    提示词侧与工具侧必须同源，所以两边都调这一个函数，别各写一份判定。
    为什么需要工具侧的硬保证：提示词里写"本轮禁止调用任何工具"实测**无效**，
    模型照样在开场轮直接 `quiz_generate`（2026-09-30 用户反馈 + 后端日志）。
    """
    if not focus_node:
        return ""
    node_id = str(focus_node.get("id") or "")
    if not node_id:
        return ""
    try:
        if not kg.list_sections(node_id):
            return ""
    except Exception as e:  # noqa: BLE001
        logger.warning(f"读小节清单失败（{node_id}）：{e}")
        return ""
    return "opener" if _is_learn_opener(messages) else "normal"


def _learn_states(kg: KnowledgeGraph, node_id: str) -> list[tuple[int, dict, dict]]:
    """小节 + 学习状态，带上 1-based 序号：`[(i, section, learn_state), ...]`。

    与 `_learn_start` 一起组成**起点规则的唯一实现** —— 学习块的清单 / 起点理由、
    B1 出题兜底、B2 越级校验三处共用，别各写一份（漂移起来很难查）。
    """
    from app.core.knowledge_graph import read_learn_state
    return [(i, s, read_learn_state(s))
            for i, s in enumerate(kg.list_sections(node_id), 1)]


def _learn_start(states: list[tuple[int, dict, dict]]) -> tuple[int, dict, dict]:
    """起点 = **按顺序**第一个还没通过的小节（用户口径原话："按照先后顺序"）。

    全通过时返回最后一节（理由文案另有分支说明）。刻意不按"不懂优先"重排 ——
    那会让学生刚标记的节被后面的未标记节插队，顺序感就没了。
    """
    return next((it for it in states if not it[2]["passed"]), states[-1])


def _build_learn_block(kg: KnowledgeGraph, focus_node: dict | None,
                      opener: bool = False) -> str:
    """
    「学习模式」上下文：学生从「去学习」进来时，把该知识点的**小节清单 + 学习状态 +
    教学规则**交给模型（按小节教，而不是泛泛聊天）。

    不适用时返回空串，提示词与改动前完全一致（老节点没有小节，不受影响）。

    三层含义（与 `LEARN_MARK_*` 对齐，别写混）：
      · 标记（已懂/不懂，`mark` + `mark_by` 记谁标的）—— 只是“怎么教”的依据，**不等于通过**；
      · `read`（我已读完）—— 用户手动勾的；
      · `passed` —— 出题答对后系统自动记（全部通过 → 节点掌握度置 100）。

    `opener=True` 时只给"开场说明"这一件事，**不给**任何教学/出题规则，也**不注入小节正文** ——
    详见函数中段的注释（这是被真机日志逼出来的设计）。
    """
    if not focus_node:
        return ""
    node_id = str(focus_node.get("id") or "")
    if not node_id:
        return ""
    try:
        sections = kg.list_sections(node_id)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"读小节清单失败（{node_id}）：{e}")
        return ""
    if not sections:
        return ""

    from app.core.knowledge_graph import (
        LEARN_MARK_BY_AI, LEARN_MARK_CONFUSED, LEARN_MARK_UNDERSTOOD,
    )

    states = _learn_states(kg, node_id)
    lines = []
    for i, s, st in states:
        if st["passed"]:
            flag = "已通过出题验证"
        elif st["mark"] == LEARN_MARK_UNDERSTOOD:
            # 「AI 讲完自动记」与「学生自评」要分开说 —— 前者是走过一遍讲解、后者只是学生觉得懂了，
            # 但两者都要**先出题验证**（已懂 ≠ 通过）。
            who = "AI 已讲完（自动记为已懂）" if st.get("mark_by") == LEARN_MARK_BY_AI else "学生自评已懂"
            flag = f"{who}（**待出题验证**）"
        elif st["mark"] == LEARN_MARK_CONFUSED:
            # 「学生自评不懂」与「系统降级（连续两次答错）」要分开说（同「已懂」那半边）：
            # 教学动作一样（都讲），但归因说错会让模型顺着"学生自己说不会"的语境讲话。
            who = ("系统降级（连续两次答错）" if st.get("mark_by") == LEARN_MARK_BY_AI
                   else "学生自评")
            flag = f"{who}「不懂」（**要讲**）"
        else:
            flag = "**没看过 / 未标记**（**要讲**）"
        if st["read"]:
            flag += "、已读完"
        lines.append(f"  {i}. [{s.get('id')}] {s.get('title')} —— {flag}")

    passed_n = sum(1 for _, _, st in states if st["passed"])

    start_idx, start_sec, start_state = _learn_start(states)
    if start_state["passed"]:
        start_reason = "全部小节都已通过 —— 只需简短肯定并告诉学生这个知识点拿下了"
    elif start_state["mark"] == LEARN_MARK_CONFUSED:
        # 系统降级（连续两次答错，mark_by=ai）**不能**说成"学生标了不懂"（2026-09-30）——
        # 两者都进入讲解，但归因不同：前者是系统判的，后者是学生自己说的。
        start_reason = ("连续两次答错、系统已降级为「不懂」—— 从这里开始把它讲一遍"
                        if start_state.get("mark_by") == LEARN_MARK_BY_AI
                        else "学生标了不懂，从这里开始讲")
    elif start_state["mark"] == LEARN_MARK_UNDERSTOOD:
        start_reason = "已懂 —— **先出题验证**，别重讲"
    else:
        start_reason = "还没看过 —— 按新课讲"

    body = ""
    try:
        body = (kg.read_section(node_id, start_sec.get("id")) or "").strip()
    except Exception:  # noqa: BLE001
        body = ""
    if len(body) > 900:
        body = body[:900] + "\n…（正文过长已截断，需要更多细节用 rag_search 检索）"

    # ── 开场轮：只报菜单 ─────────────────────────────────────
    # 为什么另写一套（而不是在下面的规则里再叠一条"先写菜单再调工具"）：
    #   真机日志（2026-09-30 00:22）—— 学生点「去学习」后第一轮，模型直接调 quiz_generate，
    #   最终回复只有 28 个字符（"题目马上出来"），清单一个字没提。前置两版"先报菜单"叠加式
    #   要求都压不住它：只要规则 1（已懂 → 直接出题）在场，它就会先出题。
    #   → 开场轮**把其余规则全部拿掉**（并抽掉起点正文，免得顺手开讲），只留"报菜单"一件事，
    #     冲突就不存在了。代价是开场它会占一轮，学生回一句再开始 —— 也正是用户要的顺序。
    if opener:
        return f"""
【学习模式・开场轮】学生刚点「去学习」进来 —— **这是本次学习的第一轮**，本轮只做开场说明。

知识点：{focus_node.get('name')}（{node_id}）　共 {len(sections)} 小节，已通过 {passed_n} 节。
小节清单（**顺序即教学顺序**）：
{chr(10).join(lines)}

本轮起点：第 {start_idx} 节「{start_sec.get('title')}」({start_sec.get('id')}) —— {start_reason}

**本轮唯一任务**：把上面这份清单用**你自己的话**讲给学生听（一小段话，不要照抄、不要写成表格），
必须说清两点：
  1. **分类点名**：哪些节是「已懂」（口径是"我先出一道题验证一下你是不是真会"）、哪些「不懂」、
     哪些「还没看过」—— 某一类没有就不提它。
  2. **接下来的走法**：从第 {start_idx} 节开始，**按顺序一节一节过**；已懂的先出题验证，
     不懂和没看过的就开讲。

⛔ **本轮禁止调用任何工具**（quiz_generate / rag_search / mark_section_understood 都不要），
   **也不要出题、不要开讲具体知识点** —— 只输出那段开场说明，然后停下等学生回应；
   学生回一句（"好""继续"）之后，**下一轮**才开始出题或讲解。
""".strip()

    return f"""
【学习模式】学生刚点「去学习」进来：本知识点要按**小节**一层层教完，不是泛泛聊天。

知识点：{focus_node.get('name')}（{node_id}）　共 {len(sections)} 小节，已通过 {passed_n} 节。
小节清单（**顺序即教学顺序**）：
{chr(10).join(lines)}

本轮起点：第 {start_idx} 节「{start_sec.get('title')}」({start_sec.get('id')}) —— {start_reason}

⛔ **状态权威（优先于你自己说过的话）**：哪一节过没过**只看上面的清单** ——
   你上一轮回复里说过"这一节过了 / 接着往下走"**不算数**；小节清单没说通过就是没通过。
   本轮**只处理第 {start_idx} 节**，后面的节一律不碰；若你上一轮说错了（清单里它并未通过），
   本轮就直接如实更正，再回到第 {start_idx} 节。

该节正文（拿来讲解，不必逐字念）：
---
{body}
---

教学规则（必须遵守）：
0. **每轮回复必须先给文字反馈，再说下一步**（顺序不能反）：
   上一题的判分结果已由系统注在提示词里 —— 答对要先点出关键要点并告知"这一节过了、进入下一节"，
   答错要先按错点讲解（**不要直接报答案**），然后才出下一题或开讲。
   ⛔ **判分结论以系统注入的为准** —— 系统说"答错"就是答错，**不许**把它说成"答对了"或
   "这一节过了"。（真机 2026-09-30：学生那道题判 0/10 答错，模型却回"Bingo，答对了！
   这一节就这么过了"，于是按"已通过"跳去讲下一节，学生干等下一节的题等不到。）
   ⛔ **不允许整轮只回一句"题目已派出/稍等片刻"** —— 学生看不到任何反馈，会觉得你只会出题。
   （另：开场轮已经报过清单了，不要再把清单重复报一遍。）

1. 「已懂」的小节**不要重讲** —— 要出题验证它。**但出题前必须先问学生一句**
   （"要不要出一道题验证一下这一节？"），**他答应了**（"好""出吧""考考我"）**下一轮再调
   `quiz_generate`（带上该节的 section_id）**；学生说"先讲讲 / 不要"就改成讲解这一节。
   ⛔ **服务端硬拦：学生没明确同意时 `quiz_generate` 会被拒**（回执里会告诉你该怎么办）——
   所以别抢跑，先问。
   ⛔ **只考清单里标着"待出题验证"的那一节（当前起点）**，而且要带上它的 `section_id`：
   节点级出题（不带 section_id）、考后面的节、考一节还没讲的节 —— 服务端都会拒。
   ⛔ **不要**自己在正文里手写题目 —— 手写的题系统收不到：判分、小节状态都不会更新，
   学生答了也白答。答对即通过；答错先把错点讲清楚，再**问一句**"要不要再来一道同类的"，
   他答应了再出。
   ⛔ **不要只在正文里说"题目稍等 / 会自动推给你"却不调工具** —— 他同意了就**必须调**
   （不调等于没出题）。（真机 2026-09-30：模型连着两轮这么说、调用数 0，学生干等；
   后端现已在"学生同意的那一轮"补兜底，但你别指望它。）
   ⛔ 学习模式下**不受**"刚出过一道题要间隔 2~3 个来回""等学生答完再出"这类通用约束限制 ——
   到了下一节就可以问、就可以考；**但每一次出题都要重新拿到同意**。
2. 「不懂」和「还没看过」的小节：按顺序**从第一节能听进去的开始讲**，讲具体（定义 → 例子 → 易错点）。
3. **讲完一节，必须调用 `mark_section_understood`（带上 node_id + 该节 section_id）把它记为「已懂」**
   —— 「没看过」和「不懂」的小节只要讲完了都算「已懂」。什么叫"讲完"：① 你把这一节的要点
   讲了一轮；**或** ② 学生明确表态这一节懂了 / 要往下走（"懂了""下一节吧"）—— **两者任一都算**，
   不要因为"觉得还没讲透"就无限追问、永远不记账（真机实测就是这么漏的）。
   但「已懂」不等于通过：这一笔只是如实记下"讲过了"，**通过与否只看本节出题是否答对**。
   学生自己的标记仍由他在节点详情里点，你不用管，你**不能**把某节改成「不懂」。
   ⚠️ 必须**真的调用工具**：只在正文里说"我帮你记下了"而没调工具 = 什么都没发生
   （学生下次进来看还是「没看过」）。
4. 某一节**出题答对**后系统自动记通过；**全部小节通过**时该知识点自动变成已掌握 —— 你只需
   口头告诉学生当前进度（已通过 x/N 节）。
5. 每轮只推进一小步（最多一节），讲完停下等学生回应，不要一口气把整章讲完。
""".strip()


# ══════════════════════════════════════════════════════════════════
#  学习模式的**服务端兜底**（B 方案，2026-09-30）
#
#  为什么要有这一层：学习模式的推进全靠提示词里的软约束，真机连着被打脸 ——
#    · 模型连着两轮说"题目稍等一下会自动推给你"，`quiz_generate` 调用数 0（题永远不来）；
#    · 模型把 0/10 讲成"Bingo，答对了"，然后跳去讲后面的小节（学生干等）。
#  所以这一层不再指望模型听话：**该出题就由后端出**、**越级记账一律硬拒**。
# ══════════════════════════════════════════════════════════════════

def learn_target_section(kg: KnowledgeGraph, focus_node: dict | None) -> dict | None:
    """
    "本轮该处理的小节" = 顺序上第一个未通过的节，连同它现在的状态。

    返回 `{node_id, index, section_id, title, mark, needs_quiz}`；不适用时 None。
    `needs_quiz=True` 表示该节是「已懂」→ 本轮该**出题验证**（而不是开讲）。
    """
    if not focus_node:
        return None
    node_id = str(focus_node.get("id") or "")
    if not node_id:
        return None
    try:
        states = _learn_states(kg, node_id)
    except Exception as e:  # noqa: BLE001 —— 兜底能力出问题不能影响对话
        logger.warning(f"读小节状态失败（{node_id}）：{e}")
        return None
    if not states:
        return None
    from app.core.knowledge_graph import LEARN_MARK_UNDERSTOOD
    idx, sec, st = _learn_start(states)
    return {
        "node_id": node_id,
        "index": idx,
        "section_id": str(sec.get("id") or ""),
        "title": str(sec.get("title") or ""),
        "mark": st.get("mark"),
        "passed": bool(st.get("passed")),
        "needs_quiz": (not st.get("passed")) and st.get("mark") == LEARN_MARK_UNDERSTOOD,
    }


# ══════════════════════════════════════════════════════════════════
#  出题前的「学生同意」（2026-09-30）
#
#  判定逻辑与文案在 `app/core/quiz/consent.py`（**全库唯一实现**，工具层也用同一份）。
#  这里只负责：把"本轮学生说了什么 / 他同意出题吗"挂到 kg 上（每请求新建的实例），
#  供 `quiz_generate` 读 —— 工具 handler 只有 `(args, kg)`，没有会话上下文。
# ══════════════════════════════════════════════════════════════════

def _stash_quiz_consent(kg: KnowledgeGraph, messages: list) -> str:
    """
    把"本轮学生输入 + 是否同意出题"挂到 kg 上，供**工具层**硬拦（`quiz_generate` 读它）。

    **不设属性时工具一律放行**：`kg` 上没有这两个属性 = 没有会话上下文
    （脚本 / 测试 / 节点详情「按小节出题」接口直调）—— 硬拦只作用于对话路径。
    """
    from app.core.quiz.consent import classify_quiz_consent
    text = _last_user_text(messages)
    consent = classify_quiz_consent(text)
    kg.student_input = text
    kg.quiz_consent = consent
    return consent


def _has_pending_quiz(user_id: int, store=None) -> bool:
    """该用户手上是否已有一道"未作答的对话题"（有就不再塞新题，避免一次堆两道）。

    `store` 可注入（测试用）—— 不传才取全局 `quiz_manager` 单例；
    整函数用 try 包住：题库不可读时**当作没有待答题**（宁可不兜底，也不能卡住对话）。
    """
    try:
        if store is None:
            from app.core.quiz.quiz_store import quiz_manager
            store = quiz_manager._get_store(user_id)
        return store.get_pending_question() is not None
    except Exception as e:  # noqa: BLE001
        logger.warning(f"查待作答题目失败（user={user_id}）：{e}")
        return False


def _stash_learn_target(kg: KnowledgeGraph, focus_node: dict | None) -> dict | None:
    """
    把"本轮该处理的小节"挂到 kg 上，供**工具层**硬校验（B2：越级记账直接拒）。

    为什么走这个渠道：工具 handler 只拿得到 `(args, kg)` —— 没有会话上下文。
    `kg` 是**每请求新建**的实例（`KnowledgeGraph(user_id=…)`，请求结束即 close），
    当"本请求的学习目标"用是安全的，不会跨请求串味。
    """
    target = learn_target_section(kg, focus_node)
    if target:
        kg.learn_target = target
    return target


def ensure_section_quiz_scheduled(kg: KnowledgeGraph, focus_node: dict | None,
                                  result, *, store=None) -> bool:
    """
    **B1 出题兜底**：学习模式后续轮里，若"本轮该出题验证的小节"确实没出题，由后端补一道。

    触发条件（全部满足才补）：
      · **学生本轮明确同意出题**（`kg.quiz_consent == "yes"`）—— 先问再出，学生点头才动；
      · 本轮起点小节是「已懂」（待出题验证）—— 不懂 / 没看过的节该讲，不该考；
      · 模型本轮**没有调过** `quiz_generate`（看 `result.rounds` 的工具轨迹，**不解析文本**）；
      · 学生手上还没有未作答的题（否则一次堆两道，他把谁当"这一题"都分不清）；
      · 后台没有同用户任务在跑（`start_background_generation` 自己会挡，返回 False）。

    返回是否真的排期成功。任何异常都吞掉：兜底是增强项，不能影响对话。
    """
    try:
        rounds = getattr(result, "rounds", None) or []
        if any((r or {}).get("tool") == "quiz_generate" for r in rounds):
            return False                       # 模型自己出了题，不插手
        # ⛔ 硬前置：**学生本轮明确同意**才兜底（2026-09-30 用户口径：出题前必须先问）——
        #    没有同意就什么都不做，把"先问一句"留给模型（题宁可晚一轮，也不能不请自来）。
        from app.core.quiz.consent import CONSENT_QUIZ_YES
        if getattr(kg, "quiz_consent", None) != CONSENT_QUIZ_YES:
            return False
        target = learn_target_section(kg, focus_node)
        if not target or not target["needs_quiz"] or not target["section_id"]:
            return False
        user_id = int(getattr(kg, "user_id", 0) or 0)
        if _has_pending_quiz(user_id, store=store):
            logger.info(f"学习模式出题兜底跳过：学生尚有未作答的题（{target['node_id']}/"
                        f"{target['section_id']}）")
            return False
        from app.core.quiz.chat_quiz import start_background_generation
        ok = start_background_generation(
            user_id, node_id=target["node_id"], section_id=target["section_id"])
        logger.info(f"学习模式出题兜底（模型未出题，后端补一道）："
                    f"{target['node_id']}/{target['section_id']} → "
                    f"{'已排期' if ok else '已有出题任务在跑，跳过'}")
        return ok
    except Exception as e:  # noqa: BLE001
        logger.warning(f"学习模式出题兜底失败（不阻断对话）：{e}")
        return False


async def _build_system_prompt(messages: list, kg: KnowledgeGraph,
                              inject_tools: bool = False, current_node: str = "",
                              kb: dict | None = None) -> tuple[str, str]:
    """
    构建系统提示词（合并原 _build_stream_prompt / _build_chat_prompt）。
    
    参数:
        inject_tools: True=注入详细图谱 + 工具能力说明（生产唯一用法：/chat 与 /chat/stream 都传它）
                      False=精简图谱、不注入工具说明（旧两段式架构"流式阶段"的遗留开关，
                      已无生产调用点，保留给"纯教学、不给工具"的实验）
        current_node: 当前教学位置的知识点 ID（可选；不存在时位置段写「未指定」）
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
    graph_summary = await _build_graph_summary(kg, detailed=inject_tools, focus_node_id=current_node)

    # 加载用户画像（空画像返回 ""，不会把空模板注入提示词）
    profile = UserProfile(user_id=kg.user_id)
    profile_text = profile.get_summary()

    # 学生当前位置（参照系契约 I2-1 / AC-L2-2）：不得静默为空。
    # 传了就校验 ID 真实存在（I1-2 同源：不把图谱里没有的 ID 当位置注入），
    # 没传或 ID 不存在则显式写「未指定」，由模型按对话推断 —— 不新增任何检索。
    _focus = kg.get_node(current_node) if current_node else None
    current_position = (
        f"「{_focus['name']}」({_focus['id']})" if _focus
        else "未指定（由本次对话内容推断，推断出后固定在同一知识点上）"
    )

    # 注入检索上下文（RAG 是增强而非必需：检索失败或为空时不影响主提示词）
    # 去耦合：检索编排统一走 rag_pipeline，一次 run 按数据源分组生成图谱/知识库两个区块
    # usage_mode 由上面同一个 profile 实例带下来，避免一次请求重复读画像文件
    # 检索块先取一次：下面拼装要用，且降级重建时不重复检索
    retrieval = await _build_retrieval_context(last_user_msg, kg.user_id, kb,
                                              usage_mode=profile.get_usage_mode())

    # 学习模式上下文（「去学习」进来时）：小节清单 + 学习状态 + 教学规则。
    # 不适用（老节点 / 未选知识点）→ 空串，提示词与改动前完全一致。
    # 开场轮走另一套（只报菜单），且**本轮禁工具**（协议层保证，见 run_agent_loop 的 no_tools）。
    learn_kind = learn_mode_kind(kg, _focus, messages)
    learn_block = (_build_learn_block(kg, _focus, opener=(learn_kind == "opener"))
                   if learn_kind else "")
    if learn_kind:
        logger.info(
            f"学习模式/{'开场轮（本轮禁调工具，只输出开场说明）' if learn_kind == 'opener' else '后续轮'}"
            f"　知识点={(_focus or {}).get('id')}"
        )

    def _assemble(graph_text: str) -> str:
        """按固定顺序拼装 system prompt（= 固定段 S2–S7 全集）。"""
        prompt = get_system_prompt(
            student_message=last_user_msg,
            graph_summary=graph_text,
            user_profile=profile_text,
            current_position=current_position,
        )
        if retrieval:
            prompt += retrieval
        if learn_block:
            prompt += learn_block
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
        graph_summary = await _build_graph_summary(
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
                                   graph_hops: int = 0,
                                   sources: set[str] | None = None) -> str:
    """
    通过 RAG 管道检索相关片段，构造注入系统提示词的检索上下文。

    参数:
        student_message: 学生当前消息
        user_id:         用户 ID
        kb:              知识库**检索范围** {node_ids, name} | None（None = 不限范围）。
                         只用于收窄范围，不决定知识库源是否参与（源自证，见 KbRagSource）
        usage_mode:      版权/用途模式（调用方从画像读好后传入，缺省 personal）
        graph_hops:      图谱扩跳深度（0=纯语义检索，默认）。>0 时沿前置关系
                         额外补出语义不相似的前置知识片段，供 A/B 实验对比。
        sources:         **显式**源选择（None = 全部已注册源）；如 {"graph"} 表示只注入
                         图谱片段（评测脚本要隔离变量时用）。注意这是"点明要哪些"，
                         与"漏传参数导致源消失"是两件事。

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
        user_id=user_id, query=student_message, top_k=5, kb=kb, sources=sources,
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

async def process_message(user_id: int, messages: list,
                        current_node: str = "",
                        kb: dict | None = None) -> tuple[str, dict]:
    """
    处理一条学生消息。

    参数:
        user_id:      数据库用户 ID（从 JWT 解析）
        messages:     完整对话历史（Pydantic ChatMessage 列表）
        current_node: 当前教学位置的知识点 ID

    返回:
        (AI回复文本, 图谱分析结果)

    注意：
        图谱分析（_analyze_and_apply）作为后台任务异步执行，
        不阻塞对话回复的返回，避免前端等待超时。
    """
    kg = KnowledgeGraph(user_id=user_id)

    try:
        # 1. 判分（与 /chat/stream **同序**，2026-09-30 对齐）：
        #    判分先跑（会改小节状态：通过 / 降级 / attempts），再建提示词，
        #    这样学习块里的清单/起点反映的是**判分后**的最新状态。
        auto_grade_note = await auto_grade_pending(kg, _last_user_text(messages))

        # 2. 构建系统提示词（含图谱上下文 + RAG + 工具能力）
        system_prompt, last_user_msg = await _build_system_prompt(
            messages, kg, inject_tools=True, current_node=current_node, kb=kb
        )
        if auto_grade_note:
            system_prompt = f"{system_prompt}\n\n{auto_grade_note}"

        # 3. 发送前守卫：清理较早工具结果 + 历史超预算时分层压缩（统计由守卫内部记日志）
        messages, _ = trim_history_to_budget(system_prompt, messages)

        # 4. Agent 主循环：LLM ↔ 工具 多轮串联，直到自然给出最终回复
        #    ★ 传 user_id：即使无 SSE 订阅，运行记录也写入 agent_runs（默认可观测）
        #    ★ 传私有队列：本路径不消费事件，但请求内事件**不得**走 event_bus 的
        #      per-user 广播队列 —— 常驻长连接 /knowledge/events 会抢走并丢弃它们，
        #      还会污染通知流（见 core/agent/events.py）。队列无人读取，随 run 回收。
        #    ★ 学习模式开场轮 → no_tools：第一轮只允许输出开场说明（硬保证，见 loop.py）
        focus_node = kg.get_node(current_node) if current_node else None
        learn_kind = learn_mode_kind(kg, focus_node, messages)
        # B2：把"本轮该处理的小节"先挂到 kg 上，工具层据此**硬拒越级记账**
        learn_target = _stash_learn_target(kg, focus_node) if learn_kind == "normal" else None
        # 出题同意（2026-09-30）：学生没明确同意时，`quiz_generate` 会被服务端拒掉
        _stash_quiz_consent(kg, messages)
        result = await run_agent_loop(
            system_prompt, messages, kg=kg, user_id=user_id,
            event_queue=asyncio.Queue(),
            no_tools=learn_kind == "opener",
        )
        reply = result.text

        # B1 出题兜底（2026-09-30）：学习模式后续轮，模型没调 quiz_generate 就由后端补一道
        # —— 学生不必因为模型的手滑而干等（真机：连着两轮口头承诺，一次工具都没调）
        if learn_target and learn_target["needs_quiz"]:
            ensure_section_quiz_scheduled(kg, focus_node, result)

        # 5. 图谱分析作为后台任务执行，不阻塞对话回复
        #    ★ 传入 user_id 而非 kg 实例，让 _analyze_and_apply 自己管理 kg 生命周期
        asyncio.create_task(_analyze_and_apply(last_user_msg, reply, user_id))

    except Exception as e:
        error_msg = str(e)
        if not error_msg.startswith("[E-"):
            error_msg = log_error(ErrorCode.CHAT_PROCESS_FAILED, detail=str(e), exception=e)
        publish_error_event(ErrorCode.CHAT_PROCESS_FAILED, error_msg, "chat_service", str(e)[:200])
        kg.close()
        return error_msg, {"suggestions": [], "applied": [], "pending": []}

    # ★ 修复：_analyze_and_apply 传入 user_id，让它自己管理 kg 生命周期。
    # 此处的 kg 在 run_agent_loop 结束后已无后续操作，可安全关闭。
    kg.close()
    return reply, {"suggestions": [], "applied": [], "pending": []}


# ══════════════════════════════════════════════════════════════════
#  流式对话处理（新接口 /chat/stream 使用）
# ══════════════════════════════════════════════════════════════════

async def process_message_stream(
    messages: list,
    user_id: int,
    current_node: str = "",
    kb: dict | None = None,
) -> AsyncGenerator[str, None]:
    """
    统一 SSE 流（方案 B）：run_agent_loop 为主答案生成器，
    循环内的事件（thinking/tool_start/tool_result/text_delta）实时推到前端。

    架构：
      1. 建**本请求私有队列**（event_queue），启动 agent_loop 作为 asyncio.Task
         （事件直投该队列，不经 event_bus 的 per-user 队列）
      2. 同时消费该队列 → 转 SSE yield
      3. agent_loop 完成后做图谱分析 + trace 落盘 + graph_updated 事件（走全局广播）
      4. yield [DONE]

    ⚠️ 队列必须私有：per-user 队列是**两个消费者共享**的（本函数 + 常驻长连接
    /knowledge/events），共享时 text_delta 会被长连接抢走并丢弃 → 前端拿到空流。
    详见 core/agent/events.py 的说明（2026-09-26 故障根因）。

    向后兼容：旧前端收到 {"token": "..."} 仍正常（text_delta 事件转 token 格式）。
    """
    kg = KnowledgeGraph(user_id=user_id)
    try:
        # ★ 判分必须**先于**提示词构建（2026-09-30 时序修正）：
        #   判分会改小节状态（通过 / 降级 / attempts），而学习块是从状态**现算**的 ——
        #   原先"先建提示词再判分"会让同一轮里的清单是**旧状态**（刚降级的节还写着
        #   "已懂（待出题验证）"），与"状态权威：只看上面的清单"自相矛盾。
        auto_grade_note = await auto_grade_pending(kg, _last_user_text(messages))

        tool_prompt, _ = await _build_system_prompt(
            messages, kg, inject_tools=True,
            current_node=current_node, kb=kb
        )

        # 判分结果作为**后置**块注入：它描述的是"本轮刚发生的事"，放最后最醒目
        # （同时规避老接口对多条 system 消息支持不确定的问题）。
        if auto_grade_note:
            tool_prompt = f"{tool_prompt}\n\n{auto_grade_note}"

        # 发送前守卫：清理较早工具结果 + 历史超预算时分层压缩（统计由守卫内部记日志）
        messages, _ = trim_history_to_budget(tool_prompt, messages)

        # 本请求私有事件队列：先建队列再起 agent，agent 的第一个事件不会丢；
        # 且不占用 per-user 广播队列（否则常驻长连接会抢走 text_delta）
        event_queue: asyncio.Queue = asyncio.Queue()
        # 学习模式开场轮 → no_tools：第一轮只允许输出开场说明（硬保证，见 loop.py）
        focus_node = kg.get_node(current_node) if current_node else None
        learn_kind = learn_mode_kind(kg, focus_node, messages)
        # B2：把"本轮该处理的小节"先挂到 kg 上，工具层据此**硬拒越级记账**
        learn_target = _stash_learn_target(kg, focus_node) if learn_kind == "normal" else None
        # 出题同意（2026-09-30）：学生没明确同意时，`quiz_generate` 会被服务端拒掉
        _stash_quiz_consent(kg, messages)
        agent_task = asyncio.create_task(
            run_agent_loop(
                tool_prompt, messages, kg=kg, user_id=user_id, event_queue=event_queue,
                no_tools=learn_kind == "opener",
            )
        )

        # 消费本请求私有队列 → SSE
        async for sse_data in _consume_agent_events(event_queue, agent_task):
            yield sse_data

        # 获取 agent 结果（运行记录已由 run_agent_loop 内部写入 agent_runs，无需再 save_trace）
        result = await agent_task

        # B1 出题兜底（2026-09-30）：学习模式后续轮，模型没调 quiz_generate 就由后端补一道
        if learn_target and learn_target["needs_quiz"]:
            ensure_section_quiz_scheduled(kg, focus_node, result)

        # 图谱分析（复用现有逻辑）
        user_msgs = [m for m in messages if (m.role if hasattr(m, 'role') else m['role']) == 'user']
        if user_msgs and result.text:
            last_user = user_msgs[-1].content if hasattr(user_msgs[-1], 'content') else user_msgs[-1]['content']
            await _analyze_and_apply(last_user, result.text, user_id)

        publish("graph_updated")

        # 冗余兜底帧：agent loop 的最终文本是「收尾一次性单帧」下发的（非逐 token），
        # 那一帧若在 _consume_agent_events 的排空窗口丢失，前端会整条消息拿不到内容，
        # 表现为气泡凭空消失。这里在 [DONE] 前再带一份，
        # 前端仅在「一帧 token 都没收到」时用它兜底（见 api/index.js finalReply）。
        if result.text:
            yield f"data: {json.dumps({'type': 'final', 'text': result.text}, ensure_ascii=False)}\n\n"

        yield "data: [DONE]\n\n"
    except Exception as e:
        error_msg = str(e)
        if not error_msg.startswith("[E-"):
            error_msg = log_error(ErrorCode.CHAT_PROCESS_FAILED, detail=str(e), exception=e)
        yield f"data: {json.dumps({'error': error_msg})}\n\n"
    finally:
        kg.close()


async def _consume_agent_events(queue: asyncio.Queue,
                                agent_task: asyncio.Task) -> AsyncGenerator[str, None]:
    """
    消费**本请求私有**事件队列，转为 SSE 格式 yield。

    为什么队列是入参而不是 user_id：`event_bus` 的 per-user 队列被两个消费者共享
    （本函数 + 常驻长连接 /knowledge/events），共享时事件被谁取到是随机的 ——
    实测长连接抢走 text_delta 并丢弃，对话侧拿到空流（2026-09-26 故障）。

    旧实现的另一个缺陷：用 `async for sse in subscribe(user_id)` 消费，
    而 subscribe 内部是 `while True: await q.get()`。当 agent_task 完成
    且队列排空后，下一次 q.get() 会永久阻塞——break 检查在循环体内，
    要 q.get() 返回后才能执行到，但队列已空永远不会返回。

    现实现用 asyncio.wait 竞争 q.get() 与 agent_task 完成信号：
      - 事件先到 → 处理事件，继续循环
      - agent 先完成 → 排空剩余事件后退出
      - 同时完成 → 处理当前事件，排空剩余后退出
    """
    q = queue

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

        # agent 完成 → 排空剩余事件（先让一轮循环，兼容生产者走 call_soon 的投递方式）
        if agent_task in done:
            await asyncio.sleep(0)
            while not q.empty():
                sse = _format_agent_sse(q.get_nowait())
                if sse is not None:
                    yield sse
            break


def _format_agent_sse(event: dict) -> str | None:
    """把事件 dict 格式化为 SSE data 行；未知类型返回 None（静默丢弃）。

    本通道只承载 **agent run 内事件**（见 core/agent/events.py 的私有队列）。
    graph_updated / quiz_ready / error 走 event_bus 全局或 per-user 广播，
    由常驻长连接消费，不经过这里；节点相关事件在对话流里出现即属接错线。
    """
    evt_type = event.get("type")

    if evt_type == TEXT_DELTA:
        return f"data: {json.dumps({'token': event.get('text', '')})}\n\n"
    if evt_type in ("thinking", "tool_start", "tool_result",
                    "agent_start", "agent_done"):
        return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
    logger.debug(f"未知事件类型已丢弃: {evt_type}")
    return None
