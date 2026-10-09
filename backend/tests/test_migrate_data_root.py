"""迁移脚本 migrate_data_root（A1b-2）：plan / dry-run / apply 的单测。

全离线：monkeypatch `storage` 的 DATA_ROOT 与 LEGACY_* 到 tmp_path，不碰真实数据。
"""
from pathlib import Path

from app.core import storage
from scripts import migrate_data_root as mig


def _roots(monkeypatch, tmp_path):
    """把 storage 的单根与旧根全部指向 tmp 下的小目录，返回 (data_root, legacy_backend, legacy_records)。"""
    data_root = tmp_path / "data"
    legacy_backend = tmp_path / "backend_data"
    legacy_records = tmp_path / "app_data"
    monkeypatch.setattr(storage, "DATA_ROOT", data_root)
    monkeypatch.setattr(storage, "LEGACY_BACKEND_DATA_ROOT", legacy_backend)
    monkeypatch.setattr(storage, "LEGACY_RECORDS_ROOT", legacy_records)
    return data_root, legacy_backend, legacy_records


def test_plan_ready_uses_legacy_src_and_data_root_dst(monkeypatch, tmp_path):
    """目标布局：src = 旧根/<域>，dst = DATA_ROOT/<域>（A1b 单根）。"""
    data_root, legacy_backend, legacy_records = _roots(monkeypatch, tmp_path)
    (legacy_backend / "kb").mkdir(parents=True)
    (legacy_records / "agent_runs").mkdir(parents=True)
    (legacy_backend / "quiz").mkdir(parents=True)   # 旧根有
    (data_root / "quiz").mkdir(parents=True)        # 新根也有 → already_migrated

    items = {i["domain"]: i for i in mig.plan()}

    # 旧根有、新没有 → ready
    assert items["kb"]["status"] == "ready"
    assert items["kb"]["src"] == legacy_backend / "kb"
    assert items["kb"]["dst"] == data_root / "kb"
    assert items["agent_runs"]["status"] == "ready"
    assert items["agent_runs"]["src"] == legacy_records / "agent_runs"
    # 无旧源 → no_source；且旧根里那个 P0-1 孤儿 agent_runs 不在域列表里（不会迁移）
    assert items["collector"]["status"] == "no_source"
    assert "agent_runs" not in str(items["collector"]["src"])
    # 目标已存在 → already_migrated
    assert items["quiz"]["status"] == "already_migrated"


def test_apply_moves_ready_dirs(monkeypatch, tmp_path):
    """apply 只动 ready：把旧根目录原样搬到单根，且不覆盖已迁移的。"""
    data_root, legacy_backend, legacy_records = _roots(monkeypatch, tmp_path)
    (legacy_backend / "kb").mkdir(parents=True)
    (legacy_backend / "kb" / "u1").mkdir(parents=True)
    (legacy_backend / "kb" / "u1" / "kb.db").write_text("x", encoding="utf-8")
    (legacy_records / "agent_debug").mkdir(parents=True)
    (legacy_records / "agent_debug" / "debug_log.db").write_text("y", encoding="utf-8")
    (data_root / "rag").mkdir(parents=True)   # 已在单根（target exists）—— apply 不应覆盖
    (data_root / "rag" / "stay.txt").write_text("keep", encoding="utf-8")

    items = mig.plan()
    stats = mig.apply(items)

    assert stats["moved"] == 2, "kb + agent_debug 两个 ready 目录被移动"
    assert (data_root / "kb" / "u1" / "kb.db").exists(), "kb 内容搬到单根"
    assert not (legacy_backend / "kb").exists(), "旧 kb 已搬走"
    assert (data_root / "agent_debug" / "debug_log.db").exists()
    assert (data_root / "rag" / "stay.txt").exists(), "已迁移目标不被覆盖"


def test_apply_is_idempotent(monkeypatch, tmp_path):
    """第二次 apply：没有 ready → 全部已是 already_migrated，不会重复搬。"""
    data_root, legacy_backend, legacy_records = _roots(monkeypatch, tmp_path)
    (legacy_backend / "kb").mkdir(parents=True)
    (legacy_backend / "kb" / "a.txt").write_text("a", encoding="utf-8")

    first = mig.apply(mig.plan())
    second = mig.apply(mig.plan())

    assert first["moved"] == 1
    assert second["moved"] == 0, "幂等：第二次不再移动"
    assert (data_root / "kb" / "a.txt").exists()
