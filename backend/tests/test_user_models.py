"""
每用户 LLM 模型配置与状态机单元测试（全离线，不碰真实网络）。

覆盖（对齐「每用户自有模型 + 运行时降级」设计）：
- 新建模型 = 未验证态，API Key 只以掩码对外
- 连通性测试结果驱动状态机：ok → available，fail → unavailable + last_error
- 候选链 = 当前激活模型优先 + 其余可用模型（未验证/停用不进链）
- 无自定义模型 → 候选链为空（调用方回落到 .env 系统默认档）
- 运行生效档位（effective_id）由真实调用结果回写
- 按 user_id 隔离
"""
import pytest

from app.core.llm.user_models import (
    ModelStore,
    STATUS_AVAILABLE,
    STATUS_DISABLED,
    STATUS_UNAVAILABLE,
    STATUS_UNVERIFIED,
)


def _store(tmp_path, uid: int = 1) -> ModelStore:
    return ModelStore(uid, data_dir=tmp_path)


def test_add_model_unverified_and_key_masked(tmp_path):
    s = _store(tmp_path)
    m = s.add(name="校内网关", base_url="https://gw/v1",
              api_key="sk-abcdefgh1234", model="dau")
    assert m["status"] == STATUS_UNVERIFIED
    assert m["api_key_masked"] == "sk-a****1234"
    assert "api_key" not in m
    # 落盘保留完整 key（调用真实 API 时要用）
    assert s.secret(m["id"])["api_key"] == "sk-abcdefgh1234"


def test_mark_test_result_transitions_status(tmp_path):
    s = _store(tmp_path)
    m = s.add(name="a", base_url="u", api_key="k1234567890", model="m")
    s.mark_test(m["id"], ok=False, error="HTTP 401")
    assert s.get(m["id"])["status"] == STATUS_UNAVAILABLE
    assert s.get(m["id"])["last_error"] == "HTTP 401"
    s.mark_test(m["id"], ok=True)
    assert s.get(m["id"])["status"] == STATUS_AVAILABLE
    assert s.get(m["id"])["last_error"] == ""


def test_candidates_put_active_first_then_available(tmp_path):
    s = _store(tmp_path)
    a = s.add(name="A", base_url="u", api_key="k1", model="ma")
    b = s.add(name="B", base_url="u", api_key="k2", model="mb")
    s.add(name="C", base_url="u", api_key="k3", model="mc")   # 未验证
    s.mark_test(a["id"], ok=True)
    s.mark_test(b["id"], ok=True)
    s.set_active(b["id"])
    assert [x["id"] for x in s.candidates()] == [b["id"], a["id"]]


def test_candidates_empty_when_no_model(tmp_path):
    """无自定义模型 → 空链，调用方回落到 .env 的系统默认模型。"""
    assert _store(tmp_path).candidates() == []


def test_disabled_model_excluded_even_if_active(tmp_path):
    s = _store(tmp_path)
    a = s.add(name="A", base_url="u", api_key="k1", model="ma")
    s.mark_test(a["id"], ok=True)
    s.set_active(a["id"])
    s.set_status(a["id"], STATUS_DISABLED)
    assert s.candidates() == []


def test_delete_active_clears_active_id(tmp_path):
    s = _store(tmp_path)
    a = s.add(name="A", base_url="u", api_key="k1", model="ma")
    s.set_active(a["id"])
    assert s.delete(a["id"]) is True
    assert s.snapshot()["active_id"] is None
    assert s.delete("nope") is False


def test_effective_tracks_last_success(tmp_path):
    s = _store(tmp_path)
    a = s.add(name="A", base_url="u", api_key="k1", model="ma")
    b = s.add(name="B", base_url="u", api_key="k2", model="mb")
    s.mark_effective(b["id"])
    assert s.snapshot()["effective_id"] == b["id"]
    assert s.snapshot()["effective_at"] > 0
    assert s.get(b["id"])["last_error"] == ""
    # a 从未生效过
    assert s.get(a["id"])["id"] != s.snapshot()["effective_id"]


def test_users_are_isolated(tmp_path):
    s1, s2 = _store(tmp_path, 1), _store(tmp_path, 2)
    s1.add(name="A", base_url="u", api_key="k1", model="ma")
    assert s2.snapshot()["models"] == []


def test_update_model_fields_and_reset_needs_retest(tmp_path):
    s = _store(tmp_path)
    a = s.add(name="A", base_url="u", api_key="k1", model="ma")
    s.mark_test(a["id"], ok=True)
    updated = s.update(a["id"], name="A2", model="ma2")
    assert updated["name"] == "A2" and updated["model"] == "ma2"
    # 改了接入参数 → 旧结论失效，退回未验证
    assert updated["status"] == STATUS_UNVERIFIED


def test_update_missing_model_raises(tmp_path):
    with pytest.raises(KeyError):
        _store(tmp_path).update("nope", name="x")
