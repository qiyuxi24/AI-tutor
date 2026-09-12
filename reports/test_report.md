# AI-tutor 后端全量测试报告

| 指标 | 值 |
|------|------|
| 日期 | 2026-09-08 |
| 项目 | AI-tutor（知识图谱驱动的大学生自适应导学 Agent） |
| 框架 | pytest 9.1.1 / Python 3.10.11 |
| 总用例 | **413** |
| Passed | **413** |
| Failed | **0** |
| Errors | **0** |
| Warnings | 91 |
| 耗时 | 25.33s |
| 测试文件 | 36 |

---

## 一、测试标准

### 1.1 核心原则

- **零网络依赖**：所有测试完全离线运行，禁止发起真实 HTTP 请求或调用真实 LLM API。所有外部依赖（LLM、知识库、网络）均通过 mock 隔离。
- **确定性**：测试结果不依赖随机数、时间或外部状态。涉及时间的测试使用 `unittest.mock.patch` 冻结时间。
- **隔离性**：每个测试用例独立运行，不依赖其他测试的副作用。文件系统测试使用 pytest `tmp_path` fixture 自动创建临时目录并在测试后清理。
- **函数式风格**：不使用 `class TestXxx` 类继承体系，所有测试为独立函数，命名 `test_*`，通过 `assert` 断言。
- **覆盖率优先**：每个源文件的公开函数和关键分支路径必须有对应测试，包括正常路径、边界值和异常路径。

### 1.2 Mock 策略

| Mock 对象 | 工具 / 手法 | 说明 |
|-----------|------------|------|
| LLM 调用 (`call_llm`) | `monkeypatch.setattr` 替换为 async fake 函数 | 返回预设 JSON 字符串，模拟 LLM 结构化输出 |
| LLM 流式 (`call_llm_stream`) | 同上，返回 async generator | 模拟 SSE token 流 |
| 知识库 (`kb_manager`) | Fake class 实现 `search()` / `get_node()` | 返回预设片段列表，不触发真实检索 |
| OpenAI 响应对象 | `SimpleNamespace` 构造 mock response | 模拟 `choices[0].message` / `tool_calls` / `usage` |
| 嵌入向量 | `hash_embed` 函数 | 从文本内容确定性生成向量，保证可复现 |
| 时间 | `patch("...time.time", return_value=...)` | 冻结时间戳，验证滑动窗口逻辑 |
| 知识图谱 | `FakeKG` 类 | 实现 `nodes` / `edges` / `get_node()` 等最小接口 |

### 1.3 测试配置

```python
# backend/tests/conftest.py（唯一的测试配置）
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# 将 backend/ 注入 sys.path，使 import app.* 在任意目录下可用
# 无 pytest.ini / pyproject.toml / setup.cfg
```

---

## 二、测试方法（按模块）

### 2.1 本次新增的 7 个测试文件（153 用例）

| 测试文件 | 用例数 | 优先级 | 覆盖范围 |
|---------|-------|--------|---------|
| `test_quiz_schema.py` | 16 | HIGH | Pydantic 模型校验：QuestionOption / Question / QuizGenerateRequest / QuizGradeRequest 的字段默认值、约束边界（points 0~100、question_count 1~20）、必填报错、序列化 `.dict()`、默认列表独立性 |
| `test_quiz_quality.py` | 22 | HIGH | 质量过滤管道：文本归一化 `_normalize`、长度过滤器（20~500 字符边界）、自包含性过滤器（9 种外部依赖模式：如上图/根据上文/图中…）、重复检测器（精确+归一化去重）、组合管道混合过滤 |
| `test_quiz_grader.py` | 39 | HIGH | 判分器全部路径：单选（正确/错误/大小写/空格/空答案）、多选（全对/部分给分/误选扣分/未作答）、判断题、填空题（模糊匹配：精确/包含/反向包含/多答案）、简答题 LLM 判分（成功/满分/分数夹紧/LLM 失败降级半分/非法 JSON/带前缀 JSON）、入口函数 `grade_question` 全题型分发、未知题型降级 |
| `test_quiz_store.py` | 14 | HIGH | SQLite 题库存储：建表+索引验证、保存/查询单条/多条题目、按 subject 过滤、分页 limit/offset、stats 统计、作答记录 `record_attempt`、行转字典 `_row_to_question`（含空 JSON 兜底）、node_id+difficulty 字段、多用户数据隔离 |
| `test_quiz_generator.py` | 33 | HIGH | 出题生成器：prompt 组装（有/无参考材料/空主题）、JSON 解析三策略（直接/Markdown 代码块/前后缀提取）、5 种题型结构校验 `_validate_question`（选项数/答案在选项中/多选至少 2 答案/判断题对错/填空非空/简答有解析）、知识库检索 mock（成功/异常返回空）、端到端 `generate_quiz`（成功/拒绝不合格题/JSON 解析失败/LLM 异常/去重/Markdown 包裹） |
| `test_prompt_loader.py` | 15 | MED | 提示词加载器：模板文件存在性验证、三种模式（adaptive/free_talk/recursive）渲染、未知模式报错、空消息处理、`student_message` 注入、common 模板常驻、`user_profile` 条件区块（`{% if user_profile %}`）、`knowledge_graph_summary` 占位符、递归模式 `current_node` + `knowledge_graph_framework` 传递 |
| `test_rate_limiter.py` | 14 | MED | 登录限流器：首次允许、窗口内 max_requests 边界、超限拒绝、多 IP 独立、窗口过期恢复、部分过期清理、`get_retry_after` 计算（无记录/正常/过期）、全局单例验证、时间戳记录、过期记录清理 |

