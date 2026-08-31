"""
出题模块 — Pydantic 数据模型

职责：
- 定义题目（Question）与出题请求/结果的强类型模型
- 用 Pydantic 做结构化输出约束，防止 AI 乱出格式

题型约定：
  single        单选题（唯一正确选项）
  multiple      多选题（两个及以上正确选项）
  judge         判断题（对/错）
  fill          填空题（短文本作答）
  short_answer  简答题（开放作答，由 LLM 判分）
"""

from typing import Optional
from pydantic import BaseModel, Field


class QuestionOption(BaseModel):
    """单个选项"""
    label: str = Field(..., description="选项内容")
    value: str = Field(..., description="选项标识，如 A/B/C/D")


class Question(BaseModel):
    """一道题目（LLM 结构化输出的目标 schema）"""
    id: str = Field(..., description="题目内部 id，如 q1/q2")
    type: str = Field(..., description="题型: single/multiple/judge/fill/short_answer")
    question: str = Field(..., description="题干")
    options: list[QuestionOption] = Field(
        default_factory=list,
        description="选项（single/multiple 必填，其他题型可空）",
    )
    answer: list[str] = Field(
        default_factory=list,
        description="正确答案。single/judge 为单个 value；multiple 为多个 value；fill 为可接受答案列表；short_answer 为空（由 analysis 给参考）",
    )
    analysis: str = Field(
        default="",
        description="答案解析（判分后展示，也是简答题的参考要点）",
    )
    points: int = Field(10, ge=0, le=100, description="分值")
    comment_prompt: str = Field(
        default="",
        description="简答题评分要点/量规（short_answer 使用，供 LLM 判分参考）",
    )
    knowledge_point: str = Field(
        default="",
        description="关联知识点名称（用于回填图谱/标注）",
    )


class QuizGenerateRequest(BaseModel):
    """出题请求"""
    subject: str = Field("", max_length=100, description="出题主题/知识点，如'二叉树遍历'")
    node_id: Optional[int] = Field(
        None,
        description="限定检索的知识库文件/文件夹节点 ID（None=检索全部上传文档）",
    )
    question_count: int = Field(5, ge=1, le=20, description="出题数量")
    difficulty: str = Field("medium", description="难度: easy/medium/hard")
    question_types: list[str] = Field(
        default_factory=lambda: ["single", "multiple", "judge", "fill", "short_answer"],
        description="题型列表",
    )


class QuizGradeRequest(BaseModel):
    """判分请求"""
    question_id: Optional[int] = Field(None, description="题库中已保存的题目 id")
    question: str = Field("", description="题干（未存库时直接传入）")
    type: str = Field("single", description="题型")
    user_answer: str = Field(..., description="用户作答")
    answer: list[str] = Field(default_factory=list, description="正确答案")
    points: int = Field(10, ge=0, le=100, description="满分")
    analysis: str = Field("", description="参考答案/解析")
    comment_prompt: str = Field("", description="简答题评分要点")
