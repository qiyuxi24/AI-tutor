"""
知识库管理器：编排用户上传文档的解析、分块、向量化与检索

职责：
- 接收上传文件 → 解析为纯文本 → 分块 → 向量化（阿里云 embedding）→ 入库
- 支持按目录范围（文件夹/文件）检索文档片段
- 与知识图谱 RAG（rag_manager）完全分离

数据流：
    上传文件
      → KbStore.add_file（存元数据+解析文本）
      → 分块（通用分块器）
      → 向量化（text-embedding-v4）
      → DocVectorStore.upsert_chunk（写向量）

    对话检索
      → 用户勾选目录 → KbStore.collect_descendant_files 得到文件节点ID列表
      → search(user_id, query, node_ids=范围) 只检索范围内片段
      → 注入系统提示词
"""

import asyncio
import logging
import re
from pathlib import Path
from typing import Optional

from app.core.llm import embed_texts    # 嵌入唯一出口：llm/embed.py（模型名/截断/失败兜底集中一处）
from app.core.kb.kb_store import KbStore
from app.core.kb.doc_vector_store import DocVectorStore
from app.core.hybrid_search.whoosh_index import SparseIndex
from app.core.hybrid_search.fusion import rrf_fuse

logger = logging.getLogger("ai-tutor")

MIN_SCORE = 0.25
# B2.3 入库文本质量下限：解析文本低于该长度视为「图片型/扫描件/不可解析」，拒绝入库
MIN_PARSE_TEXT_LEN = 200
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tiff", ".gif")
# 宽召回候选池大小：向量/BM25 各取多少进融合（业界标准 30~50，取 30 较保守）
RECALL_TOP_K = 30
# 通用分块参数
CHUNK_SIZE = 500        # 目标分块字符数
CHUNK_OVERLAP = 50      # 相邻分块重叠，保证语义连续性

# 父级扩展（Parent-Child）参数
PARENT_EXPAND_CHARS = 1500   # 每个命中子块最多回溯到的父文本字符数（固定预算，父级扩展是预算决策非固定动作）
PARENT_MAX_BLOCKS = 8        # 最多拼接的相邻块数（防止极端长文件拼接过多）

# 数据目录：data/kb
_KB_DIR = Path(__file__).parent.parent.parent.parent / "data" / "kb"


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE,
               overlap: int = CHUNK_OVERLAP) -> list[dict]:
    """
    通用文本分块（按段落优先，超长按字符滑窗）。

    返回:
        [{content, heading, chunk_index}, ...]
        heading 尽量取所在段落首句前 30 字作为摘要
    """
    text = text.strip()
    if not text:
        return []

    # 按段落（连续非空行）切分
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]

    chunks: list[dict] = []
    current = ""
    for para in paragraphs:
        if len(current) + len(para) > chunk_size and current:
            chunks.extend(_split_paragraph_by_chars(current, chunk_size, overlap))
            current = para
        else:
            current += ("\n\n" + para if current else para)

    if current:
        chunks.extend(_split_paragraph_by_chars(current, chunk_size, overlap))

    # 添加 heading 和 chunk_index
    result = []
    for i, c in enumerate(chunks):
        result.append({
            "content": c,
            "heading": _extract_heading(c),
            "chunk_index": i,
        })
    return result


def _split_paragraph_by_chars(text: str, chunk_size: int, overlap: int) -> list[str]:
    """超长段落按字符滑窗切分"""
    if len(text) <= chunk_size:
        return [text]
    result = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        result.append(text[start:end])
        if end >= len(text):
            break
        start = max(start + chunk_size - overlap, start + 1)
    return result


def _extract_heading(text: str, max_len: int = 30) -> str:
    """从片段首句提取标题摘要"""
    first = text.split("\n")[0].strip()
    return first[:max_len]


