from pydantic import BaseModel
from typing import List, Literal, Optional

# 定义三种引导模式（前面加类型注解，IDE 会提示）
GuideMode = Literal['scaffolding', 'think_first', 'reverse_teaching']

class ChatMessage(BaseModel):
    role: str    # "user" 或 "assistant"
    content: str

class ChatRequest(BaseModel):
    """前端发给后端的请求体"""
    user_id: str                # 学生唯一标识
    messages: List[ChatMessage] # 全部对话历史
    mode: GuideMode             # 引导模式

class ChatResponse(BaseModel):
    """后端返回给前端的响应体"""
    reply: str             # AI 的回复
    mode: str              # 当前使用的模式
