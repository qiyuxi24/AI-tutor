# TutorAgent 教育资源采集模块（Collector）— 未完成 TODO

> 设计依据：`docs/教育资料采集模块_设计讨论.md`（决策 #1-25 + §11 里程碑）
> 创建：2026-09-05 ｜ 上次整理：2026-09-13
> **✅ Batch 1（B1.1-B1.8）与 Batch 2（B2.1-B2.5）已完成，整体搬到 `done.md` §7；本文件只留未完成项。**
> 铁律（用户）：**去耦合、独立测试（尽量）、复用已有组件**
> 标记约定：
> - `[需API]` 必须真实网络/外部服务才能验收（DASHSCOPE key / 维基联网），其余一律 mock 可测
> - `[新文件]` / `[改]` / `[复用]`
> - 每项含「测试」落点；标注的验收均指本地 mock 或离线可跑

---

## 待 API / 真网验证（B1/B2 收尾，其余子项已完成）

- [ ] **B1.6 真实嵌入灌库**：`venv/Scripts/python.exe scripts/seed_collector.py --user <演示账号> --embed api`（阿里 text-embedding-v4 需非欠费），40 词条入库后 `stats` 正常，替换 mock 哈希向量（真向量召回质量是检索指标前提）
- [ ] **Batch 1 端到端对话引用**：灌库后对话中挂载「自动采集」目录提问 DSA 概念 → 回答带 KB 引用（先完成上一条；对话侧 MiniMax-M3 已可用）
- [ ] **B2.1 OI-wiki 真网联调**：`search("数据结构与算法")` 应返回 OI-wiki 数据结构/算法基础板块候选，fetch 首页正文入库无 MkDocs 残留语法（2026-09-05 已转 `TODO.md` P0 待议，暂缓）

---

## Batch 3 — 题库导入 + web_page 漏斗 + 来源统计

### B3.1 结构化题库导入 [新文件] `adapters/dataset_quiz.py`
- [ ] 读取开放数据集 JSON（如 CMMLU）→ 映射 `Question` schema → `quiz_store.save_questions`（复用）
- [ ] subject/knowledge_point 来自数据集字段，填入库（`save_questions(subject=...)`）
- [ ] 测试：`test_dataset_quiz_adapter.py`（样例 JSON 映射、非法行跳过）
- [ ] **预研项：存储配额/TTL**（审查⑧，入库批量导入**之前**必须完成，防百万题撑爆磁盘）：`storage_quota` 配置 + 超限告警

### B3.2 通用网页正文抓取 [新文件] `adapters/web_page.py`
- [ ] 站点白名单（个人模式）+ `trafilatura` 主用 / `readability-lxml` 后备（决策 #5），双失败即弃
- [ ] `httpx` 下载 → 正文抽取 → 转 MD 入库（license 由站点映射表给定，默认 L2）
- [ ] 测试：`test_web_page_adapter.py`（mock HTML：抽取成功/失败回退/超时）

### B3.3 来源质量统计 [改]
- [ ] RAG 命中时 `times_referenced += 1`（KbRagSource.retrieve 命中即累计，异步落库，不阻塞）
- [ ] `GET /collector/stats/sources`：按来源统计引用次数排行
- [ ] 测试：`test_source_stats.py`

**Batch 3 验收**：开放题集灌入 quiz 可练；网页抓取 2-3 站可入库；来源排行可查。

---

## Batch 4 — 登录站 + CollectorRagSource + 本地模型（v1.5+）

### B4.1 Cookie 会话复用 [新文件] `collector/session.py`
- [ ] 浏览器辅助登录（复用 agent-browser/playwright）：弹窗登录一次 → Cookie 存用户级目录（隔离/不进日志）
- [ ] httpx 携带 Cookie 抓取；合法需登录站标注 L2；zlib 类不提供适配器（决策 #13）
- [ ] 测试：mock cookie 注入 + 会话请求

### B4.2 CollectorRagSource 评估 [改]
- [ ] `[改] rag_pipeline/sources.py` 新增 `CollectorRagSource`（name="collector"）+ `pipeline.register()` 一行
- [ ] 评估是否入对话引用（延迟/质量），默认仅在采集页使用（§6.8/#18）
- [ ] 测试：`test_collector_rag_source.py`

### B4.3 本地 embedding 正式启用 [改]
- [ ] 安装 sentence-transformers（可选依赖，`requirements-optional.txt` 或注释指引）
- [ ] `[改] app/core/llm/embed.py`（嵌入唯一出口，kb_manager/rag_manager 的 `_embed` 已是薄委托）：加 `EMBED_MODE=local|api|auto`，auto 降级链 API→本地→hash（复用 `kb/embedder.py` 的 `get_embedder`）
- [ ] 大规模入库批量 embedding 性能验证；成瓶颈再评估 worker 进程（决策 #20）
- [ ] 测试：本地未装时优雅回退已有测试覆盖，补 `test_embed_mode_config.py`

---

## v2 — 高级（暂不排期）

- [ ] 权威目录按需查询（§6.4）：K12 教材章节/考核类大纲实时查官方源（静态目录先覆盖主干科目）
- [ ] 整书 LLM 智能切章（需真实 LLM key，`[需API]`）
- [ ] 全学科目录数据批量内置（14 门类 + 117 学科种子词自动化生成）
- [ ] 采集覆盖率前端雷达图细化（学段→学科→板块 drill-down）

---

## 里程碑与外部依赖（汇总）

| 批次 | 主要外部依赖 | 风险 |
|------|------------|------|
| B1 | Wikipedia API（联网）/ DASHSCOPE embedding（入库默认走 API） | 维基 UA/限速已内置；embedding 可 `--embed mock` 降级（**已完成部分见 `done.md` §7.1**） |
| B2 | 无（全部离线可测） | — |
| B3 | trafilatura/readability-lxml（新增 py 依赖）、开放数据集下载 | 数据集网络下载一次缓存本地 |
| B4 | playwright/agent-browser、sentence-transformers+torch | torch 体积大，仅可选启用 |

## 遗留技术债（登记未修的已知小项）
- [ ] **#2 试卷拆分器 `quiz_splitter.py` 已就绪但零引用（未接入任何链路）**（2026-09-12 发现）：`collector/quiz_splitter.py`（整卷非结构化文本 → 逐题结构：题号/大题/选项/答案回填/解析，纯函数零 LLM）已可用且有 `tests/test_quiz_splitter.py` 9 例覆盖，但**全库零调用**（只在自身 `__main__` 自检里跑），属"能用但没人用"的库。**待决策（入口形态三选一）**：① `/kb/upload` 命中试卷形态后自动拆分；② 独立 `POST /quiz/import`（上传整卷 → 拆分 → 人工校对 → 入库）；③ 与 B3.1 `dataset_quiz` 合并做（同属"题库导入"，但形态不同：B3.1=结构化 JSON，本项=非结构化试卷文本，不要混在一支适配器里）。**接入时必须注意**：`quiz_store.save_questions` 收 `Question` **对象**而非 dict（须写 `Question(**q.to_question_dict())`）；DB `id` 自增，拆分产物的 `q1/q2` 重号入库无害。
