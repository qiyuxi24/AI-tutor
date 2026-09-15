"""工具注册表 / 提示词"单一来源"的一致性测试（2026-09-15）。

背景：工具"何时用"原先写了两份 —— spec.description（模型侧 schema）与 chat_service
手写的逐工具说明（提示词侧）—— 实测已漂移：`WEB_SEARCH_ENABLED=false` 时该工具根本不在
KG_TOOLS 里，提示词却仍教模型"联网搜索 → 调用 `mcp__websearch__web_search`"，模型会去调
一个不存在的工具。收敛后逐工具说明的唯一来源是 spec.guidance（MCP 工具回落其 description），
本文件把这套约束钉成断言，防止后人再手写第二份。
"""

import re

import pytest

from app.core.agent_tools import KG_TOOLS, all_specs, build_tools_prompt, register
from app.services.chat_service import TOOL_CAPABILITY_PROMPT

# 工具名特征：用于从提示词里挑出"看起来是工具名"的反引号 token（参数名 node_id 不匹配）
_TOOL_NAME_PREFIXES = ("mcp__", "add_", "update_", "delete_", "create_", "search_",
                       "fetch_", "download_", "rag_", "quiz_", "grade_", "web_")


def _tool_like_tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", text)
            if t.startswith(_TOOL_NAME_PREFIXES)}


def _registered_names() -> set[str]:
    return {s["name"] for s in all_specs()}


def test_native_tools_all_have_guidance():
    """原生工具必须写 guidance：留空会让提示词段落回落到 description，等于悄悄退回双源。"""
    missing = [s["name"] for s in all_specs()
               if not s["name"].startswith("mcp__") and not (s.get("guidance") or "").strip()]
    assert missing == []


def test_kg_tools_matches_registry():
    """模型侧 schema 与注册表一一对应，且不泄漏内部字段（handler/guidance/timeout_secs）。"""
    assert [t["function"]["name"] for t in KG_TOOLS] == [s["name"] for s in all_specs()]
    for t in KG_TOOLS:
        assert t["type"] == "function"
        assert set(t["function"]) == {"name", "description", "parameters"}


def test_prompt_only_mentions_registered_tools():
    """工具指南 + 跨工具策略里提到的工具名，必须真的注册过（防"教模型调不存在的工具"）。"""
    assert _tool_like_tokens(TOOL_CAPABILITY_PROMPT) - _registered_names() == set()


def test_prompt_follows_registry_when_mcp_disabled():
    """MCP 关闭（注册表里没有 mcp__ 工具）时，指南不得再出现 mcp 工具名 —— 实测曾漂移。"""
    native_only = [s for s in all_specs() if not s["name"].startswith("mcp__")]

    prompt = build_tools_prompt(native_only)

    assert "mcp__" not in prompt
    assert "### `quiz_generate`" in prompt  # 原生工具照常出现在指南里


def test_each_tool_documented_once():
    """逐工具说明只允许由注册表产出一份（防又在策略段手写一遍）。"""
    for s in all_specs():
        assert TOOL_CAPABILITY_PROMPT.count(f"### `{s['name']}`") == 1, s["name"]


def test_policy_prompt_present():
    """跨工具策略必须仍在最终提示词里（掌握度主信号 / 出题铁律 / 权限限制）。"""
    for keyword in ("掌握度由谁更新", "我懂了", "权限限制"):
        assert keyword in TOOL_CAPABILITY_PROMPT


def test_duplicate_registration_rejected():
    """同名工具重复注册直接报错（否则后注册的会静默覆盖前一个）。"""
    with pytest.raises(ValueError, match="工具名重复注册"):
        register({"name": "quiz_generate", "description": "", "parameters": {},
                  "handler": None, "guidance": "", "timeout_secs": None})
