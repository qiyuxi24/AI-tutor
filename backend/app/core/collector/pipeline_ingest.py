"""
整书/长文切章入库管线（B2.5）

上游采集拿到整书/长文档（PDF 教材、电子课本、长 MD）并解析为纯文本后，
交本管线切章入库，让"一本书"在 KB 目录树中呈 书→章 结构、逐章独立可检索：

    文本 ─ chapterizer.chapterize 规则切章 ─ 逐章 upload_and_index
    目录：自动采集/L{level}/{subject}/{书名}/{章节}.md

对齐既有决策：
- 决策 #22：只切章保留原文切片，不做 LLM 知识卡片（讲解由对话期 RAG 动态生成）
- 决策 #17 + B2.4：vectorize 按长度 —— 整章 >= VECTORIZE_MIN_CHARS 向量化；
  200~阈值间短章只写 whoosh（BM25-only）；< MIN_PARSE_TEXT_LEN 的残章跳过
- 目录前缀与 B1.3 manager 一致（自动采集/L{level}/{subject}），书名为其下一级
  子目录 → B1.8 商用过滤仍按 L{level} 前缀整树排除，零破坏

用法：
    from app.core.collector.pipeline_ingest import ingest_book_chapters
    text, _ = parse_document("教材.pdf", raw)
    out = await ingest_book_chapters(uid, "数据结构", "某教材", text)
    # {"book_folder_id": ..., "chapters": [{...}], "skipped": n}
"""

import logging
from typing import Optional, Sequence

from app.core.collector.chapterizer import BookSlice, chapterize
from app.core.collector.manager import _safe_name
from app.core.kb.kb_manager import CHUNK_SIZE, KbManager, MIN_PARSE_TEXT_LEN, kb_manager

logger = logging.getLogger("ai-tutor")

# 向量化长度阈值：整章文本达到一个完整分块（500 字）才值得做 embedding；
# 更短章节单块语义稀疏，BM25 关键词检索已足够（决策 #17 + B2.4）
VECTORIZE_MIN_CHARS = CHUNK_SIZE


async def _index_slice(kb: KbManager, user_id: int, parent_id: Optional[int],
                       idx: int, slice_: BookSlice, used_names: set[str]) -> dict:
    """入库单个章节切片（文件名冲突自动加序号），返回结果字典"""
    base = _safe_name(slice_.title) or "章节"
    name, i = base, 2
    while name in used_names:
        name = f"{base} ({i})"
        i += 1
    used_names.add(name)
    vectorize = len(slice_.text) >= VECTORIZE_MIN_CHARS
    filename = f"{name}.md"
    node_id = await kb.upload_and_index(
        user_id, filename, slice_.text.encode("utf-8"), parent_id,
        vectorize=vectorize)
    return {
        "index": idx,
        "title": slice_.title,
        "filename": filename,
        "node_id": node_id,
        "vectorized": vectorize,
    }


async def ingest_book_chapters(user_id: int, subject: str, book_title: str,
                               text: str, *,
                               toc: Sequence[str] = (),
                               license_level: str = "L0",
                               kb: Optional[KbManager] = None) -> dict:
    """把整书文本切章后逐章入库 KB。

    参数:
        user_id:       用户 ID
        subject:       学科名（目录 自动采集/L{level}/{subject}/...）
        book_title:    书名（其下子目录；整书无章可切时作为单文档名）
        text:          已解析的整书纯文本
        toc:           可选目录锚点行列表（chapterize 权威边界）
        license_level: L0~L2（进入目录路径）
        kb:            KbManager 实例（默认全局单例，测试注入临时库）

    返回:
        {"book_folder_id": int|None, "chapters": [...], "skipped": int}
        chapters 元素: {"index","title","filename","node_id","vectorized"}
        skipped: 因文本 < MIN_PARSE_TEXT_LEN（残章/纯标题）被跳过的切片数
    """
    kb = kb or kb_manager
    subject = _safe_name(subject)
    book_title = _safe_name(book_title)

    slices = chapterize(text or "", toc=toc, book_title=book_title)
    if not slices:
        return {"book_folder_id": None, "chapters": [], "skipped": 0}

    # 无章节边界（整书单切片且标题即书名）→ 退化为单文档入 {subject} 目录，
    # 对齐 B1.3 manager 单文档路径（自动采集/L{level}/{subject}/{书名}.md）
    whole_fallback = len(slices) == 1 and slices[0].title == book_title

    chapters: list[dict] = []
    skipped = 0
    used: set[str] = set()

    if whole_fallback:
        parent = kb.ensure_folder(user_id, ["自动采集", license_level, subject])
        sl = slices[0]
        if len(sl.text) < MIN_PARSE_TEXT_LEN:
            skipped += 1
            logger.warning("整书 %s 文本过短（%s 字符），已跳过", book_title, len(sl.text))
        else:
            chapters.append(await _index_slice(kb, user_id, parent, 0, sl, used))
        return {"book_folder_id": None, "chapters": chapters, "skipped": skipped}

    # 有章节边界：书目录 + 逐章入库
    book_folder_id = kb.ensure_folder(
        user_id, ["自动采集", license_level, subject, book_title])
    for idx, sl in enumerate(slices):
        if len(sl.text) < MIN_PARSE_TEXT_LEN:
            skipped += 1
            logger.warning("章节 %r 文本过短（%s 字符），已跳过",
                           sl.title, len(sl.text))
            continue
        chapters.append(await _index_slice(kb, user_id, book_folder_id, idx, sl, used))
    return {"book_folder_id": book_folder_id, "chapters": chapters, "skipped": skipped}
