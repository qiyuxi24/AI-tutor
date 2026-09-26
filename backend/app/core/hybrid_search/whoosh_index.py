"""
whoosh 稀疏全文索引：为 RAG 检索提供 BM25 关键词检索（kb 上传库 + 图谱节点共用）

设计决策：
- 使用 whoosh（纯 Python 全文检索引擎）提供倒排索引 + BM25F 打分
- 每个用户独立索引目录，与 kb 用户隔离策略一致
- 以 chunk_id 作为文档主键，与向量检索统一主键：
    kb 通路  = doc_chunks.id（NUMERIC）
    图谱通路 = "{node_id}#{chunk_index}"（ID 字符串，图谱 node_id 本身是 TEXT）
- 中英文分字段存储：
    content_zh：自定义中文分析器（CJK 字符 bigram 切分，零额外依赖）
    content_en：StemmingAnalyzer（英文词干），检索时双路查询取并集打分
- 范围过滤（node_ids）通过布尔查询的 must 条件实现，与向量检索的 node_ids 过滤对齐
- 该模块只负责「稀疏检索」，与向量检索、融合完全解耦，可独立测试

说明：whoosh 2.7.4 无内置中文分析器，这里用 NgramFilter(2,2) 对中文字符做
bigram 切分（对中文检索友好），英文走词干化，兼顾中英文且零第三方分词依赖。

⚠️ whoosh 的 schema 存在索引文件里、打开即定型，所以主键类型由 `text_ids` 参数
决定（kb 用 NUMERIC 老索引不迁移；图谱用 ID），检索/写入一律以**索引自带 schema**
为准，不用模块常量覆盖。

索引目录：
    data/kb/{user_id}/whoosh_index/     （kb 通路）
    data/rag/{user_id}/whoosh_index/    （图谱通路）
"""

import logging
import re
from pathlib import Path

from whoosh import index as whoosh_index
from whoosh.analysis import (
    LowercaseFilter,
    NgramFilter,
    RegexAnalyzer,
    RegexTokenizer,
    StemFilter,
    StopFilter,
)
from whoosh.fields import ID, NUMERIC, STORED, TEXT, Schema
from whoosh.qparser import MultifieldParser, OrGroup
from whoosh.query import And, Every, Or, Term

logger = logging.getLogger("ai-tutor")

# ────────────────────────────────────────────
#  中文分析器（自定义，零依赖）
# ────────────────────────────────────────────
# 用正则把连续中文切成单字，再通过 NgramFilter(2,2) 合并为 bigram。
# 这样「人工智能」会切成 人工/工智/智能，查询「智能」可命中。
_CJK_TOKEN = r"[\u4e00-\u9fff]+"  # 连续中文字符串
_ZH_TOKENIZER = RegexTokenizer(expression=_CJK_TOKEN)

# 对中文字符串做 bigram 展开（minsize=maxsize=2）
_ZH_ANALYZER = _ZH_TOKENIZER | NgramFilter(minsize=2, maxsize=2)

# 英文分析器：正则切词 + 小写 + 停用词 + 词干化
_EN_ANALYZER = (
    RegexTokenizer(expression=r"\w+(?:[-_]\w+)*")
    | LowercaseFilter()
    | StopFilter(lang="en")
    | StemFilter(lang="en")
)

def _make_schema(text_ids: bool = False) -> Schema:
    """
    构建索引 schema。

    :param text_ids: False（kb 通路）= chunk_id/node_id 用 NUMERIC；
                     True（图谱通路）= 改用 ID（不分词的字符串主键）。

    图谱 node_id 是 TEXT（如 "stack"），NUMERIC 字段写入/查询都会直接报错；
    而 whoosh schema 存在索引文件里、见 schema 即定型，所以只能参数化分叉 ——
    好处是**存量 kb 索引（NUMERIC）无需迁移**。图谱 schema 额外挂 node_name，
    让 BM25 命中时不必回查图谱就能给出节点展示名。
    """
    fields: dict = dict(
        chunk_id=ID(stored=True, unique=True) if text_ids else NUMERIC(stored=True, unique=True),
        node_id=ID(stored=True) if text_ids else NUMERIC(stored=True),
        doc_id=NUMERIC(stored=True),                   # 文档标识（图谱侧未用，占位 0）
        chunk_index=NUMERIC(stored=True),              # 分块序号
        heading=STORED,                                # 片段标题（透传）
        content=STORED,                                # 片段原文（透传，供融合后返回）
        content_zh=TEXT(analyzer=_ZH_ANALYZER),        # 中文索引字段（bigram）
        content_en=TEXT(analyzer=_EN_ANALYZER),        # 英文索引字段（词干化）
    )
    if text_ids:
        fields["node_name"] = STORED                   # 节点展示名（kb 侧无此字段，保持老 schema）
    return Schema(**fields)


