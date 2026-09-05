"""B1.1 collector store：增删查、UNIQUE 去重、cursor 读写"""
from pathlib import Path

import pytest

from app.core.collector.store import CollectorStore


@pytest.fixture
def store(tmp_path: Path) -> CollectorStore:
    return CollectorStore(tmp_path)


def test_add_and_get_resource(store):
    rid = store.add_resource(
        "https://zh.wikipedia.org/wiki/栈",
        content_hash="h1", license_level="L0", subject="数据结构与算法",
    )
    assert rid > 0
    r = store.get_resource(rid)
    assert r["source_url"].endswith("栈")
    assert r["license_level"] == "L0"
    assert r["subject"] == "数据结构与算法"
    assert r["status"] == "pending"
    assert store.get_by_url("https://zh.wikipedia.org/wiki/栈")["id"] == rid


def test_source_url_unique_dedup(store):
    rid1 = store.add_resource("https://example.com/a")
    rid2 = store.add_resource("https://example.com/a", content_hash="other")
    # 同 URL 幂等返回同一 id，不重复插入
    assert rid1 == rid2
    assert len(store.list_resources()) == 1


def test_content_hash_dedup_check(store):
    rid = store.add_resource("https://example.com/a", content_hash="h1")
    # pending 时不算已入库
    assert store.has_content_hash("h1") is False
    store.update_resource_status(rid, "indexed", content_hash="h1", file_node_id=9)
    assert store.has_content_hash("h1") is True
    assert store.has_content_hash("h2") is False
    assert store.get_resource(rid)["file_node_id"] == 9


def test_list_resources_filter(store):
    store.add_resource("https://example.com/a", subject="数学", mode="personal")
    store.add_resource("https://example.com/b", subject="数学", mode="commercial")
    store.add_resource("https://example.com/c", subject="语文")
    assert len(store.list_resources(subject="数学")) == 2
    assert len(store.list_resources(subject="数学", mode="personal")) == 1


def test_delete_resource(store):
    rid = store.add_resource("https://example.com/x")
    assert store.delete_resource(rid) is True
    assert store.get_resource(rid) is None
    assert store.delete_resource(rid) is False


def test_task_cursor_read_write(store):
    tid = store.create_task(user_id=1, subject="数据结构与算法", source="wikipedia")
    assert tid > 0
    t = store.get_task(tid)
    assert t["status"] == "pending"
    assert t["cursor"] == ""
    assert t["processed_count"] == 0

    # 断点续传：处理 3 批后写 cursor
    store.update_task(tid, cursor="continue||page=3", processed_count=3)
    store.update_task(tid, processed_count=5)
    t = store.get_task(tid)
    assert t["cursor"] == "continue||page=3"
    assert t["processed_count"] == 5

    store.update_task(tid, status="failed", error="timeout")
    t = store.get_task(tid)
    assert t["status"] == "failed"
    assert t["error"] == "timeout"


def test_task_finish_sets_finished_at(store):
    tid = store.create_task(user_id=1, subject="线性代数")
    store.update_task(tid, finished=True)
    t = store.get_task(tid)
    assert t["status"] == "finished"
    assert t["finished_at"]


def test_task_cancel_by_status(store):
    tid = store.create_task(user_id=1, subject="数学")
    store.update_task(tid, status="cancelled")
    assert store.get_task(tid)["status"] == "cancelled"


def test_list_tasks_filter(store):
    store.create_task(user_id=1, subject="数学")
    store.create_task(user_id=1, subject="语文")
    store.create_task(user_id=2, subject="数学")
    assert len(store.list_tasks(user_id=1)) == 2
    assert len(store.list_tasks(user_id=1, subject="数学")) == 1
