# TutorAgent 备赛改进清单

> 定位：知识图谱驱动的自适应导学 Agent
> 赛道：大学生AI创新创业组 → 大学生OPC创新创业AI agent
> 校内截止：2026-10-15

---

## P0 — 地基（演示不可翻车）

- [ ] **项目改名：AI-Tutor → TutorAgent**
  - [ ] README.md 全面更新
  - [ ] frontend/index.html `<title>` → "TutorAgent"
  - [ ] frontend/ 各组件中的项目名称引用
  - [ ] backend/ API 介绍文案、日志标识
  - [ ] start.ps1 / install.ps1 相关输出
- [ ] **LLM 请求重试机制** — 网络抖动演示崩了直接出局
- [ ] **前端 toast 替代 alert** — `alert()` 太掉价
- [ ] **CORS 配置化** — 从环境变量读取，演示可能切换端口
- [ ] **图谱重复节点处理** — `create_node_from_ai` 已存在时追加内容
- [x] **create_node 全局 ID 冲突 bug**（2026-08-27 修复）— nodes.id 全局主键但生成时只查当前用户；新增 `_node_id_exists_globally`，generate_node_id/add_node 改用全局唯一性检查

## P1 — 痛点1：学习盲目碎片（已有核心，加可视化）

- [ ] **节点按掌握度着色** — 红(<30) / 黄(30-70) / 绿(>70) 三色梯度
  - ForceGraph.vue: `nodeFill()` 当前只有二态，改三色
  - 已有 mastery 字段，纯前端改动，无需后端
- [ ] **学习路径图上高亮** — 学习路径推荐结果在图上标出路径连线+节点高亮
  - 后端 `/learning-path` 已返回 ordered_nodes
  - ForceGraph.vue 新增 "显示路径" 开关或自动高亮

## P1.4 — 文件上传知识库（类似 ima，资源管理器式目录树）

> 背景：用户上传教材/PDF/Word 等文件自动向量化，按 Windows 资源管理器式多级目录组织，可勾选单个文件或整个文件夹（递归）放进对话上下文，AI 只检索所选范围内的内容。知识图谱与上传文件**分开检索**。

- [x] **后端：目录树知识库模型 + 文档解析向量化 + API**（2026-08-27 完成）
  - 数据模型（资源管理器式多级树，非固定三层）：
    - `nodes` 表：id, parent_id, name, type(folder/file), user_id（递归树）
    - `documents` 表：node_id, file_type, extract_text, status
    - `chunks` 表：doc_id, content, embedding（复用现有向量存储）
  - 文档解析：PyMuPDF(fitz) 解析 PDF，python-docx 解析 Word，原生 Markdown/TXT
  - 上传 → 解析 → 分块 → 向量化（阿里云 text-embedding-v4）→ 入库
  - 新增 API（实际实现路径为 `/kb/*`，在 api/v1/kb.py）：
    - `GET  /kb/tree` — 获取目录树
    - `POST /kb/folder` — 新建文件夹
    - `POST /kb/upload` — 上传文件到指定文件夹（解析+向量化）
    - `DELETE /kb/node/{id}` — 删除文件/文件夹（级联删子节点+索引）
    - `GET  /kb/search` — 在目录范围内语义检索
    - `POST /kb/context` — 选择文件/文件夹进上下文（返回范围内文件节点 ID 列表）
- [x] **前端：知识库目录树界面（Element Plus）**
  - 引入 Element Plus（el-tree + el-upload，先看效果）
  - 左侧资源管理器式面板（KbPanel.vue，对话视图可折叠侧栏，宽 280px）：建文件夹、上传文件、展开/收起、勾选
  - 勾选文件夹/文件 → 设为当前对话上下文范围
- [x] **对话集成**：按所选目录范围检索 RAG 上下文（与图谱 RAG 分开）
  - ChatRequest 增加 kb_node_ids/kb_node_name
  - chat_service._build_kb_context 在所选目录范围内检索并注入
  - store.kbContext + setKbContext，sendMessageStream 携带 kb
