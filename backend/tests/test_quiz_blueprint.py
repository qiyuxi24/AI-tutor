"""出题蓝图（`app/core/quiz/blueprint.py`）的纯函数回归。

钉的是本次改造的**目的**：把"出 N 道题"摊成 N 个槽位，每个槽位钉死"考什么"
（`cover_key`）。改动前只给模型一句"请聚焦不同考点"，实测同一节点连出 3 道
"定义辨析"、另外 7 个小节一次没被考到。所以这里的核心断言都是**覆盖性质**：
轮转不撞考点、题多的题型先分、同一考点在候选不足时才复用。

`allocate` 与 `build_candidates` 都是纯函数（不读库、不调 LLM、不碰文件系统），
每个分支都能离线钉死。
"""

import pytest

from app.core.quiz.blueprint import (
    DEFAULT_OPTION_COUNT,
    Blueprint,
    CandidatePoint,
    Slot,
    allocate,
    build_candidates,
)
from app.core.quiz.schema import DEFAULT_BLOOM_MIX, DEFAULT_POINTS, PlanRow


# ─── 构造辅助 ────────────────────────────────────────────────────────


def _cands(n: int, node: str = "n1") -> list[CandidatePoint]:
    """n 个候选考点，cover_key 形如 `n1/s0`…（顺序即轮转顺序）。"""
    return [CandidatePoint(cover_key=f"{node}/s{i}", label=f"考点{i}", section_id=f"s{i}")
            for i in range(n)]


# ─── build_candidates：四个来源分支 ──────────────────────────────────


def test_build_candidates_one_per_section_with_section_key():
    """有小节时：每个小节一个候选，`cover_key` = `{node_id}/{section_id}`。

    `cover_key` 落到题行（`questions.cover_key`）后，"这个考点考过没有"才是
    可精确判断的 —— 这是整条去重/补题链路的地基，键的形状不能改。
    """
    sections = [
        {"id": "s1", "title": "定义", "chars": 800},
        {"id": "s2", "title": "性质", "chars": 600},
    ]
    out = build_candidates(node_id="n1", sections=sections)
    assert [c.cover_key for c in out] == ["n1/s1", "n1/s2"]
    assert [c.section_id for c in out] == ["s1", "s2"]
    assert [c.label for c in out] == ["定义", "性质"]
    assert [c.chars for c in out] == [800, 600]


def test_build_candidates_strips_heading_number_prefix():
    """标题里的序号前缀（`"1. 定义"` / `"一、概念"` / `"（二）性质"`）被剥掉。

    小节标题来自自动生成，常带"1."/"一、"；不剥掉的话同一节换个编号就成了
    "另一个考点"，去重与展示都会失效。`label` 只留可读文本。

    三种写法都要剥：阿拉伯数字 + 分隔符、汉字序号 + 分隔符、括号包序号。
    """
    sections = [
        {"id": "s1", "title": "1. 定义"},
        {"id": "s2", "title": "一、概念"},
        {"id": "s3", "title": "（二） 性质"},
    ]
    out = build_candidates(node_id="n1", sections=sections)
    assert [c.label for c in out] == ["定义", "概念", "性质"]
    # 剥离只影响 label，键仍然是 node_id/section_id
    assert [c.cover_key for c in out] == ["n1/s1", "n1/s2", "n1/s3"]


def test_build_candidates_keeps_leading_digits_that_are_not_ordinals():
    """以数字开头的**正文**标题不能被当成序号剥掉（回归）。

    早期正则把分隔符写成可选（`[\\s\\d…]+[.、．)）:：]?`），于是
    `"3D 打印"` 被剥成 `"D 打印"`、`"2048 游戏"` 被剥成 `"游戏"` ——
    考点名直接变成错的，还会作为 `cover_label` 透到前端和题行。
    修法是要求"数字 + 分隔符"同时出现。
    """
    sections = [
        {"id": "s1", "title": "3D 打印"},
        {"id": "s2", "title": "2048 游戏"},
        {"id": "s3", "title": "2 的幂运算"},
    ]
    out = build_candidates(node_id="n1", sections=sections)
    assert [c.label for c in out] == ["3D 打印", "2048 游戏", "2 的幂运算"]


