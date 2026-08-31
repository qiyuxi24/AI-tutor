# RAG 去耦合与目录级检索调研

> 创建：2026-08-29
> 背景：用户提出两个关键架构问题：
> 1. **RAG 是否去耦合？** 是否需要写一个"中间件"——LLM 问什么，RAG 系统再去查什么（而非无条件把检索结果塞进系统提示词）？
> 2. **知识库按目录组织，是否需要"先检索目录 → 再检索文档"的分部检索？** 还是有什么更好的设计？
>
> 本文调研论文（Agentic RAG / RAPTOR）、开源实现（LangChain ParentDocumentRetriever、RAGFlow）与工程博客，给出可落地的方案。

---

## 一、核心结论（先给答案）

1. **关于去耦合/中间件**：你的直觉是对的，业界标准做法就是 **Agentic RAG（把检索变成 LLM 可调用的工具）** 或 **Adaptive RAG（先用分类器判断要不要检索）**。但**不必一步到位改成"纯工具调用"**——那会增加每轮一次额外 LLM 往返（延迟+成本）。推荐**中间态**：保留现有的"每轮自动注入"，但把注入点做成**可插拔的 RAG 管道/路由器**，并引入**按需检索开关**。

2. **关于目录级检索**：业界确实有"先粗后细"的多级/分层检索，但**最成熟的落地不是"先查目录再查文档"的顺序流程**，而是 **Parent-Child 结构 + 两级召回**：
   - **小粒度（child chunk）做精确检索定位**
   - 命中后**回溯到父级（文档/章节）**补全上下文
   - 目录/文档路径作为**结构化元数据**参与过滤和重排，而不是作为"第一级独立检索"
   - 关键铁律：**先在小块上排序，再向父级扩展**；父级扩展是**预算决策**（按 token 决定返回章节还是标题路径），**不是固定动作**。

3. **一句话**：去耦合用"**工具化 + 路由**"，目录用"**元数据过滤 + Parent-Child 回溯**"，两者叠加即业界主流。

---

## 二、现状诊断（TutorAgent 当前 RAG 架构）

| 维度 | 当前实现 | 问题 |
|------|---------|------|
| 注入方式 | `chat_service._build_system_prompt()` 硬编码调用 `_build_rag_context()`（图谱 RAG）+ `_build_kb_context()`（上传知识库） | **强耦合**：新增数据源要改 chat_service；检索逻辑写死在 prompt 构建里 |
| 图谱 RAG | **无条件**，每轮对话自动检索 | 简单问题（"你好"）也检索，浪费 + 引入噪声 |
| 知识库 | 仅当用户前端勾选目录（`kbContext`）才检索 | 目录只作**范围过滤**（`node_ids`），不参与排序 |
| 目录组织 | 用户勾选 → `collect_files` 展开成文件列表 → `node_ids` 过滤向量检索 | 目录本身不检索、不排序，非两级结构 |
| 混合检索 | 向量（text-embedding-v4）+ whoosh BM25，`fusion.fuse` 加权融合 | ✅ 已是业界主流，保留 |

---

## 三、调研素材来源

### 论文（学术）
| 论文 | 关键点 |
|------|--------|
| **Agentic RAG: A Survey on Agentic RAG**（arXiv:2501.09136） | ⭐ 最核心。综述三种演进范式：Naïve RAG → Advanced RAG → Agentic RAG。核心转变：**从"流程驱动"到"决策驱动"**，LLM 决定**是否检索（When）、查哪里（Where/Query Routing）、怎么查（How/工具调用）、何时停止** |
| **RAPTOR: Recursive Abstractive Processing for Tree-Organized Retrieval**（Stanford, arXiv:2401.18059） | 分层/树状检索。把 chunk 递归聚类+摘要成树，查询时在树中检索。Collapsed Tree 与 Tree Traversal 两种查询策略 |
| **Adaptive-RAG: Learning to Adapt Retrieval-Augmented LLMs**（arXiv:2403.14403） | 用**查询复杂度分类器**动态决定：直接答 / 单步检索 / 多步检索 |

### 开源实现（可直接借鉴）
| 项目 | 关键点 |
|------|--------|
| **LangChain `ParentDocumentRetriever`** | ⭐ 最直接可抄。双 splitter（child 小块检索 + parent 大块返回），`child→parent` 映射在**索引阶段**建立，检索阶段透明回溯 |
| **LangChain `RecursiveRetriever`** | 更通用：节点间通过引用多级跳转（文档图谱、多级摘要） |
| **RAGFlow** | 落地了 RAPTOR Summarization 分层检索 + 文档/章节级元数据 |
| **llama_index `RecursiveRetriever`** | 构建 `IndexNode` 引用树，检索命中后递归取子节点 |

