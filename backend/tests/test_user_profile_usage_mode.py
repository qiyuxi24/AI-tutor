"""usage_mode（版权双模式）存储层测试

覆盖：默认值、白名单内 update_field 更新、权重 0 不影响完整度、
旧 JSON 缺字段自动补默认（零破坏）、已有值保留。
"""
import json

import pytest

from app.core.profile import UserProfile, FIELD_WEIGHTS


@pytest.fixture
def profile(tmp_path):
    """临时目录中的画像实例（不污染真实 data/profiles）"""
    return UserProfile(user_id=7, data_dir=tmp_path)


def _write_json(tmp_path, data):
    (tmp_path / "7.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )


# ── 默认值与白名单 ────────────────────────────────────────────

def test_default_usage_mode(profile):
    data = profile.get()
    assert data["preferences"]["usage_mode"] == "personal"


def test_usage_mode_in_whitelist_weight_zero(profile):
    # 白名单放行 update_field；weight=0 不计入完整度权重
    assert "preferences.usage_mode" in FIELD_WEIGHTS
    assert FIELD_WEIGHTS["preferences.usage_mode"] == 0


def test_update_usage_mode_via_whitelist(profile):
    profile.update_field("preferences.usage_mode", "commercial")
    assert profile.get()["preferences"]["usage_mode"] == "commercial"


def test_update_field_unknown_still_raises(profile):
    with pytest.raises(ValueError):
        profile.update_field("preferences.unknown", "x")


def test_usage_mode_does_not_affect_completeness(profile):
    # 空画像默认 percent=0；仅切换 usage_mode 后仍为 0（不视为画像完善度）
    profile.update_field("preferences.usage_mode", "commercial")
    c = profile.get_completeness()
    assert c["percent"] == 0
    assert c["total"] == 12  # usage_mode 权重 0，不计入 total


# ── 迁移（零破坏）────────────────────────────────────────────

def test_migrate_json_missing_usage_mode(tmp_path):
    """旧 JSON（无 usage_mode）读取后自动补默认，其余字段保留"""
    _write_json(tmp_path, {
        "version": 2,
        "user_id": 7,
        "basic": {"name": "小明"},
        "goals": ["学 Python"],
        "preferences": {"personality": "内向"},
    })
    data = UserProfile(user_id=7, data_dir=tmp_path).get()
    assert data["preferences"]["usage_mode"] == "personal"
    assert data["preferences"]["personality"] == "内向"
    assert data["basic"]["name"] == "小明"
    assert data["goals"] == ["学 Python"]


def test_keep_existing_usage_mode(tmp_path):
    """已有 usage_mode 的 JSON 不被覆盖为默认"""
    _write_json(tmp_path, {
        "version": 2,
        "user_id": 7,
        "preferences": {"usage_mode": "commercial", "personality": "外向"},
    })
    data = UserProfile(user_id=7, data_dir=tmp_path).get()
    assert data["preferences"]["usage_mode"] == "commercial"
    assert data["preferences"]["personality"] == "外向"
