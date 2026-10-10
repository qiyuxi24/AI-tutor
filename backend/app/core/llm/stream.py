"""对话流式变体：把 chunk 流累积成与 `chat_create` **同形**的响应对象（2026-10-10）。

为什么需要它（TODO「[P1]『流式』名不副实」）：
  `agent/loop._chat_once` 原先未开 `stream=True`，正文只能在**整段生成完**后一次性
  作为一条 `text_delta` 下发 → 长回答首屏等待 = 整段生成时间（2000 token 级 ≈ 40s+ 的
  「AI 思考中…」然后整段蹦出）。本模块让正文**逐 chunk** 流出。

真机依据（2026-10-10 探针 `.dsh_local/probe_stream*.py`，MiniMax-M3 + NWPU 备用网关）：
  · `stream=True` 逐 chunk 下发正文：首字节 425ms / 首正文 2022ms / 整段 14.4s
    （同问题非流式整段 52.8s）；
  · **tool_calls 以 delta 下发**（`id`/`name` 只在首片、`arguments` 分片拼接，
    按 `index` 聚合才能还原）—— 这是把流式接进 agent 多轮循环的前置未知项；
  · `stream_options={"include_usage": True}` 被支持（末 chunk 带 usage）；
  · `reasoning_details` 也以 delta 下发，元素形状与非流式一致
    （`{"type":"reasoning.text","text":...}`，dict 带 `"text"`）。

边界（为什么这里不是第三个"LLM 出口"）：
  · 不建 client、不自己回退 —— 候选链 + 瞬时重试仍**唯一**在 `chat_create`；
    本模块只是在它上面加一层「`stream=True` → 逐 chunk 累积」。
  · 回退/重试只作用于**建流**那一刻。流一旦建立，中途异常只能向上抛，**不换档重放**
    （已下发的 chunk 会在前端重复）。
  · `stream_options` 只有网关**明确以 400/422 拒绝**时才降级重开（丢 usage，聊天不崩）；
    其他失败（5xx / 认证 / 超时）原样抛，不把一次失败变成两次。
"""
from types import SimpleNamespace

from openai import APIStatusError

from app.core.llm.fallback import chat_create

__all__ = ["chat_create_stream", "STREAM_OPTIONS"]

# 让网关在**末 chunk**带 usage（token 计量真值）。个别老网关不认 → 见 chat_create_stream 降级。
STREAM_OPTIONS = {"include_usage": True}


def _collect_tool_deltas(slots: dict, deltas) -> None:
    """按 `index` 聚合 tool_calls delta（id/name 覆盖、arguments 拼接）。"""
    for d in deltas:
        idx = getattr(d, "index", 0) or 0
        slot = slots.setdefault(idx, {"id": "", "name": "", "arguments": ""})
        if getattr(d, "id", None):
            slot["id"] = d.id
        fn = getattr(d, "function", None)
        if fn is not None:
            if getattr(fn, "name", None):
                slot["name"] = fn.name
            if getattr(fn, "arguments", None):
                slot["arguments"] += fn.arguments


def _to_response(*, model, usage, finish, content, reasoning, tool_slots):
    """拼成与非流式 `chat.completions.create` 返回对象**同形**的轻量结构。

    下游（loop / assistant_snapshot / extract_usage / _resp_model）只按属性访问，
    所以 SimpleNamespace 足够；字段名与 OpenAI 对象保持一致。
    """
    tool_calls = [
        SimpleNamespace(id=slot["id"], type="function",
                        function=SimpleNamespace(name=slot["name"],
                                                 arguments=slot["arguments"]))
        for _, slot in sorted(tool_slots.items())
    ] or None
    message = SimpleNamespace(content=content, tool_calls=tool_calls,
                              reasoning_details=reasoning or None)
    return SimpleNamespace(
        model=model or "",
        usage=usage,
        choices=[SimpleNamespace(message=message, finish_reason=finish)],
    )


async def _accumulate(stream, on_delta) -> SimpleNamespace:
    """消费 chunk 流：正文逐片回调 `on_delta`，返回同形响应对象。"""
    content_parts: list[str] = []
    reasoning: list = []
    tool_slots: dict = {}
    model, usage, finish = "", None, None

    async for chunk in stream:
        model = getattr(chunk, "model", None) or model
        if getattr(chunk, "usage", None):        # include_usage：末 chunk（可能 choices=[]）
            usage = chunk.usage
        choices = getattr(chunk, "choices", None)
        if not choices:
            continue
        choice = choices[0]
        if getattr(choice, "finish_reason", None):
            finish = choice.finish_reason
        delta = getattr(choice, "delta", None)
        if delta is None:
            continue
        text = getattr(delta, "content", None)
        if text:
            content_parts.append(text)
            if on_delta is not None:
                on_delta(text)
        rd = getattr(delta, "reasoning_details", None)
        if rd:
            reasoning.extend(rd if isinstance(rd, list) else [rd])
        tcs = getattr(delta, "tool_calls", None)
        if tcs:
            _collect_tool_deltas(tool_slots, tcs)

    return _to_response(model=model, usage=usage, finish=finish,
                        content="".join(content_parts), reasoning=reasoning,
                        tool_slots=tool_slots)


async def chat_create_stream(*, user_id: int | None = None, on_delta=None, **kwargs):
    """流式对话调用（`chat_create` 的流式变体）。

    用法：不传 `model`（`chat_create` 按候选链注入 `model` 并做瞬时重试）；
    其余参数（messages / temperature / max_tokens / extra_body / tools / tool_choice）原样透传。

    参数:
        on_delta: `(text: str) -> None`，正文每片回调一次（首屏延迟的来源）。
        user_id:  透传 `chat_create`（走该用户自有模型链，未配置则回落系统默认档）。

    返回:
        与非流式响应同形的对象：`choices[0].message.{content,tool_calls,reasoning_details}`、
        `usage`（`include_usage` 生效时非空）、`model`、`choices[0].finish_reason`。

    异常:
        `chat_create` 映射后的 RuntimeError（建流阶段）。`stream_options` 被网关以
        400/422 拒绝时去掉该参数重开（丢 usage）；其他错误原样抛。
    """
    call_kwargs = dict(kwargs, stream=True)
    try:
        stream = await chat_create(user_id=user_id,
                                   stream_options=dict(STREAM_OPTIONS), **call_kwargs)
    except Exception as e:
        cause = e.__cause__
        if not (isinstance(cause, APIStatusError) and cause.status_code in (400, 422)):
            raise
        stream = await chat_create(user_id=user_id, **call_kwargs)   # 降级：丢 usage
    return await _accumulate(stream, on_delta)
