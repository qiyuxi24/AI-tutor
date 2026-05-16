"""大模型 API 客户端（当前为模拟版本，后续接入真实 API）"""
import httpx

async def call_llm(system_prompt: str, user_message: str) -> str:
    """
    调用大模型 API，返回 AI 回复。
    当前模拟版本：直接返回一条固定回复，验证链路通畅。
    """
    # TODO: 后续替换为真实的 API 调用
    # 模拟回复
    return (
        f"收到你的问题了。"
        f"我会用以下引导策略来帮你思考：\n\n"
        f"【系统提示词已加载】{system_prompt[:100]}...\n\n"
        f"这是一个模拟回复，验证全链路通畅。"
    )
