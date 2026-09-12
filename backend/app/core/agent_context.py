"""agent 一次运行的对话上下文缓冲（公开层）。

位置关系（agent 运行三分法，2026-09-08 起逐文件独立）：
  - agent_context.py  = 本文件：run 内部 API 消息序列的唯一持有/写入点。
                        agent_loop 不自己拼消息 —— 进入循环先构造 AgentContext，
                        之后所有新增消息一律经其 append_* 方法，把协议约束
                        （assistant 带 tool_calls 快照 / 思考块随轮保留 /
                        tool 回填按 tool_call_id 配对）收敛在单点。
  - context_guard.py  = 发送前预算守卫：chat_service 在 run_agent_loop 前调用
                        一次（一次性丢最旧），已在入口生效；loop 内每轮 tool
                        回填属必要思维链、刻意不守卫，见其 ponytail 注释。
                        若未来要做 loop 内窗口裁剪（Batch2 简单窗口化：保 system
                        + 最近 N 轮 + 首条），落点就在本文件 append_* 之前。
  - agent_events.py   = 事件分发（emit 鸭子接口）。

语义保证：
  - 构造即完成 system_prompt + 对话历史 → API 消息格式（复用 build_api_messages，
    dict / Pydantic ChatMessage 双形兼容）
  - reasoning_details（思考块）随 assistant 快照保留：MiniMax 多轮工具调用需回填
    以维持思维链连续（官方 Interleaved Thinking 最佳实践）
"""
from app.core.llm.messages import build_api_messages

__all__ = ["AgentContext", "assistant_snapshot"]


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
