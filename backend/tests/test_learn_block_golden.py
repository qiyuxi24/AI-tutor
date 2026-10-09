"""P1-5（Phase B2）等价重构的 **golden 基线**：`_build_learn_block` 注入文本必须逐字符不变。

为什么用 golden 而不是行为断言
------------------------------
B2 只做一件事：把"这一轮该怎么教"的判定从**提示词渲染**里抽到 `core/teaching/policy.py`，
**不改行为**。而"行为"在这里就等于"注入给模型的那段文本" —— 差一个空格、一个标点都可能
让模型换一种讲法，而真机采样要等 10/15 之后才有时间做。所以判据只能是**逐字符相等**。

做法（三步，顺序不能反）
  1. 先固化一批**小节状态场景**（本文件 `SCENARIOS`），覆盖每个状态分支 / 起点理由分支 /
     开场轮 / 正文截断 / 空正文 / 老节点无小节；
  2. 用**改造前**的实现把基线写进 `fixtures/learn_block_golden.json`
     （显式开关 `UPDATE_LEARN_BLOCK_GOLDEN=1`，默认只读）；
  3. 改造后断言逐字符相等 —— 只有它是绿的才允许提交。

⚠️ 基线更新是**显式**动作：默认只读，防止"顺手把基线改成新行为"来换绿。
`test_learn_mode.py` 里那 10 条 `test_block_*` 是**行为**断言，本次重构不许删改（见夜间作业单 P1-5）。

全离线：tmp_path 真实 KnowledgeGraph，不碰 LLM。
"""
import json
import os
from pathlib import Path

import pytest

from app.core.knowledge_graph import (
    LEARN_MARK_BY_AI,
    LEARN_MARK_BY_USER,
    LEARN_MARK_CONFUSED,
    LEARN_MARK_UNDERSTOOD,
    KnowledgeGraph,
)
from app.services.chat_service import _build_learn_block

USER_ID = 71
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "learn_block_golden.json"
UPDATE_ENV = "UPDATE_LEARN_BLOCK_GOLDEN"

NODE_NAME = "二重积分"
DEFAULT_BODY = "内容" * 40                      # 80 字，短于截断阈值（900）

# ── 场景表：只描述"图谱长什么样"，不描述"文本应该长什么样"（后者由基线负责）──
# sections: 依次建的小节 {title, content}
# learn:    随后施加的小节学习状态 {id, mark?, mark_by?, read?, passed?}
# openers:  该场景渲染哪几种轮次（True=开场轮）
SCENARIOS = [
    {
        "name": "legacy_no_sections",
        "sections": [],
        "learn": [],
        "openers": [False, True],
    },
    {
        "name": "untouched_two",
        "sections": [{"title": "定义与几何意义"}, {"title": "计算例题"}],
        "learn": [],
        "openers": [False, True],
    },
    {
        "name": "first_passed_second_understood_self",
        "sections": [{"title": "第一节"}, {"title": "第二节"}],
        "learn": [
            {"id": "s01", "passed": True},
            {"id": "s02", "mark": LEARN_MARK_UNDERSTOOD, "mark_by": LEARN_MARK_BY_USER},
        ],
        "openers": [False, True],
    },
    {
        "name": "ai_understood_is_start",
        "sections": [{"title": "AI 已讲完"}, {"title": "已通过"}],
        "learn": [
            {"id": "s01", "mark": LEARN_MARK_UNDERSTOOD, "mark_by": LEARN_MARK_BY_AI},
            {"id": "s02", "passed": True},
        ],
        "openers": [False, True],
    },
    {
        "name": "confused_self_is_start",
        "sections": [{"title": "自评不懂"}, {"title": "已通过"}, {"title": "没看过"}],
        "learn": [
            {"id": "s01", "mark": LEARN_MARK_CONFUSED, "mark_by": LEARN_MARK_BY_USER},
            {"id": "s02", "passed": True},
        ],
        "openers": [False, True],
    },
    {
        "name": "confused_ai_downgrade_is_start",
        "sections": [{"title": "系统降级"}, {"title": "没看过"}],
        "learn": [{"id": "s01", "mark": LEARN_MARK_CONFUSED, "mark_by": LEARN_MARK_BY_AI}],
        "openers": [False, True],
    },
    {
        "name": "all_passed",
        "sections": [{"title": "第一节"}, {"title": "第二节"}],
        "learn": [{"id": "s01", "passed": True}, {"id": "s02", "passed": True}],
        "openers": [False, True],
    },
    {
        "name": "read_flags_all_kinds",
        "sections": [{"title": "已通过"}, {"title": "自评已懂"}, {"title": "自评不懂"},
                     {"title": "没看过"}],
        "learn": [
            {"id": "s01", "passed": True, "read": True},
            {"id": "s02", "mark": LEARN_MARK_UNDERSTOOD,
             "mark_by": LEARN_MARK_BY_USER, "read": True},
            {"id": "s03", "mark": LEARN_MARK_CONFUSED,
             "mark_by": LEARN_MARK_BY_USER, "read": True},
            {"id": "s04", "read": True},
        ],
        "openers": [False, True],
    },
    {
        "name": "five_sections_mixed_start",
        "sections": [{"title": "一"}, {"title": "二"}, {"title": "三"}, {"title": "四"},
                     {"title": "五"}],
        "learn": [
            {"id": "s01", "passed": True},
            {"id": "s02", "passed": True},
            {"id": "s03", "mark": LEARN_MARK_UNDERSTOOD, "mark_by": LEARN_MARK_BY_AI},
            {"id": "s04", "mark": LEARN_MARK_CONFUSED, "mark_by": LEARN_MARK_BY_AI},
        ],
        "openers": [False, True],
    },
    {
        "name": "long_body_truncated",
        "sections": [{"title": "长正文", "content": "长" * 1200}, {"title": "第二节"}],
        "learn": [],
        "openers": [False, True],
    },
    {
        "name": "empty_body_pending",
        "sections": [{"title": "待生成", "content": ""}, {"title": "第二节"}],
        "learn": [],
        "openers": [False, True],
    },
]


