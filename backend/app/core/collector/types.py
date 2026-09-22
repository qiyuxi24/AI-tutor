"""
采集模块类型定义（B1.3 manager 的候选/任务数据模型）

来源授权分级（决策见 docs/教育资料采集/教育资料采集模块_设计讨论.md §3.2）：
- L0 公有领域/开放授权（CC BY-SA 等）      → 个人/商用均可采
- L1 作者明示授权转载                        → 个人可采，商用需人工核验
- L2 合理使用（个人学习）                    → 仅个人可采
- L3 版权不明/明确禁止                       → 一律不采
"""

import asyncio
from dataclasses import dataclass, field
from typing import Optional

# 各模式允许采集的授权等级
ALLOWED_LICENSE = {
    "personal": {"L0", "L1", "L2"},
    "commercial": {"L0"},  # V1 商用仅 L0（§3.3，L1 人工核验留给后续）
}


@dataclass
class CollectCandidate:
    """搜索阶段返回的候选资源（对应 store.resources 一行）"""

    title: str
    source_url: str
    source: str = ""            # 来源适配器名，如 wikipedia / wikibooks
    license_level: str = "L0"   # L0~L3，见模块注释
    description: str = ""
    size_bytes: int = 0
    meta: dict = field(default_factory=dict)


@dataclass
class CollectTask:
    """一次采集任务（DB 行 + 内存态拼接）"""

    id: int
    user_id: int
    subject: str = ""
    status: str = "pending"     # pending/processing/completed/cancelled/failed
    cursor: str = ""            # 断点续传：最后处理过的候选 source_url
    processed_count: int = 0
    total_count: int = 0
    source: str = ""
    created_at: str = ""
    # 内存态（不落库）：协作式取消信号；从 DB 恢复的任务由 manager 重建
    cancel_event: Optional[asyncio.Event] = None

    @property
    def is_cancelled(self) -> bool:
        return self.status == "cancelled"