### 博客（工程实践，最有借鉴价值的落地细节）
- **《RAG 系列：高级分块策略 — Parent-Child 与 Contextual Retrieval》**（掘金 2026）：给出 Parent-Child 原理 + 实验数据（context_precision 0.583→0.938，大幅提升）+ 目录级借鉴点
- **《长文档与代码 RAG 怎么检索》**（卡码笔记 2026）：⭐ 两条铁律：
  > 1. "先在小块上排序，再向父级扩展。如果先把所有 Child 展开成大块再 Rerank，相关信号会被大量父级文本冲淡"
  > 2. "父级扩展是预算决策，不是固定动作"（函数 30 行返回整个函数；类 3000 行只补签名+字段+目标方法；章节 2 页返回完整章节；几十页只补标题路径+相邻条款）
  > 3. "检索单位不等于返回单位，相关不等于可用，可用还必须满足完整、当前和有权限"

---

## 四、推荐设计（分两阶段落地）

### 阶段 A：RAG 去耦合 —— "RAG 路由器"（中间件，非纯工具调用）

> ✅ **2026-08-29 已落地 P0**：新增 `backend/app/core/rag_pipeline/` 包（types/sources/router/pipeline + 全局单例 pipeline），`chat_service._build_system_prompt` 中两处硬编码检索合并为一次 `_build_retrieval_context`（走 pipeline 按 source 分组）。已验证鲁棒性（API 挂掉静默返回空、单源异常隔离、按需开关、跨源融合去重）。
> ✅ **2026-08-30 已落地 P1（path 元数据）**：`kb_store.get_node_path`（动态拼目录路径，不落库零迁移）+ `kb_manager.search` 的 `_attach_paths` 附加来源路径 + `KbRagSource` 填入 `RagHit.path` + `_build_retrieval_context` 知识库区块标注来源（如 `### 片段 1：/数据结构/第2章/栈.md`，仅 LLM 可见）。
> ✅ **2026-08-30 已落地 P2（父级扩展）**：命中子 chunk → 左右交替拼接相邻块 → 返回更完整父文本（`doc_vector_store.get_node_chunks` + `kb_manager._expand_parents/_expand_one`）。预算 `PARENT_EXPAND_CHARS=1500` + 块数上限 `PARENT_MAX_BLOCKS=8`，超即截断（预算决策非固定动作）。
> ✅ **2026-08-30 已落地 P3（RAG 检索作 function calling 工具）**：`KG_TOOLS` 新增 `rag_search`（query/source/top_k），`execute_kg_tool` 处理分支，LLM 后台阶段可按需检索。`_run_async` 解决"同步工具执行里跑 async 检索"（线程池新循环）。剩余 P1 进阶（HyDE）见 TODO。

**目标**：把 `chat_service` 里的硬编码检索解耦成一个可插拔的检索编排层，同时不引入每轮额外 LLM 往返。

**方案：新增 `backend/app/core/rag_pipeline/` 模块，做一个"RAG 路由器 + 按需开关"**

```python
# 核心抽象（伪代码）
class RagSource(Protocol):
    name: str
    async def retrieve(self, ctx: RagContext) -> list[RagHit]: ...
    def relevance(self, query: str) -> float: ...   # 路由打分

class RagPipeline:
    """中间件：编排多个数据源，支持路由 + 按需注入"""
    def __init__(self):
        self._sources: dict[str, RagSource] = {}
    def register(self, source: RagSource): ...      # 注册数据源（解耦关键）
    async def run(self, ctx) -> list[RagHit]:
        # 1. 路由：选出最相关的数据源（或用复杂度分类器判断要不要查）
        # 2. 检索：调用选中 source.retrieve()
        # 3. 融合：跨源按分数融合（复用 hybrid_search.fusion）
        # 4. 返回统一格式 hits，注入点只消费这个
```

**关键解耦点**：
- `chat_service._build_system_prompt()` 里那两段硬编码检索，替换成 **一个调用**：`pipeline.run(ctx)`
- 图谱 RAG / 知识库 / 未来的网页检索，各自实现为 `RagSource` 并 `register` 进去
- 新增数据源 = 新增一个 `RagSource` 类 + 注册，**不改 chat_service**

**按需检索开关（轻量版 Adaptive RAG，不加 LLM 往返）**：
- 用**规则/启发式**判断是否值得检索（而不是每轮无条件查）：
  - 消息长度 < N 字符、是纯问候/寒暄（"你好""谢谢"）→ 跳过检索
  - 查询涉及图谱/知识库关键词 → 走对应源
  - 这条可用 `re`/关键词列表实现，**零 LLM 成本**
