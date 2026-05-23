from pydantic import BaseModel
from typing import List, Literal, Optional

# ================================================================
# 对话相关
# ================================================================

# 定义三种引导模式
GuideMode = Literal['scaffolding', 'think_first', 'reverse_teaching']

class ChatMessage(BaseModel):
    role: str    # "user" 或 "assistant"
    content: str

class ChatRequest(BaseModel):
    """前端发给后端的请求体"""
    user_id: str                # 学生唯一标识
    messages: List[ChatMessage] # 全部对话历史
    mode: GuideMode             # 引导模式

class ChatResponse(BaseModel):
    """后端返回给前端的响应体"""
    reply: str                      # AI 的回复
    mode: str                       # 当前使用的模式
    graph_analysis: Optional[dict] = None  # 图谱更新分析结果（可选）


# ================================================================
# 知识图谱 CRUD 请求/响应模型
# ================================================================

# 关系类型
EdgeRelation = Literal['prerequisite', 'related', 'confusion', 'extension']


class CreateNodeRequest(BaseModel):
    """创建新节点的请求体（手动创建，ID 自动生成）"""
    name: str                           # 节点名称（必填）
    tags: List[str] = []                # 标签列表
    content: Optional[str] = None       # 初始 MD 内容（可选，默认使用模板）


class CreateNodeAIStyle(BaseModel):
    """AI function calling 创建节点的请求体（需指定 ID）"""
    id: str                             # 节点英文 ID
    name: str                           # 节点名称
    tags: List[str] = []                # 标签
    summary: str = ""                   # 摘要
    difficulty: int = 3                 # 难度 1-5
    estimated_minutes: int = 15         # 预估学习分钟数
    content: str = ""                   # MD 内容
    from_nodes: List[str] = []          # 前置节点 ID 列表
    added_by: str = "ai"                # 来源标记


class UpdateNodeInfoRequest(BaseModel):
    """更新节点基本信息的请求体（不涉及 MD 内容）"""
    name: Optional[str] = None          # 新名称
    tags: Optional[List[str]] = None    # 新标签列表


class UpdateNodeContentRequest(BaseModel):
    """更新节点 MD 内容的请求体"""
    content: str                        # MD 内容
    op: Literal['replace', 'append'] = 'replace'  # 操作类型


class UpdateMasteryRequest(BaseModel):
    """更新掌握程度的请求体"""
    mastery: int                        # 掌握度 0-100
    added_by: str = "ai"                # 来源


class CreateEdgeRequest(BaseModel):
    """创建新边的请求体"""
    from_: str                          # 源节点 ID（从请求 JSON 的 "from" 字段映射）
    to: str                             # 目标节点 ID
    relation: EdgeRelation = 'related'  # 关系类型
    label: str = ""                     # 关系说明文字

    class Config:
        # 允许 JSON 中的 "from" 映射到 Python 的 from_ 字段
        fields = {'from_': 'from'}


class UpdateEdgeRequest(BaseModel):
    """更新边的请求体"""
    relation: Optional[EdgeRelation] = None  # 新关系类型
    label: Optional[str] = None              # 新标签


class CreateEdgeRawRequest(BaseModel):
    """创建边的原始请求体（兼容 AI function calling 的任意字段传入）"""
    from_: str
    to: str
    relation: str
    label: str = ""
    added_by: str = "ai"
    confidence: Optional[float] = None

    class Config:
        fields = {'from_': 'from'}
