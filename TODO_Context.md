# TutorAgent 上下文工程 — 未完成 TODO

> 创建：2026-09-15 ｜ 本轮口径：**B = 48K**（v2 定案）
> 定位：**只列未完成项**；已完成项见文末「已完成」表（**不要重做**）。
> ⚠️ `reports/` 在 `.gitignore` 里：探针脚本 `reports/probe_context_budget.py` 只存在于本工作区，**新克隆的仓库没有它**（需要时按 `docs/上下文工程/上下文工程_预算框架.md` §2.4 的表重建）。
> 铁律（用户）：**不要动其他模块**；复用已有组件；改动必须可机器复核。
> 标记约定：`[改]` 改现有文件 ／ `[新文件]` ／ `[需API]` 需真实外部服务 ／ `[待议]` 需用户先定
> 每项含「落点 + 做法 + 验收 + 依赖」，验收一律可离线跑。

---

## 0. 新窗口从这里开始（30 秒交接）

1. 先读三份文档，按此顺序：
   1. `docs/上下文工程/上下文工程_预算框架.md` —— **配额 SSOT**（B=48K / 九段 S1–S9 / 让位顺序 / 触发阶梯）
   2. `backend/app/core/agent/README.md` —— 代码结构（八模块职责 + 一次 run 时序 + 改码 10 坑）
   3. `docs/上下文工程/上下文工程_调研与差距审计.md` —— 现状审计 + 论文依据 + §10–§12 实施记录
2. 跑一次探针拿到当前基线（**改任何东西前先跑，改完再跑对比**）：
   ```bash
   backend\venv\Scripts\python.exe reports\probe_context_budget.py
   ```
3. 回归基线（2026-09-20 实测 **745 passed, 7 deselected**）：
   ```bash
   backend\venv\Scripts\python.exe -m pytest backend/tests -q -m "not llm_api"
   ```
4. 硬约束：Python 必须用 `backend/venv/Scripts/python.exe`；uvicorn 必须 `--workers 1`；`.env` 唯一真值 = **根目录 `.env`**。

---

## 当前状态速览（2026-09-15 本轮结束）

| 段 | 目标（K token） | 现状 | 缺口 |
|---|---|---|---|
| S1 输出预留 | 3 | **3**（`guard.OUTPUT_RESERVE=3000`） | ✅（D-3 待定） |
| S2 静态指令 | 1 | 0.9 | 达标 |
| S3 工具/技能 | 5.4 | 5.4（schema 占 2,661） | 达标（新增工具要算账） |
| S4 图谱结构 | 6 | 5.2（硬上限 + 降级阶梯 + **越线强制重建**） | 达标 |
| S5 用户画像 | 0.8 | ~0.8 | 达标 |
| S6 图谱检索 | 2 | **独立截断 ≤3K + query 双端放置** | ✅ |
| S7 知识库检索 | 2 | 同上（**一源超长不挤占另一源**） | ✅ |
| S8 对话历史 | 21 | **分层保留（锚点 + 近 3 轮 + 规则要点）+ 较早 tool 结果换占位符** | ✅ |
| S9 当前轮 | 6 | 无约束（协议要求，暂不动） | — |
| **预算线** | **48K** | **`LLM_CTX_BUDGET=48000`**（Docker 走同一默认值，无漂移） | ✅ |

**实测固定成本 S2–S7 = 13,984 token = 29.1%×B**（告警线 40%×B=19,200，已占 72.8%；余 31K 给 S8+S9）。每轮已记固定段占比（日志可查，不变量 B-3）。
**仍无运行时"分段记账"** —— 只能靠探针静态算，运行时看不到每段实际多少（→ P2-①）。

---

## P2 — 度量与自动化（仍未做）

### P2-① 运行时分段记账 `[改]`
- 现状只有静态探针；框架不变量 B-1~B-7 需要运行时可见。
- **落点**：`agent/store.py` 的 `agent_runs` 表（**扩展字段，不新建表；加列必须写进 `store._COLUMN_MIGRATIONS`**）+ `agent/loop.py` 落库处；`chat_service` 把分段体量传进去。
- **验收**：`GET /agent/runs/{run_id}` 能看到该次 run 的各段 token；跑 20 轮对话可以取 P95。

### P2-② 用实测拐点替换文献值 `[需API]`
- 框架 §1.2 的 S6/S7 = 2K、S8 = 21K 目前是"文献曲线 + 体量估算"，**不是本项目实测**。
- 做法：固定的 20 轮教学对话，扫 S6/S7 的 top_k 与 S8 的轮数，按正确性/成本画曲线，取拐点。
- **验收**：出一张"段配额 vs 指标"对照表，回填框架 §1.2 并升 v3。

---

## 待决策（需用户先定，别自己拍）

| # | 事项 | 影响 |
|---|---|---|
| D-1 | **S6/S7 的 top_k 与去重规则**（本轮只做了**体量截断**，策略未动）：S6 是否跳过已出现在 S4 的节点？跨源融合配额怎么分？是否需要"检索段总开关"（图谱小的时候 S6 可能纯冗余）？ | `_build_retrieval_context` 的上层策略 |
| D-2 | ~~S8 摘要用规则化还是 LLM~~ → **已按规则化落地**（`guard._summarize_turns`）；若需语义摘要，单列子任务并 A/B | 成本量级 |
| D-3 | S1 预留定 3K 后，`agent/loop.py` 的 `_chat_once` **max_tokens 要不要从 2000 提到 3000**？（M3 思考与正文共享预算，偏低会导致空回复走重试） | 回复质量 vs 成本 |
| D-4 | 是否给"固定段超线"配**硬失败**（拒绝发请求）而非"先降级、再 error 照发"？ | 严格度 |