def test_build_candidates_dedupes_by_cover_key_not_by_title():
    """去重口径是 `cover_key`（含 section_id），**不是**标题文本。

    同 id 的重复行会并成一条；不同 id 但同标题的两节会各留一条 ——
    小节是"作者/生成器刻意划分的教学单元"，两节同名也是两节内容。
    （注意：模块 docstring 第 174 行写的是"同一标题反复出现只留第一个"，
    与实现不一致，见本轮结论里的实现问题说明。）
    """
    # 同一 section_id 重复出现 → 只留第一个
    dup_id = [
        {"id": "s1", "title": "1. 定义", "chars": 100},
        {"id": "s1", "title": "定义", "chars": 999},
    ]
    out = build_candidates(node_id="n1", sections=dup_id)
    assert len(out) == 1
    assert out[0].chars == 100, "应保留先出现的那条（含其 chars）"

    # 不同 section_id、同 title → 两条都在（按实现如此，不是 bug 而是口径）
    dup_title = [
        {"id": "s1", "title": "定义"},
        {"id": "s2", "title": "定义"},
    ]
    out2 = build_candidates(node_id="n1", sections=dup_title)
    assert [c.cover_key for c in out2] == ["n1/s1", "n1/s2"]


def test_build_candidates_skips_blank_titles_and_falls_back_for_missing_id():
    """标题被剥成空串的小节跳过；缺 `id` 的小节用 `#sec-N` 占位不漏。

    空标题的小节无法展示也无法"考什么"，纳入候选只会让槽位落在一个空 label 上。
    缺 id 时仍要占一个候选位（否则小节数 8、候选 7，题量会少一道）。
    """
    sections = [
        {"id": "s1", "title": "   "},
        {"id": "", "title": "无 id 的小节"},
        {"id": "s3", "title": "有 id 的小节"},
    ]
    out = build_candidates(node_id="n1", sections=sections)
    assert [c.cover_key for c in out] == ["n1/#sec-1", "n1/s3"]
    assert [c.label for c in out] == ["无 id 的小节", "有 id 的小节"]


def test_build_candidates_uses_h2_headings_when_no_sections():
    """无小节但有 `## 二级标题` 的正文 → 按小标题建候选，键为 `{node_id}/#h2-N`。

    未小节化的老节点只有一篇 MD；此时正文的 `## ` 就是天然的考点划分。
    序号 N 从 1 开始且连续，是 `question` 侧回填 cover_key 的唯一依据。
    """
    body = (
        "# 机械运动\n\n"
        "## 1. 定义\n\n内容一\n\n"
        "## 二、参考系\n\n内容二\n\n"
        "### 三级标题不算\n\n内容三\n"
    )
    out = build_candidates(node_id="n1", sections=None, body=body)
    assert [c.cover_key for c in out] == ["n1/#h2-1", "n1/#h2-2"]
    assert [c.label for c in out] == ["定义", "参考系"]
    assert all(c.section_id == "" for c in out), "小标题考点的 section_id 为空"


def test_build_candidates_ignores_body_headings_when_sections_exist():
    """有小节时**不再**补正文小标题（小节优先，避免两级结构混用）。

    小节的划分比正文 `##` 更可信；两者混在一个候选列表里会让覆盖统计出现
    "8 个小节 + 3 个小标题 = 11 个考点"这种虚高。实现直接 early-return。
    """
    sections = [{"id": "s1", "title": "定义"}]
    body = "## 正文小标题\n\n内容\n"
    out = build_candidates(node_id="n1", sections=sections, body=body)
    assert [c.cover_key for c in out] == ["n1/s1"], "有小节时不应出现 #h2-* 候选"


def test_build_candidates_falls_back_to_single_body_candidate():
    """无小节无小标题 → **恰好 1 个**候选、键以 `/#body` 结尾。

    此时蓝图无法分派考点，只能"同一考点多角度"；`chars` 记全文长度，
    供上层判断"够不够出题"。候选为 0 会让 `allocate` 的轮转除零。
    """
    out = build_candidates(node_id="n1", sections=[], body="没有小标题的纯正文。")
    assert len(out) == 1
    assert out[0].cover_key == "n1/#body"
    assert out[0].cover_key.endswith("/#body")
    assert out[0].label == "整体"
    assert out[0].chars == len("没有小标题的纯正文。")


# ─── allocate：覆盖性质（本次改造的目的）────────────────────────────


