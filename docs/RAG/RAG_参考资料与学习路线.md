# RAG 系统学习参考资料与学习路线（补充）

> 创建：2026-08-30
> 定位：作为 `docs/RAG/RAG_去耦合与目录检索调研.md` 的**通识/进阶补充**——那边解决"我们项目怎么改"，这份解决"RAG 整体怎么学、外面有什么好东西"。
> 用法：按"方面"分类，每条标注了**类型**（综述/开源/博客）与**对应本项目已实现的模块**，学习时可对照代码。

---

## 〇、先看项目内已有的（最贴自己项目）

| 材料 | 位置 | 内容 |
|------|------|------|
| RAG 架构决策调研 | `docs/RAG/RAG_去耦合与目录检索调研.md` | Agentic RAG / RAPTOR / Parent-Child 等论文与工程调研 + 落地阶段规划 |
| 图谱 RAG 源码 | `backend/app/core/rag/`（chunker / manager / vector_store） | 图谱 MD 分块 → text-embedding-v4 向量化 → 余弦检索 |
| 混合检索 | `backend/app/core/hybrid_search/`（whoosh BM25 + fusion） | 向量 + BM25 双路召回 + 加权融合（业界主流 RRF 思路） |
| RAG 路由器（去耦合） | `backend/app/core/rag_pipeline/`（sources / router / pipeline） | 多数据源注册 + 按需开关 + 跨源融合，正是"RAG 中间件"落地 |
| 上传知识库 | `backend/app/core/kb/`（kb_store / kb_manager / doc_vector_store） | 目录树 + 文档解析 + Parent-Child 雏形（path 元数据） |

---

## 一、综述与范式演进（入门必读）

RAG 学习的第一站：搞清 **Naive RAG → Advanced RAG → Modular RAG → Agentic RAG** 的演进脉络，以及检索/生成/增强三大件。

