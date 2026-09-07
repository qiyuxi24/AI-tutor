"""
对话接口路由模块

与 services/chat_service.py 配合，处理前端发来的对话请求。
所有接口需要 JWT 认证，按用户隔离对话数据。

统一 SSE 流架构（方案 B，2026-09-07）：
  /chat        — 传统一次性回复（兼容旧版）
  /chat/stream — 统一 SSE 流，agent_loop 为主答案生成器，
                 循环内事件（thinking/tool_start/tool_result/text_delta）实时推送
"""
import json
import asyncio
from fastapi import APIRouter, Request, Depends
from fastapi.responses import StreamingResponse
from app.models.schemas import ChatRequest, ChatResponse
from app.core.auth import get_current_user
from app.services.chat_service import process_message, process_message_stream

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
    kb = {"node_ids": request.kb_node_ids or [], "name": request.kb_node_name} \
        if request.kb_node_ids else None
    reply, mode, graph_analysis = await process_message(
        user_id=user_id,
        messages=request.messages,
        mode=request.mode,
        current_node=request.current_node,
        kb=kb,
    )
    return ChatResponse(reply=reply, mode=mode, graph_analysis=graph_analysis)


@router.post("/chat/stream")
async def handle_chat_stream(request: ChatRequest,
                             user_id: int = Depends(get_current_user)):
    """
    统一流式对话端点（方案 B）

    agent_loop 为唯一答案生成器：
    - 循环内事件（thinking/tool_start/tool_result/text_delta）实时 SSE 推送
    - 图谱分析 + trace 落盘在流内完成
    - 旧前端兼容：text_delta 事件转为 {"token": "..."} 格式
    """
    async def event_stream():
        kb = {"node_ids": request.kb_node_ids or [], "name": request.kb_node_name} \
            if request.kb_node_ids else None

        async for sse_chunk in process_message_stream(
            messages=request.messages,
            mode=request.mode,
            user_id=user_id,
            current_node=request.current_node,
            kb=kb,
        ):
            yield sse_chunk

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 禁用 nginx 缓冲
        }
    )
