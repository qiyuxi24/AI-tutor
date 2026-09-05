"""
RAG 管道：统一数据类型定义。

设计目标：让检索层（各 RagSource）与消费层（chat_service 注入点）解耦。
- 消费层只依赖统一的 RagHit / RagContext，不关心具体数据源。
- 各数据源（图谱 RAG / 上传知识库 / 未来网页检索）实现 RagSource 接口后注册进 pipeline。
"""

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class RagHit:
    """
    统一检索命中项。

    source:  来源标识（如 "graph" / "kb" / "web"），消费层可用它标注引用来源
    content: 命中片段正文（注入提示词的主体）
    score:   相关度分数（约 0~1，用于排序/过滤）
    heading: 片段标题/摘要（可选）
    path:    来源路径/定位信息（可选，如知识库文档目录路径、图谱节点名）
    metadata: 附加元数据（各源自有字段原样透传，消费层不依赖）
    """
    source: str
    content: str
    score: float = 0.0
    heading: str = ""
    path: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RagContext:
    """
    一次检索请求的上下文。

    user_id:    用户 ID（每个用户数据隔离）
    query:      查询文本（通常是学生最新一条消息）
    top_k:      期望返回条数
    kb:         知识库范围 {node_ids: [...], name: str} | None（仅知识库源使用）
    mode:       用途模式 "personal"（默认）/ "commercial"，商用模式源自行过滤非商用资料
    metadata:   附加上下文（如当前节点、教学模式等），供各源自行消费
    """
    user_id: int
    query: str
    top_k: int = 5
    kb: Optional[dict] = None
    mode: str = "personal"
    metadata: dict[str, Any] = field(default_factory=dict)