class KbManager:
    """知识库管理器：管理目录树 + 文档向量化与检索"""

    def __init__(self, data_dir: Optional[Path] = None):
        self.data_dir = Path(data_dir) if data_dir else _KB_DIR
        self._stores: dict[int, KbStore] = {}
        self._vec_stores: dict[int, DocVectorStore] = {}
        self._sparse: dict[int, SparseIndex] = {}

    def _get_store(self, user_id: int) -> KbStore:
        if user_id not in self._stores:
            self._stores[user_id] = KbStore(self.data_dir / str(user_id))
        return self._stores[user_id]

    def _get_vec_store(self, user_id: int) -> DocVectorStore:
        if user_id not in self._vec_stores:
            self._vec_stores[user_id] = DocVectorStore(self.data_dir / str(user_id))
        return self._vec_stores[user_id]

    def _get_sparse(self, user_id: int) -> SparseIndex:
        """获取（并缓存）某用户的 whoosh 稀疏索引"""
        if user_id not in self._sparse:
            self._sparse[user_id] = SparseIndex(self.data_dir / str(user_id))
        return self._sparse[user_id]

    # ────────────────────────────────────────────
    #  嵌入
    # ────────────────────────────────────────────

    async def _embed(self, texts: list[str]) -> list[list[float]]:
        """
        统一走 `llm.embed.embed_texts`（与 rag_manager 同一实现）。

        保留本方法只为不破坏既有"类级 patch `_embed`"的遮罩缝：
        tests/test_pipeline_ingest.py、test_bm25_only_index.py 等与
        scripts/eval_rag.py、scripts/seed_collector.py 均以它替换嵌入实现。
        """
        return await embed_texts(texts)

    # ────────────────────────────────────────────
    #  上传 + 索引
    # ────────────────────────────────────────────

    async def upload_and_index(self, user_id: int, filename: str, content: bytes,
                                parent_id: Optional[int],
                                vectorize: bool = True) -> int:
        """
        上传文件并完成解析 + 向量化索引。

        参数:
            user_id:   用户 ID
            filename:  原始文件名
            content:   文件二进制内容
            parent_id: 上传到哪个文件夹（None=根目录）
            vectorize: 是否向量化。False=短文/题目只写 whoosh（BM25-only，省 embedding）

        返回:
            文件节点 ID（在 KbStore 中的 nodes.id）
            失败时抛 ValueError（格式不支持/解析失败）
        """
        from app.core.kb.parsers import is_supported, parse_document, supported_label
        from pathlib import Path as P
        ext = P(filename).suffix.lower()
        if not is_supported(filename):
            raise ValueError(
                f"不支持的文件格式: {ext}，当前支持 {supported_label()}"
            )

        text, _ = parse_document(filename, content)
        # B2.3 入库文本质量下限：解析文本过短（< 200 字符）视为「图片型/不可解析」，不入库
        if len(text.strip()) < MIN_PARSE_TEXT_LEN:
            logger.warning(
                f"文档 {filename} 解析文本仅 {len(text.strip())} 字符"
                f"（< {MIN_PARSE_TEXT_LEN}），疑似图片型/扫描件/不可解析，拒绝入库"
            )
            if ext in IMAGE_EXTS:
                raise ValueError("图片中未识别到足够文字（纯图片/图表/清晰度不足），无法入库")
            raise ValueError(
                f"文档可提取文本过短（< {MIN_PARSE_TEXT_LEN} 字符），"
                "疑似图片型/扫描件/不可解析，未入库"
            )

        store = self._get_store(user_id)
        node_id = store.add_file(
            user_id=user_id,
            name=filename,
            parent_id=parent_id,
            file_type=ext,
            extract_text=text,
            file_size=len(content),
        )

        # 分块 + 向量化 + 入库
        await self._index_document(user_id, node_id, text, vectorize=vectorize)
        return node_id

    async def _index_document(self, user_id: int, node_id: int, text: str,
                              vectorize: bool = True) -> int:
        """
        将解析好的文档分块并入库（向量库 + whoosh 稀疏索引）。返回分块数。

        vectorize=True（默认）：块带向量入库；embedding 调用失败时自动退化为
        BM25-only（embedding 写 NULL），不阻塞入库，保证关键词仍可检索。
        vectorize=False：短文/题目只写 whoosh，不调用 embedding。
        """
        chunks = chunk_text(text)
        if not chunks:
            return 0

        vec_store = self._get_vec_store(user_id)
        vec_store.delete_node_chunks(user_id, node_id)
        # 同步清理 whoosh 稀疏索引
        self._get_sparse(user_id).delete_node_chunks(node_id)

        embeddings: list[list[float]] = []
        if vectorize:
            embeddings = await self._embed([c["content"] for c in chunks])
            if len(embeddings) != len(chunks):
                # 嵌入结果与文本无法一一对齐（部分/全部失败）：整批退化 BM25-only，避免错位
                embeddings = []

        doc_id = node_id  # 用节点 ID 作为 doc_id（一个文件一个 doc）
        sparse = self._get_sparse(user_id)
        # 先全部写入向量库（拿 chunk_id），再批量写 whoosh（一次 commit，避免逐条段合并开销）
        sparse_docs = []
        for i, chunk in enumerate(chunks):
            emb = embeddings[i] if embeddings else None
            chunk_id = vec_store.upsert_chunk(
                user_id=user_id,
                node_id=node_id,
                doc_id=doc_id,
                chunk_index=chunk["chunk_index"],
                content=chunk["content"],
                heading=chunk["heading"],
                embedding=emb,
            )
            # 同步写入 whoosh 稀疏索引（统一以 chunk_id 为主键）
            sparse_docs.append({
                "chunk_id": chunk_id,
                "node_id": node_id,
                "doc_id": doc_id,
                "chunk_index": chunk["chunk_index"],
                "heading": chunk["heading"],
                "content": chunk["content"],
            })
        if sparse_docs:
            sparse.upsert_bulk(sparse_docs)
        return len(chunks)

    # ────────────────────────────────────────────
    #  删除
    # ────────────────────────────────────────────

    def delete_node(self, user_id: int, node_id: int) -> dict:
        """
        删除节点（文件夹递归删子节点）。同时清理向量与稀疏索引。

        返回: {"deleted_nodes": [...], "deleted_chunks": n}
        """
        store = self._get_store(user_id)
        # 删除前先收集该节点下所有文件节点 ID（删除后节点记录已不存在，无法再查类型）
        file_node_ids = store.collect_descendant_files(user_id, node_id)

        deleted_nodes = store.delete_node_recursive(user_id, node_id)

        # 清理这些文件节点的向量与稀疏索引
        vec_store = self._get_vec_store(user_id)
        deleted_chunks = vec_store.delete_docs_chunks(user_id, file_node_ids)
        self._get_sparse(user_id).delete_chunks(file_node_ids)
        return {"deleted_nodes": deleted_nodes, "deleted_chunks": deleted_chunks}

    # ────────────────────────────────────────────
    #  检索
    # ────────────────────────────────────────────

    async def search(self, user_id: int, query: str,
                     node_ids: list[int] | None = None,
                     top_k: int = 5) -> list[dict]:
        """
        在指定目录范围（文件节点列表）内混合检索（向量 + whoosh BM25，宽召回 + RRF 融合）。

        流程（对齐 RAG 召回优化标准）：
            宽召回：向量、BM25 各取 RECALL_TOP_K（默认 30）条候选
              → RRF 融合：基于排名融合成候选池（对权重不敏感，鲁棒）
              → 精排取前 top_k（默认 5）条进 LLM

        参数:
            user_id:   用户 ID
            query:     查询文本
            node_ids:  限定范围的文件节点 ID 列表（None=检索全部上传文档）
            top_k:     最终返回条数（精排后进 LLM 的数量）

        返回:
            [{chunk_id, node_id, content, heading, score, path}, ...]
            按融合分数降序，且分数 >= MIN_SCORE。
            每个命中项的 content 已做"父级扩展"：命中子 chunk 时回溯拼接相邻块
            形成更完整的上下文（受 PARENT_EXPAND_CHARS 预算约束）。
        """
        if not query.strip():
            return []

        # 路 1：稠密向量语义检索（宽召回）
        embeddings = await self._embed([query])
        vec_results: list[dict] = []
        if embeddings:
            vec_store = self._get_vec_store(user_id)
            vec_results = vec_store.search(
                user_id, embeddings[0], node_ids=node_ids, top_k=RECALL_TOP_K
            )

        # 路 2：稀疏 BM25 关键词检索（宽召回）
        sparse = self._get_sparse(user_id)
        sparse_results = sparse.search(query, node_ids=node_ids, top_k=RECALL_TOP_K)

        # RRF 融合：基于排名融合两路候选（对权重/分数尺度不敏感，鲁棒）
        fused = rrf_fuse([vec_results, sparse_results], min_score=MIN_SCORE)
        results = fused[:top_k]

        # 附加来源路径（溯源）：为每个命中片段计算其在目录树中的完整路径
        self._attach_paths(user_id, results)

        # 父级扩展（Parent-Child）：命中子 chunk 回溯拼接相邻块，提供更完整上下文
        self._expand_parents(user_id, results)
        return results

    def _attach_paths(self, user_id: int, results: list[dict]) -> None:
        """
        为检索结果附加来源目录路径（如 '/数据结构/第2章/栈.md'）。

        原地修改 results，为每项设置 'path' 字段（默认空字符串）。
        用一个共享缓存按 node_id 去重查询，避免同批命中相同文件的重复向上遍历。
        """
        if not results:
            return
        store = self._get_store(user_id)
        cache: dict[int, str] = {}
        for item in results:
            nid = item.get("node_id")
            if nid is None:
                item["path"] = ""
                continue
            if nid not in cache:
                cache[nid] = store.get_node_path(user_id, nid)
            item["path"] = cache[nid]

    def _expand_parents(self, user_id: int, results: list[dict]) -> None:
        """
        父级扩展（Parent-Child）：命中子 chunk 时，回溯拼接其相邻块，提供更完整上下文。

        设计（对齐调研结论）：
        - 检索用小 chunk（已命中，语义聚焦）；返回用大块（父文本，上下文完整）
        - 父级扩展是"预算决策，非固定动作"：受 PARENT_EXPAND_CHARS（固定预算）
          与 PARENT_MAX_BLOCKS（最多相邻块数）双重约束，超出即截断
        - 原地修改 results，将每项 content 替换为拼接后的父文本（含命中块前后相邻块）

        边界：
        - 命中块即文件首/尾块、文件只有一块时安全（前后无块则只拼现有）
        - 同一文件多次命中：用缓存按 node_id 取 chunks，避免重复查询
        """
        if not results:
            return
        vec_store = self._get_vec_store(user_id)
        chunks_cache: dict[int, list[dict]] = {}

        for item in results:
            nid = item.get("node_id")
            chunk_index = item.get("chunk_index")
            if nid is None or chunk_index is None:
                continue

            if nid not in chunks_cache:
                chunks_cache[nid] = vec_store.get_node_chunks(user_id, nid)
            blocks = chunks_cache[nid]
            if not blocks:
                continue

            expanded = self._expand_one(blocks, chunk_index, item.get("content", ""))
            if expanded:
                item["content"] = expanded

    def _expand_one(self, blocks: list[dict], hit_index: int,
                    original: str) -> str:
        """
        对单个命中块做相邻拼接扩展。

        策略（对齐 Parent-Child 调研）：
        - 命中块居中，左右交替扩展相邻块（保证上下文在命中前后均衡分布）
        - 预算：命中块原文 + PARENT_EXPAND_CHARS 追加预算；受 PARENT_MAX_BLOCKS 块数上限
        - 预算不足即停止（父级扩展是预算决策，非固定动作）

        参数:
            blocks:     该文件全部块（按 chunk_index 升序）
            hit_index:  命中块的 chunk_index
            original:   命中块原文

        返回:
            拼接后的父文本；无相邻块可拼或扩展后不比原文更长时返回空串。
        """
        # 找到命中块在列表中的位置（chunk_index 用值匹配，可能不连续）
        pos = None
        for i, b in enumerate(blocks):
            if b["chunk_index"] == hit_index:
                pos = i
                break
        if pos is None:
            return ""

        # 命中块起步，向左、右交替扩展
        selected: list[int] = [pos]
        budget = len(original) + PARENT_EXPAND_CHARS
        left, right = pos - 1, pos + 1
        left_turn = True

        while len(selected) < PARENT_MAX_BLOCKS:
            # 交替取左右方向；某方向到头则只看另一方向
            idx = None
            if left_turn:
                if left >= 0:
                    idx = left
                elif right < len(blocks):
                    idx = right
            else:
                if right < len(blocks):
                    idx = right
                elif left >= 0:
                    idx = left
            if idx is None:
                break  # 两侧都到头

            # 预算检查：加入该块后是否超预算
            new_total = sum(len(blocks[i]["content"] or "") for i in selected) \
                + len(blocks[idx]["content"] or "")
            if new_total > budget:
                break  # 预算不足，停止扩展（至少保留命中块）

            selected.append(idx)
            if left_turn:
                left -= 1
            else:
                right += 1
            left_turn = not left_turn

        selected.sort()
        parts = [blocks[i]["content"] for i in selected]
        joined = "\n\n".join(p for p in parts if p)

        # 预算兜底截断
        if len(joined) > budget:
            joined = joined[:budget]

        # 仅当扩展后有增益才返回
        if joined != original and len(joined) > len(original):
            return joined
        return ""

    # ────────────────────────────────────────────
    #  目录 & 信息
    # ────────────────────────────────────────────

    def build_tree(self, user_id: int) -> list[dict]:
        """返回目录树（嵌套结构）"""
        return self._get_store(user_id).build_tree(user_id)

    def create_folder(self, user_id: int, name: str, parent_id: Optional[int]) -> int:
        return self._get_store(user_id).create_folder(user_id, name, parent_id)

    def ensure_folder(self, user_id: int, parts: list[str]) -> int:
        """
        从根目录按 parts 逐级定位文件夹，不存在则创建，返回末级节点 ID。

        供采集模块入库使用（目录树表达「自动采集/L{level}/{subject}」，零迁移）。
        """
        store = self._get_store(user_id)
        parent: Optional[int] = None
        for name in parts:
            target = None
            for ch in store.get_children(user_id, parent):
                if ch["type"] == "folder" and ch["name"] == name:
                    target = ch["id"]
                    break
            if target is None:
                target = store.create_folder(user_id, name, parent)
            parent = target
        return parent

    def collect_files(self, user_id: int, node_id: int,
                      max_depth: Optional[int] = None) -> list[int]:
        """收集目录范围下的所有文件节点 ID（递归，可选深度限制）"""
        return self._get_store(user_id).collect_descendant_files(user_id, node_id, max_depth)

    def allowed_node_ids(self, user_id: int, mode: str = "personal") -> Optional[list[int]]:
        """
        商用模式检索白名单（文件节点 ID 列表）；None = 不限制（保持现有全库语义）。

        零迁移目录前缀过滤（决策 #23）：采集内容统一落 自动采集/L{level}/... 目录，
        商用模式（仅 L0 开放授权可商用）把「自动采集/L2」（合理使用级，禁止商用）
        整棵子树从检索范围排除；手动上传内容（目录树其余部分）不受影响。
        该用户无任何 L2 内容时返回 None，检索方保持原路径零开销。
        """
        if mode != "commercial":
            return None
        excluded = self._license_excluded_files(user_id)
        if not excluded:
            return None
        return [f for f in self._all_files(user_id) if f not in excluded]

    def _license_excluded_files(self, user_id: int,
                                root_name: str = "自动采集",
                                level_names: tuple[str, ...] = ("L2",)) -> list[int]:
        """收集 自动采集/{level_names} 目录链下的全部文件节点 ID（商用模式排除集）。"""
        store = self._get_store(user_id)
        excluded: set[int] = set()
        for root in store.get_children(user_id, None):
            if root["type"] != "folder" or root["name"] != root_name:
                continue
            for child in store.get_children(user_id, root["id"]):
                if child["type"] == "folder" and child["name"] in level_names:
                    excluded.update(store.collect_descendant_files(user_id, child["id"]))
        return sorted(excluded)

    def _all_files(self, user_id: int) -> list[int]:
        """列出该用户 KB 全部文件节点 ID（含根目录直挂文件）。"""
        store = self._get_store(user_id)
        files: set[int] = set()
        for node in store.get_children(user_id, None):
            if node["type"] == "file":
                files.add(node["id"])
            else:
                files.update(store.collect_descendant_files(user_id, node["id"]))
        return sorted(files)

    def get_node(self, user_id: int, node_id: int) -> Optional[dict]:
        return self._get_store(user_id).get_node(node_id)

    def stats(self, user_id: int) -> dict:
        store = self._get_store(user_id)
        vec_store = self._get_vec_store(user_id)
        tree = store.build_tree(user_id)
        # 统计文件数
        file_count = _count_files(tree)
        return {
            "files": file_count,
            "chunks": vec_store.count(user_id),
            "sparse_chunks": self._get_sparse(user_id).count(),
        }


def _count_files(tree: list[dict]) -> int:
    """递归统计目录树中的文件数"""
    count = 0
    for node in tree:
        if node["type"] == "file":
            count += 1
        elif node.get("children"):
            count += _count_files(node["children"])
    return count


# 全局知识库管理器单例
kb_manager = KbManager()