- [x] **递归深度限制**：已在 kb_store.collect_descendant_files 实现（max_depth 参数），后端 /kb/context 支持 max_depth，前端 UI 未接（后续加设置项）
- [x] **去耦合化解析器架构（注册表+策略模式）**（2026-08-29）
  - 新建 `backend/app/core/kb/parsers/` 包：BaseParser 抽象基类 + ParserRegistry 注册表，按扩展名自动路由
  - 内置解析器：text（txt/md/代码/json 等 30+ 种）、pdf（PyMuPDF）、docx、pptx（python-pptx）
  - 图片 OCR：png/jpg/jpeg/bmp/webp/tiff/gif（RapidOCR，可选依赖，装则启用）
  - 扫描版 PDF 自动 OCR 回退（无文本层时逐页渲染识别）
  - 老式 Office：doc/ppt/xls（探测 textract/LibreOffice/antiword 等外部工具，具备才启用）
  - 可选依赖探测：RapidOCR / 老式工具未装则对应格式自动降级为"不支持"，不破坏其他格式
  - 兼容层：旧 `parser.py` 保留 is_supported/parse_document 签名，委托给新包
  - requirements.txt 新增：python-pptx、Pillow、rapidocr-onnxruntime、onnxruntime

## P1.5 — 借鉴 OpenMAIC 模块（详见 docs/OPENMAIC_借鉴方案.md）

> OpenMAIC（清华 THU-MAIC，MIT 协议）借鉴落地，按价值排序：

- [x] **AI 出题测验模块后端**（⭐最高，补痛点2无反馈，2026-08-29 完成）
  - 后端 `app/core/quiz/` 模块：schema（Pydantic 题目模型）+ generator（RAG 教材知识库依据检索出题）+ grader（客观题规则判分/简答 LLM 判分）+ quality（质量过滤管道）+ quiz_store（SQLite 题库）
  - API：POST /quiz/generate、GET /quiz/questions、POST /quiz/{id}/grade、GET /quiz/stats
  - 出题依据=上传教材知识库混合检索（含例题真题），prompt 强约束"只基于参考材料出题"
  - 错误码 E-QUIZ-001~005
- [x] **AI 出题前端**（2026-08-30 完成）— 新增 QuizView.vue 出题页（活动栏"出题"入口）：
  - 出题配置：主题/知识点、难度(easy/medium/hard)、题量(3/5/8/10)、题型(单选/多选/判断/填空/简答)
  - 题目作答：单选/判断用 el-radio，多选用 el-checkbox，填空/简答用 el-input
  - 判分反馈：客观题规则判分、简答 LLM 判分，展示得分+评语+解析
  - API 封装：src/api/quiz.js（generateQuiz/listQuizQuestions/getQuizQuestion/gradeQuizQuestion/getQuizStats）
  - ActivityBar 加"出题"导航项（对勾图标），HomeView 接入 viewMode==='quiz'
- [ ] **AI 出题联调验证** — 启动后端用真实 DASHSCOPE key 验证出题/判分（注意 qwen-plus 聊天额度 403 的坑），验证简答 LLM 判分
- [ ] **AI 出题进阶** — 参数化母题模板（数学/物理类换参数出题，100% 正确）；RAG 出题时 node_id 限定目录范围；出题质量回溯（题目关联 knowledge_point 回填图谱）
- [ ] **学习大纲生成** — 让 AI 首轮输出结构化学习大纲（增强 adaptive prompt / 建骨架）
- [ ] **主动引导式提问** — prompt 强化"设置条件+提示"引导
- [ ] **自包含 HTML 复习卡导出** — 借鉴 OpenMAIC"离线资源内联 data:URI"，图谱知识点导出成离线复习页（演示加分）
- [ ] **"AI 同学"轻量多角色讨论** — 改 prompt 模拟虚拟同学追问（system_prompt_peer.j2，MessageBubble 区分老师/同学角色）
- [ ] **白板/图谱联动** — 手写推导 + 图谱覆盖层（SVG）
- [ ] LLM 服务商抽象层（统一 Provider 切换 deepseek/GLM 等）
- [ ] 动作引擎重构（KG_TOOLS → 抽象"动作层"）

## P1.5 — RAG 去耦合 + 目录级检索（详见 docs/RAG_去耦合与目录检索调研.md）

> 背景：用户提出两个架构问题——(1) RAG 是否去耦合、是否需要"中间件（LLM 问什么再查什么）"；(2) 知识库按目录组织，是否"先检索目录→再检索文档"。调研 Agentic RAG / RAPTOR / Parent-Child 后结论：去耦合用"**RAG 路由器（可插拔数据源）+ 按需检索开关**"，目录用"**元数据过滤 + Parent-Child 父级回溯**"，而非"先目录后文档"顺序流程。