def _build_kg(tmp_path: Path, scenario: dict) -> KnowledgeGraph:
    # ⚠️ 每个场景**独立** data_dir：同一个库里 `create_section` 会接着编号（s06、s07…），
    #    节点同名还会并轨 —— 共用一个目录会让场景互相污染，基线就不再是"某个状态"的快照。
    g = KnowledgeGraph(user_id=USER_ID, data_dir=tmp_path / f"kg_{scenario['name']}")
    with g._conn:
        g._conn.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (?, 's', 'x')",
            (USER_ID,))
    g.create_node_with_content({"id": "n1", "name": NODE_NAME, "subject": "高等数学"}, "正文")
    for spec in scenario["sections"]:
        content = spec.get("content", DEFAULT_BODY)
        g.create_section("n1", spec["title"], content=content)
    for spec in scenario["learn"]:
        params = {k: v for k, v in spec.items() if k != "id"}
        g.set_section_learn("n1", spec["id"], **params)
    return g


def _render_all(tmp_path: Path) -> dict:
    """跑完所有场景，返回 {基线键: 注入文本}；每次都用**新的** kg（模拟每请求新建）。"""
    out = {}
    for scenario in SCENARIOS:
        kg = _build_kg(tmp_path, scenario)
        try:
            focus = kg.get_node("n1")
            for opener in scenario["openers"]:
                key = f"{scenario['name']}|{'opener' if opener else 'normal'}"
                out[key] = _build_learn_block(kg, focus, opener=opener)
        finally:
            kg.close()
    return out


def _first_mismatch(rendered: dict, baseline: dict) -> str:
    """把第一个不同的键连同**逐字符**对比摘要成一行可读信息。"""
    keys_r, keys_b = set(rendered), set(baseline)
    if keys_r != keys_b:
        return f"场景集合变了：仅新有={sorted(keys_r - keys_b)} 仅旧有={sorted(keys_b - keys_r)}"
    for key in sorted(rendered):
        if rendered[key] != baseline[key]:
            new, old = rendered[key], baseline[key]
            i = next((n for n, (a, b) in enumerate(zip(new, old)) if a != b), min(len(new), len(old)))
            return (f"场景 {key} 注入文本变了（第 {i} 个字符起）：\n"
                    f"  基线: {old[i:i + 80]!r}\n"
                    f"  现在: {new[i:i + 80]!r}\n"
                    f"  长度: 基线 {len(old)} → 现在 {len(new)}")
    return ""


def test_learn_block_matches_golden_baseline(tmp_path):
    """B2 等价重构的核心判据：注入文本逐字符不变。

    基线更新（只在**明确**要改行为时用，且必须说明为什么）：
        `$env:UPDATE_LEARN_BLOCK_GOLDEN='1'` → 跑本用例 → 去掉环境变量 → 重跑确认绿。
    """
    rendered = _render_all(tmp_path)
    if os.environ.get(UPDATE_ENV) == "1":
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(
            json.dumps(rendered, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
            encoding="utf-8")
        pytest.skip(f"已重建基线：{FIXTURE}（{len(rendered)} 个场景）")

    assert FIXTURE.is_file(), (
        f"基线缺失：{FIXTURE}。先在**未改动**的实现上用 {UPDATE_ENV}=1 生成它。")
    baseline = json.loads(FIXTURE.read_text(encoding="utf-8"))
    problem = _first_mismatch(rendered, baseline)
    assert problem == "", problem


def test_golden_baseline_covers_every_branch():
    """基线的**覆盖面**也要锁住：场景被删/改坏时不能让上一条静默变成"覆盖变少还绿"。"""
    keys = {f"{s['name']}|{'opener' if o else 'normal'}"
            for s in SCENARIOS for o in s["openers"]}
    baseline = json.loads(FIXTURE.read_text(encoding="utf-8"))

    assert set(baseline) == keys
    # 五类状态分支 + 老节点空串，都要在基线里有代表
    assert baseline["legacy_no_sections|normal"] == ""
    assert baseline["legacy_no_sections|opener"] == ""
    joined = "\n".join(baseline.values())
    for marker in ("学生自评已懂", "AI 已讲完", "学生自评「不懂」", "系统降级",
                   "没看过 / 未标记", "全部小节都已通过", "已读完",
                   "正文过长已截断"):
        assert marker in joined, f"基线没覆盖到这个分支：{marker}"
    assert baseline["confused_ai_downgrade_is_start|normal"].count("第 1 节") >= 1