def test_allocate_rotates_candidates_so_no_cover_key_collides():
    """`[single×3, judge×2]` + 5 个候选 → 5 个 slot、**5 个不同 cover_key**。

    这条就是改造的目的本身：改动前"15 道题"没有任何机制保证不撞考点，
    实测同一节连出 3 道。轮转（`cursor % len(cands)`）保证"每个考点覆盖一次"
    优先于"某个题型凑够"。
    """
    bp = allocate(node_id="n1", plan=[PlanRow(type="single", count=3),
                                     PlanRow(type="judge", count=2)],
                  candidates=_cands(5))
    assert isinstance(bp, Blueprint)
    assert len(bp.slots) == 5
    keys = [s.cover_key for s in bp.slots]
    assert len(set(keys)) == 5, f"5 个槽位应覆盖 5 个不同考点，实际 {keys}"
    assert set(keys) == {f"n1/s{i}" for i in range(5)}
    assert bp.cover_keys() == keys[:5]
    assert bp.shortfall == 0


def test_allocate_slot_index_is_contiguous_from_one():
    """`slots` 的 `index` 是 1..N 连续 —— 它直接进题 id（q1/q2…）。

    跳号或重号会让"哪道题对应哪个槽位"对不上，补题时无法定位。
    """
    bp = allocate(node_id="n1", plan=[PlanRow(type="single", count=4),
                                     PlanRow(type="fill", count=3)],
                  candidates=_cands(9))
    assert [s.index for s in bp.slots] == list(range(1, 8))


def test_allocate_assigns_more_numerous_type_first():
    """**题多的题型先分**：`[single×4, judge×1]` + 3 候选 → 前 4 个 slot 都是 single。

    反过来（题少的先分）会让最后剩下的全是单选题，而单选题在长正文里最容易
    撞同一考点，覆盖率反而差 —— 这条顺序是刻意设计的，不能改成按传参顺序。
    """
    bp = allocate(node_id="n1", plan=[PlanRow(type="judge", count=1),
                                     PlanRow(type="single", count=4)],
                  candidates=_cands(3))
    assert [s.type for s in bp.slots] == ["single", "single", "single", "single", "judge"]
    assert bp.by_type() == {"single": 4, "judge": 1}


def test_allocate_option_count_only_for_option_types():
    """`option_count`：single/multiple 有值（缺省 4）；judge/fill/short_answer 为 0。

    判断题/填空题挂选项数是明确的用法错误（`PlanRow` 也会拒绝），
    槽位侧必须回 0 —— 生成时据此决定要不要产出 `options`。
    """
    bp = allocate(node_id="n1", plan=[
        PlanRow(type="single", count=1),
        PlanRow(type="multiple", count=1),
        PlanRow(type="judge", count=1),
        PlanRow(type="fill", count=1),
        PlanRow(type="short_answer", count=1),
    ], candidates=_cands(5))

    by_type = {s.type: s for s in bp.slots}
    assert by_type["single"].option_count == DEFAULT_OPTION_COUNT == 4
    assert by_type["multiple"].option_count == DEFAULT_OPTION_COUNT == 4
    assert by_type["judge"].option_count == 0
    assert by_type["fill"].option_count == 0
    assert by_type["short_answer"].option_count == 0


def test_allocate_respects_plan_row_option_count():
    """`PlanRow(type="single", count=2, option_count=3)` → slot.option_count == 3。

    选项数是可配的（教育测量学元分析建议 3），"缺省 4"不能被写死成唯一值。
    """
    bp = allocate(node_id="n1",
                  plan=[PlanRow(type="single", count=2, option_count=3)],
                  candidates=_cands(2))
    assert [s.option_count for s in bp.slots] == [3, 3]


def test_allocate_points_explicit_and_default_per_type():
    """`PlanRow(type="judge", count=1, points=25)` → slot.points == 25；
    不写 points 时按 `schema.DEFAULT_POINTS`（short_answer 20，其余 10）。

    分值直接进 `questions.points` 与判分满分，缺省值必须来自唯一真值表，
    不能在蓝图里另抄一份常量。
    """
    bp = allocate(node_id="n1", plan=[
        PlanRow(type="judge", count=1, points=25),
        PlanRow(type="single", count=1),
        PlanRow(type="short_answer", count=1),
    ], candidates=_cands(3))

    by_type = {s.type: s for s in bp.slots}
    assert by_type["judge"].points == 25
    assert by_type["single"].points == DEFAULT_POINTS["single"] == 10
    assert by_type["short_answer"].points == DEFAULT_POINTS["short_answer"] == 20