- [x] **P0: RAG 路由器解耦**（2026-08-29 完成）— 新增 `backend/app/core/rag_pipeline/`
  - `types.py`: `RagHit`（统一命中格式）+ `RagContext`（检索请求上下文）
  - `sources.py`: `RagSource` 协议 + `GraphRagSource`（图谱）+ `KbRagSource`（知识库，含目录展开）
  - `router.py`: 按需检索开关（问候/过短消息跳过，零 LLM 成本）
  - `pipeline.py`: 路由器 + 跨源融合去重 + 单源超时(8s)/异常隔离（gather return_exceptions=True）
  - chat_service 两处硬编码合并为 `_build_retrieval_context`（一次 pipeline.run 按 source 分组生成图谱/知识库区块，避免重复 embedding）
  - 已验证：API 挂掉静默返回空不抛错；单源异常/超时隔离；问候跳过；跨源融合去重；14 项单测通过
- [ ] **P1: 按需检索开关（轻量版 Adaptive RAG，零 LLM 成本）**
  - 规则/关键词判断：问候、过短消息（<N 字符）跳过检索；命中图谱/知识库关键词走对应源
  - 减少无谓 embedding API 调用（省钱省延迟，评审加分）
- [x] **P1: chunk 增加 `path` 目录路径元数据**（2026-08-30 完成，"仅 LLM 可见"）
  - `kb_store.get_node_path(user_id, node_id)`：从 node_id 向上拼完整目录路径（如 `/数据结构/第2章/栈.md`），防环+跨用户隔离
  - `kb_manager.search()` 返回结果时用 `_attach_paths` 附加 `path`（按 node_id 缓存去重查询）
  - `KbRagSource` 把 path 填入 `RagHit.path`；无 path 时回退 heading
  - `_build_retrieval_context` 知识库区块标注来源：`### 片段 1：/数据结构/第2章/栈.md`
  - 动态计算不落库，零迁移；已验证 6+4+7+3 项单测（get_node_path/attach_paths/KbRagSource/prompt 标注）
  - 可作后续重排加权信号（path 命中查询关键词加分），为 P2 父级扩展铺路
- [x] **P2: 父级扩展（相邻块拼接，预算决策）**（2026-08-30 完成）
  - 方案：命中子 chunk → 左右交替拼接相邻块 → 返回更完整父文本（用户选定"相邻块拼接"+固定预算）
  - 参数：`PARENT_EXPAND_CHARS=1500`（每个命中块追加预算）/ `PARENT_MAX_BLOCKS=8`（最多拼接块数）
  - `doc_vector_store.get_node_chunks(user_id, node_id)`：按文件取全部块（chunk_index 升序）
  - `kb_manager._expand_parents` + `_expand_one`：命中块居中左右交替扩展，预算/块数双重约束，超即截断
  - 铁律对齐：先在小块上排序（命中即已排序），再向父级扩展；父级扩展是预算决策非固定动作
  - 已验证 8+6 项单测（中间/首/尾块扩展、预算约束、块数上限、单块无扩展、不连续 chunk_index、同文件缓存）
  - chunk size 调研结论：子块 256~512 token（当前 500 字符≈250 token 已合适）；父块 1024~2048 token（固定预算 1500 字符约 750 token 适中，可后续调大）
- [x] **P1: 宽召回 + RRF 融合**（2026-08-30 完成，召回率优化路线0+1）
  - 方案：向量/BM25 各取 `RECALL_TOP_K=30`（宽召回，业界标准 30~50）→ RRF 融合 → 精排取 top_k=5
  - `fusion.py` 新增 `rrf_fuse`（Reciprocal Rank Fusion，arXiv:2402.03367 RAG-Fusion）：基于排名 `1/(k+rank)` 累加，k=60；对权重/分数尺度不敏感，多路命中优先，天然支持多查询融合
  - `kb_manager.search` 改用 `rrf_fuse`（替代原线性加权 fuse），宽召回各取 30
  - 已验证 9+5 项单测（RRF 多路命中优先/归一化/权重不敏感/min_score 过滤/极端分数不主导；search 宽召回各取30/重叠命中排前/最终返回5）
  - RRF 优势实测：线性加权被单路极端高分主导（X），RRF 稳定选两路都靠前的（B）
  - 调研文档：docs/RAG_召回与重排优化调研.md（含 HyDE +15-25%、Query Expansion +10-15% 待做）
