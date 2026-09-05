"""
用户画像 API 路由（结构化 v2）

接口清单：
  GET    /profile                  - 获取用户画像（渲染 Markdown + 结构化数据 + 完整度）
  PUT    /profile                  - 兼容旧接口：Markdown 文本更新（replace / append）
  PATCH  /profile                  - 结构化全量更新（前端表单编辑提交）
  POST   /profile/notes            - 添加 AI 观察笔记
  DELETE /profile/notes/{note_id}  - 删除观察笔记
"""

from fastapi import APIRouter, Depends, HTTPException
from app.core.user_profile import UserProfile
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
        data=profile.to_dict(),
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
    结构化全量更新画像（前端表单编辑提交）。
    仅覆盖提交的非空字段，AI 观察笔记始终保留。
    """
    profile = UserProfile(user_id=user_id)
    profile.update_data(body.data.model_dump())
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
