"""update_user_profile 工具测试 —— 结构化字段 + 观察笔记两条写入路径。

2026-09-26 补闭环：此前该工具只能追加 `ai_notes`，AI 无法写结构化字段，
"AI 观察到 → 结构化画像 → 下一轮注入"这条链路断在写入端。
"""
import json

import pytest

from app.core.agent_tools import execute_kg_tool
from app.core.profile import UserProfile
from app.core.profile import store as profile_store


@pytest.fixture(autouse=True)
def _isolate_profile_dir(monkeypatch, tmp_path):
    """工具内部自建 UserProfile（默认落 data/profiles）→ 重定向到临时目录，别污染真实画像。"""
    monkeypatch.setattr(profile_store, "default_data_dir", lambda: tmp_path)


class _Fn:
    def __init__(self, args):
        self.name = "update_user_profile"
        self.arguments = json.dumps(args)


class _ToolCall:
    def __init__(self, args):
        self.function = _Fn(args)


class _Kg:
    def __init__(self, user_id=7):
        self.user_id = user_id


def _call(**args):
    return execute_kg_tool(_ToolCall(args), _Kg())


def test_write_structured_field(tmp_path):
    r = _call(field="basic.stage", value="大二")
    assert "已更新画像字段 basic.stage" in r
    assert UserProfile(user_id=7, data_dir=tmp_path).get()["basic"]["stage"] == "大二"


def test_goals_append_does_not_clobber(tmp_path):
    """goals 追加语义：只传新目标不清空已有目标（update_field 本身是整体替换）。"""
    UserProfile(user_id=7, data_dir=tmp_path).update_field("goals", "过六级")
    _call(field="goals", value="学 Python")
    assert UserProfile(user_id=7, data_dir=tmp_path).get()["goals"] == ["过六级", "学 Python"]


def test_note_added_then_deduped(tmp_path):
    assert "已新增观察笔记" in _call(content="做题容易粗心")
    assert "未重复记录" in _call(content="做题容易粗心")
    assert len(UserProfile(user_id=7, data_dir=tmp_path).get()["ai_notes"]) == 1


def test_usage_mode_not_writable():
    """版权/合规开关属用户策略，AI 不得改写。"""
    assert "操作失败" in _call(field="preferences.usage_mode", value="commercial")


def test_empty_args_rejected():
    assert "操作失败" in _call()


def test_field_without_value_rejected():
    assert "操作失败" in _call(field="basic.name")


def test_guidance_covers_every_writable_field():
    """枚举暴露了某字段、提示词却没写，模型就不会用 → 两份清单必须同步（防漂移）。"""
    from app.core.agent_tools.tools import update_user_profile as tool
    from app.core.profile import AI_WRITABLE_FIELDS

    assert tool.PARAMETERS["properties"]["field"]["enum"] == list(AI_WRITABLE_FIELDS)
    for f in AI_WRITABLE_FIELDS:
        assert f"`{f}`" in tool.GUIDANCE, f
