# TutorAgent 备赛与工程化清单

> 定位：知识图谱驱动的自适应导学 Agent（知识图谱 + 上传知识库 RAG + AI 出题 + 资源采集）
> 赛道：**三赛同投**（国创 / AI+教育 OPC / AIC），总纲·时间线·注意事项见 `docs/比赛/COMPETITION.md`；本文件只列**未完成**工程化待办
> 今日：2026-09-13（距 10-15 双截止约 4.5 周）
>
> - **✅ 已完成项一律搬去 `done.md`（那里只追加、不重写）；本文件只保留未勾选项，保持精简省 token。**
> - **⚠️ 清单状态按代码实测校准（非按文档印象勾选）。**
> - 阶段就绪度评估结论（2026-09-08 复核）也在 `done.md`。

---

## P0 — 收尾就绪（演示不翻车 + 材料可信）

- [ ] **真实向量链路验证（阿里 embedding 额度恢复后）**
  - [ ] `scripts/seed_collector.py --embed api` 真实向量灌库
  - [ ] `scripts/eval_rag.py --embed api` 真实重跑 → 定夺 RRF vs 加权融合（当前 mock 结论：加权 0.818 > RRF 0.766）
- [ ] **quiz 简答 LLM 判分端到端联调**（走 call_llm 纯文本，MiniMax key 可用即可验，不依赖 embedding）
- [ ] **OI-wiki 真网联调**（原 B2.1 收尾）：`search("数据结构与算法")` 应返回 ds/算法基础板块候选、fetch 首页正文入库无 MkDocs 残留语法 —— 普通联网即可验（无需 DASHSCOPE），**待议**（2026-09-05 暂缓，待用户定时间）
- [ ] **演示数据 + 脚本**：填充 1 个完整学科图谱（15-20 节点 + prerequisite 边）；写 2-3 条预演对话路径

## P1 — 工程化补强（评委可问项）

- [ ] **Prompt 笔记优化**：教学后主动 `update_node_content` / `add_knowledge_node` 记笔记（提示词层，未动）
- [ ] **空图谱时的行动顺序（后续项，待议）** —— 提示词层 MPV 已完成（见 `done.md` §2.2）
  - [ ] **空图谱时从教材一键建图**接入对话链路 —— 新增 Agent 工具复用 `kb/graph_generator`。注意单工具超时 60s、建整书图耗时数分钟 → 不能同步跑在 loop 里，需后台任务形态（前端已有 `POST /kb/graph/generate` 可复用）
  - [ ] **工具层引导** —— 节点不存在时把 `E-LLM-007` 变成"请先用 `add_knowledge_node` 创建它"的可自纠正提示（P1 对照实验发现模型会拿节点名当 `node_id`）
- [ ] **建节点归属收口**（部分完成，2026-09-13）：显式参数（模型自报 `subject`/`board`，显式优先）与自动判定（`kg_taxonomy` 规则优先 + LLM 兜底）已并存且互不干扰；待收口 = 4 套写路径合并为唯一 `create_node_with_content()`、MD 模板统一、语义去重、权限守卫（顺序见 `docs/知识图谱_模块结构与封装调研.md`）

## P2 — 功能路线图（比赛可选项 / 有真实 key 后）

- [ ] **RAG 精排（Cross-Encoder）**：hybrid_search 后接 rerank，量化收益（W4 可选，须先有真实 embedding 灌库）
- [ ] **HyDE / Query Expansion**：召回 +15-25%，LLM 失败回退普通检索
- [ ] **出题进阶**：参数化母题模板、题目知识关联回填图谱
- [ ] **学习大纲生成 / 主动引导式提问 / 自包含 HTML 复习卡导出**
- [ ] **"AI 同学"多角色讨论 / 白板图谱联动（SVG）**
- [ ] **LLM 服务商抽象层**（deepseek/GLM 切换）；KG_TOOLS → 抽象"动作层"
- [ ] **RAG 引用卡片拖拽窗口**（知识卡片白板，借鉴 OpenMAIC）

---

## 遗留技术债（2026-09-08 盘点，非功能项、优先低）

- [ ] **试卷拆分器 `quiz_splitter.py` 已实现但零引用（未接入上传链路）**（2026-09-12 发现，详见 `TODO_Collector.md` 遗留技术债 #2）：需先定入口形态（`/kb/upload` 自动拆 / 独立 `POST /quiz/import` / 并入 B3.1）。
- [ ] **图谱 RAG 未接入 hybrid_search 双检索**（2026-09-11 调研发现）：图谱侧只有向量检索（`rag/manager.search`），未享 BM25 稀疏路。**评估结论：不是小改** —— `hybrid_search/whoosh_index.py` 的 schema 把 `node_id` 定为 `NUMERIC`（KB 文件节点 int），图谱 node_id 是 TEXT，复用需改 schema + 老索引迁移/双 schema 兼容。收益（图谱写回 BM25 召回）与成本需先量化，暂缓。
- [ ] **conversations 内嵌 tools/thinking 去留 + run 与会话无关联键**（9/8 起挂着，**待决策**）：需定"是否为 agent_runs 加 conversation 外键/会话 id 字段（动 schema）"，或接受现状。

## 进度速览（2026-09-13）
- 代码层工程化 ~85%（架构✓ 测试✓ 部署✓ 文档✓ CI✓ 运维✓ 限流✓ 日志✓ Agent Loop✓）
- 测试：离线零网络集（`-m "not llm_api"`）**580 passed, 7 deselected**；real_api 独立 7 passed（需真 key + pytest-asyncio，单独跑 `-m pytest tests/test_agent_loop_real_api.py`）
- 比赛落地待办：P0（演示数据 / 阿里 embedding 向量灌库+评测 / quiz 判分联调 / OI-wiki 待议）→ 材料期 10/5 起
- 已完成项归档：`done.md`
