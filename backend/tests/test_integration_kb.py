"""
端到端集成测试：mock 确定性向量，走完整「上传 → 分块 → 向量化 → 入库 → 检索」链路。

覆盖：
- 上传+索引链路完整性（文件/分块/稀疏索引三处一致）
- 混合检索命中相关文档（语义路 + BM25 路双保险）
- 目录范围过滤（限定 node_ids 只返回范围内命中）
- path 溯源（命中片段带目录路径）
- 父级扩展（长文档命中子块 → content 变长且含命中块）
- 删除清理（删节点后索引一并清除，不再命中）
- 用户隔离（user A 检索不到 user B 的文档）

零网络依赖：mock 掉 _embed，用确定性 bigram hash 向量
（共享 n-gram 的文本余弦更高，语义可测）。
"""
import asyncio
import hashlib

import numpy as np
import pytest

from app.core.kb.kb_manager import KbManager


def _run(coro):
    return asyncio.run(coro)


def hash_embed(text: str, dim: int = 256) -> list[float]:
    """确定性 hash 嵌入：字符 bigram → 槽位累加 → L2 归一化。"""
    vec = np.zeros(dim, dtype=np.float32)
    for i in range(len(text) - 1):
        tok = text[i:i + 2]
        h = int(hashlib.md5(tok.encode("utf-8")).hexdigest()[:8], 16)
        vec[h % dim] += 1.0
    norm = np.linalg.norm(vec)
    return (vec / norm).tolist() if norm else vec.tolist()


async def _fake_embed(self, texts: list[str]) -> list[list[float]]:
    return [hash_embed(t) for t in texts]


@pytest.fixture
def manager(tmp_path, monkeypatch):
    m = KbManager(tmp_path)
    # 类级 patch 才会触发 descriptor 绑定（实例级 setattr 不传 self）
    monkeypatch.setattr(KbManager, "_embed", _fake_embed)
    return m


USER = 10001
OTHER = 10002

STACK_DOC = """# 栈（Stack）

栈是后进先出（LIFO）的线性数据结构。栈只允许在一端（称为栈顶）进行插入和删除操作。

栈的基本操作包括压栈（push）、出栈（pop）和查看栈顶（peek）。压栈在栈顶加入一个元素，
出栈移除栈顶元素，peek 只读取不删除。

栈的应用场景非常广泛：函数调用栈、表达式求值、括号匹配、浏览器前进后退、撤销操作等。
递归函数的调用过程就是典型的栈应用，每层调用压栈，返回时出栈。

栈的实现方式主要有顺序栈和链式栈两种。顺序栈用数组实现，链式栈用链表实现，
两者在时间复杂度和空间复杂度上各有优劣。

栈的进阶问题包括最小栈（支持 O(1) 取最小元素）、单调栈（用于求解下一个更大元素）等。

与栈相关的经典笔试题还有：用两个栈实现队列、括号匹配校验、逆波兰表达式求值、
每日温度（单调栈求解下一个更大元素的天数差）等。掌握这些题目能显著提升对栈的理解。

栈与递归关系密切：任何递归程序都可以改写为非递归形式，其中往往要用到显式的栈。
树的先序、中序、后序遍历都可以借助栈实现非递归版本，理解栈对学习树和图至关重要。

栈的典型应用还有中缀表达式转后缀、迷宫求解、汉诺塔移动、进制转换、行编辑器、
函数调用与返回地址保存等。深入理解栈的结构特点，有助于解决编程竞赛与面试中的大量算法题，
例如有效的括号、最长有效括号、接雨水、柱状图中最大的矩形等高频考题。
"""

QUEUE_DOC = """# 队列（Queue）

队列是先进先出（FIFO）的线性数据结构。队列只允许在队尾插入元素（入队），在队首删除元素（出队）。

队列的基本操作包括入队（enqueue）、出队（dequeue）和查看队首（front）。入队在队尾添加元素，
出队移除队首元素。

队列的应用场景：任务调度、打印机缓冲、消息队列、广度优先搜索（BFS）、滑动窗口等。
操作系统中的进程调度队列就是典型应用。

队列的实现方式有顺序队列、循环队列和链式队列。循环队列能有效利用数组空间，
避免顺序队列的假溢出问题。

队列的变体包括双端队列（deque，两端都可入队出队）和优先队列（按优先级出队）。
"""


# ────────────────────────────────────────────
#  上传 + 索引链路
# ────────────────────────────────────────────

def test_upload_and_index_full_pipeline(manager):
    """上传 .md → 解析 → 分块 → 向量化 → 入库，三处索引数量一致"""
    node_id = _run(manager.upload_and_index(USER, "栈.md", STACK_DOC.encode(), None))
    assert isinstance(node_id, int) and node_id > 0

    stats = manager.stats(USER)
    assert stats["files"] == 1
    assert stats["chunks"] > 1          # 向量路已入库多块
    assert stats["sparse_chunks"] == stats["chunks"]  # 稀疏路同步

    # 目录树里有这个文件节点
    tree = manager.build_tree(USER)
    assert tree[0]["name"] == "栈.md"
    assert tree[0]["type"] == "file"


def test_upload_unsupported_format_rejected(manager):
    """不支持格式应抛 ValueError（解析器注册表路由）"""
    with pytest.raises(ValueError):
        _run(manager.upload_and_index(USER, "x.xyz", b"data", None))


