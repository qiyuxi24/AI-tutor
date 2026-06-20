from pydantic import BaseModel, Field
from typing import List, Literal, Optional

# ================================================================
# 对话相关
# ================================================================

# 引导模式：adaptive = 自适应引导，free_talk = 自由对话，recursive = 递归式教学
GuideMode = Literal['adaptive', 'free_talk', 'recursive']

class ChatMessage(BaseModel):
    role: str    # "user" 或 "assistant"
    content: str

class ChatRequest(BaseModel):
    """前端发给后端的请求体（user_id 由 JWT token 提供，无需在请求体中传递）"""
    messages: List[ChatMessage] # 全部对话历史
    mode: GuideMode             # 引导模式
    current_node: Optional[str] = None  # 递归模式：当前正在教学的知识点 ID

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
    from_: str = Field(alias='from')    # 源节点 ID（从请求 JSON 的 "from" 字段映射）
    to: str                             # 目标节点 ID
    relation: EdgeRelation = 'related'  # 关系类型
    label: str = ""                     # 关系说明文字


class UpdateEdgeRequest(BaseModel):
    """更新边的请求体"""
    relation: Optional[EdgeRelation] = None  # 新关系类型
    label: Optional[str] = None              # 新标签


class CreateEdgeRawRequest(BaseModel):
    """创建边的原始请求体（兼容 AI function calling 的任意字段传入）"""
    from_: str = Field(alias='from')
    to: str
    relation: str
    label: str = ""
    added_by: str = "ai"
    confidence: Optional[float] = None


# ================================================================
# 用户画像相关
# ================================================================

class ProfileResponse(BaseModel):
    """用户画像响应"""
    content: str                        # Markdown 格式的画像内容


class ProfileUpdateRequest(BaseModel):
    """更新用户画像的请求体"""
    content: str                        # 新的画像内容（Markdown）
    op: Literal['replace', 'append'] = 'replace'  # 操作类型


# ================================================================
# 学习路径推荐相关
# ================================================================

class DecomposeRequest(BaseModel):
    """问题拆解请求"""
    question: str                       # 用户原始问题

class DecomposeResponse(BaseModel):
    """问题拆解响应"""
    target: dict | None = None          # 目标节点 {id, name}
    nodes: list[dict] = []              # 所有知识点 [{id, name, tags}, ...]
    edges: list[dict] = []              # 依赖边 [{from, to, relation}, ...]
    created_nodes: list[str] = []       # 实际创建到图谱中的节点 ID 列表
    created_edges: int = 0              # 实际创建的边数量

class LearningPathResponse(BaseModel):
    """学习路径响应"""
    ordered_nodes: list[str] = []       # 拓扑排序后的节点 ID 列表
    nodes_detail: list[dict] = []       # 节点详情 [{id, name, mastery, difficulty, ...}, ...]
    root_nodes: list[str] = []          # 入度为 0 的根节点
    current_recommendation: str | None = None  # 推荐下一步学的节点 ID
    target_node: str | None = None      # 目标节点 ID（如果指定）

class NextToLearnResponse(BaseModel):
    """下一步学习推荐响应"""
    node_id: str | None = None          # 推荐节点 ID（全部已掌握则为 None）
    name: str = ""                      # 节点名称
    mastery: int = 0                    # 当前掌握度
    reason: str = ""                    # 推荐理由