# kb 通路 schema（NUMERIC 主键，与存量索引一致）
_SCHEMA = _make_schema()


class SparseIndex:
    """
    whoosh 稀疏索引（按用户隔离）

    :param data_dir: 用户数据目录（索引放在其下 whoosh_index/）
    :param text_ids: 主键类型开关 —— False=kb（NUMERIC node_id），True=图谱（TEXT node_id）
    """

    def __init__(self, data_dir: Path, text_ids: bool = False):
        self.data_dir = Path(data_dir)
        self.index_dir = self.data_dir / "whoosh_index"
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self._text_ids = text_ids
        self._ix = self._open_or_create()
        # 以索引自带 schema 为准（老 kb 索引是 NUMERIC，被常量覆盖会导致词条编码不匹配 → 静默查不到）
        self._schema = self._ix.schema
        self._has_node_name = "node_name" in self._schema.names()
        self._pending: "AsyncWriter | None" = None  # 延迟提交模式的共享 writer

    def _open_or_create(self):
        if whoosh_index.exists_in(str(self.index_dir)):
            return whoosh_index.open_dir(str(self.index_dir))
        return whoosh_index.create_in(str(self.index_dir), _make_schema(self._text_ids))

    def _doc_fields(self, chunk_id, node_id, doc_id, chunk_index,
                    heading: str, content: str, node_name: str | None = None) -> dict:
        """组装待写入字段（node_name 仅在 schema 含该字段时携带，kb 侧 schema 没有）。"""
        fields = dict(
            chunk_id=chunk_id, node_id=node_id, doc_id=doc_id,
            chunk_index=chunk_index, heading=heading, content=content,
            content_zh=content, content_en=content,
        )
        if node_name is not None and self._has_node_name:
            fields["node_name"] = node_name or ""
        return fields

    def close(self) -> None:
        try:
            self.flush()
            self._ix.close()
        except Exception as e:  # pragma: no cover - 关闭异常不影响主流程
            logger.debug(f"whoosh 索引关闭异常: {e}")

    # ────────────────────────────────────────────
    #  延迟提交（批量模式，大幅降低 commit 次数）
    # ────────────────────────────────────────────

    def begin_deferred(self) -> None:
        """
        进入延迟提交模式：后续写入/删除合并到同一个 writer，
        直到 flush() 才一次性 commit。适用于批量导入/重建索引。

        注意：不调用 begin_deferred 时行为不变（每次操作独立提交）。
        """
        if self._pending is None:
            self._pending = self._ix.writer()

    def is_deferred(self) -> bool:
        return self._pending is not None

    def flush(self) -> None:
        """提交并退出延迟提交模式（无 pending 时为空操作）"""
        if self._pending is not None:
            self._pending.commit()
            self._pending = None

    def _writer(self):
        """
        取当前 writer：延迟模式复用 `_pending`，否则新建一个**同步** writer。

        为什么不用 whoosh 的 AsyncWriter：它的 commit() 是"另起线程 replay"，
        方法返回时索引未必可见/顺序未必正确 —— 而本项目的用法是"索引完立刻检索"
        （图谱节点增量索引 → 下一轮对话就查），会静默漏召回。同步 writer 把这次
        commit 落到调用栈上，语义与 SQLite 索引侧一致。
        """
        return self._pending if self._pending is not None else self._ix.writer()

    def _commit(self, writer) -> None:
        """非延迟模式下立即同步提交；延迟模式下由 flush() 统一提交"""
        if self._pending is None:
            writer.commit()

    # ────────────────────────────────────────────
    #  写入
    # ────────────────────────────────────────────

    def upsert_chunk(self, chunk_id, node_id, doc_id: int,
                     chunk_index: int, heading: str, content: str,
                     node_name: str = "") -> None:
        """
        写入/更新单个片段（chunk_id 唯一，重复写自动覆盖）

        返回结果通过 whoosh 唯一字段保证幂等：
        同一 chunk_id 再次写入会删除旧文档再插入新文档。
        """
        writer = self._writer()
        try:
            writer.update_document(**self._doc_fields(
                chunk_id, node_id, doc_id, chunk_index, heading, content, node_name,
            ))
            self._commit(writer)
        except Exception as e:
            logger.error(f"whoosh 写入片段失败 (chunk_id={chunk_id}): {e}")
            if self._pending is None:
                try:
                    writer.cancel()
                except Exception:
                    pass

    def upsert_bulk(self, docs: list[dict]) -> None:
        """
        批量写入/更新多个片段（一次 writer、一次 commit，避免逐条 commit 的段合并开销）

        :param docs: [{chunk_id, node_id, doc_id, chunk_index, heading, content[, node_name]}, ...]
        """
        if not docs:
            return
        writer = self._writer()
        try:
            for d in docs:
                writer.update_document(**self._doc_fields(
                    d["chunk_id"], d["node_id"], d["doc_id"], d["chunk_index"],
                    d["heading"], d["content"], d.get("node_name"),
                ))
            self._commit(writer)
        except Exception as e:
            logger.error(f"whoosh 批量写入片段失败 ({len(docs)} 条): {e}")
            if self._pending is None:
                try:
                    writer.cancel()
                except Exception:
                    pass

    def delete_node_chunks(self, node_id) -> None:
        """删除某节点（kb 文件节点 / 图谱节点）的全部分块"""
        writer = self._writer()
        try:
            writer.delete_by_query(Term("node_id", node_id))
            self._commit(writer)
        except Exception as e:
            logger.error(f"whoosh 删除节点索引失败 (node_id={node_id}): {e}")

    def delete_chunks(self, node_ids: list) -> None:
        """删除一批文件节点（含子节点）的全部分块"""
        if not node_ids:
            return
        writer = self._writer()
        try:
            writer.delete_by_query(Or([Term("node_id", nid) for nid in node_ids]))
            self._commit(writer)
        except Exception as e:
            logger.error(f"whoosh 批量删除索引失败: {e}")

    def clear_user(self) -> None:
        """清空当前用户索引（重建时调用）"""
        writer = self._writer()
        try:
            writer.delete_by_query(Every("chunk_id"))  # 删除全部文档
            self._commit(writer)
        except Exception as e:
            logger.error(f"whoosh 清空索引失败: {e}")

    # ────────────────────────────────────────────
    #  检索
    # ────────────────────────────────────────────

    def search(self, query: str, node_ids: list | None = None,
               top_k: int = 5) -> list[dict]:
        """
        BM25 稀疏检索。

        参数:
            query:    查询文本
            node_ids: 限定范围的节点 ID 列表（None=检索全部；kb 传 int，图谱传 str）
            top_k:    返回条数

        返回:
            [{chunk_id, node_id, doc_id, chunk_index, heading, content, score[, node_name]}, ...]
            按 BM25 分数降序。score 已归一化到 (0,1] 便于与向量分数加权。
        """
        if not query.strip():
            return []

        qp = MultifieldParser(
            ["content_zh", "content_en"], schema=self._schema, group=OrGroup
        )
        try:
            parsed = qp.parse(query)
        except Exception as e:
            logger.debug(f"whoosh 查询解析失败: {e}")
            return []

        # 范围过滤：node_ids 作为 must 条件叠加
        if node_ids:
            scope = Or([Term("node_id", nid) for nid in node_ids])
            combined = And([parsed, scope])
        else:
            combined = parsed

        results = []
        try:
            with self._ix.searcher() as searcher:
                hits = searcher.search(combined, limit=top_k)
                for hit in hits:
                    score = hit.score if hit.score > 0 else 0.0
                    # BM25 分数归一化：线性压到 (0,1]
                    norm = min(1.0, score / (score + 1.0)) if score else 0.0
                    item = {
                        "chunk_id": hit["chunk_id"],
                        "node_id": hit["node_id"],
                        "doc_id": hit["doc_id"],
                        "chunk_index": hit["chunk_index"],
                        "heading": hit["heading"],
                        "content": hit["content"],
                        "score": round(norm, 4),
                    }
                    if self._has_node_name:
                        item["node_name"] = hit["node_name"] or ""
                    results.append(item)
        except Exception as e:
            logger.error(f"whoosh 检索失败: {e}")
            return []
        return results

    def count(self) -> int:
        """返回当前用户索引的文档（片段）总数"""
        try:
            with self._ix.searcher() as searcher:
                return searcher.doc_count()
        except Exception:
            return 0