- [ ] **融合策略需真实数据复核**（2026-08-30 评测发现）：CMRC2018 全量 mock 评测（1000 queries）显示
  `hybrid(RRF) Recall@1=0.766 < fuse(加权 alpha=0.6)=0.818 < bm25=0.964`——弱向量路（mock hash）会稀释
  强路排名，RRF 反而拖累 Recall@1。真实 text-embedding-v4 下需重跑确认（账户 Arrearage 暂无法验证），
  届时决定是否把 KB 检索融合改为加权/置信度自适应（参数化 alpha）。评测脚本：`backend/scripts/eval_rag.py`
- [ ] **P1 进阶: HyDE / Query Expansion**（召回 +15-25%，需真实 LLM key，见 docs/RAG_召回与重排优化调研.md）
  - 利用现有 `call_llm(enable_tools=False)` 生成假设答案/子查询，配鲁棒兜底（LLM 失败回退普通检索）
- [x] **P3: RAG 检索作为 function calling 工具**（2026-08-30 完成，Agentic RAG 完全体）
  - `KG_TOOLS` 新增 `rag_search` 工具（query 必填 + source graph/kb/all + top_k 1~5），工具总数 7→8
  - `execute_kg_tool` 新增 `rag_search` 分支 → `rag_search(query, source, top_k, user_id=kg.user_id)`
  - `_run_async` helper：同步工具执行里跑 async RAG 检索（有运行中事件循环时提交线程池新循环，否则 asyncio.run）
  - `KbRagSource.should_query` 改为 `bool(ctx.kb)`（node_ids 空/None 表示检索全部上传文档，支撑 rag_search 的 kb/all 检索）
  - 鲁棒性：API 失败返回友好提示（"未检索到相关内容…"）不抛错；无 user_id 友好提示；top_k 钳制 1~5
  - chat_service TOOL_CAPABILITY_PROMPT 补充 rag_search 能力说明（工具列表 + 额外能力第8条）
  - 已验证 8 项（跨事件循环/来源标注/出处 path/无 user_id/工具定义/分支执行/top_k 钳制）+ 真实 API 失败鲁棒返回

## 测试与评测体系（2026-08-30 建立）

- [x] **第 1 层单元测试**：`backend/tests/` 90+ 用例（分块/融合/父级扩展/路由/pipeline/稀疏索引/图谱切片/rag_search），全部 mock 零网络，3s 级
- [x] **第 2 层端到端集成测试**：`tests/test_integration_kb.py` 11 用例（mock 确定性向量），覆盖上传→分块→入库→检索全链路、目录范围过滤、path 溯源、父级扩展、删除清理、用户隔离
- [x] **第 3 层离线评测集**（复用开源 CMRC2018 trial，256 文档 / 1000 查询）：
  - 脚本：`backend/scripts/eval_rag.py`（`--embed mock|api`，四种策略对比，报告 JSON 落 `eval_data/report_*.json`）
  - mock 基线（hash bigram 弱向量，可复现）：vector R@1=0.470 / bm25 R@1=0.964 / hybrid(RRF) 0.766 / fuse(α=0.6) 0.818
  - **结论 1**：抽取式 QA 场景 BM25 词面命中极强；**结论 2**：弱向量路下 RRF 融合拖累强路，加权融合更稳（详见上方融合复核条目）
  - **真实 text-embedding-v4 评测待跑**（DASHSCOPE 账户 Arrearage 欠费，充值后 `python scripts/eval_rag.py --embed api`）
- [x] **whoosh 批量写入优化**：`SparseIndex` 新增 `upsert_bulk` + `begin_deferred/flush` 延迟提交模式，`_index_document` 批量写稀疏索引。Windows 下 whoosh 每次 commit 固定 ~2.4s（疑似 Defender 扫描 segment），延迟提交把 256 篇文档入库从 180s 降到 4.7s（38 倍），真实批量导入同样受益

## P1.5 — RAG 可视化拖拽窗口（新增，基于 RAG 引用溯源）

> 背景：加入 RAG 系统后，检索到的知识片段需要可视化呈现。目标是做一个用户可图形化拖拽的窗口，类似"知识卡片白板"，用户能自由拖拽/排列 RAG 检索出的相关知识点卡片，直观看到"当前话题 ↔ 图谱中的相关知识点"。

- [ ] **调研 OpenMAIC 组件复用可行性**（2026-08-27 已调研）
  - 清华 MAIC 团队开源项目 OpenMAIC（AGPL-3.0）：https://github.com/THU-MAIC/OpenMAIC
  - 前端：Next.js + React + TS + Zustand + Tailwind；可视化：Canvas 幻灯片渲染器、SVG 白板、3D、思维导图
  - **结论**：与我们的 Vue3 技术栈不匹配，React 组件无法直接复用；但可**借鉴交互设计**（SVG 白板拖拽、思维导图式知识卡片）
  - 备选参考：Vue 生态的知识图谱可视化套件（Vue + D3 力导向图，与现有 ForceGraph 技术栈一致）
