"""对话请求消息组装（system_prompt + 历史 → OpenAI API 消息格式）。"""


def build_api_messages(system_prompt: str, messages: list) -> list[dict]:
    """将 system_prompt + 对话历史合并为 OpenAI API 消息格式（dict / Pydantic ChatMessage 双形兼容）。"""
    api_messages = [{"role": "system", "content": system_prompt}]
    for msg in messages:
        api_messages.append({
            "role": msg["role"] if isinstance(msg, dict) else msg.role,
            "content": msg["content"] if isinstance(msg, dict) else msg.content
        })
    return api_messages
