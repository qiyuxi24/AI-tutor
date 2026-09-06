"""
B2.5 切章入库管线测试（mock _embed + 临时目录，零网络）：
- 整书多章文本 → 自动采集/L0/{学科}/{书名}/{章节}.md 逐章入库，章节可独立检索
- 长章向量化（vectorize=True）/ 阈值内短章 BM25-only（vectorize=False）
- 残章（纯标题/超短 < MIN_PARSE_TEXT_LEN=200）跳过不落库
- 无章节边界 → 退化为单文档入 {学科} 目录（书名.md），保持 B1.3 路径
"""
import asyncio

import pytest

from app.core.collector.pipeline_ingest import VECTORIZE_MIN_CHARS, ingest_book_chapters
from app.core.kb.kb_manager import KbManager, MIN_PARSE_TEXT_LEN
from tests.test_integration_kb import hash_embed

USER = 42
LONG_CHAPTER = "数据结构与算法"
BOOK = "算法导论：数据结构篇"


def _run(coro):
    return asyncio.run(coro)


async def _fake_embed(self, texts: list[str]) -> list[list[float]]:
    return [hash_embed(t) for t in texts]


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(KbManager, "_embed", _fake_embed)
    km = KbManager(tmp_path / "kb")
    return {"km": km, "user": USER}


def _paths(km: KbManager, user_id: int) -> list[str]:
    out: list[str] = []

    def walk(nodes: list, base: str = "") -> None:
        for n in nodes:
            p = f"{base}/{n['name']}"
            out.append(p)
            if n.get("children"):
                walk(n["children"], p)

    walk(km.build_tree(user_id))
    return out


def _book_text() -> str:
    """长章（>阈值，向量化）+ 短章（>=200 且 < 阈值，BM25-only）+ 纯标题残章"""
    # 长章 > VECTORIZE_MIN_CHARS(500)；短章 >= MIN_PARSE_TEXT_LEN(200) 且 < 500
    long_body = "本章讲解栈与队列。栈是后进先出的线性结构，队列是先进先出的线性结构。" * 15
    short_body = "本章讲哈希冲突处理：开放寻址与链地址法两种基本策略。" * 8
    assert len(long_body) >= VECTORIZE_MIN_CHARS
    assert MIN_PARSE_TEXT_LEN <= len(short_body) < VECTORIZE_MIN_CHARS
    return (
        f"# 第一章 栈与队列\n\n{long_body}\n\n"
        f"# 第二章 哈希表\n\n{short_body}\n\n"
        "# 第三章 练习题"  # 残章：只有标题无正文 → 跳过
    )


# ────────────────────────────────────────────
#  有章节边界：逐章入库 + vectorize 按长度
# ────────────────────────────────────────────

def test_chaptered_book_indexes_under_book_folder(env):
    km = env["km"]
    out = _run(ingest_book_chapters(USER, LONG_CHAPTER, BOOK, _book_text(), kb=km))

    assert out["book_folder_id"]
    # 长章向量化；短章（小于阈值）BM25-only（chapterizer 剥离 md # 前缀）
    by_title = {c["title"]: c for c in out["chapters"]}
    assert by_title["第一章 栈与队列"]["vectorized"] is True
    assert by_title["第二章 哈希表"]["vectorized"] is False
    assert out["skipped"] == 1  # 第三章纯标题残章

    paths = _paths(km, USER)
    assert f"/自动采集/L0/{LONG_CHAPTER}/{BOOK}" in paths
    assert any(p.startswith(f"/自动采集/L0/{LONG_CHAPTER}/{BOOK}/") and p.endswith(".md")
               for p in paths)
    # 残章标题不产生文件
    assert not any("第三章" in p for p in paths)


def test_long_chapter_searchable_and_short_bm25_only(env):
    """长章可向量检索/混合命中；短章无向量（embedding NULL）但 BM25 可命中"""
    km = env["km"]
    out = _run(ingest_book_chapters(USER, LONG_CHAPTER, BOOK, _book_text(), kb=km))
    node_ids = {c["node_id"] for c in out["chapters"]}

    vec_store = km._get_vec_store(USER)
    hits = vec_store.search(USER, hash_embed("栈是后进先出 队列是先进先出"), top_k=10)
    vec_nodes = {h["node_id"] for h in hits}
    assert vec_nodes and vec_nodes <= node_ids

    # 短章无向量 → 纯向量检索不含它
    short_chapter = next(c for c in out["chapters"] if not c["vectorized"])
    assert short_chapter["node_id"] not in vec_nodes

    # 混合检索仍命中短章（BM25 路）
    results = _run(km.search(USER, "开放寻址 链地址法"))
    assert any(r["node_id"] == short_chapter["node_id"] for r in results)


def test_short_chapter_markdown_clean_filename(env):
    """md 标题原文保留在章节文本，文件名为干净标题（无路径/非法字符）"""
    km = env["km"]
    text = "第一章 栈与队列\n" + "栈内容叙述。" * 40
    out = _run(ingest_book_chapters(USER, LONG_CHAPTER, "书名/含: 非法", text, kb=km))
    assert len(out["chapters"]) == 1
    filename = out["chapters"][0]["filename"]
    assert filename.startswith("第一章")
    assert "/" not in filename and ":" not in filename


# ────────────────────────────────────────────
#  无章节边界 → 退化单文档路径（B1.3 兼容）
# ────────────────────────────────────────────

def test_unstructured_book_falls_back_to_single_doc(env):
    km = env["km"]
    body = "这本教材没有任何章节标题。只有通篇连续叙述。" * 15
    out = _run(ingest_book_chapters(USER, LONG_CHAPTER, BOOK, body, kb=km))
    assert out["book_folder_id"] is None
    assert len(out["chapters"]) == 1

    paths = _paths(km, USER)
    assert f"/自动采集/L0/{LONG_CHAPTER}/{BOOK}.md" in paths  # 学科目录下直接放书名.md
    assert not any(p.endswith(".md") and f"/{LONG_CHAPTER}/{BOOK}/" in p for p in paths)


def test_too_short_text_skipped_no_crash(env):
    km = env["km"]
    # 单切片但 < 200 → 跳过且不抛错
    out = _run(ingest_book_chapters(USER, LONG_CHAPTER, "短书", "只有一句话。", kb=km))
    assert out["chapters"] == []
    assert out["skipped"] == 1

    # 空文本同样安全
    empty = _run(ingest_book_chapters(USER, LONG_CHAPTER, "空书", "", kb=km))
    assert empty["chapters"] == [] and empty["skipped"] == 0


def test_toc_precise_slicing(env):
    """目录锚点粒度 = 切分粒度：传 toc 时 1.1 级也会独立成章"""
    km = env["km"]
    text = ("第一章 绪论\n" + "背景介绍内容。" * 30 + "\n"
            "1.1 术语约定\n" + "术语定义内容。" * 30 + "\n")
    toc = ["第一章 绪论..................1", "1.1 术语约定..................3"]
    out = _run(ingest_book_chapters(USER, LONG_CHAPTER, "有目录的书", text,
                                    toc=toc, kb=km))
    assert len(out["chapters"]) == 2
    assert [c["title"] for c in out["chapters"]] == ["第一章 绪论", "1.1 术语约定"]
