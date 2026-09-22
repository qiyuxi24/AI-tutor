# RAG 召回率与重排优化调研

> 创建：2026-08-30
> 背景：用户问三件事——(1) 重排加权怎么搞（是否两种搜索方式同时加权再回来？）；(2) 召回的文本数量应该是多少？；(3) 有没有论文能提高召回率，继续改造。
>
> 核心澄清：用户说的"两种搜索方式同时加权再回来"其实是我们**已经实现的 Hybrid Search**（向量+BM25 加权融合）。真正的优化空间在于：**召回数量（Top-K 太窄）+ 查询扩展（Query Expansion/HyDE）+ 精排（Rerank）** 三个环节。

---

## 一、核心结论（先给答案）

1. **召回数量**：业界标准是**宽召回 + 精排**两段式——先召回 **Top-30~50**（甚至 Top-100），再用 Reranker 精排到 **Top-5**。我们当前 `search(top_k=5)` 太窄，会漏召回，是**最优先要改的**。

2. **提升召回率的技术**（有论文/权威实践支撑，按性价比排序）：
   | 技术 | 原理 | 召回提升 | 代价 |
   |------|------|---------|------|
   | **HyDE**（arXiv 2312.04467，2022） | LLM 先生成一段"假设答案"，用它检索（答案向量更接近真实答案文档） | **+15-25%**（零样本） | 1 次 LLM 调用 |
   | **Query Expansion / Multi-Query + RRF**（RAG-Fusion，arXiv:2402.03367） | LLM 把一个问题生成 N 个子问题，分别检索，RRF 融合 | **+10-15%** | N 次检索 + 1 次 LLM |
   | **Rerank（Cross-Encoder）**（BGE-Reranker/Cohere） | 宽召回后交叉编码精排 | 排序质量大幅提升 | 需模型（本地/API） |

3. **"两种搜索方式同时加权"**：已是现有 Hybrid Search（fuse，alpha=0.6 线性加权）。可选升级为 **RRF 融合**（对权重不敏感，比线性加权更鲁棒，且天然支持多路/多查询融合）。

---

## 二、召回数量：该召回多少个？

### 业界标准流程（2026）
```
检索（Recall）: 向量 + BM25 各取 Top-30~50
  → 融合（Fusion）: RRF 或加权合并成 Top-30~50 候选池
  → 精排（Rerank）: Cross-Encoder 精排候选池
  → 输出: Top-3~5 进 LLM 上下文
```
引用：「先用向量检索做宽召回（Top-50），再用 Cross-Encoder 做精排（Top-5），这是 2026 年 RAG 的标准做法。」— 多篇 2026 实践博客共识。

### 关键权衡
- **召回太少（Top-5）**：漏召回 → 相关文档根本没进候选池，后续一切优化白搭（Rerank 再准也排不到没召回的）
- **召回太多 + 直接全给 LLM**：上下文太长、token 成本高、噪声多
- **正确做法**：召回广、进 LLM 少——中间靠 Rerank 把关

### 我们的现状
- `kb_manager.search(top_k=5)`：向量和 BM25 各取 5，融合后 5 条 → **太窄，漏召回**
- 需要：内部宽召回（如各取 30）+ 外部 top_k（进 LLM 的数量仍 5）

---

## 三、提升召回率的论文技术详解

### 3.1 HyDE（Hypothetical Document Embeddings）
- **核心直觉**：在向量空间里，"答案的向量"比"问题的向量"更接近"真实答案文档"。问题偏疑问，答案偏陈述，不在一个语义空间。
- **步骤**：LLM 生成 200 字假设答案 → embedding → 检索 Top-K
- **效果**：零样本召回 **+15-25%**；只 1 次 LLM 调用，成本低
- **坑**：假设答案可能"瞎编"污染方向 → 用强模型 + temperature=0 + 截断；不适合"找特定文档"（该用元数据过滤）
- **路由**：短 query / 零样本 → 用 HyDE；长 query / 多面向 → 用 Query Expansion

### 3.2 Query Expansion / Multi-Query + RRF（RAG-Fusion）
- **RAG-Fusion 论文**（Zackary Rackauckas, arXiv:2402.03367）：LLM 把原始查询生成多个子查询（从不同角度），分别检索，用 **Reciprocal Rank Fusion（RRF）** 融合
- **RRF 公式**：`rrfscore = Σ 1/(rank + k)`，k 是平滑因子（论文用 k=60）
- **优点**：多视角覆盖，召回 +10-15%；RRF 无需训练，对权重不敏感
- **代价**：检索次数 ×N，耗时约为传统 RAG 1.77 倍
- **注意**：生成的子查询若偏离意图会偏题 → 需控制生成质量