def test_upload_empty_text_rejected(manager):
    """空内容抛 ValueError"""
    with pytest.raises(ValueError):
        _run(manager.upload_and_index(USER, "empty.md", b"", None))


# ────────────────────────────────────────────
#  检索
# ────────────────────────────────────────────

def test_search_hits_relevant_doc(manager):
    """两篇不同主题文档，查询命中最相关的一篇"""
    sid = _run(manager.upload_and_index(USER, "栈.md", STACK_DOC.encode(), None))
    qid = _run(manager.upload_and_index(USER, "队列.md", QUEUE_DOC.encode(), None))

    results = _run(manager.search(USER, "栈的压栈出栈操作", top_k=3))
    assert results, "应至少命中一条"
    top = results[0]
    assert top["node_id"] == sid, "top1 应是栈文档"
    assert "栈" in top["content"]

    results2 = _run(manager.search(USER, "队列的入队出队先进先出", top_k=3))
    assert results2[0]["node_id"] == qid, "top1 应是队列文档"


def test_search_directory_scope(manager):
    """目录范围过滤：限定 node_ids 后只返回范围内命中"""
    folder_a = manager.create_folder(USER, "数据结构", None)
    folder_b = manager.create_folder(USER, "算法", None)
    sid = _run(manager.upload_and_index(USER, "栈.md", STACK_DOC.encode(), folder_a))
    _run(manager.upload_and_index(USER, "队列.md", QUEUE_DOC.encode(), folder_b))

    # 只检索文件夹 A（递归收集其文件）
    scope = manager.collect_files(USER, folder_a)
    assert scope == [sid]

    results = _run(manager.search(USER, "栈的压栈出栈", node_ids=scope, top_k=3))
    assert results, "范围内应命中"
    assert all(r["node_id"] == sid for r in results), "不应混入范围外文档"


def test_search_path_attached(manager):
    """检索结果带来源路径（溯源元数据）"""
    folder = manager.create_folder(USER, "数据结构", None)
    _run(manager.upload_and_index(USER, "栈.md", STACK_DOC.encode(), folder))

    results = _run(manager.search(USER, "栈的压栈出栈", top_k=3))
    assert results
    assert results[0]["path"].endswith("/数据结构/栈.md"), results[0]["path"]


def test_search_parent_expand_enriches(manager):
    """父级扩展：命中子块后 content 拼接相邻块变长"""
    _run(manager.upload_and_index(USER, "栈.md", STACK_DOC.encode(), None))

    # 检索一个只出现在某个子块的短语，且该文档是多块长文档
    results = _run(manager.search(USER, "单调栈 下一个更大元素", top_k=3))
    assert results, "长查询应命中"
    # 命中内容应包含查询中的关键子串（说明命中块确实相关）
    assert any("单调栈" in r["content"] or "下一个更大" in r["content"] for r in results)
    # 扩展后内容比单块更长（父级扩展生效的标志之一：content 超过 CHUNK_SIZE 或含多个段落）
    expanded = results[0]["content"]
    assert "\n\n" in expanded or len(expanded) > 200, "父级扩展后应拼接相邻块"


def test_search_no_query_returns_empty(manager):
    _run(manager.upload_and_index(USER, "栈.md", STACK_DOC.encode(), None))
    assert _run(manager.search(USER, "  ", top_k=3)) == []


# ────────────────────────────────────────────
#  删除
# ────────────────────────────────────────────

def test_delete_node_cleans_all_indexes(manager):
    """删除文件后：目录树、向量、稀疏索引全部清理，检索不再命中"""
    node_id = _run(manager.upload_and_index(USER, "栈.md", STACK_DOC.encode(), None))
    before = manager.stats(USER)
    assert before["chunks"] > 0

    deleted = manager.delete_node(USER, node_id)
    assert deleted["deleted_nodes"] == [node_id]
    assert deleted["deleted_chunks"] == before["chunks"]

    stats = manager.stats(USER)
    assert stats["files"] == 0 and stats["chunks"] == 0 and stats["sparse_chunks"] == 0
    assert _run(manager.search(USER, "栈的压栈出栈", top_k=3)) == []


def test_delete_folder_recursive(manager):
    """删除文件夹应递归删除其中所有文件并清理索引"""
    folder = manager.create_folder(USER, "数据结构", None)
    _run(manager.upload_and_index(USER, "栈.md", STACK_DOC.encode(), folder))
    _run(manager.upload_and_index(USER, "队列.md", QUEUE_DOC.encode(), folder))

    deleted = manager.delete_node(USER, folder)
    assert len(deleted["deleted_nodes"]) == 3  # 文件夹 + 2 文件
    assert manager.stats(USER)["files"] == 0
    assert manager.stats(USER)["chunks"] == 0


# ────────────────────────────────────────────
#  用户隔离
# ────────────────────────────────────────────

def test_user_isolation(manager):
    """user A 的文档对 user B 不可见"""
    _run(manager.upload_and_index(USER, "栈.md", STACK_DOC.encode(), None))

    assert manager.stats(OTHER)["files"] == 0
    assert manager.stats(OTHER)["chunks"] == 0
    assert _run(manager.search(OTHER, "栈的压栈出栈", top_k=3)) == []

    # 越权访问：直接查 OTHER 下不存在的节点路径为空
    assert manager._get_store(OTHER).get_node_path(OTHER, 1) == ""
