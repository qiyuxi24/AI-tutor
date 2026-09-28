"""
用户画像 API 路由（结构化 v2）

接口清单：
  GET    /profile                  - 获取用户画像（渲染 Markdown + 结构化数据 + 完整度）
  PUT    /profile                  - 兼容旧接口：Markdown 文本更新（replace / append）
  PATCH  /profile                  - 结构化全量更新（前端表单编辑提交）
  POST   /profile/notes            - 添加 AI 观察笔记
  DELETE /profile/notes/{note_id}  - 删除观察笔记
  GET    /profile/avatar           - 取当前用户头像（PNG；未设置 404）
  POST   /profile/avatar           - 上传头像（multipart，字段名 file）
  DELETE /profile/avatar           - 恢复默认头像（幂等）

头像刻意**不放进画像 JSON**（见 docs/用户头像_设计与实施方案.md §5.1）：画像是要注入
系统提示词的文本，塞二进制既烧 token 又多一份泄漏面；它的"有无"由本组端点的 404 表达。
"""

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from app.core.profile import (
    AVATAR_SIZE,
    AvatarError,
    AvatarStore,
    UserProfile,
    process_avatar,
)
from app.core.auth import get_current_user
from app.models.schemas import (
    ProfileResponse,
    ProfileUpdateRequest,
    ProfileDataUpdateRequest,
    ProfileNoteCreateRequest,
)

router = APIRouter()


def _build_response(profile: UserProfile) -> ProfileResponse:
    """组装统一响应：渲染 MD + 结构化数据 + 完整度"""
    return ProfileResponse(
        content=profile.to_markdown(),
        data=profile.get(),
        completeness=profile.get_completeness(),
    )


@router.get("/profile", response_model=ProfileResponse)
async def get_profile(user_id: int = Depends(get_current_user)):
    """
    获取当前用户的画像：
    - content：渲染后的 Markdown（前端预览 / AI 视角）
    - data：结构化画像数据（表单编辑）
    - completeness：完整度统计
    用户尚未创建画像时返回默认模板。
    """
    profile = UserProfile(user_id=user_id)
    return _build_response(profile)


@router.put("/profile", response_model=ProfileResponse)
async def update_profile(
    body: ProfileUpdateRequest,
    user_id: int = Depends(get_current_user),
):
    """
    兼容旧接口：以 Markdown 文本更新画像。
    - op="replace"：全量解析替换
    - op="append"：解析新信息合并进现有画像（只补空缺字段 + 追加观察笔记）
    """
    if not body.content:
        raise HTTPException(status_code=422, detail="content 不能为空")
    profile = UserProfile(user_id=user_id)
    profile.update(content=body.content, mode=body.op)
    return _build_response(profile)


@router.patch("/profile", response_model=ProfileResponse)
async def update_profile_data(
    body: ProfileDataUpdateRequest,
    user_id: int = Depends(get_current_user),
):
    """
    结构化更新画像（前端表单编辑提交）。
    只覆盖请求中显式提交的字段（exclude_unset），未提交字段保持原值；AI 观察笔记默认保留。
    """
    profile = UserProfile(user_id=user_id)
    profile.update_data(body.data.model_dump(exclude_unset=True))
    return _build_response(profile)


@router.post("/profile/notes", response_model=ProfileResponse)
async def add_profile_note(
    body: ProfileNoteCreateRequest,
    user_id: int = Depends(get_current_user),
):
    """添加一条 AI 观察笔记（带时间戳，结构化存储）"""
    profile = UserProfile(user_id=user_id)
    try:
        profile.add_note(body.content, source="ai")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return _build_response(profile)


@router.delete("/profile/notes/{note_id}", response_model=ProfileResponse)
async def delete_profile_note(
    note_id: str,
    user_id: int = Depends(get_current_user),
):
    """删除一条 AI 观察笔记"""
    profile = UserProfile(user_id=user_id)
    if not profile.delete_note(note_id):
        raise HTTPException(status_code=404, detail="观察笔记不存在")
    return _build_response(profile)


# ════════════════════════════════════════════════════════════════
#  用户头像（独立于画像 JSON 的图片资源）
#
#  user_id 一律来自 token，**不接受请求参数** —— 越权面为零。
# ════════════════════════════════════════════════════════════════

@router.get("/profile/avatar")
async def get_avatar(user_id: int = Depends(get_current_user)):
    """取当前用户头像（PNG）。

    未设置返回 404 —— 前端据此回落到默认人形图标，不需要在画像里存"有没有"字段。
    """
    store = AvatarStore(user_id)
    raw = store.read()
    if not raw:
        raise HTTPException(status_code=404, detail="尚未设置头像")
    return Response(
        content=raw,
        media_type="image/png",
        headers={
            # 私有资源：可缓存但每次回源校验（前端另有 objectURL，正常不会走 HTTP 缓存）
            "ETag": f'"{store.signature()}"',
            "Cache-Control": "private, max-age=0, must-revalidate",
        },
    )


@router.post("/profile/avatar")
async def upload_avatar(
    file: UploadFile = File(...),
    user_id: int = Depends(get_current_user),
):
    """上传/替换头像：处理成 256×256 的 RGBA PNG 后覆盖写。

    错误分级：413 文件超限 / 415 格式不支持 / 422 解码失败（含空文件）。
    """
    raw = await file.read()
    try:
        png = process_avatar(file.filename or "", raw)
    except AvatarError as e:
        status = {"too_large": 413, "unsupported": 415}.get(e.code, 422)
        raise HTTPException(status_code=status, detail=str(e))
    written = AvatarStore(user_id).save(png)
    return {"ok": True, "bytes": written, "size": AVATAR_SIZE}


@router.delete("/profile/avatar")
async def delete_avatar(user_id: int = Depends(get_current_user)):
    """恢复默认头像。幂等：本来就没设置过也返回 200。"""
    removed = AvatarStore(user_id).delete()
    return {"ok": True, "removed": removed}
