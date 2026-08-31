"""
本地/轻量语义嵌入层（用于语义去重，可插拔）

用途：知识图谱生成时对新知识点做语义去重（合并"栈/堆栈"等同义概念）。

方案（可插拔，按优先级尝试）：
1. text-embedding-v4（阿里云 API）：项目已有能力，中文语义效果好。
   语义去重只在节点生成时触发、文本量小，API 成本极低。有 key 时优先。
2. sentence-transformers（可选，本地离线）：真正语义嵌入，免 API 成本，
   但依赖 torch（较重），需显式安装。
3. 纯 numpy 哈希嵌入（内置，零依赖兜底）：基于字符 n-gram 的 feature hashing。
   完全离线可用，但语义区分度有限（"栈"vs"堆栈"相似度不高），
   依赖 LLM 二次确认兜底。

设计原则：语义去重是"候选粗筛 + LLM 二次确认"，嵌入只负责粗筛候选，
最终是否合并由 LLM 判断，因此嵌入层允许轻量近似即可。
"""

import logging
import hashlib
import math
from typing import Optional

import numpy as np

from app.core.config import settings

logger = logging.getLogger("ai-tutor")

# 哈希嵌入维度
HASH_DIM = 512
# n-gram 范围
NGRAM_MIN = 1
NGRAM_MAX = 3


def _ngrams(text: str, n_min: int = NGRAM_MIN, n_max: int = NGRAM_MAX):
    """生成文本的字符 n-gram 序列（去空白）"""
    compact = "".join(text.split())
    length = len(compact)
    for n in range(n_min, n_max + 1):
        if n > length:
            break
        for i in range(length - n + 1):
            yield compact[i:i + n]


def hash_embed(text: str, dim: int = HASH_DIM) -> list[float]:
    """
    纯 numpy 哈希嵌入：字符 n-gram → 特征哈希 → TF 加权向量。

    优点：零依赖、离线可用、确定性（同输入同输出）。
    局限：无语义泛化（"堆栈"与"栈"在字面不同时相似度低），
         因此需配合 LLM 二次确认，或在哈希层上叠加同义提示。

    返回:
        归一化后的 embedding 向量（list[float]）
    """
    vec = np.zeros(dim, dtype=np.float32)
    freq: dict[int, int] = {}
    for gram in _ngrams(text):
        h = int(hashlib.md5(gram.encode("utf-8")).hexdigest(), 16)
        idx = h % dim
        freq[idx] = freq.get(idx, 0) + 1
    for idx, count in freq.items():
        # TF 加权 + 符号哈希（保留方向的轻微扰动以区分不同特征）
        sign = 1.0 if (idx & 1) == 0 else -1.0
        vec[idx] += sign * count
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec = vec / norm
    return vec.tolist()


class BaseEmbedder:
    """嵌入器接口"""

    name = "base"

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError

    def similarity(self, a: list[float], b: list[float]) -> float:
        """余弦相似度（要求等长向量）"""
        va = np.asarray(a, dtype=np.float32)
        vb = np.asarray(b, dtype=np.float32)
        if len(va) == 0 or len(vb) == 0 or len(va) != len(vb):
            return 0.0
        na, nb = np.linalg.norm(va), np.linalg.norm(vb)
        if na == 0 or nb == 0:
            return 0.0
        return float(np.dot(va, vb) / (na * nb))


class HashEmbedder(BaseEmbedder):
    """纯 numpy 哈希嵌入（内置，零依赖）"""

    name = "hash"

    def __init__(self, dim: int = HASH_DIM):
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [hash_embed(t, self.dim) for t in texts]


class SentenceTransformerEmbedder(BaseEmbedder):
    """
    sentence-transformers 本地嵌入（可选）。
    未安装时实例化抛 ImportError，由工厂函数捕获回退。
    """

    name = "sentence-transformers"

    def __init__(self, model_name: str = "paraphrase-multilingual-MiniLM-L12-v2"):
        from sentence_transformers import SentenceTransformer
        self._model = SentenceTransformer(model_name)

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [v.tolist() for v in self._model.encode(texts, normalize_embeddings=True)]


class ApiEmbedder(BaseEmbedder):
    """阿里云 text-embedding-v4 API 嵌入（默认首选）"""

    name = "text-embedding-v4"

    def __init__(self):
        self._client = None

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(
                api_key=settings.dashscope_api_key,
                base_url=settings.llm_base_url,
            )
        return self._client

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not settings.dashscope_api_key:
            return []
        try:
            # 语义去重文本量小、调用低频，用同步客户端可接受
            resp = self._get_client().embeddings.create(
                model="text-embedding-v4", input=texts,
            )
            vectors = [None] * len(texts)
            for item in resp.data:
                vectors[item.index] = item.embedding
            return [v for v in vectors if v is not None]
        except Exception as e:
            logger.warning(f"text-embedding-v4 嵌入失败: {e}")
            return []


# 全局单例缓存
_hash_embedder: Optional[BaseEmbedder] = None
_local_embedder: Optional[BaseEmbedder] = None
_api_embedder: Optional[BaseEmbedder] = None


def get_embedder(prefer_api: bool = True, prefer_local: bool = False) -> BaseEmbedder:
    """
    获取嵌入器（按优先级：API → 本地 sentence-transformers → 哈希）。

    参数:
        prefer_api:    是否优先使用 text-embedding-v4 API（有 key 时效果最好）
        prefer_local:  是否优先尝试本地 sentence-transformers（需显式安装）
    """
    global _hash_embedder, _local_embedder, _api_embedder

    # 缓存哈希兜底（懒加载）
    if _hash_embedder is None:
        _hash_embedder = HashEmbedder()

    # 优先本地（用户明确要求离线时）
    if _local_embedder is None and prefer_local:
        try:
            _local_embedder = SentenceTransformerEmbedder()
            logger.info("语义去重使用 sentence-transformers 本地嵌入")
        except Exception:
            _local_embedder = None
    if _local_embedder is not None:
        return _local_embedder

    # 其次 API（默认，效果好）
    if _api_embedder is None and prefer_api:
        if settings.dashscope_api_key:
            _api_embedder = ApiEmbedder()
            logger.info("语义去重使用 text-embedding-v4 API 嵌入")
    if _api_embedder is not None:
        return _api_embedder

    # 最后哈希兜底（离线）
    return _hash_embedder