def test_allocate_bloom_levels_are_interleaved_not_clustered():
    """Bloom 交错而非聚集：`[single×6]` 里 `remember` ≤ 2 且至少 2 种层次。

    若实现是"先 remember×N 再 understand×M"，前几节会全是记忆题、后面全是
    应用题 —— 交错才能让每个考点摊到不同层次。默认配比 30/20/20/10/10/10
    在 total=6 时算出 remember=1、understand=4、apply=1。
    """
    bp = allocate(node_id="n1", plan=[PlanRow(type="single", count=6)],
                  candidates=_cands(6))
    blooms = [s.bloom for s in bp.slots]
    assert len(blooms) == 6
    assert blooms.count("remember") <= 2
    assert len(set(blooms)) >= 2
    # 交错：remember 不应把前面 2 个槽位全占了（聚集写法会出现 blooms[:2] 全同）
    assert len(set(blooms[:3])) >= 2
    # 配比总权重仍是 1（表本身是数据，改表要改这里）
    assert pytest.approx(sum(DEFAULT_BLOOM_MIX.values()), abs=1e-9) == 1.0


def test_allocate_bloom_uses_only_understand_when_mix_is_zero():
    """配比表全 0（或空 dict）→ 全部落到 understand，不除零、不返回空序列。

    调用方可能传 `{}`（表示"不指定"）；`_bloom_sequence` 必须兜住，
    否则 `blooms[len(slots) % 0]` 会 ZeroDivisionError。
    """
    bp = allocate(node_id="n1", plan=[PlanRow(type="single", count=3)],
                  candidates=_cands(3), bloom_mix={})
    assert [s.bloom for s in bp.slots] == ["understand"] * 3


def test_allocate_still_emits_all_slots_but_reports_shortfall():
    """候选数少于题数时**仍然产出全部 slot**（同考点复用合法），并报 `shortfall > 0`。

    这是"允许同点多角度"与"如实报告缺口"的分界：5 道题只有 2 个考点时，
    所有槽位照出（由调用方按 `strict` 决定是否整体失败），但 `shortfall` 必须
    > 0 让上层知道"规格超出内容能承载的范围"。

    `shortfall` 的口径 = **同考点同题型被排了第二次及以上的槽位数**。
    早期实现写成 `total - len(cands) * 不同题型数`，会让
    `[single×4, judge×1]` 配 3 个候选（明明有一个考点被同一题型问了两次）
    算出 0，上层据此判断"缺哪些考点"就会漏报。
    """
    bp = allocate(node_id="n1", plan=[PlanRow(type="single", count=5)],
                  candidates=_cands(2))
    assert len(bp.slots) == 5, "候选不足也要产出全部槽位"
    assert bp.shortfall > 0
    assert bp.shortfall == 3
    assert len(bp.cover_keys()) == 2, "复用后仍只有 2 个不同考点"
    # 轮转模式：0,1,0,1,0
    assert [s.cover_key for s in bp.slots] == ["n1/s0", "n1/s1", "n1/s0",
                                               "n1/s1", "n1/s0"]
    assert "内容承载不足" in bp.reason


def test_allocate_shortfall_counts_per_type_not_per_candidate():
    """`shortfall` 按"同考点同题型"计，不是"题数 - 候选数"（回归）。

    `[single×4, judge×1]` 配 3 个候选：单选题在这 3 个考点上轮转，第 4 道
    必须复用 s0 → 1 道 shortfall；判断题只 1 道，不产生 shortfall。
    总计 1。若按旧口径 `total - len(cands)*types = 5 - 3*2 = -1 → 0`，
    会错误地报告"完全满足"。
    """
    bp = allocate(node_id="n1",
                  plan=[PlanRow(type="single", count=4), PlanRow(type="judge", count=1)],
                  candidates=_cands(3))
    assert len(bp.slots) == 5
    assert bp.shortfall == 1
    single_keys = [s.cover_key for s in bp.slots if s.type == "single"]
    assert single_keys[:3] == ["n1/s0", "n1/s1", "n1/s2"], "前 3 道单选应各覆盖一个考点"
    assert single_keys[3] == "n1/s0", "第 4 道单选才复用"


