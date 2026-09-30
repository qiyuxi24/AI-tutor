"""用户模型管理 API（每用户自有对话模型 + 运行时降级档位）。

端点：
  GET    /llm/models               - 列表 + 当前激活 / 当前生效档位 + 系统默认档
  POST   /llm/models               - 新建模型（默认未验证）
  PATCH  /llm/models/{id}          - 改接入参数（退回未验证）/ 停用或恢复
  DELETE /llm/models/{id}          - 删除（删的是激活模型则回落系统默认档）
  POST   /llm/models/{id}/test     - 连通性测试（真打一次最小请求）
  POST   /llm/models/{id}/activate - 设为当前使用的模型

安全：API Key 只在服务端保存，任何响应都只回掩码（`api_key_masked`）。
用户隔离：`user_id` 一律取自 token，不接受请求参数 —— 越权面为零。
"""
import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from app.core.auth import get_current_user
from app.core.config import settings
from app.core.llm.clients import MODEL_NAME
from app.core.llm.user_models import ModelStore

logger = logging.getLogger("ai-tutor")

router = APIRouter(tags=["模型管理"])


class ModelCreateRequest(BaseModel):
    """新建模型（Key 明文只在请求里出现一次，之后只存服务端）。"""
    name: str = Field(min_length=1, max_length=64)
    base_url: str = Field(pattern=r"^https?://", max_length=512)
    api_key: str = Field(min_length=1, max_length=512)
    model: str = Field(min_length=1, max_length=128)


class ModelUpdateRequest(BaseModel):
    """改接入参数 / 停用恢复。status 只开放这两个值：不能自封 available。"""
    name: str | None = Field(default=None, min_length=1, max_length=64)
    base_url: str | None = Field(default=None, pattern=r"^https?://", max_length=512)
    api_key: str | None = Field(default=None, min_length=1, max_length=512)
    model: str | None = Field(default=None, min_length=1, max_length=128)
    status: Literal["disabled", "unverified"] | None = None


def _store(user_id: int) -> ModelStore:
    """取用户的模型配置存储（独立函数 = 测试注入点）。"""
    return ModelStore(user_id)


def _require(user_id: int, model_id: str) -> None:
    """模型不存在 → 404（KeyError 不该冒到 500）。"""
    try:
        _store(user_id).get(model_id)
    except KeyError:
        raise HTTPException(404, "模型不存在")


def _snapshot(user_id: int) -> dict:
    """列表响应 = 用户配置 + 系统默认档信息（界面要显示"没配时用的是什么"）。"""
    body = _store(user_id).snapshot()
    body["system_default"] = {"model": MODEL_NAME, "base_url": settings.llm_base_url}
    return body


@router.get("/llm/models")
async def list_models(user_id: int = Depends(get_current_user)):
    return _snapshot(user_id)


@router.post("/llm/models")
async def create_model(body: ModelCreateRequest,
                       user_id: int = Depends(get_current_user)):
    return _store(user_id).add(name=body.name, base_url=body.base_url,
                               api_key=body.api_key, model=body.model)


@router.patch("/llm/models/{model_id}")
async def update_model(model_id: str, body: ModelUpdateRequest,
                       user_id: int = Depends(get_current_user)):
    _require(user_id, model_id)
    store = _store(user_id)
    fields = body.model_dump(exclude_unset=True, exclude={"status"})
    result = store.update(model_id, **fields) if fields else store.get(model_id)
    if body.status is not None:
        result = store.set_status(model_id, body.status)
    return result


@router.delete("/llm/models/{model_id}")
async def delete_model(model_id: str, user_id: int = Depends(get_current_user)):
    if not _store(user_id).delete(model_id):
        raise HTTPException(404, "模型不存在")
    return {"ok": True}


@router.post("/llm/models/{model_id}/activate")
async def activate_model(model_id: str, user_id: int = Depends(get_current_user)):
    _require(user_id, model_id)
    return _store(user_id).set_active(model_id)


async def _probe_model(base_url: str, api_key: str, model: str) -> tuple[bool, str]:
    """连通性探测：真打一次最小请求，只判 HTTP 层能否走通。

    刻意不复用 chat_create / 客户端缓存 —— 探测用的是还没保存的配置，且超时要短
    （用户在界面上等），失败原因原样回给用户看。
    """
    client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=20.0)
    try:
        await client.chat.completions.create(
            model=model, messages=[{"role": "user", "content": "ping"}], max_tokens=1)
        return True, ""
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:200]}"


@router.post("/llm/models/{model_id}/test")
async def test_model(model_id: str, user_id: int = Depends(get_current_user)):
    _require(user_id, model_id)
    store = _store(user_id)
    entry = store.secret(model_id)
    ok, error = await _probe_model(entry["base_url"], entry["api_key"], entry["model"])
    return store.mark_test(model_id, ok, error)
