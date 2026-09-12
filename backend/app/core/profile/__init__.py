"""
用户画像包（结构化 v2）。

画像在一次对话里被两处消费：注入教学提示词（get_summary）、检索合规开关（usage_mode）。
分包让每条链路只依赖自己需要的那层：

    schema    数据结构：默认值 / 字段权重（可写白名单）/ 路径读写 / 补丁合并（无 IO）
    store     JSON 存储：原子写 + 旧版 MD 迁移
    markdown  与 Markdown 互转：渲染给 LLM、解析旧版数据
    manager   UserProfile 门面 —— 业务代码只应通过这里访问画像

import 约定：
    业务代码   from app.core.profile import UserProfile, get_usage_mode
    不要直接 import 子模块（内部结构可能调整）
"""

from .manager import UserProfile
from .markdown import parse_markdown_to_data, render_profile_markdown
from .schema import (
    FIELD_WEIGHTS,
    PROFILE_SCHEMA_VERSION,
    TOTAL_WEIGHT,
    default_profile_data,
)


def get_usage_mode(user_id: int) -> str:
    """usage_mode 的唯一读取入口（检索链路专用）。

    画像损坏、权限异常等一律降级为 personal —— 版权模式读不到不该阻断检索。
    """
    try:
        return UserProfile(user_id).get_usage_mode()
    except Exception:
        return "personal"


__all__ = [
    "UserProfile",
    "get_usage_mode",
    "default_profile_data",
    "render_profile_markdown",
    "parse_markdown_to_data",
    "FIELD_WEIGHTS",
    "TOTAL_WEIGHT",
    "PROFILE_SCHEMA_VERSION",
]
