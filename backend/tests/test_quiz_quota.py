"""
题库存储配额（B3.1 预研项：入库前必须完成，防百万题撑爆磁盘）。
覆盖：check_quota 三态边界（未超/恰好到顶/超限）、limit=0 不限制、
import_from_json 默认接入配置配额（不传 check_quota_fn 也受保护）。
"""

from app.core.collector.adapters import dataset_quiz
from app.core.quiz import quota
from app.core.quiz.quiz_store import QuizManager

USER = 42


def _patch(monkeypatch, tmp_path) -> QuizManager:
    mgr = QuizManager(tmp_path)
    monkeypatch.setattr(dataset_quiz, "quiz_manager", mgr)
    return mgr


def _write_rows(tmp_path, n) -> str:
    import json
    rows = [{"question": f"第{i}题：栈的特点是什么？",
             "choices": ["甲", "乙"], "answer": "A"} for i in range(n)]
    p = tmp_path / "ds.json"
    p.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return str(p)


# ── 纯函数边界 ──

def test_check_quota_under_limit():
    ok, msg = quota.check_quota(current=10, incoming=20, limit=100)
    assert ok and msg == ""


def test_check_quota_exactly_at_limit_allowed():
    """current+incoming == limit：允许（下次导入才拒），且 100% 占用必须告警。"""
    ok, msg = quota.check_quota(current=90, incoming=10, limit=100)
    assert ok and "100/100" in msg


def test_check_quota_over_limit_rejected():
    ok, msg = quota.check_quota(current=90, incoming=11, limit=100)
    assert not ok and "100" in msg and "101" in msg


def test_check_quota_zero_means_unlimited():
    ok, msg = quota.check_quota(current=10**9, incoming=10**6, limit=0)
    assert ok and msg == ""


def test_check_quota_warns_near_limit():
    """≥90% 占用时放行但返回告警信息。"""
    ok, msg = quota.check_quota(current=85, incoming=10, limit=100)
    assert ok and msg != ""


# ── import_from_json 默认接入 ──

def test_import_rejected_by_default_quota(tmp_path, monkeypatch):
    """不传 check_quota_fn 时，默认配置配额仍生效（防止调用方忘传）。"""
    _patch(monkeypatch, tmp_path)
    monkeypatch.setattr(quota.settings, "quiz_storage_quota", 5)
    # 库里已有 4 题 + 本次导入 3 题 = 7 > 5
    dataset_quiz.import_from_json(_write_rows(tmp_path, 4), USER)
    try:
        dataset_quiz.import_from_json(_write_rows(tmp_path, 3), USER)
        assert False, "应抛 RuntimeError"
    except RuntimeError as e:
        assert "配额" in str(e) or "quota" in str(e)


def test_import_within_default_quota_passes(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    monkeypatch.setattr(quota.settings, "quiz_storage_quota", 10)
    result = dataset_quiz.import_from_json(_write_rows(tmp_path, 3), USER)
    assert result["inserted"] == 3


def test_import_quota_disabled(tmp_path, monkeypatch):
    """quota=0 → 不限制。"""
    _patch(monkeypatch, tmp_path)
    monkeypatch.setattr(quota.settings, "quiz_storage_quota", 0)
    result = dataset_quiz.import_from_json(_write_rows(tmp_path, 8), USER)
    assert result["inserted"] == 8
