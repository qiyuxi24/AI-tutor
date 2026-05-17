"""对话服务层：编排整个对话处理流程"""
from app.core.prompt_loader import get_system_prompt
from app.core.llm_client import call_llm

async def process_message(user_id: str, messages: list, mode: str) -> tuple[str, str]:
    """
    处理一条学生消息，返回 (AI 回复，使用的模式)
    
    流程：
    1. 根据模式加载提示词模板并渲染
    2. 调用大模型 API 获取回复
    """
    # 1. 用最新用户消息生成系统提示词
    last_user_msg = next((m.content for m in reversed(messages) if m.role == 'user'), '')
    system_prompt = get_system_prompt(mode, last_user_msg)
    
    # 2. 调用 AI（传入完整历史）
    reply = await call_llm(system_prompt, messages)
    
    return reply, mode
