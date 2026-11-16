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

出题规格（2026-11-16，借鉴 tldw_server 的 `question_plan`）：
  `question_plan` 把「允许哪些题型」（集合）升级为「每种各几道」（带数量的清单）。
  集合的契约是"最多这些类型"，模型的自由发挥无从验收；清单的契约是"每类恰好 N 道"，
  可逐条核对、缺口可定位。两者**互斥**，老客户端继续走 `question_types`。
"""

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# 全部合法题型（唯一清单；提示词/校验/前端都应从这里派生，别再各写一份）
QUIZ_TYPES: tuple[str, ...] = ("single", "multiple", "judge", "fill", "short_answer")
# 需要选项的题型（`option_count` 只对它们有意义）
OPTION_TYPES: tuple[str, ...] = ("single", "multiple")
# 认知层次（软标签，只用于蓝图配比，**不做语义校验**）
BLOOM_LEVELS: tuple[str, ...] = ("remember", "understand", "apply",
                                 "analyze", "evaluate", "create")
# 缺省认知层次配比（教育测量学常见口径：记忆/理解/应用/分析/评价/创造 = 30/20/20/10/10/10）
DEFAULT_BLOOM_MIX: dict[str, float] = {
    "remember": 0.30, "understand": 0.20, "apply": 0.20,
    "analyze": 0.10, "evaluate": 0.10, "create": 0.10,
}
# 各题型的缺省分值（简答题作答成本高，给 20；其余 10 —— 与历史全 10 分保持兼容）
DEFAULT_POINTS: dict[str, int] = {
    "single": 10, "multiple": 10, "judge": 10, "fill": 10, "short_answer": 20,
}


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


class PlanRow(BaseModel):
    """出题规格的一行：**某种题型出几道**（借鉴 tldw_server 的 `question_plan`）。

    与旧的 `question_types: list[str]` 的差别是契约强度：
    `question_types=["single","judge"]` 只表示"允许这两型"，模型爱出几道出几道；
    `[PlanRow(type="single", count=5), PlanRow(type="judge", count=2)]` 表示
    "单选 5 道、判断 2 道，合计 7 道" —— 可逐条核对、缺口可定位到行。

    `extra="forbid"` 是刻意的：拼错字段名（如 `optionCount`）应当报错，
    而不是被静默忽略后按默认值出题（那种失败最贵 —— 结果看起来"正常"）。
    """

    model_config = ConfigDict(extra="forbid")

    type: str = Field(..., description="题型: single/multiple/judge/fill/short_answer")
    count: int = Field(..., ge=1, le=20, description="该题型出几道")
    option_count: Optional[int] = Field(
        None, ge=2, le=6,
        description="选项数（**仅** single/multiple 可填）。单选缺省 4；"
                    "教育测量学元分析（Rodriguez 2005）建议 3 选项，可按需配 3",
    )
    points: Optional[int] = Field(
        None, ge=0, le=100,
        description="每题分值；缺省按 schema.DEFAULT_POINTS 的题型缺省值",
    )
    bloom: Optional[str] = Field(
        None, description="认知层次软标签（remember…create）；留空由蓝图按配比分配",
    )

    @field_validator("type")
    @classmethod
    def _check_type(cls, v: str) -> str:
        if v not in QUIZ_TYPES:
            raise ValueError(f"未知题型 {v!r}，合法值：{'/'.join(QUIZ_TYPES)}")
        return v

    @field_validator("bloom")
    @classmethod
    def _check_bloom(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in BLOOM_LEVELS:
            raise ValueError(f"未知认知层次 {v!r}，合法值：{'/'.join(BLOOM_LEVELS)}")
        return v

    @model_validator(mode="after")
    def _check_option_count(self) -> "PlanRow":
        # 判断题/填空题挂 option_count 是明确的用法错误（tldw 同样拒绝），
        # 静默忽略会让调用方以为"3 选项生效了"
        if self.option_count is not None and self.type not in OPTION_TYPES:
            raise ValueError(
                f"题型 {self.type} 不支持 option_count（只有 {'/'.join(OPTION_TYPES)} 支持）")
        return self

    @property
    def resolved_points(self) -> int:
        """实际分值：显式给了就用，否则按题型缺省。"""
        return self.points if self.points is not None else DEFAULT_POINTS.get(self.type, 10)


class PlanPreviewRequest(BaseModel):
    """题量预览请求（**纯计算，不调 LLM、不落库**）。

    前端「自动」模式打开页面就打一次，把"15 道 · 8 个小节 · 17377 字"显示出来。
    用户不信任一个不解释的数字 —— 这正是旧的 `[3,5,8,10]` 下拉的问题所在。
    """

    subject: str = Field("", max_length=100, description="出题主题（无图谱节点时用于检索估算）")
    graph_node_id: str = Field("", description="图谱节点 id；给了就按小节数 + 正文长度精确算")
    count_mode: str = Field("auto", description="auto/manual/plan")
    question_count: Optional[int] = Field(None, ge=1, le=20, description="count_mode=manual 时的题数")
    question_plan: Optional[list[PlanRow]] = Field(
        None, description="count_mode=plan 时的清单；给了就以它合计为总数")

    @field_validator("count_mode")
    @classmethod
    def _check_mode(cls, v: str) -> str:
        if v not in ("auto", "manual", "plan"):
            raise ValueError("count_mode 只能是 auto/manual/plan")
        return v

    @model_validator(mode="after")
    def _check_mode_payload(self) -> "PlanPreviewRequest":
        if self.count_mode == "manual" and self.question_count is None:
            raise ValueError("count_mode=manual 时必须给 question_count")
        if self.count_mode == "plan" and not self.question_plan:
            raise ValueError("count_mode=plan 时必须给 question_plan")
        return self


class QuizGenerateRequest(BaseModel):
    """出题请求

    **两条互斥路径**（`model_validator` 保证）：
      · 新路径：`question_count` + `question_plan`（带数量的清单，可验收）
      · 老路径：`question_types`（题型集合，行为与改动前逐字节等价）

    同时给 `question_plan` 与**非默认** `question_types` 会报错 —— 静默选一个
    等于让调用方以为另一个生效了。
    """

    subject: str = Field("", max_length=100, description="出题主题/知识点，如'二叉树遍历'")
    node_id: Optional[int] = Field(
        None,
        description="限定检索的知识库文件/文件夹节点 ID（None=检索全部上传文档）",
    )
    question_count: int = Field(5, ge=1, le=20, description="出题数量")
    difficulty: str = Field("medium", description="难度: easy/medium/hard")
    question_types: list[str] = Field(
        default_factory=lambda: ["single", "multiple", "judge", "fill", "short_answer"],
        description="题型列表（老路径）。**给了 question_plan 时不要再传**",
    )
    question_plan: Optional[list[PlanRow]] = Field(
        None,
        description="出题规格清单：每种题型各几道。合计必须等于 question_count。"
                    "给了它就按规格生成，凑不齐按 strict 决定是否整体失败",
    )
    count_mode: str = Field(
        "manual",
        description="题量来源：auto（按内容预算算）/manual（question_count）"
                    "/plan（question_plan 合计）。仅用于回显与审计，不改变生成逻辑",
    )
    strict: bool = Field(
        False,
        description="凑不齐是否整体失败。**默认 False**：缺少量（≤20%）仍交付，"
                    "但响应里 satisfied=false 并列出缺哪些考点；True 时一道都不落库",
    )
    graph_node_id: str = Field(
        "",
        description="出题针对的图谱节点 id（从节点详情进来时传）。入库时覆盖题目的 "
                    "knowledge_point，判分据此回写该节点掌握度；空=不关联图谱",
    )
    missing_cover_keys: list[str] = Field(
        default_factory=list,
        description="只补这些考点（上次出题没铺到的 cover_key）。给了就把候选考点"
                    "限制在这几个上，题量仍由 question_count 决定 —— 用于「补齐缺失」，"
                    "不是重出整卷",
    )

    @field_validator("missing_cover_keys")
    @classmethod
    def _check_missing_keys(cls, v: list[str]) -> list[str]:
        # 去空、去重、保序：前端可能把 `#body` 这种空 section_id 也带上来
        out: list[str] = []
        for k in v or []:
            s = (k or "").strip()
            if s and s not in out:
                out.append(s)
        return out

    @field_validator("question_types")
    @classmethod
    def _check_types(cls, v: Optional[list[str]]) -> Optional[list[str]]:
        if v is None:
            return None
        if not v:
            raise ValueError("question_types 不能是空列表（不选题型请用 question_plan）")
        bad = [t for t in v if t not in QUIZ_TYPES]
        if bad:
            raise ValueError(f"未知题型：{'、'.join(bad)}")
        if len(set(v)) != len(v):
            raise ValueError("question_types 有重复项")
        return v

    @field_validator("count_mode")
    @classmethod
    def _check_count_mode(cls, v: str) -> str:
        if v not in ("auto", "manual", "plan"):
            raise ValueError("count_mode 只能是 auto/manual/plan")
        return v

    @model_validator(mode="after")
    def _check_plan(self) -> "QuizGenerateRequest":
        # 老路径没有"考点"概念，给了 missing_cover_keys 只能被静默忽略 —— 那正是
        # 这个字段原本踩过的坑（前端传了、后端 Pydantic 默默吞掉、行为却是重出整卷）。
        # 所以显式拒绝，让调用方立刻看到自己走错了路径。
        if self.missing_cover_keys and not self.question_plan:
            raise ValueError("missing_cover_keys 只在 question_plan 路径下有效"
                             "（老路径按 question_types 自由出题，没有考点概念）")
        if self.question_plan is None:
            return self
        # 重复题型：tldw 在 v1 也拒绝（"Duplicate question_type rows are rejected"）。
        # 理由：两行同类型无法判断"5 道单选"是指 5 还是 10，蓝图分配也无从对照。
        seen = [r.type for r in self.question_plan]
        dup = sorted({t for t in seen if seen.count(t) > 1})
        if dup:
            raise ValueError(f"question_plan 里题型重复：{'、'.join(dup)}（每型只能一行）")
        # 显式传了 question_types = 两套契约打架。
        # 用 `model_fields_set` 而不是 `is not None` —— `question_types` 有
        # default_factory，永远非 None；只有"调用方真的传了这个字段"才算冲突。
        if "question_types" in self.model_fields_set:
            raise ValueError("question_plan 与 question_types 互斥，只能传一个")
        total = sum(r.count for r in self.question_plan)
        if total != self.question_count:
            raise ValueError(
                f"question_plan 合计 {total} 道与 question_count {self.question_count} 不一致")
        return self

    @property
    def effective_plan(self) -> list["PlanRow"]:
        """归一成一份清单：新路径原样返回，老路径返回**空列表**。

        空列表 = "没给规格"，生成器走原有分支（`question_types` 原样传给
        `_types_for_batch` 逐批切分）。**不要**在这里把老路径等分成 PlanRow ——
        那会悄悄改掉既有行为，而老路径必须与改动前逐字节等价。
        """
        return list(self.question_plan) if self.question_plan else []


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