def test_allocate_empty_plan_yields_empty_blueprint_with_reason():
    """`plan=[]` → `Blueprint.total == 0`、`reason` 非空。

    老路径（只给 `question_types`）走的就是空 plan，此时蓝图不参与分派；
    但 `reason` 不能说空话，否则日志里分不清"没给规格"和"规格被算成 0 道"。
    """
    bp = allocate(node_id="n1", plan=[], candidates=_cands(3))
    assert bp.total == 0
    assert bp.slots == []
    assert bp.reason.strip() != ""
    assert bp.shortfall == 0
    assert len(bp.candidates) == 3, "plan 为空也要保留候选清单（供预览展示）"


def test_allocate_with_no_candidates_falls_back_to_single_body_point():
    """`candidates=[]` → 兜底成一个 `{node_id}/#body` 考点，不除零。

    节点刚建、正文还没生成时调用方可能拿到空候选；`cursor % 0` 会直接崩，
    所以必须有兜底考点（这也让"整篇一个考点"的退化路径始终可用）。
    """
    bp = allocate(node_id="n1", plan=[PlanRow(type="single", count=2)],
                  candidates=[])
    assert len(bp.slots) == 2
    assert [s.cover_key for s in bp.slots] == ["n1/#body", "n1/#body"]
    assert bp.candidates[0].cover_key == "n1/#body"
    assert bp.candidates[0].label == "整体"


def test_allocate_propagates_difficulty_into_every_slot():
    """整卷难度写进每个 slot（本期不做逐题难度），且 section_id 跟随候选。

    难度与知识点要一路带到入库侧（`questions.difficulty` / `knowledge_point`），
    中途丢字段会让题目落到库里却没有难度标签。
    """
    bp = allocate(node_id="n1", plan=[PlanRow(type="single", count=2)],
                  candidates=_cands(2), difficulty="hard")
    assert [s.difficulty for s in bp.slots] == ["hard", "hard"]
    assert [s.section_id for s in bp.slots] == ["s0", "s1"]
    assert [s.cover_label for s in bp.slots] == ["考点0", "考点1"]


# ─── Blueprint 的形状方法 ────────────────────────────────────────────


def test_blueprint_by_type_counts_slots():
    """`by_type()` → `{题型: 槽位数}`，只统计实际产出的槽位。

    前端据此显示"单选 3 / 判断 2"，用于核对是否与 `question_plan` 一致。
    """
    bp = allocate(node_id="n1", plan=[PlanRow(type="single", count=3),
                                     PlanRow(type="judge", count=2)],
                  candidates=_cands(5))
    assert bp.by_type() == {"single": 3, "judge": 2}


def test_blueprint_cover_keys_dedupes_preserving_first_order():
    """`cover_keys()` → 去重后的考点键，**按首次出现顺序**。

    前端按这个顺序展示"覆盖了哪些考点"，顺序即轮转顺序；用 set 会丢顺序，
    用户看到的考点列表每次刷新都变。
    """
    bp = allocate(node_id="n1", plan=[PlanRow(type="single", count=5)],
                  candidates=_cands(2))
    assert bp.cover_keys() == ["n1/s0", "n1/s1"]
    assert isinstance(bp.cover_keys(), list)
    # 每个键在 slots 里确实出现过
    assert {s.cover_key for s in bp.slots} == set(bp.cover_keys())


def test_slot_and_candidate_as_dict_shapes():
    """`Slot.as_dict()` / `CandidatePoint.as_dict()` 的键集合是契约。

    两者都要经 SSE/JSON 传给前端（槽位清单 + 候选考点预览），
    少一个键前端就少一列；多一层嵌套会破坏前端已有的解析。
    """
    slot = Slot(index=1, cover_key="n1/s1", cover_label="定义", type="single")
    d = slot.as_dict()
    assert set(d) == {"index", "cover_key", "cover_label", "type", "points",
                      "bloom", "option_count", "difficulty", "section_id"}
    assert d["index"] == 1 and d["option_count"] == DEFAULT_OPTION_COUNT

    cand = CandidatePoint(cover_key="n1/s1", label="定义", section_id="s1", chars=12)
    cd = cand.as_dict()
    assert set(cd) == {"cover_key", "label", "section_id", "chars"}
    assert cd == {"cover_key": "n1/s1", "label": "定义", "section_id": "s1", "chars": 12}
