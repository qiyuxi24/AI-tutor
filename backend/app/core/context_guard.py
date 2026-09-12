"""发送前上下文守卫 —— 历史超预算时裁剪最旧对话，控制单轮输入成本。

背景（2026-09-08）：MiniMax-M3 上下文 1M，教学长对话几乎不会触发 API 拒绝；
真正的代价是"每轮把整段历史重发给按输入计费的模型"，随会话变长线性烧钱。
本模块在进入 run_agent_loop 前对 (system_prompt + messages) 做一次本地预计数
（复用 token_counter.count_messages_tokens，与 agent_loop 记档的
estimated_prompt_tokens 同一口径），超预算则从头部丢弃最旧轮次并插入一条
省略说明——让模型知道早期内容已归档，而非幻觉式"我记得你很久前说过"。

取舍（ponytail:）：
- 只丢最旧、不做 LLM 摘要——被裁内容已沉淀进图谱/画像/agent_runs，
  system_prompt 每轮重新注入图谱与画像摘要，裁剪对教学连续性影响最小；
  升级路径 = 需保留早期知识时，先把被裁段压成一句话摘要置顶再发。
- 软目标守卫：system_prompt 自身超预算时无消息可裁，照发并记 warning
  ——1M 窗口兜底不会拒绝请求，仅提示预算配置偏小。
"""
import logging

from app.core.config import settings
from app.core.token_counter import count_messages_tokens

logger = logging.getLogger("ai-tutor")

# 输出预留：对齐 agent_loop 默认 max_tokens=2000，避免挤占模型作答空间
OUTPUT_RESERVE = 2000

# 裁剪后插入的省略说明。以 user 角色开头，保持"首条非 system 为 user"合法；
# 同时告知模型存在被归档的早期历史，信息不足时应自行引导而非编造。
_TRIM_NOTICE = (
    "（本会话更早的部分对话（共 {dropped} 条消息）已因篇幅被归档省略。"
    "若学生问及更早讲过的内容，可先调用知识图谱/检索核对，信息不足时礼貌请学生复述关键词。"
    "正常教学无需向学生提及本提醒。）"
)


def _as_api(msg) -> dict:
    """dict / Pydantic(ChatMessage) 双形 → API 消息 dict（对齐 llm.messages 双形约定）。"""
    return msg if isinstance(msg, dict) else {"role": msg.role, "content": msg.content}


def trim_history_to_budget(
    system_prompt: str,
    messages: list,
    *,
    budget: int | None = None,
    model: str | None = None,
    out_reserve: int = OUTPUT_RESERVE,
) -> tuple[list, dict | None]:
    """发送前守卫：system_prompt + 历史超预算时，从头部丢弃最旧消息并插入省略说明。

    参数:
        system_prompt: 组装完成的系统提示词（含图谱/RAG/工具说明）
        messages:      对话历史（dict 或 Pydantic ChatMessage 均可，双形）
        budget:        单轮发送预算（tokens，含 system_prompt）。None=settings.llm_ctx_budget
        model:         模型名（token 校准用）。None=settings.model_name
        out_reserve:   输出预留，从预算中扣除

    返回:
        (messages, info)：info=None 表示未超预算（原样返回原列表，零行为变化）；
        info={"dropped","before_tokens","after_tokens","budget"} 表示发生过裁剪。
    """
    budget = settings.llm_ctx_budget if budget is None else budget
    model = settings.model_name if model is None else model
    target = max(1, budget - out_reserve)

    api = [_as_api(m) for m in messages]
    sys_msg = {"role": "system", "content": system_prompt}
    before = count_messages_tokens([sys_msg, *api], model=model)
    if before <= target:
        return messages, None

    def fits(drop: int) -> bool:
        # 丢弃头部 drop 条后是否 fit（fits 单调：删越多越小）
        return count_messages_tokens([sys_msg, *api[drop:]], model=model) <= target

    # 二分求"最大可裁条数"（最多保留 1 条 = 当前消息）
    lo, hi = 0, max(0, len(api) - 1)
    drop = 0
    if fits(0):
        while lo <= hi:
            mid = (lo + hi) // 2
            if fits(mid):
                drop, lo = mid, mid + 1
            else:
                hi = mid - 1
    else:
        # system_prompt 自身已超预算：无可裁余地，退化为只剩当前消息（软目标，1M 窗口兜底照发）
        drop = hi

    # 保证裁剪后消息序列以 user 开头（API 要求首条非 system 为 user）
    while drop < len(api) - 1 and api[drop]["role"] != "user":
        drop += 1

    if drop == 0:
        return messages, None

    kept = [{"role": "user", "content": _TRIM_NOTICE.format(dropped=drop)}, *api[drop:]]
    after = count_messages_tokens([sys_msg, *kept], model=model)
    logger.info(f"上下文守卫: 裁掉最旧 {drop} 条消息，{before}→{after} tokens (预算 {budget})")
    return kept, {"dropped": drop, "before_tokens": before, "after_tokens": after, "budget": budget}
