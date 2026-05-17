"""对话接口路由"""
from fastapi import APIRouter
from app.models.schemas import ChatRequest, ChatResponse
from app.services.chat_service import process_message

router = APIRouter()

@router.post("/chat", response_model=ChatResponse)
async def handle_chat(request: ChatRequest):
    """处理对话请求"""
    reply, mode = await process_message(
        user_id=request.user_id,
        messages=request.messages,
        mode=request.mode,
    )
    return ChatResponse(reply=reply, mode=mode)
