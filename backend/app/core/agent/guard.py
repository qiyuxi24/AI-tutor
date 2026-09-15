"""发送前上下文守卫 —— 历史超预算时做「分层保留」而非简单丢弃，控制单轮输入成本。

背景（2026-09-08）：MiniMax-M3 上下文 1M，教学长对话几乎不会触发 API 拒绝；
真正的代价是"每轮把整段历史重发给按输入计费的模型"，随会话变长线性烧钱。
本模块在进入 run_agent_loop 前对 (system_prompt + messages) 做一次本地预计数
（复用 token_counter.count_messages_tokens，与 agent_loop 记档的
estimated_prompt_tokens 同一口径），超预算则分层压缩并插入省略说明
——让模型知道早期内容已归档，而非幻觉式"我记得你很久前说过"。

S8 分层保留（2026-09-15，TODO_Context P1-②，替代原「只丢最旧」）：

    [省略说明 + 被裁轮次要点] + [首条 user 原文（任务锚点）] + [最近 N 轮完整]

- 首条 user = **任务锚点**（呼应 StreamingLLM 的 attention sink）：丢了模型会失去"我们在干什么"；
- 要点行**规则化生成**（每轮取学生首句 + 教师首句），**不上 LLM** —— 每轮打一次 LLM 做摘要
  与"省 token"的初衷冲突；被裁内容已沉淀进图谱/画像/agent_runs，属**可还原压缩**；
- 预算极紧时两档降级：先砍要点行只留提示语 → 仍放不下则软目标照发并 warning。

⚠️ **Tool Result Clearing 不在这里**（2026-09-15 实测修正，别搬回来）：
本模块处理的是 `chat_service` 传来的历史，而 `ChatMessage` 只有 `role` / `content`
（`models/schemas.py`）——**历史里永远没有 tool 消息**，在这里清理是死代码。
真正的落点是 loop 内 `agent/context.py::AgentContext.clear_old_tool_results`
（tool 回填只发生在单次 run 内部）。另一条独立理由：`llm/messages.build_api_messages`
只透传 `role` / `content`，会丢掉 `tool_call_id` —— 谁在这里改 tool 消息都会造出非法序列。

取舍（ponytail:）：
- 不做 LLM 摘要：成本与目标冲突；确需语义摘要则单列子任务并 A/B。
- 软目标守卫：system_prompt 自身超预算时无消息可裁，照发并记 warning
  ——1M 窗口兜底不会拒绝请求；真正的强制降级在组装侧（`chat_service._build_system_prompt`）。
"""
import logging

from app.core.config import settings
from app.core.token_counter import count_messages_tokens

logger = logging.getLogger("ai-tutor")

# 输出预留：预算框架 §1.2 的 S1 目标（3K）；agent_loop 默认 max_tokens=2000 留有余量
# （M3 思考与正文共享该预算，见 TODO_Context D-3：是否同步提到 3000 待定）
OUTPUT_RESERVE = 3000

RECENT_TURNS_KEEP = 3       # 分层保留：完整保留的最近轮数
SUMMARY_MAX_TURNS = 4       # 要点行最多列举的轮数（防"摘要"自身膨胀）
SUMMARY_SNIPPET_CHARS = 40  # 要点行每侧首句长度上限

# 省略说明的头/尾；中间夹被裁轮次的要点行。含"归档"字样，便于日志与测试核对。
_TRIM_NOTICE_HEAD = "（本会话更早的 {n} 条消息已因篇幅被归档省略。"
_TRIM_NOTICE_TAIL = (
    "\n若学生问及更早讲过的内容，可先调用知识图谱/检索核对，"
    "信息不足时礼貌请学生复述关键词。正常教学无需向学生提及本提醒。）"
)


def _as_api(msg) -> dict:
    """dict / Pydantic(ChatMessage) 双形 → API 消息 dict（对齐 llm.messages 双形约定）。"""
    return msg if isinstance(msg, dict) else {"role": msg.role, "content": msg.content}


def _first_sentence(text: str, limit: int = SUMMARY_SNIPPET_CHARS) -> str:
    """取首句（以句末标点为界），裁到 limit 字符 —— 要点行不做语义摘要。"""
    text = " ".join((text or "").split())
    for sep in ("。", "！", "？", "!", "?", "；", ";"):
        idx = text.find(sep)
        if 0 <= idx < limit:
            return text[:idx + 1]
    return text[:limit]


def _split_turns(msgs: list[dict]) -> list[list[dict]]:
    """按 user 消息切分成轮次（同轮的其他角色消息归入该轮）。"""
    turns: list[list[dict]] = []
    cur: list[dict] = []
    for m in msgs:
        if m.get("role") == "user" and cur:
            turns.append(cur)
            cur = []
        cur.append(m)
    if cur:
        turns.append(cur)
    return turns