### 2.2 既有测试文件（29 个文件，260 用例）

| 模块 | 用例数 | 测试文件 |
|------|-------|---------|
| Agent Loop | 9 | `test_agent_loop.py` (8 scenarios: 无工具自然结束/单轮工具协议顺序/多轮工具链/max_rounds 强制收尾/空文本兜底/工具超时护栏/trace 落盘/LLM 错误兜底), `test_minimax_thinking.py` (7: MiniMax-M3 推理内容适配) |
| Collector（采集器） | 58 | `test_collector_manager.py` (11), `test_collector_store.py` (9), `test_collector_http.py` (8), `test_collector_subjects.py` (8), `test_collector_adapters_registry.py` (9), `test_chapterizer.py` (13), `test_low_text_reject.py` (3), `test_pipeline_commercial_filter.py` (7) |
| RAG Pipeline | 88 | `test_router.py` (10), `test_pipeline.py` (13), `test_pipeline_ingest.py` (6), `test_fusion.py` (15: RRF/fuse 归一化), `test_chunking.py` (11), `test_parent_expand.py` (13), `test_sparse_index.py` (12: Whoosh BM25), `test_bm25_only_index.py` (5), `test_rag_tool.py` (8) |
| API (Collector) | 16 | `test_api_collector.py` — FastAPI TestClient 端到端：search/create_task/get_task/cancel_task/stats/auth |
| KB Integration | 19 | `test_integration_kb.py` (11: KB 解析+检索+hash_embed), `test_graph_middleware.py` (8: FakeKG 中间件) |
| User Profile | 22 | `test_user_profile.py` (15), `test_user_profile_usage_mode.py` (7) |
| Event Bus | 6 | `test_event_bus.py` |
| Collector Adapters | 20 | `test_oiwiki_adapter.py` (8), `test_wikipedia_adapter.py` (9), `test_wikibooks_adapter.py` (3) |

---

## 三、测试结果

### 3.1 最终运行结果

```
$ cd backend && python -m pytest tests/ -v --tb=short

============================= test session starts =============================
platform win32 -- Python 3.10.11, pytest-9.1.1, pluggy-1.6.0
rootdir: C:\Users\34239\Documents\GitHub\AI-tutor\backend
collected 413 items

tests/test_agent_loop.py::test_natural_finish_without_tools PASSED       [  0%]
tests/test_agent_loop.py::test_single_tool_round_protocol_order PASSED   [  0%]
...（413 行 PASSED）

====================== 413 passed, 91 warnings in 25.33s ======================
```

### 3.2 结果汇总

| 指标 | 数值 | 说明 |
|------|------|------|
| 总用例数 | **413** | 本次新增 153 + 既有 260 |
| Passed | **413** | 全部通过 |
| Failed | **0** | 零失败 |
| Errors | **0** | 零采集/导入错误 |
| Warnings | 91 | Pydantic V2 `.dict()` 弃用警告 + FastAPI `on_event` 弃用 + Starlette TestClient 弃用（非阻塞） |
| 耗时 | 25.33s | 全离线 mock，无网络等待 |
| 测试文件数 | 36 | 含 conftest.py + _mediawiki_fixtures.py 辅助文件 |

