"""B2.2 chapterizer：中文多级编号、无目录兜底整段、越界防护"""
from app.core.collector.chapterizer import chapterize


def titles(slices):
    return [s.title for s in slices]


# ── 中文多级编号：章级压制子节 ──────────────────────────────

def test_chinese_chapters_suppress_subsections():
    text = """第一章 绪论
本章介绍背景。
1.1 研究意义
意义阐述。
1.2 研究现状
现状描述。

第二章 栈与队列
本章介绍线性结构。
2.1 栈
后进先出。
2.2 队列
先进先出。
"""
    slices = chapterize(text)
    assert titles(slices) == ["第一章 绪论", "第二章 栈与队列"]
    # 子节并入章内不产生额外切片
    assert "1.1 研究意义" in slices[0].text and "1.2 研究现状" in slices[0].text
    assert "2.2 队列" in slices[1].text
    # 原文保留
    assert "后进先出" in slices[1].text


def test_numeric_ton_one_level_suppresses_nested():
    text = """1、数据结构概述
数组与链表。

1.1 数组
连续存储。

2、算法基础
复杂度分析。

2.1 递归
自调用。
"""
    slices = chapterize(text)
    assert titles(slices) == ["1、数据结构概述", "2、算法基础"]
    assert "1.1 数组" in slices[0].text and "2.1 递归" in slices[1].text


def test_nested_only_splits_at_sections():
    """全书只有 1.1/1.2 级编号（无一级）→ 按最浅层（节）切"""
    text = """1.1 概述
第一段。
1.2 定义
第二段。
2.1 相关定理
第三段。
"""
    slices = chapterize(text)
    assert titles(slices) == ["1.1 概述", "1.2 定义", "2.1 相关定理"]
    assert slices[1].text == "1.2 定义\n第二段。"  # 内容独立不串章


# ── 无目录兜底 ────────────────────────────────────────────

def test_no_heading_whole_text_single_slice():
    body = "纯正文段落。\n没有标题行。\n句子都以句号结尾。"
    slices = chapterize(body)
    assert len(slices) == 1
    assert slices[0].title == "前言"
    assert slices[0].text == body


def test_empty_or_blank_text_returns_empty():
    assert chapterize("") == []
    assert chapterize("   \n  \t") == []


def test_toc_unmatched_falls_back_to_regex():
    text = """第一章 概述
正文内容甲。
第二章 详述
正文内容乙。
"""
    # 目录给了但正文对不上 → 纯规则兜底仍按章切
    slices = chapterize(text, toc=["第X章 不存在"])
    assert titles(slices) == ["第一章 概述", "第二章 详述"]


def test_toc_unmatched_no_heading_returns_whole():
    body = "没有任何标题的正文。\n只是普通文字。"
    slices = chapterize(body, toc=["第一部分 虚构目录"])
    assert len(slices) == 1
    assert slices[0].title == "前言"
    assert slices[0].text == body


# ── 目录锚点优先（含行尾点线页码清洗） ──────────────────────

def test_toc_guides_boundaries_and_cleans_pageno():
    text = """第一章 绪论
章首内容。
1.1 研究意义
节内内容。
第二章 方法
方法内容。
"""
    toc = [
        "第一章 绪论..................1",
        "1.1 研究意义..................3",
        "第二章 方法..................10",
    ]
    slices = chapterize(text, toc=toc)
    # 目录粒度 = 切分粒度（比纯规则更细）
    assert titles(slices) == ["第一章 绪论", "1.1 研究意义", "第二章 方法"]
    assert "节内内容" in slices[1].text


# ── 越界防护 ─────────────────────────────────────────────

def test_single_boundary_with_frontmatter():
    text = "开头序言内容\n\n第一章 绪论\n正文段落。"
    slices = chapterize(text)
    assert titles(slices) == ["前言", "第一章 绪论"]
    assert slices[0].text == "开头序言内容"
    assert slices[-1].text == "第一章 绪论\n正文段落。"


def test_consecutive_headings_no_empty_slices():
    text = "第一章 绪论\n第二章 主体\n主体内容。"
    slices = chapterize(text)
    assert titles(slices) == ["第一章 绪论", "第二章 主体"]
    assert all(s.text.strip() for s in slices)  # 无空切片


def test_sentence_lines_not_treated_as_headings():
    body = "1. 这是正文第一句。\n2. 这是第二句。\n普通段落其余内容。"
    slices = chapterize(body)
    assert len(slices) == 1  # 句末标点行不误切
    assert slices[0].text == body


def test_markdown_and_fullwidth_space_headings():
    text = "# 第1章　绪论\n内容。\n## 1.1　小节\n更多内容。"
    slices = chapterize(text)
    assert titles(slices) == ["第1章 绪论"]  # 子节并入（1.1 不单独成片）
    assert "更多内容" in slices[0].text  # 子节正文并入第一章


def test_last_heading_no_tail_keeps_body():
    text = "第一章 绪论\n内容。\n第二章 收尾"
    slices = chapterize(text)
    assert titles(slices) == ["第一章 绪论", "第二章 收尾"]
    assert slices[-1].text == "第二章 收尾"