def _summarize_turns(turns: list[list[dict]]) -> str:
    """规则化要点：每轮「学生问 → 你答」各取首句（不上 LLM，见模块 docstring 取舍）。"""
    lines = []
    for turn in turns[:SUMMARY_MAX_TURNS]:
        ask = next((m["content"] for m in turn if m.get("role") == "user"), "")
        answer = next((m["content"] for m in turn if m.get("role") == "assistant"), "")
        line = f"- 学生问「{_first_sentence(ask)}」"
        if answer:
            line += f"，你答「{_first_sentence(answer)}」"
        lines.append(line)
    if len(turns) > SUMMARY_MAX_TURNS:
        lines.append(f"- （更早还有 {len(turns) - SUMMARY_MAX_TURNS} 轮，已归档）")
    return "\n".join(lines)


def _layered_notice(dropped_msgs: list[dict], *, use_summary: bool) -> str:
    """分层省略说明（含被裁轮次要点，最多 SUMMARY_MAX_TURNS 行）。"""
    notice = _TRIM_NOTICE_HEAD.format(n=len(dropped_msgs))
    if use_summary:
        summary = _summarize_turns(_split_turns(dropped_msgs))
        if summary:
            notice += "\n被省略轮次的要点：\n" + summary
    return notice + _TRIM_NOTICE_TAIL


def _layer_history(api: list[dict], sys_msg: dict, target: int, model: str,
                   keep_recent_turns: int) -> tuple[list[dict], int]:
    """分层压缩：返回 (新序列, 被裁消息条数)；无可裁内容时返回 (api, 0)。"""
    user_positions = [i for i, m in enumerate(api) if m.get("role") == "user"]
    if len(user_positions) <= 1:
        # 没有更早的轮次可裁（只有当前问题）：退化为软目标守卫，照发并点名
        logger.warning(
            f"上下文守卫: system_prompt + 当前问题已超预算 {target}（无更早历史可分层压缩）"
            f"→ 照发。请下调注入体量（图谱见 GRAPH_INJECT_MAX_CHARS）"
        )
        return api, 0

    anchor = user_positions[0]

    def fits(seq: list[dict]) -> bool:
        return count_messages_tokens([sys_msg, *seq], model=model) <= target

    def build(start: int, use_summary: bool) -> list[dict]:
        """省略说明 +（锚点原文）+ 近端 start 起全部；start 之前的部分压成要点行。"""
        squeezed = api[:start] if anchor >= start else api[anchor + 1:start]
        seq = [{"role": "user", "content": _layered_notice(squeezed, use_summary=use_summary)}]
        if anchor < start:
            seq.append(api[anchor])  # 任务锚点原文保留
        seq.extend(api[start:])
        return seq

    # 尽量多保留：候选起点从"最近 keep_recent_turns 轮"里挑最靠前的（fits 关于 start 单调）
    for cand in user_positions[-keep_recent_turns:]:
        seq = build(cand, True)
        if fits(seq):
            return seq, len(api) - (len(seq) - 1)

    # 预算连"最近 N 轮"都放不下 → 只留当前问题；要点行也放不下就先砍要点（两级降级）
    last = user_positions[-1]
    for use_summary in (True, False):
        seq = build(last, use_summary)
        if fits(seq):
            return seq, len(api) - (len(seq) - 1)

    seq = build(last, False)
    logger.warning(
        f"上下文守卫: 分层压缩后仍超预算 {target}（只剩当前问题 + 省略说明）→ 照发。"
        f"说明固定段（system_prompt）本身已超预算，请下调注入体量"
    )
    return seq, len(api) - (len(seq) - 1)


def trim_history_to_budget(
    system_prompt: str,
    messages: list,
    *,
    budget: int | None = None,
    model: str | None = None,
    out_reserve: int = OUTPUT_RESERVE,
    keep_recent_turns: int = RECENT_TURNS_KEEP,
) -> tuple[list, dict | None]:
    """发送前守卫：历史超预算时分层压缩（锚点 + 最近 N 轮 + 规则要点）。

    参数:
        system_prompt:     组装完成的系统提示词（含图谱/RAG/工具说明）
        messages:          对话历史（dict 或 Pydantic ChatMessage 均可，双形）
        budget:            单轮发送预算（tokens，含 system_prompt）。None=settings.llm_ctx_budget
        model:             模型名（token 校准用）。None=settings.model_name
        out_reserve:       输出预留，从预算中扣除
        keep_recent_turns: 分层保留的最近轮数（完整保留）

    返回:
        (messages, info)：info=None 表示未超预算（原样返回原列表，零行为变化）；
        否则 info={"dropped","before_tokens","after_tokens","budget"}。
    """
    budget = settings.llm_ctx_budget if budget is None else budget
    model = settings.model_name if model is None else model
    target = max(1, budget - out_reserve)

    api = [_as_api(m) for m in messages]
    sys_msg = {"role": "system", "content": system_prompt}
    before = count_messages_tokens([sys_msg, *api], model=model)
    if before <= target:
        return messages, None

    api, dropped = _layer_history(api, sys_msg, target, model, keep_recent_turns)
    if dropped == 0:
        return messages, None

    after = count_messages_tokens([sys_msg, *api], model=model)
    logger.info(
        f"上下文守卫: S8 分层压缩 {dropped} 条消息"
        f"（锚点 + 最近 {keep_recent_turns} 轮 + 规则要点），"
        f"{before}→{after} tokens（预算 {budget}）"
    )
    return api, {"dropped": dropped, "before_tokens": before,
                 "after_tokens": after, "budget": budget}
