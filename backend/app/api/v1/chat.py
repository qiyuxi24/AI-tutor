"""
对话接口路由模块

与 services/chat_service.py 配合，处理前端发来的对话请求。
"""
from fastapi import APIRouter
from app.models.schemas import ChatRequest, ChatResponse
from app.services.chat_service import process_message

router = APIRouter()


@router.post("/chat", response_model=ChatResponse)
async def handle_chat(request: ChatRequest):
    """
    处理对话请求
    
    process_message 现在返回三个值：
    - reply: AI 回复文本
    - mode: 当前引导模式
    - graph_analysis: 图谱分析结果（含 applied/pending 建议）
    """
    reply, mode, graph_analysis = await process_message(
        user_id=request.user_id,
        messages=request.messages,
        mode=request.mode,
    )
    return ChatResponse(reply=reply, mode=mode, graph_analysis=graph_analysis)
