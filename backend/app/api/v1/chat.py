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
from fastapi.responses import StreamingResponse, JSONResponse
from app.models.schemas import ChatRequest, ChatResponse
from app.core.auth import get_current_user
from app.core.rate_limiter import chat_rate_limiter
from app.services.chat_service import process_message, process_message_stream

router = APIRouter()


def _build_kb_context(request: ChatRequest) -> dict | None:
    """
    构造本次对话的知识库**检索范围**（RagContext.kb）——只表达范围，不表达开关。

    - 前端选定了范围 → 只在该批文件里检索（node_ids 由 KbRagSource 展开目录）
    - 未选定 → 返回 None = 不限范围，检索该用户全部上传资料

    本函数返回什么**都不会**影响"知识库源是否参与"：那是源自证的事
    （`KbRagSource.should_query` 查"有没有已索引资料"）。曾经这里返回 None 就等于
    静默关掉整个知识库源 —— 一个漏传的业务参数不该有这种权力。
    """
    if not request.kb_node_ids:
        return None
    return {
        "node_ids": request.kb_node_ids,
        "name": request.kb_node_name or "我的知识库",
    }


def _check_chat_rate_limit(request: Request) -> bool:
    """检查聊天接口频率限制，超限返回 429。"""
    client_ip = request.client.host if request.client else "unknown"
    if not chat_rate_limiter.is_allowed(client_ip):
        retry_after = chat_rate_limiter.get_retry_after(client_ip)
        return JSONResponse(
            status_code=429,
            content={"detail": f"请求过于频繁，请 {retry_after} 秒后重试"},
            headers={"Retry-After": str(retry_after)},
        )
    return None


@router.post("/chat", response_model=ChatResponse)
async def handle_chat(request: ChatRequest, raw_request: Request,
                      user_id: int = Depends(get_current_user)):
    """
    处理对话请求（一次性回复，兼容旧版前端）
    """
    rate_limit_resp = _check_chat_rate_limit(raw_request)
    if rate_limit_resp:
        return rate_limit_resp

    kb = _build_kb_context(request)
    reply, graph_analysis = await process_message(
        user_id=user_id,
        messages=request.messages,
        current_node=request.current_node,
        kb=kb,
    )
    return ChatResponse(reply=reply, graph_analysis=graph_analysis)


@router.post("/chat/stream")
async def handle_chat_stream(request: ChatRequest, raw_request: Request,
                             user_id: int = Depends(get_current_user)):
    """
    统一流式对话端点（方案 B）
    """
    rate_limit_resp = _check_chat_rate_limit(raw_request)
    if rate_limit_resp:
        return rate_limit_resp

    async def event_stream():
        kb = _build_kb_context(request)

        async for sse_chunk in process_message_stream(
            messages=request.messages,
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
            "X-Accel-Buffering": "no",
        }
    )
