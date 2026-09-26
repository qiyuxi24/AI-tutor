# 项目记忆

> 只留**跨会话稳定的决策与约束**。命令/坑/文档路由见根 `AGENTS.md`（已注入，不重复）；历程见 `docs/项目历程_决策与效果记录.md`。
> 最近整理：2026-09-24（第二十一次：压缩去重，实现细节交还 docs）。

## 项目概述 / 用户偏好
- TutorAgent：知识图谱驱动的自适应导学 Agent；Vue3+Vite+D3+Pinia+EP / FastAPI+SQLite+Jinja2+LLM(OpenAI 兼容)。对话/Agent = MiniMax-M3；嵌入固定阿里 text-embedding-v4（独立 key/base，**2026-09-23 已恢复**）。
- 比赛材料冻结（2026-09-12 起）：`docs/比赛/` 不主动读/改/同步，仅用户点名才碰。
- 用户偏好：渐进式改进、清单式推进；重视代码安全与架构一致性。**写代码必加载 `ponytail`**（写文档/讨论豁免）——机制是压缩 diff，固定成本 ~2K token/任务，对外**不得宣称"省 token / 更稳定"**；债务标记 = 代码内 `ponytail:` 注释。
- 用户重视"用数据说话"：改动要有评测数字 + 实验产物入库。

## 仓库 / 环境 / 部署
- `origin` = `qiyuxi24/AI-tutor`；`github-desktop-zhuzixuan2007` = 同学仓库（**只读**）。同学两批改动已并入 main（`44d3d84` + `673e008`），**尚未 push**。
- **并发写入是常态**：提交必须路径限定（禁 `git add -A`），内容与预期不符先查 mtime；**整理/审计类任务不擅自 commit**。
- ⚠️ 推 GitHub：直连 443 被阻断 → `git -c http.proxy=http://127.0.0.1:7897 push origin main`。装包前 `$env:NO_PROXY="*"`（注册表 `ProxyEnable=1` 残留 → pip `ProxyError`）。
- 远程服务器：`ssh wojtek@100.90.96.111`（Tailscale 免密）；TutorAgent 跑在 `http://100.90.96.111:8080`（容器 `ai-tutor`）；一键 `deploy/deploy.ps1`。sudo 密码 = 登录密码。
- `start.ps1` 后端必须独立控制台；清残留 `taskkill /T /F`；部署镜像 = 工作区快照，须 `docker compose up -d --build`。

## 运维后台（独立服务，2026-09-19）
- `frontend-admin/` :5174 + `backend-admin/` :8001；`admin_users`/`admin_audit_logs` 与主库同存 `data/knowledge/knowledge.db`；初始超管 `admin`/`admin123`。
- 后端约定：纯 `sqlite3` + dataclass/os.getenv（**不用 pydantic-settings**）+ `bcrypt` + 同步 `def` 路由；业务更新与审计**同一事务**；管理员 CRUD 需 `require_super_admin()` + 三条自锁。
- **删号顺序铁律**：`delete_user_rows()`（同事务不 commit）→ 审计 → `commit()` → `purge_user_storage()`。测试 `cd backend-admin && ..\backend\venv\Scripts\python.exe -m pytest tests -q`（37 例）。

## 硬约束 / 存储布局
- ⚠️ 主存储：图谱/用户 = `data/knowledge/knowledge.db` + `data/knowledge/nodes/{uid}/`（`nodes.file_path` 装饰性，**不能 unlink**）；对话 = `data/conversations/conversations.db`；知识库/向量 = `backend/data/{kb,rag}/{uid}/`；agent_runs = `backend/data/agent_runs/agent_runs.db`。
- **嵌入两条线不合并**：检索侧 `llm/embed.py::embed_texts`（async、无兜底）vs 语义去重侧 `kb/embedder.py::ApiEmbedder`（同步 + hash 兜底）；同步桥在 FastAPI 里会跑新事件循环 → "Event loop is closed"。
- ⚠️ 排查 agent 行为第一站 = `core/agent/debug_log.py`（控制台 + `debug_log.db`，14 天）。防循环靠 `_LoopGuard`（总调用 12 / 墙钟 180s / 同参数重复 ≤2 / 连续失败 3）；`stop_reason` 三处一致。
- ⚠️ `/knowledge/events` 必须 `subscribe(user_id=)`；`/agent/runs` 的 stats 路由须在 `{run_id}` 前。
- **换嵌入模型必须重建索引**（同维度也不可比）；代码只在维度不符时 skip → 同维多模型静默出垃圾。

