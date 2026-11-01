"""
对话历史 API 路由

提供对话的 CRUD 接口，按用户隔离。

**真相归属 = 服务端**（2026-10-31，架构审查 P0-1 / C3a）：
对话历史由 `/chat*` 按 `conv_id` 从 `conversations.db` 组装，回合结束由服务端写入。
本路由的职责相应变成"读 + 迁移"：

- `GET/DELETE`：客户端拉取权威列表 / 墓碑删除（跨端传播）；
- `POST /conversations` + `/sync`：**只用于首次迁移**（老用户 localStorage 上行）
  与离线补写，不是常规写入口；
- 已删对话是**墓碑**：`sync` 写不回来，`GET` 会把 id 放进 `tombstones` 让客户端清本地。
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

    `tombstones`（P0-1/S3）：已删对话 id —— 客户端据此清掉本地副本，
    否则下次全量 sync 会把它们"复活"（服务端墓碑会拒绝写入，但本地仍显示）。
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
            ],
            "tombstones": store.tombstones(),
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
    客户端快照 upsert —— **迁移/离线补写用，不是日常写入口**

    日常对话历史由 `/chat*` 在回合结束时落库（`chat_service._persist_turn`）。
    本端点仅供：老数据迁移、离线轮次补写。已删对话（墓碑）会被拒绝写入。

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

@router.patch("/conversations/{conv_id}")
async def rename_conversation(
    conv_id: str,
    data: dict = Body(...),
    user_id: int = Depends(get_current_user),
):
    """
    只改标题（P0-1 后新增）

    旧实现靠"前端全量 sync 顺带带上 title"，而 sync 现在只用于迁移 →
    改名需要自己的通道。标题是元数据，不影响历史归属。
    """
    title = (data.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="缺少必填字段：title")

    store = ConversationStore(user_id=user_id)
    try:
        if not store.rename(conv_id, title):
            raise HTTPException(status_code=404, detail="对话不存在")
        return {"status": "ok", "id": conv_id, "title": title}
    finally:
        store.close()


@router.delete("/conversations/{conv_id}")
async def delete_conversation(conv_id: str, user_id: int = Depends(get_current_user)):
    """
    删除指定对话（**墓碑**，P0-1/S3）

    软删而非物理删：另一台设备的本地副本要靠墓碑才知道"这条被删了"，
    否则它的下一次全量 sync 会让对话复活。幂等（重复删返回 ok）。
    """
    store = ConversationStore(user_id=user_id)
    try:
        # 幂等：两端同时删 / 重复点击都返回 ok（404 会让前端误判"删除失败"）
        store.soft_delete(conv_id)
        return {"status": "ok", "tombstones": store.tombstones()}
    finally:
        store.close()


# ════════════════════════════════════════════
#  POST /conversations/sync — 迁移同步（上行本地副本）
# ════════════════════════════════════════════

@router.post("/conversations/sync")
async def sync_conversations(
    data: dict = Body(...),
    user_id: int = Depends(get_current_user),
):
    """
    迁移同步：前端上传本地副本，后端合并后返回**库里真值**。

    2026-10-31（P0-1）起前端 `persist()` 不再全量上行，本端点只在 `init()` 调一次：
    - 老用户首次运行新版 → 把 localStorage 里的历史送上来（迁移入口）
    - 离线期间聊的轮次 → 补写
    - localStorage 为空（新设备）→ 返回服务端全量，前端据此覆盖本地缓存

    请求体：
    {
        "conversations": [
            {"id": "xxx", "title": "...", "messages": [...], "createdAt": 123},
            ...
        ]
    }

    返回：
    {
        "conversations": [...库里全部对话...],
        "tombstones": [...已删对话 id，客户端据此删本地...]
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
