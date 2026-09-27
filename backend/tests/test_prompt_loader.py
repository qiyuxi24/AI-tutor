"""Prompt loader 提示词加载器测试。

2026-09-26：三种引导模式（adaptive / free_talk / recursive）合并为唯一一套最简提示词，
本文件随之从"逐模式渲染"改为"唯一模板渲染 + 注入守卫"。

⚠️ 2026-09-14 修复：`system_prompt_common.j2` 里图谱/画像占位符曾写成**单花括号**
`{knowledge_graph_summary}`（Jinja2 只认 `{{ }}`）→ 渲染时被当字面文本原样输出，
**知识图谱从未注入过 AI 提示词**（57 个节点的 id 在 prompt 里出现 0 次）。
本文件曾有测试把这个错误行为当成"预期"锁死（断言 `"knowledge_graph_summary" in result`）——
现改为断言**值真的被替换进来**，防止回退。
"""
from app.core import prompt_loader
from app.core.prompt_loader import get_system_prompt


# ─── 模板目录验证 ────────────────────────────────────────────────

def test_prompt_dir_exists():
    """确保 data/prompts 目录与唯一模板文件存在"""
    assert prompt_loader.PROMPT_DIR.exists()
    assert (prompt_loader.PROMPT_DIR / prompt_loader.TEMPLATE).exists()


def test_no_mode_dispatch():
    """模式概念已删除：模板目录只留一套提示词，loader 不再按 mode 分派。"""
    assert not hasattr(prompt_loader, "MODE_TEMPLATE_MAP")
    names = {p.name for p in prompt_loader.PROMPT_DIR.glob("*.j2")}
    assert names == {prompt_loader.TEMPLATE}


# ─── get_system_prompt 基本渲染 ───────────────────────────────────

def test_get_system_prompt_renders_message():
    result = get_system_prompt(student_message="我想学栈")
    assert "我想学栈" in result
    assert len(result) > 50


def test_get_system_prompt_empty_message():
    result = get_system_prompt(student_message="")
    assert isinstance(result, str)
    assert len(result) > 0


def test_common_template_always_present():
    """共同底线等内容（全局底线、框架约束）始终出现在输出中"""
    result = get_system_prompt(student_message="hi")
    assert "资深教师" in result or "苏格拉底" in result
    assert "全局底线" in result


def test_teaching_method_section_present():
    """唯一的「教学方式」段存在（合并前是模式模板的内容）"""
    result = get_system_prompt(student_message="hi")
    assert "教学方式" in result
    assert "拆解引导" in result
    assert "类比举例" in result


# ─── 注入守卫（单花括号 bug 的回归线）────────────────────────────

def test_graph_summary_is_actually_injected():
    """图谱摘要必须**真的被替换进** prompt（曾因模板用单花括号而静默丢失）。

    回归守卫：只断言"占位符名字出现"是不够的 —— 模板写错时名字照样出现，
    但图谱内容一个节点都没有。所以断言**值**与**字面占位符残留**两件事。
    """
    summary = "### 现有节点（共 2 个）\n  [binary_tree] 二叉树 (掌握度:0)"
    result = get_system_prompt(student_message="hi", graph_summary=summary)
    assert "binary_tree" in result, "图谱内容没有被注入"
    assert "掌握度:0" in result
    assert "{knowledge_graph_summary}" not in result, "占位符未被 Jinja2 替换（单花括号 bug 回退）"


def test_user_profile_section_appears_when_provided():
    """传入非空 user_profile 时，「学生画像」区块出现（{% if user_profile %} 生效）"""
    result = get_system_prompt(student_message="hi", user_profile="偏好：图形化理解")
    assert "学生画像" in result
    assert "偏好：图形化理解" in result
    assert "{user_profile}" not in result


def test_user_profile_section_absent_when_empty():
    """不传 user_profile 时，「学生画像」区块不出现"""
    result = get_system_prompt(student_message="hi")
    assert "学生画像" not in result


def test_current_position_injected():
    """位置段由调用方传入的显式文案渲染（未指定时也要有值，不得整段消失）"""
    result = get_system_prompt(student_message="hi", current_position="「递归」(rec)")
    assert "学生当前所处位置" in result
    assert "「递归」(rec)" in result


def test_template_has_positive_scope_and_mastery_semantics():
    """参照系契约 I1-3 / I2-3 的守卫（2026-09-26 G3+G4）。

    I1-3：框架约束曾是纯负面表述（"严禁脱离此框架"），模型不知道"**能**讲什么"；
    I2-3：掌握度只是清单里的一个数字，提示词里没有任何"据此调整"的要求。
    两者都只靠模板文本实现，所以断言文本本身（同 test_kg_themes 的做法）。
    """
    result = get_system_prompt(
        student_message="hi",
        graph_summary="### 现有节点（共 1 个）\n  [bt] 二叉树 (掌握度:0)",
    )
    assert "可讲范围" in result                      # I1-3 正面清单
    assert "邻域" in result                          # I1-3 邻域说明
    assert "域外概念不引入" in result
    assert "掌握度决定怎么讲" in result              # I2-3 行为语义
    assert "已掌握" in result and "精简" in result   # 高掌握度 → 精简/跳过
    assert "薄弱" in result and "降低难度" in result  # 低掌握度 → 换角度/降难度

    # 分档阈值必须与唯一真值源一致（graph_middleware.MASTERY_WEAK / _MASTERED）
    from app.core.graph_middleware import MASTERY_WEAK, MASTERY_MASTERED
    assert f"1–{MASTERY_WEAK - 1}" in result
    assert f"{MASTERY_WEAK}–{MASTERY_MASTERED - 1}" in result
    assert f"≥{MASTERY_MASTERED}" in result


def test_template_has_no_single_brace_placeholders():
    """模板级回归守卫：唯一模板不得再出现单花括号占位符。"""
    src = (prompt_loader.PROMPT_DIR / prompt_loader.TEMPLATE).read_text(encoding="utf-8")
    assert "{knowledge_graph_summary}" not in src
    assert "{user_profile}" not in src
    assert "{student_message}" not in src
    assert "{{ knowledge_graph_summary }}" in src
    assert "{{ user_profile }}" in src
    assert "{{ student_message }}" in src
