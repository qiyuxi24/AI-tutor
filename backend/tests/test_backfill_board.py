"""`scripts/backfill_board.py` 的纯函数契约（章节名 → 板块名）。

板块（`nodes.board`）是「学科 → 板块 → 节点」三级归属树的中间层，前端
`frontend/src/utils/graphTree.js:45` 直接读它。早期建图把概念树的 `boards[]`
拍平丢弃 → 存量 board 几乎全空；回填脚本靠 `nodes.source_ref` 里**真实书章节**
反推，本文件锁住"怎么从章节名推出板块名"这一条判据。

为什么不测 `plan()` 整体：那要多造 nodes/edges 表；这里只锁最容易写错的
正则与"剥不出就留空"的保守行为。
"""
import sys
from pathlib import Path

# 脚本在 backend/scripts/ 下，不在测试包里
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from backfill_board import split_ref, topic_of  # noqa: E402


def test_chapter_prefix_is_stripped():
    """「第N章 <主题>」→ 主题；章号单独返回供同章补全。"""
    assert topic_of("第3章 线性表") == ("3", "线性表")
    assert topic_of("第二章 匀变速直线运动的研究") == ("二", "匀变速直线运动的研究")
    assert topic_of("第12节    串") == ("12", "串")


def test_chapter_without_subtitle_yields_no_topic():
    """只有章号没有副标题 → 主题为空（由同章其他节点补，补不出就留空）。"""
    assert topic_of("第一章") == ("一", "")
    assert topic_of("第3章") == ("3", "")


def test_plain_section_is_kept_as_is():
    """没有「第N章」前缀的章节名原样当主题。"""
    assert topic_of("线性表") == ("", "线性表")
    assert topic_of("") == ("", "")
    assert topic_of("   ") == ("", "")


def test_split_ref_handles_missing_separator():
    assert split_ref("12|第3章 线性表") == ("12", "第3章 线性表")
    assert split_ref("第3章 线性表") == ("", "第3章 线性表")
    assert split_ref("") == ("", "")
    assert split_ref(None) == ("", "")
