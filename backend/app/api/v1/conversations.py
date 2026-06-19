"""
对话历史 API 路由

提供对话的 CRUD 接口，按用户隔离。
前端双写策略：localStorage + 后端同步。
"""

from fastapi import APIRouter, HTTPException, Depends, Body
from typing import Optional
from app.core.auth import get_current_user
from app.core.conversation_store import ConversationStore
from app.core.error_codes import ErrorCode, log_error

router = APIRouter()


# ════════════════════════════════════════════
#  GET /conversations — 获取对话列表（不含消息内容）
# ════════════════════════════════════════════

@router.get("/conversations")
async def list_conversations(user_id: int = Depends(get_current_user)):
    """
    返回当前用户的所有对话摘要（id, title, createdAt, updatedAt, messageCount）
    不含 messages 内容，减少传输量。需要完整对话时调用 GET /conversations/{id}
    """
    store = ConversationStore(user_id=user_id)
    try:
        convs = store.list_conversations()
        # 字段名适配前端 camelCase 习惯
        return {
            "conversations": [
                {
                    "id": c["id"],
                    "title": c["title"],
                    "createdAt": c["created_at"],
                    "updatedAt": c["updated_at"],
                    "messageCount": c["message_count"],
                }
                for c in convs
            ]
        }
    finally:
        store.close()


# ════════════════════════════════════════════
#  GET /conversations/{conv_id} — 获取单个对话详情
# ════════════════════════════════════════════

@router.get("/conversations/{conv_id}")
async def get_conversation(conv_id: str, user_id: int = Depends(get_current_user)):
    """
    获取单个对话的完整数据（含 messages 数组）
    """
    store = ConversationStore(user_id=user_id)
    try:
        conv = store.get_conversation(conv_id)
        if conv is None:
            raise HTTPException(status_code=404, detail="对话不存在")
        return {
            "id": conv["id"],
            "title": conv["title"],
            "messages": conv["messages"],
            "createdAt": conv["created_at"],
            "updatedAt": conv["updated_at"],
        }
    finally:
        store.close()


# ════════════════════════════════════════════
#  POST /conversations — 保存/更新对话
# ════════════════════════════════════════════

@router.post("/conversations")
async def save_conversation(
    data: dict = Body(...),
    user_id: int = Depends(get_current_user),
):
    """
    保存或更新对话（upsert）

    请求体：
    {
        "id": "xxx",           // 对话 ID（必填）
        "title": "对话标题",    // 可选
        "messages": [...],     // 消息数组 [{role, content}, ...]
        "createdAt": 123456,   // 创建时间戳
        "updatedAt": 123456    // 更新时间戳（可选）
    }

    返回：{"status": "ok", "id": "xxx"}
    """
    if "id" not in data or not data["id"]:
        raise HTTPException(status_code=400, detail="缺少必填字段：id")

    store = ConversationStore(user_id=user_id)
    try:
        store.save_conversation({
            "id": data["id"],
            "title": data.get("title", "新对话"),
            "messages": data.get("messages", []),
            "created_at": data.get("createdAt", data.get("created_at", 0)),
            "updated_at": data.get("updatedAt", data.get("updated_at", 0)),
        })
        return {"status": "ok", "id": data["id"]}
    except Exception as e:
        log_error(ErrorCode.COMM_SERVER_ERROR, detail=str(e))
        raise HTTPException(status_code=500, detail="保存对话失败")
    finally:
        store.close()


# ════════════════════════════════════════════
#  DELETE /conversations/{conv_id} — 删除对话
# ════════════════════════════════════════════

@router.delete("/conversations/{conv_id}")
async def delete_conversation(conv_id: str, user_id: int = Depends(get_current_user)):
    """
    删除指定对话
    """
    store = ConversationStore(user_id=user_id)
    try:
        deleted = store.delete_conversation(conv_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="对话不存在")
        return {"status": "ok"}
    finally:
        store.close()


# ════════════════════════════════════════════
#  POST /conversations/sync — 全量同步（双向合并）
# ════════════════════════════════════════════

@router.post("/conversations/sync")
async def sync_conversations(
    data: dict = Body(...),
    user_id: int = Depends(get_current_user),
):
    """
    全量同步：前端上传所有本地对话，后端合并后返回完整列表。

    使用场景：
    - 前端 init 时调用，将 localStorage 数据同步到后端
    - 如果 localStorage 为空（新设备），后端返回服务器端数据

    请求体：
    {
        "conversations": [
            {"id": "xxx", "title": "...", "messages": [...], "createdAt": 123},
            ...
        ]
    }

    返回：
    {
        "conversations": [...合并后的完整对话列表...]
    }
    """
    client_convs = data.get("conversations", [])
    store = ConversationStore(user_id=user_id)
    try:
        result = store.sync_from_client(client_convs)
        return result
    except Exception as e:
        log_error(ErrorCode.COMM_SERVER_ERROR, detail=str(e))
        raise HTTPException(status_code=500, detail="同步对话失败")
    finally:
        store.close()