- 若想更准，可加一个**独立的复杂度分类小模型**（Adaptive-RAG 做法），但对本项目初期规则够用

**是否要走"纯工具调用"（Agentic RAG 完全体）？**
- 你们的系统已经是**两阶段**：Phase 1 流式 + Phase 2 后台 function calling（图谱操作）
- 可以把"RAG 检索"也注册成一个 function calling 工具（如 `retrieve_knowledge(query, source)`），让 LLM 在后台阶段按需调用——**但这是高级形态，建议阶段 B 之后再做**
- 原因：纯工具调用让用户**等待 LLM 先决定再检索**，流式首 token 变慢；对教学场景，**预先注入相关上下文让老师回答更自然**往往更好

### 阶段 B：目录级检索 —— "Parent-Child + 目录元数据回溯"

**目标**：让"按目录组织"的资料检索更准、上下文更完整，而不是"先查目录再查文档"的顺序流程。

**设计（复用现有结构，改动小）：**

```
索引阶段（改动点：chunk 增加 parent 引用 + 目录路径元数据）：
  文档 chunk 写入时，除了现有的 doc_id/node_id，增加：
    parent_id    → 所属文档/章节的"父块"ID（可选，用于大块返回）
    path         → 目录路径，如 "数据结构/第2章/栈.md"
    heading      → 已存在（chunk 摘要）

检索阶段（核心改动：两级召回 + 元数据过滤）：
  1. 向量 + BM25 在 child chunk 粒度召回（现有 hybrid_search 不动）
  2. 过滤：用 node_ids（用户勾选目录展开）做范围过滤（已有）
  3. 【新增】路径重排/加权：命中 chunk 若 path 命中查询关键词，可加权（暂未做）
  4. 【✅ 已做】父级扩展（预算决策）：命中 chunk 左右拼接相邻块，返回更完整父文本
     （`_expand_parents`，预算 1500 字符 + 最多 8 块，超即截断）
```

**为什么不做"先检索目录→再检索文档"的两级顺序？**
- 从 Parent-Child 博客的**铁律**看：目录/文档是"大块"，如果先按目录/文档级检索、再在其下精检，会让**第一级召回把语义信号冲淡**（目录名通常很短、语义信息少，向量匹配不准）
- 目录的**正确角色是"元数据过滤器 + 重排信号"**，不是"独立检索入口"
- 只有当**目录数量巨大**（几百上千个文件夹）需要先粗筛时，才值得做真正的"目录级粗检索"；你们当前规模目录是用户手动建的，量小，用元数据过滤足够

**推荐落地顺序（低成本优先）：**
1. **P0：RAG 路由器解耦**（阶段 A 的核心）—— 新增 `rag_pipeline`，把 chat_service 硬编码拆掉
2. **P1：按需检索开关**（规则启发式，不加 LLM）—— 问候/过短消息跳过检索
3. **P1：chunk 加 `path` 元数据** —— 检索结果带目录路径，返回时给 LLM 标明"来自哪个文档"，且可作为重排加权信号
4. **P2：父级扩展（预算决策）** —— 命中 chunk 时按 token 预算回溯返回父文档/章节，而非只给 500 字
5. **P3：RAG 检索作为 function calling 工具**（✅ 已完成）—— 后台阶段 LLM 按需调用 `rag_search(query, source, top_k)`

---

## 五、评审维度对齐（备赛用）

这些设计与"大学生 OPC 创新创业 AI Agent 赛道"评审点契合：
- **去耦合/架构** → RAG 路由器体现"模块化、可扩展架构"，评审看架构设计
- **检索准确度** → Parent-Child + 元数据回溯提升 context_precision/recall，回答更准
- **成本/延迟优化** → 按需检索开关减少无谓的 embedding API 调用（省钱省延迟）
- **可解释性** → 检索结果带 `path`/来源，符合"溯源"要求

---

## 六、改动文件清单（预估）

| 文件 | 改动 |
|------|------|
| `backend/app/core/rag_pipeline/`（新增） | `__init__.py` + `sources.py`（RagSource 协议）+ `pipeline.py`（路由器/融合）+ `router.py`（按需开关规则） |
| `backend/app/core/rag/manager.py` | 实现 `RagSource` 接口（图谱源） |
| `backend/app/core/kb/kb_manager.py` | 实现 `RagSource` 接口（知识库源）；`search` 增加返回 `path`；可选父级扩展 |
| `backend/app/services/chat_service.py` | 把 `_build_rag_context`/`_build_kb_context` 两处硬编码替换为 `pipeline.run(ctx)` |
| `backend/app/core/kb/doc_vector_store.py` | 增加 `path` 列（如需父级扩展，增加 parent_id 引用） |
