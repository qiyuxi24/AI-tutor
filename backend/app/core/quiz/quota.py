"""
题库存储配额（B3.1 预研项：防批量导入百万题撑爆磁盘）。

设计：
- 上限 = settings.quiz_storage_quota（每用户题目数，0 = 不限制）；
  仅约束 quiz 批量导入入口（dataset_quiz.import_from_json），
  不约束对话内单题生成（量小且是核心教学路径，不值得每次 stats 查询）。
- 判定纯函数 check_quota(current, incoming, limit)，与存储解耦，便于边界测试。
- 三态：未超（放行静默）/ ≥90%（放行 + 告警 msg）/ 超限（拒绝 + msg）。
"""

from __future__ import annotations

import logging

from app.core.config import settings

logger = logging.getLogger(__name__)

_WARN_RATIO = 0.9


def check_quota(current: int, incoming: int, limit: int) -> tuple[bool, str]:
    """判定本次导入是否放行。

    Returns:
        (allowed, msg)：allowed=False 必拒（调用方 raise）；
        allowed=True 且 msg 非空 = 接近上限的告警（调用方记 warning）。
    """
    if limit <= 0:
        return True, ""
    total = current + incoming
    if total > limit:
        return False, (
            f"题库配额超限：当前 {current} 题 + 本次 {incoming} 题 = {total}，"
            f"上限 {limit}（QUIZ_STORAGE_QUOTA 可调）。"
        )
    if total >= limit * _WARN_RATIO:
        return True, f"题库接近配额：{total}/{limit}（{total / limit:.0%}）。"
    return True, ""


def default_quota_checker(current: int, incoming: int) -> tuple[bool, str]:
    """按全局配置判定的 check_quota_fn 形态（dataset_quiz.import_from_json 默认使用）。"""
    return check_quota(current, incoming, settings.quiz_storage_quota)
