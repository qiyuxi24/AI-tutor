"""
用户画像 API 路由

接口清单：
  GET    /profile          - 获取用户画像（不存在时返回默认模板）
  PUT    /profile          - 更新用户画像（全量替换）
  PATCH  /profile          - 追加内容到用户画像
"""

from fastapi import APIRouter, Depends, HTTPException
from app.core.user_profile import UserProfile
from app.core.auth import get_current_user
from app.models.schemas import ProfileResponse, ProfileUpdateRequest

router = APIRouter()


@router.get("/profile", response_model=ProfileResponse)
async def get_profile(user_id: int = Depends(get_current_user)):
    """
    获取当前用户的画像内容（Markdown 格式）。
    如果用户尚未创建画像，返回默认模板。
    """
    profile = UserProfile(user_id=user_id)
    content = profile.get()
    return ProfileResponse(content=content)


@router.put("/profile", response_model=ProfileResponse)
async def update_profile(
    body: ProfileUpdateRequest,
    user_id: int = Depends(get_current_user),
):
    """
    更新当前用户的画像内容。
    
    - op="replace"：全量替换
    - op="append"：追加到末尾
    """
    profile = UserProfile(user_id=user_id)
    updated = profile.update(content=body.content, mode=body.op)
    return ProfileResponse(content=updated)
