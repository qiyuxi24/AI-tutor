"""
Prompt loader 提示词加载器测试。
覆盖：三种模式渲染、未知模式报错、模板拼接、参数传递。

⚠️ 2026-09-14 修复：`system_prompt_common.j2` 里图谱/画像占位符曾写成**单花括号**
`{knowledge_graph_summary}`（Jinja2 只认 `{{ }}`）→ 渲染时被当字面文本原样输出，
**知识图谱从未注入过 AI 提示词**（57 个节点的 id 在 prompt 里出现 0 次）。
本文件曾有测试把这个错误行为当成"预期"锁死（断言 `"knowledge_graph_summary" in result`）——
现改为断言**值真的被替换进来**，防止回退。
"""
import pytest

from app.core import prompt_loader
from app.core.prompt_loader import get_system_prompt, MODE_TEMPLATE_MAP


# ─── 模板目录验证 ────────────────────────────────────────────────

def test_prompt_dir_exists():
    """确保 data/prompts 目录和模板文件存在"""
    assert prompt_loader.PROMPT_DIR.exists()

    for mode, filename in MODE_TEMPLATE_MAP.items():
        path = prompt_loader.PROMPT_DIR / filename
        assert path.exists(), f"模板文件 {filename} 不存在"

    common = prompt_loader.PROMPT_DIR / prompt_loader.COMMON_TEMPLATE
    assert common.exists()


# ─── get_system_prompt 基本渲染 ───────────────────────────────────

def test_get_system_prompt_adaptive():
    """adaptive 模式：拼接 common + adaptive 模板，student_message 被注入"""
    result = get_system_prompt(
        mode="adaptive",
        student_message="我想学栈",
        graph_summary="图谱：数据结构",
        user_profile="偏好：可视化",
    )
    assert "我想学栈" in result
    assert len(result) > 50


def test_get_system_prompt_free_talk():
    result = get_system_prompt(
        mode="free_talk",
        student_message="随便聊聊",
    )
    assert "随便聊聊" in result


def test_get_system_prompt_recursive():
    result = get_system_prompt(
        mode="recursive",
        student_message="学到二叉树",
        current_node="binary_tree",
        knowledge_graph_framework="### 框架节点\n  [1] 二叉树",
    )
    assert "学到二叉树" in result
    assert "binary_tree" in result
    assert "框架节点" in result


def test_get_system_prompt_unknown_mode():
    with pytest.raises(ValueError, match="未知引导模式"):
        get_system_prompt(mode="invalid", student_message="x")


def test_get_system_prompt_empty_message():
    result = get_system_prompt(mode="adaptive", student_message="")
    assert isinstance(result, str)
    assert len(result) > 0


def test_get_system_prompt_no_optional_args():
    result = get_system_prompt(mode="adaptive", student_message="测试")
    assert "测试" in result


def test_get_system_prompt_all_three_modes():
    """三种模式都能正常渲染"""
    for mode in ("adaptive", "free_talk", "recursive"):
        kwargs = {"student_message": f"test_{mode}"}
        if mode == "recursive":
            kwargs["current_node"] = "node_1"
            kwargs["knowledge_graph_framework"] = "framework"
        result = get_system_prompt(mode=mode, **kwargs)
        assert f"test_{mode}" in result
        assert len(result) > 50


# ─── common 模板行为 ─────────────────────────────────────────────

def test_common_template_always_present():
    """common 模板内容（全局底线等）始终出现在输出中"""
    result = get_system_prompt(mode="free_talk", student_message="hi")
    assert "资深教师" in result or "苏格拉底" in result
    assert "全局底线" in result


def test_user_profile_section_appears_when_provided():
    """传入非空 user_profile 时，「学生画像」区块出现（{% if user_profile %} 生效）"""
    result_with = get_system_prompt(
        mode="free_talk",
        student_message="hi",
        user_profile="偏好：图形化理解",
    )
    assert "学生画像" in result_with


def test_user_profile_section_absent_when_empty():
    """不传 user_profile 时，「学生画像」区块不出现"""
    result_without = get_system_prompt(
        mode="free_talk",
        student_message="hi",
    )
    assert "学生画像" not in result_without


def test_graph_summary_is_actually_injected():
    """图谱摘要必须**真的被替换进** prompt（曾因模板用单花括号而静默丢失）。

    回归守卫：只断言"占位符名字出现"是不够的 —— 模板写错时名字照样出现，
    但图谱内容一个节点都没有。所以断言**值**与**字面占位符残留**两件事。
    """
    summary = "### 现有节点（共 2 个）\n  [binary_tree] 二叉树 (掌握度:0)"
    result = get_system_prompt(
        mode="free_talk",
        student_message="hi",
        graph_summary=summary,
    )
    assert "binary_tree" in result, "图谱内容没有被注入"
    assert "掌握度:0" in result
    assert "{knowledge_graph_summary}" not in result, "占位符未被 Jinja2 替换（单花括号 bug 回退）"


def test_user_profile_is_actually_injected():
    """学生画像内容必须真的被替换进 prompt（同上的单花括号 bug）。"""
    result = get_system_prompt(
        mode="free_talk",
        student_message="hi",
        user_profile="偏好：图形化理解",
    )
    assert "偏好：图形化理解" in result
    assert "{user_profile}" not in result


def test_common_template_has_no_single_brace_placeholders():
    """模板级回归守卫：common 模板不得再出现单花括号占位符。"""
    src = (prompt_loader.PROMPT_DIR / prompt_loader.COMMON_TEMPLATE).read_text(
        encoding="utf-8")
    assert "{knowledge_graph_summary}" not in src
    assert "{user_profile}" not in src
    assert "{{ knowledge_graph_summary }}" in src
    assert "{{ user_profile }}" in src


# ─── 递归模式参数 ────────────────────────────────────────────────

def test_recursive_extra_kwargs_passed():
    """递归模式的 extra_kwargs 被正确传给模板"""
    result = get_system_prompt(
        mode="recursive",
        student_message="msg",
        current_node="my_node",
        knowledge_graph_framework="MY_FRAMEWORK",
    )
    assert "my_node" in result
    assert "MY_FRAMEWORK" in result


def test_recursive_mode_section():
    """递归模式包含递归特有的区块"""
    result = get_system_prompt(
        mode="recursive",
        student_message="学习",
        current_node="node_a",
        knowledge_graph_framework="框架",
    )
    assert "递归" in result or "recursive" in result.lower() or "框架" in result


# ─── 拼接验证 ────────────────────────────────────────────────────

def test_concatenation_common_and_mode():
    """输出包含 common 模板和 mode 模板的内容"""
    result = get_system_prompt(
        mode="adaptive",
        student_message="拼接测试",
    )
    # common 模板内容
    assert "全局底线" in result
    # mode 模板内容（adaptive 特有）
    assert "拼接测试" in result
    assert "自适应" in result or "引导" in result