- [ ] **RAG 引用卡片拖拽窗口**
  - 后端：`/rag/search` 已返回节点片段 + score，可补充返回节点坐标/关联信息
  - 前端：新增可拖拽的"知识卡片面板"，展示 RAG 检索结果
    - 卡片显示：节点名、片段内容、相似度 score、来源节点
    - 卡片可自由拖拽/排列（借鉴 SVG 白板交互）
    - 点击卡片可跳转到对应图谱节点（联动现有 NodeDetail/ForceGraph）
    - 卡片与图谱连线，展示"当前话题 ↔ 相关知识点"关系
- [ ] **对话侧边 RAG 面板** — 对话时实时展示本次回答引用了哪些知识片段
  - SSE 后台阶段完成后，通过事件推送 RAG 引用，前端在侧边栏展示

## P2 — 痛点2：学完没有反馈（缺口最大，需新增）

- [ ] **学科切换** — 节点加 `subject` 字段，前端筛选
  - 后端：nodes 表加 subject 列，API 加 `?subject=xxx` 过滤
  - 前端：新增学科选择器（下拉/标签）
  - 已有数据迁移默认为 "default"
- [ ] **学习进度仪表盘** — 关键指标概览
  - 后端：新增 `/knowledge/stats?subject=xxx` API
    - 返回：总节点数、已掌握节点数(>70)、学习中(0<70)、未学(=0)
    - 平均掌握度、预估总时长、已学时长
  - 前端：HomeView 新增仪表盘面板，纯数值+进度条展示
- [ ] **节点掌握度手动调整** — 用户可手动改掌握度
  - 现有 API PUT `/node/{id}/mastery`
  - 前端 NodeDetail 加手动调整滑块/按钮

## P3 — 痛点3：记笔记痛苦（核心已有，调prompt+UI）

- [ ] **Prompt 调优 — 让 AI 主动做笔记**
  - `system_prompt_adaptive.j2` 追加指令：
    - 每次教学后调用 `update_node_content` 总结关键知识点
    - 使用 `add_knowledge_node` 为重要的新概念创建独立节点
    - 格式：简明扼要，适合复习
  - 其他模式模板同理调整
- [ ] **节点内容编辑体验优化**
  - 当前 textarea 编辑不够友好
  - 考虑：分屏预览编辑、Markdown 工具栏
- [ ] **图谱知识一键导出** — 当前学科所有节点内容汇总导出
  - 后端：新增 `/knowledge/export?subject=xxx` 返回合并 Markdown
  - 前端：下载按钮

## P4 — 演示数据 & 收尾

- [ ] **填充一个完整学科图谱** — 建议：数据结构 / 线性代数
  - 15-20 个核心节点 + prerequisite 边
  - 前置录入一部分，确保 AI 可触发新节点创建（展示动态生长）
- [ ] **准备演示对话脚本** — 2-3 条路径，预演流畅不卡壳
- [ ] **验证演示环境** — 离线/弱网兜底
- [ ] **README 全面更新** — 项目改名+新功能文档

---

## 已完成的（保留参考）

- [x] 知识图谱可视化与管理（D3.js + CRUD）
- [x] 图谱搜索 + 焦点跳转
- [x] 知识点间可点击跳转
- [x] 拓扑排序学习路径推荐（Kahn 算法）
- [x] 问题拆解自动生成骨架图谱
- [x] 两阶段流式对话（SSE + 后台 function calling）
- [x] 四种教学模式（adaptive / free_talk / recursive / learning-path）
- [x] 对话历史管理（SQLite）
- [x] JWT 用户认证
- [x] 用户画像系统（动态更新）
- [x] 进程内事件总线
- [x] 标准化错误码
- [x] 一键安装/启动脚本
- [x] RAG 语义检索系统（2026-08-27）
  - [x] 知识图谱 MD 分块 + 向量化（阿里云 text-embedding-v4）
  - [x] SQLite 向量存储 + 余弦相似度检索
  - [x] 对话系统提示词注入 RAG 上下文（补充叠加）
  - [x] 图谱分析后增量索引联动
  - [x] RAG API：/rag/index、/rag/search、/rag/stats
  - [ ] 前端知识库检索界面（待做）
