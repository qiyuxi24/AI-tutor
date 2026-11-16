"""用户模型管理 API（每用户自有对话模型 + 运行时降级档位）。

端点：
  GET    /llm/models               - 列表 + 当前激活 / 当前生效档位 + 系统默认档 + 方言预设
  POST   /llm/models               - 新建模型（默认未验证）
  PATCH  /llm/models/{id}          - 改接入参数（退回未验证）/ 停用或恢复
  DELETE /llm/models/{id}          - 删除（删的是激活模型则回落系统默认档）
  POST   /llm/models/{id}/test     - 连通性测试（真打一次最小请求）
  POST   /llm/models/{id}/activate - 设为当前使用的模型
  POST   /llm/models/{id}/deactivate - 取消激活（回落到系统默认档 / 其余备用）

安全：API Key 只在服务端保存，任何响应都只回掩码（`api_key_masked`）。
用户隔离：`user_id` 一律取自 token，不接受请求参数 —— 越权面为零。
**SSRF**：`base_url` 会被服务端出站请求（探测 / 真实对话）→ 一律过 `net_guard.is_blocked_url`
（全库唯一 SSRF 防线），拒绝内网/回环/保留地址 —— 否则登录用户可借 base_url 探测内网。

**方言（2026-11-16）**：`provider` 字段决定请求体带什么方言参数（见 `core/llm/provider.py`）。
空串 = 按模型名自动判定，这是默认值；只有"模型名看不出来"的自建网关才需要显式指定。
探测请求也走同一张表 —— 否则 DeepSeek reasoner 会因 `max_tokens=1` 被误判 unavailable。
"""
import asyncio
import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from app.core.net_guard import is_blocked_url
from app.core.auth import get_current_user
from app.core.config import settings
from app.core.llm import provider as _provider
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
    # 方言键；空串 = 按模型名自动判定（绝大多数情况）
    provider: str = Field(default="", max_length=32)


class ModelUpdateRequest(BaseModel):
    """改接入参数 / 停用恢复。status 只开放这两个值：不能自封 available。"""
    name: str | None = Field(default=None, min_length=1, max_length=64)
    base_url: str | None = Field(default=None, pattern=r"^https?://", max_length=512)
    api_key: str | None = Field(default=None, min_length=1, max_length=512)
    model: str | None = Field(default=None, min_length=1, max_length=128)
    provider: str | None = Field(default=None, max_length=32)
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


async def _validate_base_url(url: str) -> None:
    """校验用户配置的 base_url 不指向内网（SSRF 防线，唯一实现见 `core/net_guard.py`）。

    DNS 解析会阻塞，放线程里跑；拒绝时把原因回给用户（可自纠正）。
    """
    reason = await asyncio.to_thread(is_blocked_url, url)
    if reason:
        raise HTTPException(400, f"base_url 不被允许：{reason}")


def _snapshot(user_id: int) -> dict:
    """列表响应 = 用户配置 + 系统默认档信息（界面要显示"没配时用的是什么"）。

    `presets` 让设置页能"选服务商 → 自动填 base_url / 模型名"，不必让用户抄地址；
    `system_default.provider` 是 .env 那档的系统默认模型解析出的方言键（界面显示用）。
    """
    body = _store(user_id).snapshot()
    body["system_default"] = {
        "model": MODEL_NAME,
        "base_url": settings.llm_base_url,
        "provider": _provider.provider_for(MODEL_NAME).key,
    }
    body["presets"] = _provider.presets()
    return body


@router.get("/llm/models")
async def list_models(user_id: int = Depends(get_current_user)):
    return _snapshot(user_id)


@router.post("/llm/models")
async def create_model(body: ModelCreateRequest,
                       user_id: int = Depends(get_current_user)):
    if not _provider.is_known(body.provider):
        raise HTTPException(400, f"未知的服务商: {body.provider}")
    await _validate_base_url(body.base_url)
    return _store(user_id).add(name=body.name, base_url=body.base_url,
                               api_key=body.api_key, model=body.model,
                               provider=body.provider)


@router.patch("/llm/models/{model_id}")
async def update_model(model_id: str, body: ModelUpdateRequest,
                       user_id: int = Depends(get_current_user)):
    _require(user_id, model_id)
    if body.provider is not None and not _provider.is_known(body.provider):
        raise HTTPException(400, f"未知的服务商: {body.provider}")
    if body.base_url is not None:
        await _validate_base_url(body.base_url)
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


@router.post("/llm/models/deactivate")
async def deactivate_model(user_id: int = Depends(get_current_user)):
    """取消激活 → 回落系统默认档（对话框里「用系统默认」的落点）。

    路径放在 `/{model_id}/activate` **之前**定义不必要（FastAPI 按方法+路径匹配，
    `deactivate` 不会撞 `{model_id}` 的 PATCH/DELETE）；单独端点是为了让前端
    不必知道"取消"对应哪个 id。
    """
    return _store(user_id).clear_active()


async def _probe_model(base_url: str, api_key: str, model: str,
                       provider: str = "") -> tuple[bool, str]:
    """连通性探测：真打一次最小请求，只判 HTTP 层能否走通。

    刻意不复用 chat_create / 客户端缓存 —— 探测用的是还没保存的配置，且超时要短
    （用户在界面上等），失败原因原样回给用户看。

    `max_tokens` 与 `extra_body` 走**方言表**（2026-11-16）：否则 DeepSeek reasoner
    会因 `max_tokens=1` 被网关拒绝 → 一个好模型被误判 `unavailable`；MiniMax 缺
    `reasoning_split` 则思考混进 content。
    """
    dialect = _provider.provider_for(model, provider)
    client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=20.0)
    try:
        await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=dialect.probe_tokens,
            extra_body=_provider.extra_body_for(dialect, thinking=True),
        )
        return True, ""
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:200]}"


@router.post("/llm/models/{model_id}/test")
async def test_model(model_id: str, user_id: int = Depends(get_current_user)):
    _require(user_id, model_id)
    store = _store(user_id)
    entry = store.secret(model_id)
    # 存量数据可能早于 SSRF 校验落地 → 真打之前再复核一次
    await _validate_base_url(entry["base_url"])
    ok, error = await _probe_model(entry["base_url"], entry["api_key"], entry["model"],
                                   entry.get("provider", ""))
    return store.mark_test(model_id, ok, error)