### 3.3 Rerank（Cross-Encoder 精排）
- **原理**：宽召回候选池里，用交叉编码器（query + doc 一起编码）精排，比向量（双塔）更准
- **方案**：
  - **云端 API**：Cohere Rerank（商业）
  - **本地开源**：BGE-Reranker（BAAI，中文好，轻量，几十 MB~1GB）
  - **免模型**：基于现有分数的重排（path 关键词加权、RRF、MMR 多样性）
- **我们的约束**：项目轻量，torch 未装 → 本地 BGE-Reranker 需装 torch（重）。**初期推荐免模型重排**（RRF + path 加权），后续可加 BGE。

---

## 四、推荐落地路线（结合我们的技术栈）

我们已有：Hybrid Search（向量+BM25）、whoosh、numpy、阿里云 embedding。缺的是"宽召回 + 精排"。

### 路线 0：先做"宽召回"（最低成本，收益最大）
> ✅ **2026-08-30 已落地**（与路线1合并完成）：`kb_manager.search` 内部改为**宽召回**（向量/BM25 各取 `RECALL_TOP_K=30`），RRF 融合成候选池，精排取 Top-5 返回。

### 路线 1：RRF 融合升级（可选，替代线性加权）
> ✅ **2026-08-30 已落地**：`fusion.py` 新增 `rrf_fuse`（Reciprocal Rank Fusion，arXiv:2402.03367）。`search` 用 RRF 替代原线性加权 `fuse`。已验证：多路命中优先、对权重/分数尺度不敏感、极端高分不主导（实测线性加权被单路高分 X 主导，RRF 稳定选两路都靠前的 B）。天然支持 RAG-Fusion 多路融合，为 Query Expansion 铺路。

### 路线 2：HyDE 或 Query Expansion（需 LLM，召回 +15-25%）
- 利用现有 `call_llm(system_prompt, messages, enable_tools=False)` 生成假设答案/子查询
- 优先级：**HyDE > Query Expansion**（1 次 LLM + 1 次检索，成本低效果好）
- **风险**：DASHSCOPE qwen-plus 聊天额度有 403 的坑（memory 里记录过），需要真实可用 key
- **兜底**：LLM 失败时回退到普通 query 检索（已有鲁棒模式）

### 路线 3：Rerank 精排（远期，需模型）
- 本地 BGE-Reranker（需装 torch，重）或云端 API
- **在路线 0/2 之后**再考虑

---

## 五、用户问题的直接回答

### "重排加权怎么搞？两种搜索方式同时加权再回来？"
- **是**：你现在想的就是 Hybrid Search（向量 + BM25 两路加权融合），我们**已经有**（`fusion.fuse`，alpha=0.6）
- **优化**：①加权可升级为 RRF（对权重不敏感）；②精排可加 path 关键词加权（前面 P1 已存了 path）
- **重点不是"加权"本身，而是"先宽召回再精排"的流程**

### "召回的文本数量应该是多少？"
- **召回（候选池）**：Top-30~50（业界标准）
- **进 LLM（精排后）**：Top-3~5
- **我们的现状**：`top_k=5` 直接召回太窄 → 先改内部宽召回 30

### "有没有论文提高召回率？"
- **HyDE**：零样本召回 +15-25%
- **RAG-Fusion（Multi-Query + RRF）**：召回 +10-15%
- **Cross-Encoder Rerank**：精排质量提升
- 三篇/三套都有论文支撑，最优先落地"宽召回 + 轻量精排"（零依赖）。

---

## 六、改动文件清单（预估）

| 文件 | 改动 |
|------|------|
| `backend/app/core/kb/kb_manager.py` | `search` 内部宽召回（向量/BM25 各取 30，融合后精排取 5） |
| `backend/app/core/hybrid_search/fusion.py` | 新增 `rrf_fuse`（可选） |
| `backend/app/core/kb/`（可选） | HyDE / Query Expansion 查询扩展模块（调 call_llm） |
| `backend/app/core/rag_pipeline/` | 若查询扩展，放在 pipeline 前置（query 改写） |
