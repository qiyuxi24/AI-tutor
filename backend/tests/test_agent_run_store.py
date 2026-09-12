"""agent_run_store 单元测试：写/读/隔离/排序边界。

store 已被 agent_loop 落库与 token_estimator 历史预估覆盖主路径，
此处补充 schema 级边界：upsert 幂等、越权隔离、历史 token 排序过滤。
"""
from app.core import agent_run_store as store


def _seed(db_dir, run_id, user_id=1, ct=50, status="ok", started_at=None):
    import time
    store.save_run({
        "run_id": run_id, "user_id": user_id, "status": status,
        "started_at": started_at if started_at is not None else time.time(),
        "ended_at": time.time() + 1,
        "total_llm_calls": 2, "context_tokens": 100,
        "token_usage": {"prompt_tokens": 100, "completion_tokens": ct,
                        "total_tokens": 100 + ct},
        "estimated_prompt_tokens": 95, "final_text": "x", "evidence": [],
    }, db_dir=db_dir)


def test_save_list_get_roundtrip(tmp_path):
    """落库 → 列表（元数据无 evidence）→ 详情（含 evidence）。"""
    _seed(tmp_path, "r1", user_id=3)
    store.save_run({
        "run_id": "r2", "user_id": 3, "status": "error", "started_at": 1.0,
        "ended_at": 2.0, "total_llm_calls": 1, "context_tokens": 10,
        "token_usage": {}, "estimated_prompt_tokens": 10,
        "final_text": "", "evidence": [{"kind": "thinking", "text": "hi", "ts": 1.0}],
    }, db_dir=tmp_path)

    runs = store.list_runs(3, db_dir=tmp_path)
    assert len(runs) == 2
    assert all("evidence" not in r for r in runs)
    assert runs[0]["started_at"] >= runs[1]["started_at"]  # 倒序

    run = store.get_run(3, "r2", db_dir=tmp_path)
    assert run["status"] == "error"
    assert run["evidence"][0]["kind"] == "thinking"


def test_get_run_scoped_to_user(tmp_path):
    """他人运行 → 详情返回 None（越权隔离）。"""
    _seed(tmp_path, "mine", user_id=1)
    assert store.get_run(2, "mine", db_dir=tmp_path) is None


def test_save_run_upsert_idempotent(tmp_path):
    """同一 run_id 重复落库 → 仍只有一行且字段被更新。"""
    _seed(tmp_path, "r1", user_id=1, ct=50)
    _seed(tmp_path, "r1", user_id=1, ct=999)
    runs = store.list_runs(1, db_dir=tmp_path)
    assert len(runs) == 1
    assert runs[0]["token_usage"]["completion_tokens"] == 999


def test_recent_completion_tokens_order_and_filter(tmp_path):
    """历史 token：按时间倒序取最近 N 条，过滤 0 值，返回旧→新。"""
    for i, ct in enumerate([0, 100, 300]):  # 0 值应被过滤
        _seed(tmp_path, f"r{i}", user_id=7, ct=ct, started_at=float(i))
    assert store.recent_completion_tokens(7, n=20, db_dir=tmp_path) == [100, 300]
    assert store.recent_completion_tokens(8, db_dir=tmp_path) == []


def test_default_db_dir_points_to_data(tmp_path):
    """默认库目录应位于 backend/data/agent_runs（随 Docker ./backend/data 卷持久化）。"""
    d = store.default_db_dir()
    assert d.name == "agent_runs"
    assert "backend" in d.parts
    assert "data" in d.parts


# ═══════════════ 清理 / 删除 / 统计（2026-09-08 治理面） ═══════════════

def test_prune_layered_retention(tmp_path):
    """prune 分层保留：近 30 天完整保留、30~180 天摘 evidence、更老整行删。"""
    import time
    now = time.time()
    day = 86400
    _seed(tmp_path, "fresh", user_id=1, started_at=now)
    _seed(tmp_path, "old", user_id=1, started_at=now - 60 * day)
    _seed(tmp_path, "gone", user_id=1, started_at=now - 200 * day)
    # 给 middle 灌入真实 evidence，验证"摘除"确实清空
    store.save_run({
        "run_id": "old", "user_id": 1, "status": "ok",
        "started_at": now - 60 * day, "ended_at": now,
        "total_llm_calls": 1, "context_tokens": 10, "token_usage": {},
        "estimated_prompt_tokens": 10, "final_text": "x",
        "evidence": [{"kind": "thinking", "text": "机密过程", "ts": 1.0}],
    }, db_dir=tmp_path)

    res = store.prune(db_dir=tmp_path)
    assert res["deleted"] == 1    # gone 整行删
    assert res["stripped"] == 1   # old 摘 evidence
    assert store.get_run(1, "fresh", db_dir=tmp_path) is not None
    assert store.get_run(1, "old", db_dir=tmp_path)["evidence"] == []
    assert store.get_run(1, "gone", db_dir=tmp_path) is None
    # 幂等：再次执行无新变化
    assert store.prune(db_dir=tmp_path) == {"deleted": 0, "stripped": 0}


def test_prune_crosses_users_but_delete_is_scoped(tmp_path):
    """prune 按时间全局执行（跨用户）；delete_run/delete_user_runs 严格本人隔离。"""
    import time
    now = time.time()
    day = 86400
    _seed(tmp_path, "mine_old", user_id=1, started_at=now - 60 * day)
    _seed(tmp_path, "others_old", user_id=2, started_at=now - 60 * day)
    _seed(tmp_path, "others_fresh", user_id=2, started_at=now)
    for run_id, uid in (("mine_old", 1), ("others_old", 2)):  # 灌入真实 evidence
        store.save_run({"run_id": run_id, "user_id": uid,
                        "started_at": now - 60 * day, "token_usage": {},
                        "evidence": [{"kind": "thinking", "text": "e", "ts": 1}]},
                       db_dir=tmp_path)

    res = store.prune(db_dir=tmp_path)
    assert res["stripped"] == 2  # 两个用户的过期行都被摘（全局时间策略）

    assert store.delete_run(1, "others_old", db_dir=tmp_path) is False  # 越权删除无效
    assert store.get_run(2, "others_old", db_dir=tmp_path) is not None
    assert store.delete_run(1, "mine_old", db_dir=tmp_path) is True

    assert store.delete_user_runs(2, db_dir=tmp_path) == 2
    assert store.list_runs(2, db_dir=tmp_path) == []
    assert store.count_runs(1, db_dir=tmp_path) == 0


def test_run_stats_and_paging(tmp_path):
    """run_stats 聚合（token/成败/体积）+ list 分页 offset + count 总数。"""
    for i in range(3):
        _seed(tmp_path, f"r{i}", user_id=1, ct=10 * (i + 1),
              status="error" if i == 0 else "ok", started_at=float(i))

    st = store.run_stats(1, db_dir=tmp_path)
    assert st["total"] == 3 and st["ok"] == 2 and st["errors"] == 1
    assert st["completion_tokens"] == 60 and st["prompt_tokens"] == 300
    assert st["llm_calls"] == 6
    assert st["evidence_bytes"] == 6  # 3 行 × '[]' 2 字节
    assert store.count_runs(1, db_dir=tmp_path) == 3

    page = store.list_runs(1, limit=2, offset=1, db_dir=tmp_path)
    assert len(page) == 2
    assert page[0]["run_id"] == "r1"  # 倒序 r2,r1,r0 → offset=1 起为 r1,r0
