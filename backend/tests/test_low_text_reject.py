"""
B2.3 入库文本质量下限：解析文本 < MIN_PARSE_TEXT_LEN(200) 视为
「图片型/扫描件/不可解析」，warning + 拒绝入库（不落文件节点）。

零网络：长文本成功用例 mock _embed；短文本拒绝用例在分块/嵌入前即抛错，不触发嵌入。
"""
import asyncio

import pytest

from app.core.kb.kb_manager import KbManager, MIN_PARSE_TEXT_LEN


def _run(coro):
    return asyncio.run(coro)


async def _fake_embed(self, texts):
    return [[0.1] * 8 for _ in texts]


@pytest.fixture
def manager(tmp_path, monkeypatch):
    m = KbManager(tmp_path)
    monkeypatch.setattr(KbManager, "_embed", _fake_embed)
    return m


USER = 10001


def test_short_text_rejected_not_indexed(manager):
    """短文本（< 200 字符）抛 ValueError 且不留文件节点记录"""
    short = "# 太短\n只有一句话。"  # 远小于 200 字符
    assert len(short) < MIN_PARSE_TEXT_LEN
    with pytest.raises(ValueError, match="文本过短"):
        _run(manager.upload_and_index(USER, "short.md", short.encode(), None))
    assert manager.stats(USER)["files"] == 0
    assert manager.stats(USER)["chunks"] == 0


def test_boundary_just_below_and_at_threshold(manager):
    """阈值边界：199 字符拒、200 字符收（text.strip 后判定）"""
    below = "数" * (MIN_PARSE_TEXT_LEN - 1)
    with pytest.raises(ValueError, match="文本过短"):
        _run(manager.upload_and_index(USER, "below.md", below.encode(), None))
    assert manager.stats(USER)["files"] == 0

    at = "数" * MIN_PARSE_TEXT_LEN
    node_id = _run(manager.upload_and_index(USER, "at.md", at.encode(), None))
    assert isinstance(node_id, int) and node_id > 0
    assert manager.stats(USER)["files"] == 1


def test_empty_text_still_rejected(manager):
    """空文档保持原有拒绝行为（回归）"""
    with pytest.raises(ValueError):
        _run(manager.upload_and_index(USER, "empty.md", b"", None))