| 资料 | 类型 | 看点 |
|------|------|------|
| [Retrieval-Augmented Generation for LLMs: A Survey](https://arxiv.org/pdf/2312.10997v4) | ⭐综述（必读） | 最经典的 RAG Survey，定义 Naive/Advanced/Modular 三范式与评估体系 |
| [A Survey on RAG Meets LLMs](https://arxiv.org/html/2405.06211v1) | 综述 | 从架构、训练策略、应用三视角梳理 RA-LLM |
| [RAG for NLP: A Survey](https://ar5iv.labs.arxiv.org/html/2407.13193) | 综述 | 聚焦 retriever 与 retrieval fusion，**附 tutorial 代码** |
| [A Comprehensive Survey of RAG](https://arxiv.org/pdf/2410.12837.pdf) | 综述 | 演进 + 现状 + 未来方向，覆盖面广 |
| [Survey on Retrieval & Structuring Augmented Generation](https://arxiv.org/html/2509.10697v1) | 综述（较新） | 检索 + 文本结构化（taxonomy/层级分类）结合 |

> 本项目对照：我们的实现大致处于 **Advanced RAG**（混合检索 + 双路召回）→ 正在向 **Agentic/Modular RAG**（rag_pipeline 路由器）过渡。读综述时重点看"Modular 阶段为什么把检索模块化"，正是我们解耦的动机。

---

## 二、分块策略（Chunking）

检索质量的上限由索引质量决定，分块是第一步。

| 资料 | 类型 | 看点 |
|------|------|------|
| [Advanced Retrieval Augmented Generation（Fan Pu Zeng 课件）](https://fanpu.io/assets/presentations/Advanced%20Retrieval%20Augmented%20Generation.pdf) | 讲义 PDF | 基础分块（固定 token / 重叠 / 递归）→ 粒度选择（doc/passage/sentence/proposition）系统梳理 |
| [Advanced RAG Pipeline Complete Guide](https://www.youngju.dev/blog/culture/2026-04-13-advanced-rag-pipeline-optimization-retrieval-guide-2025.en) | 博客 | Character / Recursive / **Semantic Chunking** 对比与代码 |
| [RAG 检索策略：分块、多路召回、rerank](https://blog.csdn.net/qq_62234605/article/details/162636585) | 博客 | 中文实战，分块→多路召回→精排完整链路 |
| [深入理解 RAG：知识增强架构](https://blog.csdn.net/hyc010110/article/details/148873611) | 博客 | HyDE / 混合检索 / 重排的中文入门 |

> 本项目对照：`backend/app/core/rag/chunker.py`（图谱 MD 分块）+ `kb/` 文档分块。进阶方向是 **Parent-Child 分块**（小 chunk 检索 + 大 chunk 返回），`docs/RAG/RAG_去耦合与目录检索调研.md` 阶段 B 已规划。

---

## 三、检索增强（查询改写 / 扩展 / HyDE）

"怎么查"决定了召回上限，是 Advanced RAG 的主要优化点。

| 资料 | 类型 | 看点 |
|------|------|------|
| [2026 RAG 全景：从大模型基座到 Agent 记忆中枢](https://cloud.tencent.com.cn/developer/article/2654878) | ⭐中文长文 | 检索前/检索中/检索后三阶段优化全覆盖（Query Rewriting / Expansion / HyDE），万字吃透 |
| [NirDiamant/RAG_Techniques](https://github.com/NirDiamant/RAG_Techniques) | ⭐开源仓库 | 80+ 项 RAG 技术清单，含查询改写、匿名化、自适应检索、验证，实操代码 |
| [sourangshupal/advanced-rag-tutorials](https://github.com/sourangshupal/advanced-rag-tutorials) | 开源教程 | LangChain/LlamaIndex 双框架的查询优化、混合检索、重排 notebook |
| [一文彻底搞懂 RAG（Python+Java 双版本实战）](https://blog.csdn.net/qq_42055933/article/details/163252477) | 博客 | 原理→流程→架构演进，含路由决策/多 Agent/工具调用 |

> 本项目对照：`rag_pipeline/router.py` 已做**按需检索开关**（问候/过短消息跳过），是轻量版 Adaptive RAG；HyDE / 查询扩展尚未实现，可作为后续加分项。

---

## 四、重排序（Reranking）

"召回 20-50 条 → 精排到 3-5 条"，是**性价比最高的检索升级**（成本低、收益大）。

| 资料 | 类型 | 看点 |
|------|------|------|
| [12 Advanced RAG Techniques（Atlan）](https://atlan.com/know/advanced-rag-techniques/) | 博客 | Cross-encoder 重排为何是"混合检索之后最高 ROI 的升级" |
| [Building Production-Ready RAG Systems](https://devstarsj.github.io/2026/04/17/production-rag-systems-advanced-techniques/) | 博客 | 含 `CrossEncoder('ms-marco-MiniLM-L-6-v2')` 可直接抄的代码 |
| [Advanced RAG: Beyond Basic Chunk Retrieval（Pristren）](https://pristren.com/blog/retrieval-augmented-generation-advanced/) | 博客 | top-20~50 → cross-encoder → top-5 的完整思路 |
| [RAG Deep Dive: Advanced Retrieval（Kalvad）](https://blog.kalvad.com/rag-deep-dive-series-advanced-retrieval/) | 博客 | Hybrid search / Parent-child / Metadata filtering 概念向 |

> 本项目对照：`hybrid_search/fusion.py` 已做向量+BM25 融合（RRF 类思路），但**没有 cross-encoder 精排**——这是最值得补的一环，且 `requirements.txt` 已注释 sentence-transformers 备选。

---

## 五、评估体系（RAGAS）

不评估就没法迭代。RAGAS 四大指标形成"检索×生成"×"精确×完整"矩阵。

| 资料 | 类型 | 看点 |
|------|------|------|
| [RAGAS 官网](https://www.ragas.io/) | 官网 | Faithfulness / Answer Relevancy / Context Precision / Context Recall 定义 |
| [RAGAS 论文（arXiv:2309.15217）](https://arxiv.org/pdf/2309.15217v1.pdf) | ⭐论文 | 无 ground truth 的自动化评估框架，四指标来源 |
| [RAGAS 中文文档（指标概览）](https://docs.ragas.org.cn/en/stable/concepts/metrics/overview/) | 文档 | 指标思维导图 + LLM 指标 vs 非 LLM 指标 |
| [RAGAS 从 0.79 到 0.85 的完整复盘](https://blog.csdn.net/weixin_53902256/article/details/158208275) | 博客 | 检索质量决定生成质量上限，中文实战复盘 |
| [RAGAS 4 个指标一测便知](https://juejin.cn/post/7615551904537919530) | 博客 | 上下文精度/召回率通俗解读（"别拿垃圾信息污染生成器"） |

> 本项目对照：`docs/RAG/RAG_去耦合与目录检索调研.md` 评审维度已引用 context_precision 等指标；项目目前无自动化评测，接 RAGAS 可作为 P2 加分项。

---

## 六、开源框架选型（参考但不照搬）

了解主流框架的取舍，能帮我们判断"自研 vs 复用"边界。

| 框架 | 定位 | 选型要点 |
|------|------|---------|
| [LangChain](https://github.com/langchain-ai/langchain) | 组件库 | 生态最大（700+ 集成），但抽象层级多、重；ParentDocumentRetriever 值得借鉴 |
| [LlamaIndex](https://github.com/run-llama/llama_index) | 数据特化 | 文档摄取/复杂检索最强，RecursiveRetriever、元数据过滤快 |
| [Haystack](https://github.com/deepset-ai/haystack) | 工业管道 | 生产部署友好、可审计，Apache-2.0 |
| [RAGFlow](https://github.com/infiniflow/ragflow) | 应用平台 | 深度文档解析 + 引用溯源 + RAPTOR 分层检索，落地参照 |
| [Dify](https://github.com/langgenius/dify) | 低代码平台 | 工作流编排、快速搭演示 |
| [AnythingLLM](https://github.com/Mintplex-Labs/anything-llm) | 无代码 | 文档对话桌面应用，参考交互 |

| 对比/盘点文章 | 看点 |
|------|------|
| [2026 主流 RAG 开源项目都在这里了](https://www.cnblogs.com/PLM-Teamcenter/p/20807318) | 全框架星数/优势/协议速查表 |
| [开源 RAG 知识库框架 15 大方案对比](https://zeeklog.com/kai-yuan-ragzhi-shi-ku-kuang-jia-pan-dian-15da-zhu-liu-fang-an-dui-bi-yu-xuan-xing-zhi-nan-xiang-xi-da-mo-xing-ru-men-dao-jing-tong-shou-cang-zhe-pian-jiu-zu-gou-liao-2) | 深度对比 + 选型指南 |

> 本项目对照：我们走的是**轻量自研**路线（FastAPI + SQLite，未引入重框架），README 与 docs 已明确。选型文章主要用来"抄思路、看边界"，尤其 LangChain ParentDocumentRetriever 和 RAGFlow 的分层检索设计。

---

## 七、Agentic RAG（高级演进）

当"每轮都检索"不够聪明时，让 LLM 决定**是否查、查哪里、何时停**。

| 资料 | 类型 | 看点 |
|------|------|------|
| [Agentic RAG: A Survey（arXiv:2501.09136）](https://arxiv.org/abs/2501.09136) | ⭐综述 | Naïve→Advanced→Agentic 演进，When/Where/How 决策框架（本项目调研已引用） |
| [Adaptive-RAG（arXiv:2403.14403）](https://arxiv.org/abs/2403.14403) | 论文 | 查询复杂度分类器动态决定直接答/单步/多步检索 |
| [RAPTOR（Stanford, arXiv:2401.18059）](https://arxiv.org/abs/2401.18059) | 论文 | 递归聚类+摘要成树的分层检索 |
| [RAG 从入门到入土：Agent 时代](https://juejin.cn/post/7641103590654230582) | 博客 | 数据量/文档类型/问题类型 → 选 Basic / Advanced / Agentic 的决策树 |
| [高阶 RAG 系统搭建（智能路由）](https://blog.csdn.net/weixin_46190318/article/details/159283786) | 博客 | 智能路由器分发请求到不同数据源/管道 |

> 本项目对照：`rag_pipeline/router.py` 是"轻量版 Adaptive RAG"；`TODO.md` 规划了把 RAG 检索注册为 function calling 工具（Agentic 完全体）。

---

## 八、建议学习路径（结合本项目）

```
第 1 步  通识：读「综述与范式演进」→ 建立 Naive/Advanced/Modular/Agentic 心智模型
第 2 步  对照代码：读 backend/app/core/rag + hybrid_search + rag_pipeline 三份源码
         （配合 docs/RAG/RAG_去耦合与目录检索调研.md 理解为什么这么设计）
第 3 步  深入检索：学「检索增强」+「分块策略」→ 对照自己的 chunker / router
第 4 步  加精排：实现 cross-encoder rerank（hybrid_search 之后接一层）
         → 这是当前项目性价比最高的下一步
第 5 步  建评测：接 RAGAS 四指标，为后续每次改动建立量化基线
第 6 步  看框架：读选型文章，明确哪些该自研、哪些抄思路
第 7 步  进阶：Agentic RAG（function calling 检索）作为参赛加分项
```

---

## 附：常用 RAG 技术词表（速查）

| 术语 | 一句话 |
|------|--------|
| Naive RAG | 文档→分块→向量化→每轮检索注入，最早形态 |
| Advanced RAG | 检索前（改写/扩展/HyDE）+ 检索（混合/精排）+ 检索后（压缩）全面优化 |
| Modular RAG | 各组件模块化可插拔、可路由（我们的 rag_pipeline 方向） |
| Agentic RAG | LLM 自主决策是否检索/查哪里/何时停 |
| Hybrid Search | 向量（语义）+ BM25（关键词）双路召回再融合 |
| RRF | Reciprocal Rank Fusion，按排名倒数融合双路结果（简单有效） |
| Cross-Encoder | query 与 doc 一起编码打分，比向量相似度更准，用于精排 |
| Parent-Child | 小 chunk 检索 + 大 chunk（父级）返回，兼顾精度与上下文 |
| HyDE | LLM 先"脑补"一个答案，用它的向量去检索，提升召回 |
| RAGAS | 自动化评测框架，四指标覆盖检索与生成 |
| Metadata Filtering | 用目录/学科/时间等元数据先缩小检索范围 |
