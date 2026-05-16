"""对话服务层：编排整个对话处理流程"""
from app.core.prompt_loader import get_system_prompt
from app.core.llm_client import call_llm

async def process_message(user_id: str, message: str, mode: str) -> tuple[str, str]:
    """
    处理一条学生消息，返回 (AI 回复，使用的模式)
    
    流程：
    1. 根据模式加载提示词模板并渲染
    2. 调用大模型 API 获取回复
    """
    # 1. 生成系统提示词
    system_prompt = get_system_prompt(mode, message)
    
    # 2. 调用 AI
    reply = await call_llm(system_prompt, message)
    
    return reply, mode
