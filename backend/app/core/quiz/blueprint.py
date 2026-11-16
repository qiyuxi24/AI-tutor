"""出题蓝图（blueprint）：**先定「考什么」，再出题**。

要解决的问题
-----------
改动前的出题是"给个主题 + 一句『请聚焦不同考点』，让模型自由发挥"。实测后果：

* uid5 的 33 道题里 **6 道**是"下列说法正确的是"套式（同一句式反复用）；
* 跨题集出现 0.87 相似度的近重复（`quality._deduplicate` 只做去标点小写比对）；
* 同一个知识点可以连出 3 道"定义辨析"，而节点里另外 7 个小节一次没被考到
  （机械运动 8 节 / 17377 字，只出过 4 道题，覆盖率 7%）。

根因是**没有"考什么"这一层**：题量有数字、题型有清单、难度有枚举，唯独
"这 15 道题分别考哪几个考点"是空的，于是只能靠模型自觉。

这个模块做什么
-------------
把「15 道题」摊成「15 个槽位（slot）」，每个槽位钉死：

    Slot(cover_key, cover_label, type, points, bloom, option_count, difficulty)

`cover_key` 就是"考什么"：小节级考点用 `{node_id}/{section_id}`，
没有小节的用正文小标题 `{node_id}/#h2-{n}`。落到题行（`questions.cover_key`）之后：

* 去重从"题干文本像不像"升级为"**这个考点考过没有**"（可精确判断）；
* 凑不满时能回答"缺的是哪几个考点"（而不是"差了 3 道"）；
* 同一批题不可能撞考点 —— 轮转分配保证每个考点被覆盖一次后才轮到第二次。

分配算法（纯规则，**不用 LLM**）
-----------------------------
1. 候选考点 = 各小节 + （可选）正文小标题，按出现顺序；
2. 按 plan 里各题型的 count 从多到少排序（题多的先分，避免最后剩下的全是单选题）；
3. 轮转：`考点[i % len(候选)]`，i 按题型分组的槽位序号
   —— 保证"每个考点覆盖一次"优先于"某个题型凑够"；
4. 认知层次按配比表轮转填充（默认 30/20/20/10/10/10，取整后余数补给 understand 层）；
5. 候选考点少于槽位数时：先允许同一考点出不同题型的题（这是合法的"同点多角度"），
   仍不够才缩减槽位并如实报告缺口。

为什么不用 LLM 规划
------------------
考点来自小节标题 / 正文小标题 —— 这些是**已经算好的结构化数据**。让 LLM 再规划一遍
只会引入不确定性，且多一次付费调用。LLM 该介入的地方是"这一节里有哪几个侧面"，
而那件事 `kb/section_generator.py::_plan_sections` 在生成正文时已经做过了。

本模块只有纯函数：不读库、不调 LLM、不碰文件系统 —— 每个分支都能离线钉死。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.core.quiz.schema import (
    DEFAULT_BLOOM_MIX,
    DEFAULT_POINTS,
    OPTION_TYPES,
    BLOOM_LEVELS,
    PlanRow,
)

__all__ = [
    "Slot",
    "CandidatePoint",
    "Blueprint",
    "build_candidates",
    "allocate",
    "DEFAULT_OPTION_COUNT",
]

# 单选题/多选题的缺省选项数（教育测量学元分析建议 3，但中文干扰项通常需要 4 才够区分）
DEFAULT_OPTION_COUNT = 4

# 正文小标题识别：Markdown 的 `## xxx` / `### xxx`（只取二级，三级太碎）
_H2_RE = re.compile(r"^##\s+(?!#)(.+)$", re.MULTILINE)
# 标题噪声："1. 定义" / "一、概念" / "（二）性质" 这类序号前缀要去掉再比对。
# ⚠️ 分隔符是**必需**的：早期写成 `[\s\d一二三四五六七八九十]+[.、．)）:：]?\s*`
# （分隔符可选）会把 "3D 打印" 剥成 "D 打印"、"2048 游戏" 剥成 "游戏" —— 以数字
# 开头的**正文**被当成序号吃掉。现在要求"数字/汉字序号 + 分隔符"同时出现。
_HEADING_NOISE_RE = re.compile(
    r"^(?:"
    r"[0-9]+\s*[.、．)）:：]"          # 1. / 2、 / 3) / 4：  （阿拉伯数字 + 分隔符）
    r"|[一二三四五六七八九十百]+\s*[.、．)）:：]"   # 一、 / 二）  （汉字序号 + 分隔符）
    r"|[（(]\s*[0-9一二三四五六七八九十]+\s*[)）]"  # （一） / (2)  （括号包序号）
    r")\s*"
)


@dataclass(frozen=True)
class CandidatePoint:
    """一个候选考点（"可以考什么"）。"""

    cover_key: str          # 唯一键：`{node_id}/{section_id}` 或 `{node_id}/#h2-3`
    label: str              # 展示名（小节标题 / 小标题文本）
    section_id: str = ""    # 小节 id（正文小标题考点为空）
    chars: int = 0          # 该考点的可考内容长度（用于排序/裁剪）

    def as_dict(self) -> dict:
        return {"cover_key": self.cover_key, "label": self.label,
                "section_id": self.section_id, "chars": self.chars}


@dataclass(frozen=True)
class Slot:
    """一个出题槽位："在这个考点上出这么一道题"。

    生成时每个槽位一条独立请求（可并发、可单独重试、可单独补），
    所以"补题"不再是"再来一批"，而是"哪个槽位还空着就补哪个"。
    """

    index: int              # 1-based 序号（进 id：q1/q2…）
    cover_key: str
    cover_label: str
    type: str
    points: int = 10
    bloom: str = "understand"
    option_count: int = DEFAULT_OPTION_COUNT
    difficulty: str = "medium"
    section_id: str = ""

    def as_dict(self) -> dict:
        return {
            "index": self.index, "cover_key": self.cover_key,
            "cover_label": self.cover_label, "type": self.type,
            "points": self.points, "bloom": self.bloom,
            "option_count": self.option_count, "difficulty": self.difficulty,
            "section_id": self.section_id,
        }


@dataclass
class Blueprint:
    """一份出题蓝图：槽位清单 + 缺口信息。"""

    slots: list[Slot] = field(default_factory=list)
    candidates: list[CandidatePoint] = field(default_factory=list)
    # 因候选考点太少而**无法**安排的槽位数（>0 表示规格本身超出内容能覆盖的范围）
    shortfall: int = 0
    reason: str = ""

    @property
    def total(self) -> int:
        return len(self.slots)

    def by_type(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for s in self.slots:
            out[s.type] = out.get(s.type, 0) + 1
        return out

    def cover_keys(self) -> list[str]:
        """去重后的考点键（按首次出现顺序）—— 前端据此展示"覆盖了哪些考点"。"""
        seen: list[str] = []
        for s in self.slots:
            if s.cover_key not in seen:
                seen.append(s.cover_key)
        return seen


def _strip_heading(text: str) -> str:
    return _HEADING_NOISE_RE.sub("", (text or "").strip()).strip()


def build_candidates(*, node_id: str, sections: list[dict] | None,
                     body: str = "") -> list[CandidatePoint]:
    """从"小节清单 + 正文小标题"构造候选考点。

    参数:
        node_id:  图谱节点 id（拼 `cover_key` 用）
        sections: `kg.list_sections(node_id)` 的产物（`[{id,title,brief,...}]`）；
                  未小节化的老节点传 `None` / `[]`
        body:     节点正文（`kg.node_content_text(node_id)`）。有小节时只用来
                  补候选（小标题），无小节时全部靠它

    规则:
        1. **有小节就以小节为准**（小节是作者/生成器刻意划分的教学单元，
           比正文里的 `##` 更可信），小标题只在小节数不足时才补；
        2. 没有小节 → 正文 `## 二级标题` 各算一个考点；
        3. 连小标题都没有 → 整篇算**一个**考点（`{node_id}/#body`），
           此时蓝图无法分派，只能"同一考点多角度"；
        4. 去重口径是 **`cover_key`**（`{node_id}/{section_id}`）：同一 section_id
           重复出现只留第一条；不同 section_id 但同标题的两节**各留一条**
           （它们是两个教学单元，标题撞车不代表内容重复）。
    """
    node = str(node_id or "")
    out: list[CandidatePoint] = []
    seen: set[str] = set()

    for sec in (sections or []):
        sid = str(sec.get("id") or "").strip()
        title = _strip_heading(str(sec.get("title") or ""))
        if not title:
            continue
        key = f"{node}/{sid}" if sid else f"{node}/#sec-{len(out) + 1}"
        if key in seen:
            continue
        seen.add(key)
        out.append(CandidatePoint(cover_key=key, label=title, section_id=sid,
                                  chars=int(sec.get("chars") or 0)))

    if out:
        # 小节已覆盖 → 小标题只作补充（不做，避免把"小节"与"小标题"混成两级结构）
        return out

    text = body or ""
    for i, m in enumerate(_H2_RE.finditer(text), 1):
        title = _strip_heading(m.group(1))
        if not title:
            continue
        key = f"{node}/#h2-{i}"
        if key in seen:
            continue
        seen.add(key)
        out.append(CandidatePoint(cover_key=key, label=title, chars=0))

    if out:
        return out

    # 兜底：整篇一个考点。此时 `chars` 记正文长度，供上层判断"够不够出题"
    return [CandidatePoint(cover_key=f"{node}/#body", label="整体",
                           chars=len(text))]


def _bloom_sequence(total: int, mix: dict[str, float] | None = None) -> list[str]:
    """按配比表生成 `total` 个认知层次（取整后余数补给 understand 层）。

    配比表是数据不是分支代码 —— 换构成 = 换一张表（对齐 Ragas 的
    `default_query_distribution`：权重表 + 归一到 1）。
    """
    mix = mix or DEFAULT_BLOOM_MIX
    weights = [(k, float(mix.get(k, 0) or 0)) for k in BLOOM_LEVELS]
    s = sum(w for _, w in weights)
    if s <= 0:
        return ["understand"] * total
    weights = [(k, w / s) for k, w in weights]

    counts = {k: int(total * w) for k, w in weights}
    remain = total - sum(counts.values())
    # 余数补给 understand（中位层次，最不容易出偏）；配比里没有它就补给第一个非零项
    top_up = "understand" if any(k == "understand" and w > 0 for k, w in weights) \
        else next((k for k, w in weights if w > 0), "understand")
    counts[top_up] = counts.get(top_up, 0) + remain

    seq: list[str] = []
    # 交错排列而不是"先 5 个 remember 再 3 个 understand" —— 交错能让
    # 每个考点都摊到不同层次，避免"前面几节全是记忆题、后面全是应用题"
    pools = {k: c for k, c in counts.items() if c > 0}
    while sum(pools.values()) > 0:
        for k in BLOOM_LEVELS:
            if pools.get(k, 0) > 0 and len(seq) < total:
                seq.append(k)
                pools[k] -= 1
    return seq[:total]


def allocate(*, node_id: str, plan: list[PlanRow], candidates: list[CandidatePoint],
             difficulty: str = "medium",
             bloom_mix: dict[str, float] | None = None) -> Blueprint:
    """把 `plan`（每种题型几道）摊到候选考点上，产出槽位清单。

    参数:
        node_id:     图谱节点 id（仅供 `reason` 展示）
        plan:        `schema.PlanRow` 清单（**已通过互斥/合计校验**）
        candidates:  `build_candidates(...)` 的产物；空 → 兜底成一个"整体"考点
        difficulty:  整卷难度（进每个 slot；本期不做逐题难度）
        bloom_mix:   认知层次配比（None = `schema.DEFAULT_BLOOM_MIX`）

    分配顺序（**题多的题型先分**）:
        以 `[single×5, judge×2]` 为例 —— 先铺 5 个单选题的考点，再铺 2 个判断题。
        反过来（题少的先分）会让最后剩下的全是单选题，而单选题在长正文里最容易
        撞同一考点，覆盖率反而差。

    返回:
        `Blueprint`。`shortfall > 0` 表示 plan 要求的题数超出候选考点能承载的量
        （此时 slots 仍然产出 —— 同一考点换题型是合法的"同点多角度"，
        由调用方按 `strict` 决定是否拒绝）。
    """
    cands = list(candidates) or [CandidatePoint(cover_key=f"{node_id}/#body",
                                                label="整体")]
    if not plan:
        return Blueprint(slots=[], candidates=cands, shortfall=0,
                         reason="未给规格（question_plan 为空）")

    # 题多的题型先分；同 count 时按 schema.QUIZ_TYPES 顺序保持稳定（可复现）
    order = {t: i for i, t in enumerate(("single", "multiple", "judge",
                                         "fill", "short_answer"))}
    rows = sorted(plan, key=lambda r: (-r.count, order.get(r.type, 99)))

    total = sum(r.count for r in rows)
    blooms = _bloom_sequence(total, bloom_mix)

    slots: list[Slot] = []
    cursor = 0          # 全局递增 → 轮转分配（保证覆盖优先）
    used: dict[tuple[str, str], int] = {}   # (cover_key, type) → 已排几道

    for row in rows:
        option_count = row.option_count or DEFAULT_OPTION_COUNT
        points = row.resolved_points if row.points is None else row.points
        for _ in range(row.count):
            # 轮转复用是**允许**的（同一考点换题型 = 同点多角度，这是好事）；
            # 但"同考点同题型出两道"就是规格超出内容承载能力了，记进 shortfall。
            cand = cands[cursor % len(cands)]
            cursor += 1
            key = (cand.cover_key, row.type)
            used[key] = used.get(key, 0) + 1
            slots.append(Slot(
                index=len(slots) + 1,
                cover_key=cand.cover_key,
                cover_label=cand.label,
                type=row.type,
                points=points,
                bloom=row.bloom or blooms[len(slots) % len(blooms)],
                option_count=option_count if row.type in OPTION_TYPES else 0,
                difficulty=difficulty,
                section_id=cand.section_id,
            ))

    # shortfall = 同考点同题型被排了第二次及以上的槽位数。
    # 这才是"规格超出内容能覆盖的范围"的直接度量 ——
    # 早期写成 `total - len(cands) * 不同题型数`，会让 `[single×4, judge×1]`
    # 配 3 个候选（明明有一个考点被同一题型问了两次）算出 0，上层据此判断
    # "缺哪些考点"就会漏报。
    shortfall = sum(max(0, n - 1) for n in used.values())

    covered = len({s.cover_key for s in slots})
    reason = (f"{total} 道 / {len(cands)} 个候选考点，实际覆盖 {covered} 个")
    if shortfall:
        reason += f"（{shortfall} 道与已排题同考点同题型，内容承载不足）"

    return Blueprint(slots=slots, candidates=cands, shortfall=shortfall, reason=reason)
