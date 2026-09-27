"""对话持久化存储（ConversationStore）：全量同步的时间戳语义回归。

背景（2026-09-26 排查对话功能时发现）：
  前端 `syncToBackend` 只上传 {id, title, messages, createdAt}，**不传 updatedAt**；
  而 `save_conversation` 把缺省的 updated_at 退化成 `created_at` →
  刚聊过的老对话，排序键还是"三个月前的创建时刻"，在侧边栏沉底。
  `POST /conversations` 更糟：`conversations.py` 传 `updated_at=0`，
  键存在但值为 0 → 直接落库 0。

本文件锁死三条不变量：
  1. updated_at 缺省（或为 0）→ 必须取**服务端当前时间**，不得回退成 created_at；
  2. 全量同步反复上传**同一份内容**时**不得推进** updated_at ——
     否则每次 persist 都把全部对话刷成同一时刻，排序同样失去意义；
  3. 客户端显式给出的 updated_at 优先（单条保存接口的语义不变）。
"""
import time

from app.core.conversation_store import ConversationStore


def _msgs(text: str) -> list[dict]:
    return [{"role": "user", "content": text}]


def test_sync_uses_server_now_not_created_at(tmp_path):
    """updated_at 缺省 → 服务端当前时间，而不是三年前的 createdAt。"""
    store = ConversationStore(user_id=1, data_dir=tmp_path)
    store.sync_from_client([{"id": "c1", "messages": _msgs("hi"), "createdAt": 1000.0}])
    assert store.get_conversation("c1")["updated_at"] > 1e9


def test_synced_list_ordering_reflects_last_content_change(tmp_path):
    """按「最后一次内容变更」排序：老对话被追加内容后应排到最前。"""
    store = ConversationStore(user_id=1, data_dir=tmp_path)
    store.sync_from_client([{"id": "old", "messages": _msgs("1"), "createdAt": 1000.0}])
    time.sleep(0.02)
    store.sync_from_client([{"id": "new", "messages": _msgs("2"), "createdAt": 2000.0}])
    time.sleep(0.02)

    changed = _msgs("1") + [{"role": "assistant", "content": "x"}]
    merged = store.sync_from_client([
        {"id": "old", "messages": changed, "createdAt": 1000.0},
        {"id": "new", "messages": _msgs("2"), "createdAt": 2000.0},
    ])["conversations"]

    assert all(c["updatedAt"] > 1e9 for c in merged), f"排序键被写成 0: {merged}"
    assert [c["id"] for c in merged] == ["old", "new"]

    # 内容未变 → 时间不推进，排序保持（防"每次 persist 全量刷新时间"）
    again = store.sync_from_client([
        {"id": "old", "messages": changed, "createdAt": 1000.0},
        {"id": "new", "messages": _msgs("2"), "createdAt": 2000.0},
    ])["conversations"]
    assert [c["id"] for c in again] == ["old", "new"]


def test_explicit_updated_at_from_client_wins(tmp_path):
    """客户端显式给了 updated_at 且内容有变 → 用客户端值（单条保存接口语义不变）。"""
    store = ConversationStore(user_id=1, data_dir=tmp_path)
    store.save_conversation({
        "id": "c1", "messages": _msgs("hi"),
        "created_at": 1000.0, "updated_at": 5000.0,
    })
    assert store.get_conversation("c1")["updated_at"] == 5000.0

    store.save_conversation({
        "id": "c1", "messages": _msgs("changed"),
        "created_at": 1000.0, "updated_at": 6000.0,
    })
    assert store.get_conversation("c1")["updated_at"] == 6000.0