## 上下文工程
- 配额 SSOT = `docs/上下文工程/上下文工程_预算框架.md`（**B=48K** / S1–S9 / 让位顺序 历史→检索→图谱→画像）；活清单 = `TODO_Context.md`。
- 已落地：`LLM_CTX_BUDGET=48000` + `OUTPUT_RESERVE=3000`；固定段 40% 告警 + 45% 强制减半；S6/S7 按源截断 ≤3K；S8 分层保留；Tool Result Clearing；`prune(30,180)`。
- ⚠️ **跨请求历史无 tool 消息** → 改历史 tool 消息是死代码或造非法序列（真机 400）。
- **token 记账三层**：`agent_runs.token_usage` / `llm_usage`（`call_llm` 必须传 `kind=`）/ `debug_log.db`。

## 知识图谱
- 图谱：SQLite（`nodes`/`edges`/`node_aliases`/`mastery_events`）+ 节点 MD；**边方向 `A → B` = A 是 B 的前置**。
- **四份 SSOT**：① 存储结构/检索通路/改造清单 = `docs/知识图谱/知识图谱_数据结构与检索通路_评审与改良方案.md`（KG-D1~D15；**表定义真值仍是 `knowledge_graph.py::_create_tables`**）；② 设计说明/业界对比/准确率口径 = `知识图谱_数据结构设计说明与业界对比.md`；③ 生成质量 = 根 `TODO_Graph_Quality.md`（GQ-1…GQ-14）；④ 角色契约 = `知识图谱_参照系契约.md` v4。裁决链：运行时行为 > 契约 > AGENTS.md > 其他文档。
- **KG-D 进度（2026-09-23）**：已落地 D1（subject 列）、D2（tags 不写难度档）、D3（`node_aliases` 判重）、D4（`mastery_events`，唯一写入点 = `update_node_info`）、D5（检索关键词兜底 + chunk_index 全局递增）、D6（edges 时间戳 + 唯一索引）+ `nodes.updated_at`。**刻意未做**：`weight`（无写入方）、`relation` 触发器、无向归一化（存量无反向重复）。**待拍板**：D7 层级粒度（用户已定"真实章节点 + 物化 path 两者都做"）。
- 生成质量：节点过浅已修；重名重复已修 —— 判重唯一实现 `find_node_by_name`，`create_node_with_content` 命中即并轨并**返回实际落点 ID**；体检/合并 = `scripts/inspect_graph_quality.py`（`--user N`、`--fix-dupes [--apply]`）。
- 掌握度四档 `mastery_bucket()`（WEAK 30 / MASTERED 70）；**唯一主信号 = 出题判分**（`grade_answer` +20）；出题跨调用去重（`avoid_questions`）；触发类提示词必须写"不要做什么"。