## 待核验（外部信息，做之前先确认）

- **MiniMax-M3 / DashScope 是否提供 prefix-KV 缓存及计费** → 决定"稳定前缀"的收益（Manus 实测缓存与未缓存差 **10 倍**价），也决定"不重排"的实际价值。框架 §9.3 挂了很久。
- **M3 在 48K 处的真实有效长度** → §2.1 的"多数模型有效 < 50%"是**别的模型**的结论；48K 这个数需要本项目自己的数据背书（P2-②可顺带做）。
- Anthropic Agent Skills 官方页正文（当前只取到二手整理页）→ 影响 S3 的渐进披露细节。
- **P1-④ query 双端放置的 A/B**：本轮按论文（Lost in the Middle）落地，**尚未在本项目数据集上验证**（框架 §4.4 要求"唯一允许的注入顺序改动，必须有 A/B"）。

---

## 相关但不在本 TODO 范围（在 `TODO.md`）

- `conversations` 内嵌 tools/thinking 去留 + run 与会话无关联键（待决策）。
- 图谱 RAG 未接 `hybrid_search` 双检索（评估结论：不是小改，需改 whoosh schema）。
- `core/` 顶层仍平铺 15 个文件的分包规整（改动面 30+，可选）。

---

## 已完成（**不要重做**，细节见各自文档）

| 日期 | 事项 | 依据 |
|---|---|---|
| 2026-09-13 | `call_llm(max_tokens=, thinking=)` 参数化 + `finish_reason=="length"` 截断检测 | 调研审计 §10 |
| 2026-09-13 | **图谱注入预算化**：`GRAPH_INJECT_MAX_CHARS=12000` + 降级阶梯（省摘要 → 限量节点 + 焦点邻域优先）+ 尾部"展示范围"说明；实测 300 节点 44,713 → 7,418 token | 调研审计 §11、`tests/test_graph_injection.py` |
| 2026-09-13 | 分析器路径同受上限约束（缺口 #8 顺带修） | 同上 |
| 2026-09-14 | `system_prompt_common.j2` 单花括号占位符 bug（图谱此前**压根没注入**）→ 已修 + 回归守卫 | `tests/test_prompt_loader.py` 头部 |
| 2026-09-15 | 删 recursive 模式重复图谱注入块（三模式统一由 `{{ knowledge_graph_summary }}` 注入；实测 -58%） | 调研审计 §12 |
| 2026-09-15 | 工具说明双源收口（注册表 `guidance` 生成提示词段落） | `core/agent_tools/README.md` |
| 2026-09-15 | **`core/agent/` 包重构**（loop/context/guard/events/store + README） | `core/agent/README.md` |
| 2026-09-15 | **预算框架 v2**（B=48K、九段、让位顺序、触发阶梯、不变量 B-1~B-7） | `docs/上下文工程/上下文工程_预算框架.md` |
| 2026-09-15 | **P0-① 预算口径对齐**：`LLM_CTX_BUDGET` 32000→48000（config 两处 + `.env.example`）、`guard.OUTPUT_RESERVE` 2000→3000 | `tests/test_context_guard.py::test_budget_defaults_match_framework` |
| 2026-09-15 | **P0-② 固定段占比告警（B-3）**：`_build_system_prompt` 每轮记固定段占比，越 40%×B 记 warning（点名告警线与降级入口） | `tests/test_fixed_segment_budget.py` |
| 2026-09-15 | **P0-③ 固定段越线强制降级**：越 45%×B 时按当前体量**减半重建**图谱注入；仍越线记 error 照发（`guard` 裁不动字符串 → 落点在组装侧） | 同上 |
| 2026-09-15 | **P1-① S6/S7 分开截断（B-6）**：两源各自 ≤3K token、独立截断、尾部标注展示范围（`_trim_hits`/`_truncation_note`） | `tests/test_retrieval_budget.py` |
| 2026-09-15 | **P1-② S8 历史分层**：`[省略说明+规则要点] + [首条 user 锚点] + [最近 N 轮]`，两档降级（先砍要点行，再软目标照发） | `tests/test_history_layering.py` |
| 2026-09-15 | **P1-③ Tool Result Clearing**：较早工具批次的 tool 正文换占位符，保留 `role`/`tool_call_id` 与 assistant `tool_calls` 配对，最近 2 批原样。**⚠️ 落点修正：`agent/context.py`（loop 内），不是 guard** —— 跨请求历史里根本不含 tool 消息，原 TODO 的"优先 guard"判断与实际数据流不符（真机 400 `tool result's tool id() not found` 暴露） | `tests/test_history_layering.py` + `tests/test_agent_loop.py::test_old_tool_results_cleared_across_rounds` |
| 2026-09-15 | **P1-④ query 双端放置**：检索块前后各插一次当前问题（≤300 字符） | `tests/test_retrieval_budget.py` |

---

## 明确不做（框架 §八，别反复讨论）

- 把检索段做大（Databricks 实测：正确率峰值 8–32K，64K 后边际归零甚至转负）。
- 按**标称窗口**配预算（B=48K 已含"有效长度打折"，不再叠加）。
- 为排版重排注入顺序（Chroma 实测**打乱的干草堆一致优于结构化的**）——例外只有 P1-④ 那一条。
- 把 skill / 工具长正文常驻 prompt（渐进披露：常驻只放 metadata + 何时用）。
- 引入 Mem0 / Zep / Letta 等记忆框架（与现有图谱 + 画像 + agent_runs 同构）。
- 多 Agent 上下文隔离（单线教学场景 + 成本最多 15×）。
- S8 摘要走 LLM（每轮一次 LLM 与"省 token"初衷冲突；已按规则化落地）。
