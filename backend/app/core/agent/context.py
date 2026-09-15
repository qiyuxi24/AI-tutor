"""agent 一次运行的对话上下文缓冲（公开层）。

位置关系（同包 `core/agent/` 内的五个模块，2026-09-15 自 core/ 平铺归并）：
  - context.py = 本文件：run 内部 API 消息序列的唯一持有/写入点。
                 loop.py 不自己拼消息 —— 进入循环先构造 AgentContext，
                 之后所有新增消息一律经其 append_* 方法，把协议约束
                 （assistant 带 tool_calls 快照 / 思考块随轮保留 /
                 tool 回填按 tool_call_id 配对）收敛在单点。
  - guard.py   = 发送前预算守卫：chat_service 在 run_agent_loop 前调用一次
                 （S8 分层保留：锚点 + 最近 N 轮 + 规则要点）。loop 内不做历史
                 裁剪 —— 每轮回填的 tool 结果属必要思维链，中途丢弃会让模型
                 看到断裂的推理。
  - 本文件另负责 **Tool Result Clearing**（S8，2026-09-15，P1-③）：较早工具
                 批次的正文换占位符。位置**必须在这里而不是 guard** —— 跨请求
                 的历史（`models/schemas.ChatMessage`）只有 role/content，压根
                 不含 tool 消息；而 `llm/messages.build_api_messages` 只透传
                 role/content，会丢 `tool_call_id`（谁在那里改 tool 消息都会
                 造出非法序列 → 服务端 400）。
  - events.py  = 事件分发（emit 鸭子接口）。

语义保证：
  - 构造即完成 system_prompt + 对话历史 → API 消息格式（复用 build_api_messages，
    dict / Pydantic ChatMessage 双形兼容）
  - reasoning_details（思考块）随 assistant 快照保留：MiniMax 多轮工具调用需回填
    以维持思维链连续（官方 Interleaved Thinking 最佳实践）
"""
from app.core.llm.messages import build_api_messages

__all__ = ["AgentContext", "assistant_snapshot",
           "TOOL_RESULT_CLEARED", "TOOL_RESULTS_KEEP_BATCHES"]

# S8 Tool Result Clearing（2026-09-15，TODO_Context P1-③，Anthropic「工具结果可重放」）：
# 清掉的是正文，模型需要时能重新调用工具取回；保留最近 K 个批次 = 当前推理链。
TOOL_RESULT_CLEARED = "[已清理的工具结果，需要时请重新调用]"
TOOL_RESULTS_KEEP_BATCHES = 2


def assistant_snapshot(msg) -> dict:
    """把模型带 tool_calls 的回复按协议快照为 assistant 消息（纯函数）。

    保留思考块：reasoning_split 默认开启时该字段才会返回，多轮工具调用需回填。
    """
    snap = {
        "role": "assistant",
        "content": msg.content or "",
        "tool_calls": [
            {"id": tc.id, "type": "function",
             "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
            for tc in msg.tool_calls
        ],
    }
    rd = getattr(msg, "reasoning_details", None)
    if rd:
        snap["reasoning_details"] = rd if isinstance(rd, list) else [rd]
    return snap


class AgentContext:
    """一次 agent run 的上下文缓冲 —— 本 run 的 API 消息序列在此累积。"""

    def __init__(self, system_prompt: str, messages: list):
        self.messages: list[dict] = build_api_messages(system_prompt, messages)

    def append_assistant_turn(self, msg) -> None:
        """模型回复（含 tool_calls）快照入栈，保证下轮请求协议完整。"""
        self.messages.append(assistant_snapshot(msg))

    def append_assistant_text(self, text: str) -> None:
        """无工具调用场景的 assistant 文本入栈（如强制收尾前的净文本快照）。"""
        self.messages.append({"role": "assistant", "content": text})

    def append_tool_result(self, tool_call_id: str, content: str) -> None:
        """工具执行结果回填 —— 与 assistant 的 tool_call 按 id 配对（协议要求）。"""
        self.messages.append({"role": "tool", "tool_call_id": tool_call_id, "content": content})

    def append_user(self, content: str) -> None:
        """追加 user 指令（如达上限后的强制收尾提示）。"""
        self.messages.append({"role": "user", "content": content})

    def clear_old_tool_results(self, keep_batches: int = TOOL_RESULTS_KEEP_BATCHES) -> int:
        """把较早工具批次的 tool 结果正文换成占位符，返回清理条数（0 = 未改动）。

        批次 = 一条带 `tool_calls` 的 assistant 消息 + 紧随其后回填的 tool 消息；
        最近 `keep_batches` 批完整保留 —— 那是当前推理链，清了模型会看到断裂的推理。
        **只改 `content`**：`tool_call_id` 必须原样（与 assistant 的 `tool_calls` 配对，
        破了服务端直接 400）。幂等，loop 每轮发送前调用一次。
        """
        batch, batch_of = 0, []
        for m in self.messages:
            if m.get("role") == "assistant" and m.get("tool_calls"):
                batch += 1
            batch_of.append(batch)
        if batch <= keep_batches:
            return 0

        limit = batch - keep_batches
        cleared = 0
        for i, m in enumerate(self.messages):
            if (0 < batch_of[i] <= limit and m.get("role") == "tool"
                    and m.get("content") != TOOL_RESULT_CLEARED):
                m["content"] = TOOL_RESULT_CLEARED
                cleared += 1
        return cleared
