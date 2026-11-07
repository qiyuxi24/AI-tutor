"""对话同步**时间戳单位**一致性（P0，2026-11-05）。

背景（Lead 逐行复核源码确认）
----------------------------
前端 `chatStore.js` 用 `Date.now()`（毫秒 ~1.7e12），后端
`conversation_store.py` 用 `time.time()`（秒 ~1.7e9）。两处防覆盖判断都直接
比较这两个数：

- 后端 `save_conversation`：`float(current["updated_at"] or 0) > incoming_updated`
  —— current 是秒、incoming 是毫秒 → 恒假 → `ON CONFLICT DO UPDATE` 用旧设备
  缓存覆盖服务端新历史（"服务端权威 / 多端防覆盖"失效）。
- 前端 `adoptRemote`：`remoteAt > localAt` —— 远端秒、本地毫秒 → 恒假 →
  客户端永不采纳服务端较新版本。

本文件锁死**后端边界归一化**：`save_conversation` / `sync_from_client` 入口对
明显是毫秒（`> 1e12`）的 `updated_at`（以及 `createdAt` 跟着换算，避免 created
与 updated 单位劈叉）归一化成秒。这是防"老前端 / 第三方击穿"的双向防御之一，
即使前端换算没生效，服务端也不会被毫秒值击穿。

全离线：`ConversationStore` 一律落 `tmp_path`（与
`test_conversation_authority.py` 同款）。
"""
import time

from app.core.conversation_store import ConversationStore

USER_ID = 1


def _msgs(text: str) -> list[dict]:
    return [{"role": "user", "content": text}]


def _store(tmp_path) -> ConversationStore:
    return ConversationStore(user_id=USER_ID, data_dir=tmp_path / "conv")


# ══════════════════════════════════════════════════════════════════
#  1. 真实错配场景：秒级 current vs 毫秒级 incoming
# ══════════════════════════════════════════════════════════════════

def test_seconds_current_rejects_milliseconds_incoming_overwrite(tmp_path):
    """库里是秒级 updated_at，客户端上行毫秒级旧快照 → 必须拒绝覆盖。

    复现路径：服务端先由 `append_turn` 落一条较新历史（秒），然后一台持有
    旧缓存的设备带着毫秒级 `updated_at` 做 sync。修复前 `current(秒) >
    incoming(毫秒)` 恒假 → 旧消息盖掉新历史。
    """
    store = _store(tmp_path)
    try:
        store.append_turn("c1", "问", "服务端较新回答")
        current = store.get_conversation("c1")
        current_at = current["updated_at"]
        assert 1e9 < current_at < 1e12, f"前提：服务端应为秒级，实际 {current_at}"

        # 旧设备的本地快照是毫秒级，且时间点比服务端历史更早（2020 vs now）
        stale_ms = 1_600_000_000_000
        returned = store.save_conversation({
            "id": "c1",
            "messages": _msgs("旧设备缓存"),
            "created_at": stale_ms,
            "updated_at": stale_ms,
        })

        # 防覆盖：新历史没被盖回去；且"返回库里版本"的契约保持（秒）
        after = store.get_conversation("c1")
        assert after["messages"][-1]["content"] == "服务端较新回答", "旧快照覆盖了服务端新历史"
        assert after["updated_at"] == current_at, "被拒写入不应推进 updated_at"
        assert returned == current_at, "应返回库里版本，供调用方刷新本地快照"
    finally:
        store.close()


def test_sync_from_client_rejects_normalized_stale_milliseconds_snapshot(tmp_path):
    """归一化后旧快照被正确拒绝（sync 全量路径同样生效）。

    库里版本是较新的秒级时间；上行 `updatedAt` 是毫秒级但换算成秒后更旧 →
    归一化后走 `current > incoming` 的拒绝分支。没有归一化时，毫秒值会直接
    绕过比较被写进库（且把 updated_at 污染成毫秒，后续排序/再比较全乱）。
    """
    store = _store(tmp_path)
    try:
        newer_sec = time.time()
        store.save_conversation({
            "id": "c1",
            "messages": _msgs("新历史"),
            "created_at": newer_sec - 100,
            "updated_at": newer_sec,
        })

        older_ms = (newer_sec - 3600) * 1000   # 比库里早 1 小时，毫秒
        merged = store.sync_from_client([{
            "id": "c1",
            "messages": _msgs("旧快照"),
            "createdAt": older_ms,
            "updatedAt": older_ms,
        }])["conversations"]

        assert merged[0]["messages"][-1]["content"] == "新历史", "归一化后旧快照仍被写入"
        # 库里 updated_at 仍是秒级、未被毫秒污染
        assert 1e9 < merged[0]["updatedAt"] < 1e12, f"updated_at 被毫秒污染: {merged[0]['updatedAt']}"
        # createdAt 也换算成秒，与 updatedAt 同单位
        assert 1e9 < merged[0]["createdAt"] < 1e12
    finally:
        store.close()
