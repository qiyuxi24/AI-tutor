"""
对话接口路由模块

与 services/chat_service.py 配合，处理前端发来的对话请求。
所有接口需要 JWT 认证，按用户隔离对话数据。

两阶段流式架构：
  /chat        — 传统一次性回复（兼容旧版）
  /chat/stream — 流式 SSE 端点（推荐），先流式输出文本，后台再执行工具调用
"""
import json
import asyncio
from fastapi import APIRouter, BackgroundTasks, Request, Depends
from fastapi.responses import StreamingResponse
from app.models.schemas import ChatRequest, ChatResponse
from app.core.auth import get_current_user
from app.services.chat_service import process_message, process_message_stream, process_background_tools

router = APIRouter()


@router.post("/chat", response_model=ChatResponse)
async def handle_chat(request: ChatRequest, user_id: int = Depends(get_current_user)):
    """
    处理对话请求（一次性回复，兼容旧版前端）
    
    process_message 现在返回三个值：
    - reply: AI 回复文本
    - mode: 当前引导模式
    - graph_analysis: 图谱分析结果（含 applied/pending 建议）
    """
    reply, mode, graph_analysis = await process_message(
        user_id=user_id,
        messages=request.messages,
        mode=request.mode,
        current_node=request.current_node,
    )
    return ChatResponse(reply=reply, mode=mode, graph_analysis=graph_analysis)


@router.post("/chat/stream")
async def handle_chat_stream(request: ChatRequest, background_tasks: BackgroundTasks,
                             user_id: int = Depends(get_current_user)):
    """
    流式对话端点（两阶段分离）
    
    阶段1（流式）: SSE 逐 token 推送 AI 文本回复给前端
    阶段2（后台）: 流式结束后，BackgroundTasks 触发工具调用 + 图谱分析
    """
    async def event_stream():
        # 收集完整的 AI 回复文本（供后台阶段使用）
        full_reply_parts = []
        
        async for sse_chunk in process_message_stream(
            messages=request.messages,
            mode=request.mode,
            user_id=user_id,
            current_node=request.current_node,
        ):
            # 提取 token 文本（从 SSE 格式中解析）
            if sse_chunk.startswith("data: ") and sse_chunk != "data: [DONE]\n\n":
                try:
                    payload = json.loads(sse_chunk[6:].strip())
                    if "token" in payload:
                        full_reply_parts.append(payload["token"])
                except (json.JSONDecodeError, KeyError):
                    pass
            
            yield sse_chunk
        
        # 流式结束后，将完整回复附加到消息列表并触发后台任务
        full_reply = "".join(full_reply_parts)
        if full_reply:
            # 构造含完整 AI 回复的消息列表供后台使用
            enriched_messages = list(request.messages)
            enriched_messages.append({"role": "assistant", "content": full_reply})
            background_tasks.add_task(
                process_background_tools,
                enriched_messages,
                request.mode,
                user_id,
                request.current_node,
            )
    
    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 禁用 nginx 缓冲
        }
    )
