"""
先修关系（prerequisite）多准则无监督推断

## 问题

目前 prerequisite 边完全由 LLM 在生成图谱时一次性给出：不可复现、不可解释、
无法量化准确率（"为什么 A 是 B 的前置？"答不出），也无法评测。

## 方法（核心思路）

学术界把这件事叫概念先修关系识别（Concept Prerequisite Relation, PR）。
本模块参照 Alatrash et al., IJCKG 2025（arXiv:2509.05393）的**多准则投票**路线：
对同一学科内的每个「有序节点对」(A → B)，让多条**互相独立**的准则各自投票
（支持 / 弃权 / 反对），加权汇总成分数，超过阈值才认定为先修关系。
每条候选边都携带**逐准则证据**，因此结论可解释、可复现、可调参。

论文的四个特征族 → 本项目的落地映射（我们没有 Wikipedia 超链接，也没有标注数据）：

| 特征族 | 论文信号 | 本项目准则 | 权重 |
|---|---|---|---|
| 链接族 | Wikipedia 超链接 | C1 正文引用（讲解 B 的正文里引用 A 的名字，且方向不对称） | 3.5 |
| 文本族 | 术语构成 / 难度 | C2 名称包含（"树" ⊂ "二叉树"）、C6 难度差 | 2.5 / 1.0 |
| 文本族 | 语义距离 | C5 嵌入相似度（高度相似 → **反对**：那是同义词，该合并而非连先修边） | 2.0 |
| 图族   | 邻域结构 | C4 共同前置邻居（同层概念 → **反对**） | 1.5 |
| 文档族 | 文档位置 | C3 入库顺序（≈ 教材扫读顺序，见下） | 1.0 |

分数 = Σ(权重 × 投票) / Σ(全部权重)，取值 [-1, 1]；全准则同向 = 1。
阈值是**唯一的召回/精度旋钮**（调高更保守）。

### 与论文的差异（诚实记录）
- 论文 10 条准则 + 投票；本模块只实现 6 条 —— 其余准则依赖 Wikipedia 超链接、
  课程先修标注等本项目没有的信号。缺失信号一律**弃权**，不猜。
- 我们用加权和而非多数票：证据强度差别很大（正文引用 >> 难度差）。
- 我们多一层**无环保证**：候选按分数降序贪心加边，丢弃会成环的边
  （先修关系必须是 DAG，否则学习路径无从拓扑排序）。

### C3 文档族的近似与天花板
`generate_subject_graph` 按「书序 × 分块序」顺序写库，所以 `created_at` 顺序
≈ 教材扫读顺序。这是**近似**：手动创建的节点、二次增量生成的节点会打乱它。
调用方可传 `order=` 显式给出权威顺序（如教材章节号）覆盖该近似。
`ponytail:` 顺序信号只做弱票（权重 1.0），要更准就传 order，不要加大权重。

## 设计
- `infer_prerequisites` 是**纯函数**：输入节点/边/正文/嵌入器，输出候选边，不碰数据库、无副作用。
- `apply_candidates` 是唯一的写库入口（供 API 层调用），逐条容错。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("ai-tutor")

# ── 准则权重（分母固定 = 全部权重之和，便于用一个阈值当召回/精度旋钮）──
W_REFERENCE = 3.5    # C1 正文引用
W_NAME = 2.5         # C2 名称包含
W_SYNONYM = 2.0      # C5 嵌入相似（反对票）
W_COMMON = 1.5       # C4 共同前置（反对票）
W_ORDER = 1.0        # C3 入库/教材顺序
W_DIFFICULTY = 1.0   # C6 难度差
TOTAL_WEIGHT = W_REFERENCE + W_NAME + W_SYNONYM + W_COMMON + W_ORDER + W_DIFFICULTY

# 认定阈值：0.30 ≈ "一条强准则(3.5/11.5) 或 一条中等准则+一条弱准则" 即可成立
DEFAULT_THRESHOLD = 0.30
# 嵌入相似度高于此值 → 视为同义词（应去重，不是先修关系）
SYNONYM_SIM = 0.90
# 单字概念（栈/树/图）在中文里天然是其他术语的子串（"树"命中"二叉树"），
# 误命中率高 → 要求出现 ≥2 次才算一次引用；2 字及以上名称出现 1 次即可。
SHORT_NAME_LEN = 2
SHORT_NAME_MIN_HITS = 2
# 单个节点最多接受多少条入边（先修父节点），防止枢纽概念堆满噪声边
DEFAULT_MAX_PARENTS = 5


# ══════════════════════════════════════════════════════════════════
#  数据结构
# ══════════════════════════════════════════════════════════════════

@dataclass
class Vote:
    """一条准则对某个有序对的投票"""
    criterion: str
    value: int        # +1 支持 / 0 弃权 / -1 反对
    weight: float
    reason: str

    def to_dict(self) -> dict:
        return {"criterion": self.criterion, "value": self.value,
                "weight": self.weight, "reason": self.reason}


@dataclass
class PrereqCandidate:
    """一条被推断出的先修边（A → B：A 是 B 的前置）"""
    source: str
    target: str
    score: float
    votes: list[Vote]

    @property
    def reasons(self) -> list[str]:
        """只保留有方向性的证据（弃权的理由不进结论）"""
        return [f"{v.criterion}: {v.reason}" for v in self.votes if v.value != 0]

    def to_dict(self, names: Optional[dict] = None) -> dict:
        names = names or {}
        return {
            "from": self.source,
            "to": self.target,
            "from_name": names.get(self.source, ""),
            "to_name": names.get(self.target, ""),
            "score": round(self.score, 4),
            "votes": [v.to_dict() for v in self.votes],
        }


class _Context:
    """一次推断所需的预计算上下文（避免每个准则重复算）"""

    def __init__(self, nodes: list[dict], edges: list[dict],
                 content: dict, order: Optional[dict],
                 embedder, vectors: Optional[dict]):
        self.nodes = nodes
        self.by_id = {n["id"]: n for n in nodes}
        self.name = {n["id"]: str(n.get("name", "")).strip() for n in nodes}
        self.summary = {n["id"]: str(n.get("summary", "")).strip() for n in nodes}
        self.content = content or {}
        self.order = self._ranks(nodes, order)
        self.embedder = embedder
        self.vectors = vectors or {}

        # 已有 prerequisite 结构：前驱集合 + 邻接（用于跨跳过滤与环检测）
        self.preds: dict[str, set] = {n["id"]: set() for n in nodes}
        self.adj: dict[str, set] = {n["id"]: set() for n in nodes}
        for e in edges:
            if e.get("relation") != "prerequisite":
                continue
            f, t = e.get("from_node"), e.get("to_node")
            if f in self.adj and t in self.preds:
                self.adj[f].add(t)
                self.preds[t].add(f)
        self.existing_prereq = {(f, t) for f in self.adj for t in self.adj[f]}

    @staticmethod
    def _ranks(nodes: list[dict], order: Optional[dict]) -> dict:
        """文档族次序：显式 order 优先，否则按 created_at（≈ 教材扫读顺序）"""
        if order:
            return {n["id"]: int(order[n["id"]]) for n in nodes if n["id"] in order}
        ranked = sorted(nodes, key=lambda n: (str(n.get("created_at") or ""), str(n["id"])))
        return {n["id"]: i for i, n in enumerate(ranked)}

    def signature(self, node_id: str) -> str:
        """嵌入签名：名称 + 摘要（与 graph_generator 的去重签名同构）"""
        s = self.name.get(node_id, "")
        summ = self.summary.get(node_id, "")
        return f"{s}。 {summ[:60]}" if summ else s


# ══════════════════════════════════════════════════════════════════
#  六条准则（A → B 方向）
# ══════════════════════════════════════════════════════════════════

def _hits(text: str, name: str) -> int:
    """名称在正文中的有效命中次数（单字名称需 ≥2 次才计，抑制子串误命中）"""
    if not name or not text:
        return 0
    n = text.count(name)
    if len(name) < SHORT_NAME_LEN and n < SHORT_NAME_MIN_HITS:
        return 0
    return n


def _vote_reference(a: str, b: str, ctx: _Context) -> Vote:
    """C1 正文引用：讲解 B 的正文引用 A 的名字（不对称计数）→ A 更基础"""
    na, nb = ctx.name[a], ctx.name[b]
    ca, cb = ctx.content.get(a, ""), ctx.content.get(b, "")
    if not ca and not cb:
        return Vote("C1正文引用", 0, W_REFERENCE, "双方都无正文，无法判断")
    a_in_b = _hits(cb, na)
    b_in_a = _hits(ca, nb)
    if a_in_b == 0 and b_in_a == 0:
        return Vote("C1正文引用", 0, W_REFERENCE, "双方正文互不引用")
    if a_in_b > b_in_a:
        return Vote("C1正文引用", 1, W_REFERENCE,
                    f"B 的正文引用 A {a_in_b} 次，反向 {b_in_a} 次")
    if b_in_a > a_in_b:
        return Vote("C1正文引用", -1, W_REFERENCE,
                    f"A 的正文引用 B {b_in_a} 次，反向 {a_in_b} 次")
    return Vote("C1正文引用", 0, W_REFERENCE, "互相引用次数相同，无方向")


def _vote_name(a: str, b: str, ctx: _Context) -> Vote:
    """C2 名称包含：术语构成依赖（"树" ⊂ "二叉树"）"""
    na, nb = ctx.name[a], ctx.name[b]
    if not na or not nb or na == nb:
        return Vote("C2名称包含", 0, W_NAME, "名称相同或为空")
    if na in nb:
        return Vote("C2名称包含", 1, W_NAME, f"「{na}」是「{nb}」的构成部分")
    if nb in na:
        return Vote("C2名称包含", -1, W_NAME, f"「{nb}」是「{na}」的构成部分")
    return Vote("C2名称包含", 0, W_NAME, "名称无包含关系")


def _vote_order(a: str, b: str, ctx: _Context) -> Vote:
    """C3 次序：先入库/先在教材中出现者更基础（弱票，见模块 docstring 天花板）"""
    ra, rb = ctx.order.get(a), ctx.order.get(b)
    if ra is None or rb is None or ra == rb:
        return Vote("C3次序", 0, W_ORDER, "次序信息缺失或相同")
    if ra < rb:
        return Vote("C3次序", 1, W_ORDER, "A 在教材/入库次序上更早")
    return Vote("C3次序", -1, W_ORDER, "B 在教材/入库次序上更早")


def _vote_difficulty(a: str, b: str, ctx: _Context) -> Vote:
    """C6 难度差：先修通常更简单"""
    da = int(ctx.by_id[a].get("difficulty", 3) or 3)
    db = int(ctx.by_id[b].get("difficulty", 3) or 3)
    if da == db:
        return Vote("C6难度差", 0, W_DIFFICULTY, "难度相同")
    if da < db:
        return Vote("C6难度差", 1, W_DIFFICULTY, f"难度 {da} < {db}")
    return Vote("C6难度差", -1, W_DIFFICULTY, f"难度 {da} > {db}")


def _vote_common_pred(a: str, b: str, ctx: _Context) -> Vote:
    """
    C4 共同前置：前置邻居高度重合 → 同层概念，反对先修（去假阳性）

    只在证据足够强时才投反对票，否则弃权 —— 因为「共享 1 个前置」无法区分
    「兄弟」（同层，应对齐）与「链式传递」（A→B→C 且 A→C，应连边）：
    例如 A→B 与 A→C 并存时，B、C 的前置集合都是 {A}，此时贸然否决
    B→C 会直接杀掉正确的传递边。故要求：共同前置 ≥2 且两集合无包含关系。
    """
    pa, pb = ctx.preds[a], ctx.preds[b]
    shared = pa & pb
    if len(shared) < 2:
        return Vote("C4共同前置", 0, W_COMMON, f"共同前置仅 {len(shared)} 个，不足以判断层级")
    if pa <= pb or pb <= pa:
        return Vote("C4共同前置", 0, W_COMMON, "前置集合为包含关系，更像链式传递")
    jaccard = len(shared) / len(pa | pb)
    if jaccard >= 0.5:
        return Vote("C4共同前置", -1, W_COMMON,
                    f"先修邻居重合度 {jaccard:.2f}，疑似同层概念")
    return Vote("C4共同前置", 0, W_COMMON, f"先修邻居重合度 {jaccard:.2f}")


def _vote_similarity(a: str, b: str, ctx: _Context) -> Vote:
    """C5 嵌入相似：高度相似 → 同义词，应合并而非连先修边（反对）"""
    va, vb = ctx.vectors.get(a), ctx.vectors.get(b)
    if va is None or vb is None or ctx.embedder is None:
        return Vote("C5语义相似", 0, W_SYNONYM, "嵌入不可用")
    sim = ctx.embedder.similarity(va, vb)
    if sim >= SYNONYM_SIM:
        return Vote("C5语义相似", -1, W_SYNONYM, f"相似度 {sim:.2f}，疑似同一概念")
    return Vote("C5语义相似", 0, W_SYNONYM, f"相似度 {sim:.2f}，非同一概念")


CRITERIA = (_vote_reference, _vote_name, _vote_order,
            _vote_difficulty, _vote_common_pred, _vote_similarity)


# ══════════════════════════════════════════════════════════════════
#  主流程
# ══════════════════════════════════════════════════════════════════

def _build_vectors(ctx: _Context, embedder) -> None:
    """一次性编码全部节点签名（嵌入失败则静默降级：C5 弃权）"""
    if embedder is None:
        return
    ids = [n["id"] for n in ctx.nodes]
    texts = [ctx.signature(i) for i in ids]
    try:
        vecs = embedder.embed(texts)
    except Exception as e:  # 嵌入是可选信号，失败不影响其余准则
        logger.warning(f"先修推断嵌入失败，C5 准则弃权: {e}")
        return
    if not vecs or len(vecs) != len(texts):
        return
    ctx.vectors = dict(zip(ids, vecs))


def infer_prerequisites(
    nodes: list[dict],
    edges: Optional[list[dict]] = None,
    *,
    content: Optional[dict] = None,
    order: Optional[dict] = None,
    embedder=None,
    vectors: Optional[dict] = None,
    threshold: float = DEFAULT_THRESHOLD,
    max_parents_per_node: int = DEFAULT_MAX_PARENTS,
) -> list[PrereqCandidate]:
    """
    推断先修关系（纯函数，不写库）。

    参数:
        nodes: 同一学科的节点列表（每个含 id/name，可选 summary/difficulty/created_at）
        edges: 该学科已有边（用于跨跳过滤、环检测与 C4 共同前置），默认空
        content: {node_id: 正文文本}（C1 正文引用用；缺失则该准则弃权）
        order: {node_id: 次序}（教材权威顺序，覆盖 created_at 近似）
        embedder: 具备 embed(texts) / similarity(a, b) 的嵌入器；None 则 C5 弃权
        vectors: 预置的 {node_id: 向量}（测试注入用，优先于 embedder）
        threshold: 认定阈值（唯一召回/精度旋钮，越大越保守）
        max_parents_per_node: 单节点最多入边数

    返回:
        按 score 降序的候选先修边列表（保证无环，且不含已存在/跨跳冗余的边）。
    """
    edges = edges or []
    if len(nodes) < 2:
        return []

    ctx = _Context(nodes, edges, content or {}, order, embedder, vectors)
    if not ctx.vectors:
        _build_vectors(ctx, embedder)

    # ── 1. 打分 ──
    scored: list[PrereqCandidate] = []
    for a in ctx.by_id:
        for b in ctx.by_id:
            if a == b:
                continue
            if (a, b) in ctx.existing_prereq:
                continue           # 已有该先修边
            votes = [fn(a, b, ctx) for fn in CRITERIA]
            score = sum(v.value * v.weight for v in votes) / TOTAL_WEIGHT
            if score >= threshold:
                scored.append(PrereqCandidate(a, b, score, votes))

    scored.sort(key=lambda c: (-c.score, c.source, c.target))

    # ── 2. 贪心接受：跳过跨跳冗余 + 保 DAG + 限制入度 ──
    accepted: list[PrereqCandidate] = []
    adj = {nid: set(s) for nid, s in ctx.adj.items()}
    parent_count: dict[str, int] = {}

    for cand in scored:
        if _reachable(adj, cand.source, cand.target):
            continue               # 已存在间接路径，加直连是冗余（跨跳边）
        if _reachable(adj, cand.target, cand.source):
            continue               # 会成环
        if parent_count.get(cand.target, 0) >= max_parents_per_node:
            continue
        adj[cand.source].add(cand.target)
        parent_count[cand.target] = parent_count.get(cand.target, 0) + 1
        accepted.append(cand)

    return accepted


def _reachable(adj: dict, src: str, dst: str) -> bool:
    """adj 中是否存在 src ⇝ dst 的有向路径（BFS）"""
    seen = {src}
    stack = [src]
    while stack:
        cur = stack.pop()
        for nxt in adj.get(cur, ()):
            if nxt == dst:
                return True
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return False


def apply_candidates(kg, candidates: list[PrereqCandidate], *, caller: str = "ai") -> dict:
    """
    把候选先修边写入图谱（唯一的写库入口）。

    逐条容错：已存在 / 会成环 / AI 权限不足的边记录原因后跳过，不影响其余边。

    参数:
        caller: "ai" —— 推断结果作为 AI 建议写入。注意 `KnowledgeGraph.add_edge` 的
                护栏仍然生效：**两端都是人类创建**的节点之间，AI 不允许加 prerequisite
                （这类边会以 PermissionError 进入 skipped，属预期行为，不是失败）；
                "human" —— 用户显式采纳建议时调用，绕过上述护栏。
    """
    created, skipped = [], []
    for cand in candidates:
        try:
            kg.add_edge({
                "from": cand.source,
                "to": cand.target,
                "relation": "prerequisite",
                "label": "前置知识",
                "added_by": caller,
                "confidence": round(cand.score, 4),
            }, caller=caller)
            created.append(cand.to_dict())
        except (ValueError, PermissionError) as e:
            skipped.append({"from": cand.source, "to": cand.target, "reason": str(e)})
    return {"created": created, "skipped": skipped}