## RAG
- 四层：图谱 rag(`core/rag/`) + 上传 kb(`core/kb/`) + `hybrid_search/` + 对话注入 `rag_pipeline/`（`RagSource` 协议 + 规则 router；**gather 必须 `return_exceptions=True`**；单源 8s 超时静默返空）。
- 检索形态：两路各宽召回 30 → **加权融合 alpha=0.6/0.4**（2026-09-23 起为生产默认，RRF 退为备选）→ top5；whoosh 自定义 CJK bigram（**单字查询不命中**）；PDF 逐页探针 + `<<<PAGE n>>>` 契约在 `parsers/base.py`。
- 图谱 RAG 双检索已落地（2026-09-23）：`rag/manager.py` 加 BM25 腿，与嵌入**解耦**（嵌入欠费时节点仍进 BM25）；融合主键 `"{node_id}#{chunk_index}"`；whoosh 改**同步 writer**（AsyncWriter 的 commit 异步 → 索引完立刻检索会漏召回）。详情与数字 = `docs/RAG/RAG_图谱混合检索与真实嵌入评测_报告.md`。
- 未落地（按性价比）：① 跨源统一融合（KG-D10，pipeline 现按分排序 + content 去重，两源分数不可比）；② 图谱检索 gold 升级（现为 CMRC2018 段落级自动标注）；③ HyDE / Query Expansion（`docs/RAG/RAG_召回与重排优化调研.md` 路线 2，TODO P2）；④ Cross-Encoder 精排（需 torch，远期）。
- 评测集（`backend/eval_data/`，**数字不可外推**）：
  - kb 通路 `eval_rag.py`（CMRC2018 1000 query，真实嵌入）：fuse R@1 0.9790 / MRR 0.9878 最优。
  - 图谱通路 `eval_graph_rag.py`（308 节点 / 567 query，真实嵌入）：vector 0.8254 → **fuse 0.8624**（+3.7pp），MRR 0.8945→0.9195；mock 嵌入下 0.4603→0.7302。
  - 前置推断 `eval_prerequisite.py`（8 条 gold）：F1 0.714→0.941。
  - 图谱开/关 A/B `eval_graph_injection.py`（14 例）：定位率 39.0% vs 36.6%（**持平**），但引用真实 node_id 0→5、token −65%、状态误述 2→0；指标不稳定（GQ-14），引用前先读 `知识图谱_P1扩跳与AB对照实验.md §8`。
  - ⚠️ CMRC2018 字面重叠极高 → BM25 天然占优，不要把 0.87 当"BM25 足够好"的证据。
- **版权/开源边界**：`core/open_source.py` = 「来源能否留存」**唯一判定出口**（fail-closed 到 L3）；采集侧未登记站点默认 L3。**新增"会留存内容"的 URL 入口必须同时过 `net_guard.is_blocked_url` 与 `is_open`**；联网侧唯一存档出口 = `core/agent_tools/web_archive.py`。维基用 **zhconv** 统一简体（可选依赖，测试 `importorskip`）。
- 画像 `core/profile/`；quiz：API 5 端点 + agent 工具（`quiz_generate` 后台异步 → `quiz_ready` → `grade_answer`；`_INFLIGHT` 必须在 `create_task` 前占位）。
- 未接线（有实现+测试+零生产引用）：`collector/pipeline_ingest.py`+`chapterizer.py`、`collector/quiz_splitter.py`。
- 测试基线（2026-09-24 实测）：`pytest backend/tests -q -m "not llm_api"` → **962 passed, 7 deselected**。真实 API 测试已授权（`-m llm_api` / `scripts/smoke_*.py`，须 `$env:PYTHONPATH="."` 从 backend 跑）；离线 mock 通过 ≠ 真机通过。

## 比赛选题（AIC 第八届）
> 细节见 `docs/比赛/AIC/`，此处只留结论。
- **定选赛题5 AI+学科交叉**（备选赛题7 OPC）。硬要求：锚定一门具体课（建议"数据结构"）+ 真实试点/用户反馈（20 分）+ **材料禁出现校名/Logo/指导教师**。视频 3-5 分钟 ≤300MB，方案 PDF ≤10M。
- ⚠️ 旧口径作废：第六届"乘数公式/一镜到底"已被第八届《赛题规则汇总》(260506) 百分制评分表取代。
- 路径：陕西**未设省级赛区** → 西工大走**区域赛**；校赛 10/10 前 → 区域赛报名缴费 10/15 20:00 → 省赛 11/01 前 → 总决赛 11 月中下旬。