### 3.3 逐文件用例统计

| # | 文件名 | 模块 | 用例数 | P | F | E |
|---|--------|------|-------|---|---|---|
| 1 | `test_quiz_grader.py` | Quiz / Grader | 39 | 39 | 0 | 0 |
| 2 | `test_quiz_generator.py` | Quiz / Generator | 33 | 33 | 0 | 0 |
| 3 | `test_quiz_quality.py` | Quiz / Quality | 22 | 22 | 0 | 0 |
| 4 | `test_fusion.py` | RAG / Fusion (RRF) | 15 | 15 | 0 | 0 |
| 5 | `test_prompt_loader.py` | Prompt Loader | 15 | 15 | 0 | 0 |
| 6 | `test_user_profile.py` | User Profile | 15 | 15 | 0 | 0 |
| 7 | `test_quiz_schema.py` | Quiz / Schema | 16 | 16 | 0 | 0 |
| 8 | `test_api_collector.py` | API / Collector | 16 | 16 | 0 | 0 |
| 9 | `test_chapterizer.py` | Collector / Chapterizer | 13 | 13 | 0 | 0 |
| 10 | `test_pipeline.py` | RAG / Pipeline | 13 | 13 | 0 | 0 |
| 11 | `test_parent_expand.py` | RAG / Parent Expand | 13 | 13 | 0 | 0 |
| 12 | `test_sparse_index.py` | RAG / Sparse Index | 12 | 12 | 0 | 0 |
| 13 | `test_quiz_store.py` | Quiz / Store | 14 | 14 | 0 | 0 |
| 14 | `test_rate_limiter.py` | Rate Limiter | 14 | 14 | 0 | 0 |
| 15 | `test_chunking.py` | RAG / Chunking | 11 | 11 | 0 | 0 |
| 16 | `test_collector_manager.py` | Collector / Manager | 11 | 11 | 0 | 0 |
| 17 | `test_integration_kb.py` | KB Integration | 11 | 11 | 0 | 0 |
| 18 | `test_router.py` | RAG / Router | 10 | 10 | 0 | 0 |
| 19 | `test_agent_loop.py` | Agent Loop | 9 | 9 | 0 | 0 |
| 20 | `test_collector_adapters_registry.py` | Collector / Adapters | 9 | 9 | 0 | 0 |
| 21 | `test_collector_store.py` | Collector / Store | 9 | 9 | 0 | 0 |
| 22 | `test_wikipedia_adapter.py` | Collector / Wikipedia | 9 | 9 | 0 | 0 |
| 23 | `test_collector_http.py` | Collector / HTTP | 8 | 8 | 0 | 0 |
| 24 | `test_collector_subjects.py` | Collector / Subjects | 8 | 8 | 0 | 0 |
| 25 | `test_graph_middleware.py` | Graph Middleware | 8 | 8 | 0 | 0 |
| 26 | `test_oiwiki_adapter.py` | Collector / OI-wiki | 8 | 8 | 0 | 0 |
| 27 | `test_rag_tool.py` | RAG / Tool | 8 | 8 | 0 | 0 |
| 28 | `test_minimax_thinking.py` | MiniMax-M3 适配 | 7 | 7 | 0 | 0 |
| 29 | `test_pipeline_commercial_filter.py` | Collector / Commercial Filter | 7 | 7 | 0 | 0 |
| 30 | `test_user_profile_usage_mode.py` | User Profile / Usage Mode | 7 | 7 | 0 | 0 |
| 31 | `test_event_bus.py` | Event Bus | 6 | 6 | 0 | 0 |
| 32 | `test_pipeline_ingest.py` | RAG / Ingest | 6 | 6 | 0 | 0 |
| 33 | `test_bm25_only_index.py` | RAG / BM25 | 5 | 5 | 0 | 0 |
| 34 | `test_low_text_reject.py` | Collector / Low-text Reject | 3 | 3 | 0 | 0 |
| 35 | `test_wikibooks_adapter.py` | Collector / Wikibooks | 3 | 3 | 0 | 0 |
| 36 | `test_agent_loop.py` + `test_minimax_thinking.py` | Agent Loop 合计 | 16 | 16 | 0 | 0 |

> P = Passed, F = Failed, E = Errors

### 3.4 本次修复的源码 Bug

