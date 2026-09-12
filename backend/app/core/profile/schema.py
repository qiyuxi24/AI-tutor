"""
用户画像 —— 数据结构层（唯一真相源）。

只负责"画像数据长什么样"：
  - 默认结构 / schema 版本
  - 字段权重（同时充当 update_field 的可写字段白名单）
  - 路径读写、值有效性判断、补丁合并

不含文件 IO（store.py）、不含 Markdown 转换（markdown.py）、不含业务编排（manager.py）。
"""

import re
import uuid
from copy import deepcopy
from datetime import datetime

PROFILE_SCHEMA_VERSION = 2

# 字段权重：既作 update_field 的可写白名单，又作完整度分母。
# usage_mode 是版权合规开关（personal/commercial），需要可写但 weight=0（不计入完整度）。
FIELD_WEIGHTS = {
    "basic.name": 1,
    "basic.age": 1,
    "basic.stage": 1,
    "goals": 2,
    "knowledge_background": 2,
    "learning.pace": 1,
    "learning.weekly_hours": 1,
    "preferences.personality": 1,
    "preferences.teaching_style_like": 1,
    "preferences.teaching_style_avoid": 1,
    "preferences.usage_mode": 0,
}
TOTAL_WEIGHT = sum(FIELD_WEIGHTS.values())

# update_data 允许写入的顶层键（version/user_id/created_at 等元字段一律忽略，防止被前端改写）
EDITABLE_SECTIONS = ("basic", "learning", "preferences", "goals",
                     "knowledge_background", "ai_notes")

# 旧版模板里的"待填写"占位值
_PLACEHOLDER_RE = re.compile(r"^\s*[（(]\s*待")


def now() -> str:
    """当前时间 ISO 字符串（秒级精度）"""
    return datetime.now().isoformat(timespec="seconds")


def new_note_id() -> str:
    """生成观察笔记 ID"""
    return f"n_{uuid.uuid4().hex[:10]}"


def is_filled(value) -> bool:
    """字段是否已填写（空白与"（待填写）"占位值都算未填）"""
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return False
        return not _PLACEHOLDER_RE.match(text)
    if isinstance(value, list):
        return any(is_filled(v) for v in value)
    return value is not None


def get_path(data: dict, path: str, default=None):
    """按 'a.b.c' 路径取值"""
    cur = data
    for p in path.split("."):
        if not isinstance(cur, dict) or p not in cur:
            return default
        cur = cur[p]
    return cur


def set_path(data: dict, path: str, value) -> None:
    """按 'a.b.c' 路径写值（自动创建中间 dict）"""
    parts = path.split(".")
    cur = data
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    cur[parts[-1]] = value


def merge_patch(target: dict, patch: dict) -> dict:
    """把 patch 递归合并进 target 并返回（dict 逐键合并，其余类型整体覆盖）。

    只覆盖 patch 中显式出现的键，未出现的保持 target 原值；值做深拷贝，不与调用方共享引用。
    """
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            merge_patch(target[key], value)
        else:
            target[key] = deepcopy(value)
    return target


def default_profile_data(user_id: int) -> dict:
    """一份全新的默认结构化画像"""
    return {
        "version": PROFILE_SCHEMA_VERSION,
        "user_id": user_id,
        "created_at": now(),
        "updated_at": now(),
        "basic": {"name": "", "age": "", "stage": ""},
        "goals": [],
        "knowledge_background": "",
        "learning": {"pace": "", "weekly_hours": ""},
        "preferences": {
            "personality": "",
            "teaching_style_like": "",
            "teaching_style_avoid": "",
            "usage_mode": "personal",   # personal / commercial（版权双模式，采集合规）
        },
        "ai_notes": [],
    }


def is_empty_profile(data: dict) -> bool:
    """画像是否无任何实质内容（所有可填字段未填且无观察笔记）。

    只看 weight>0 的字段：weight=0 的是开关类字段（usage_mode），有默认值但不算内容。
    """
    if data.get("ai_notes"):
        return False
    return not any(is_filled(get_path(data, path))
                   for path, weight in FIELD_WEIGHTS.items() if weight)