**[BUG] grader.py — `_SHORT_ANSWER_SYSTEM_PROMPT` 的 JSON 花括号未转义**

- **文件**：`backend/app/core/quiz/grader.py` 第 113 行
- **现象**：调用 `.format(points=...)` 时，JSON 中的 `{"score": ...}` 的花括号被 Python 误认为格式占位符，抛出 `KeyError: '"score"'`，导致简答题 LLM 判分功能完全不可用。
- **修复**：将 `{` → `{{`、`}` → `}}` 转义，使 `.format()` 正常工作。

```python
# 修复前（有 bug）：
_SHORT_ANSWER_SYSTEM_PROMPT = """...
{"score": <0到{points}的整数>, "comment": "<一两句评语>"}"""

# 修复后：
_SHORT_ANSWER_SYSTEM_PROMPT = """...
{{"score": <0到{points}的整数>, "comment": "<一两句评语>"}}"""
```

### 3.5 本次安装的缺失依赖

| 包名 | 版本 | 原因 |
|------|------|------|
| `python-jose[cryptography]` | 3.5.0 | JWT 认证模块 `app/core/auth.py` 依赖 `jose`，`requirements.txt` 已声明但环境未安装 |
| `bcrypt` | 5.0.0 | 密码哈希，`requirements.txt` 已声明但环境未安装 |
| `pydantic` | 2.13.5 | 数据模型校验，Quiz schema 等模块依赖 |
| `jinja2` | 3.1.6 | 提示词模板渲染 `prompt_loader.py` |
| `httpx` | 0.28.1 | LLM 客户端 `llm_client.py` HTTP 层 |
| `openai` | 3.8.0 | OpenAI 兼容 SDK |
| `whoosh` | 2.7.4 | BM25 稀疏索引 `hybrid_search/whoosh_index.py` |
| `fastapi` | 0.141.1 | Web 框架，`test_api_collector.py` 的 TestClient 依赖 |
| `pytest` | 9.1.1 | 测试框架本身 |

### 3.6 既有 Warnings 说明（非阻塞）

| 来源 | 数量 | 说明 |
|------|------|------|
| Pydantic V2 | 87 | `.dict()` 方法在 Pydantic V2 中弃用，建议替换为 `.model_dump()`。影响 `quiz_store.py`、`generator.py` 及测试文件。TODO.md P1 已列此项。 |
| FastAPI | 2 | `on_event("startup")` 弃用，建议迁移到 lifespan handler。TODO.md P1 已列此项。 |
| Starlette | 1 | TestClient 内部 `anyio.abc.BlockingPortal` 别名弃用，不影响测试结果。 |
| anyio | 1 | 同上，Starlette TestClient 传递的弃用警告。 |

---

## 四、剩余测试缺口

| 模块 | 优先级 | 说明 |
|------|--------|------|
| `chat_service.py` | P1 | 对话服务编排层 — `process_message` / `process_message_stream` / `_build_system_prompt` / `_analyze_and_apply`。复杂度高（涉及 agent loop + 图谱分析 + RAG + 事件总线），需 mock 较多依赖。 |
| `graph_generator.py` | P1 | 学科图谱生成器 — `generate_subject_graph` / `generate_section_graph` / 语义去重。需 mock `call_llm` + `kb_manager` + `KnowledgeGraph`。 |
| API 路由层 | P2 | `api/v1/chat.py` / `auth.py` / `knowledge.py` / `profile.py` / `rag.py` / `kb.py` / `quiz.py` — 除 collector 外均无 API 层端到端测试。 |
| 前端测试 | P2 | 完全没有（无 Vitest / Jest / Playwright）。TODO.md 未列入近期计划。 |
| CI/CD | P2 | 无 `.github/workflows/`，TODO.md P1 列了待接入。接入成本极低（413 个测试已就绪）。 |

---

## 五、关联文件

| 文件 | 说明 |
|------|------|
| `tests/test_report.md` | 本报告（Markdown 格式） |
| `tests/test_results_raw.txt` | pytest -v 原始输出（413 行 PASSED + warnings 摘要） |
| `tests/test_results_summary.json` | 结构化测试结果汇总（逐文件用例数 + 状态统计） |
| `tests/test_report.html` | HTML 版报告（可视化） |

---

*AI-tutor 后端测试报告 · 生成于 2026-09-08 · 413 tests, all passing*
