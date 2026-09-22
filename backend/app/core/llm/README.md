# llm —— LLM 原语包（唯一真打模型 API 的地方）

> **一句话**：全项目所有"跟大模型服务商打交道"的原语都在这里 —— 客户端、消息组装、
> 思考适配、重试、主备回退、嵌入、JSON 提取。**上层业务不直接 import `openai`。**
>
> 沿革：2026-09-08 自旧 `llm_client.py`（718 行上帝模块）按职责拆包。
>
> 契约：**对外只认 `__init__.py` re-export 的名字**（见其 `__all__`）。子模块路径可重构，
> 公开符号不变。

---

## 1. 模块分工

| 文件 | 角色 | 对外符号 | 一句话 |
|---|---|---|---|
| `clients.py` | 客户端单例 | `client` `embed_client` `fallback_client` `MODEL_NAME` | 对话 / 嵌入 / 备用三个 `AsyncOpenAI`，**模块导入时创建** |
| `messages.py` | 消息组装 | `build_api_messages` | `system_prompt` + 历史 → OpenAI 消息格式（dict / Pydantic 双形兼容） |
| `thinking.py` | MiniMax-M3 适配 | `extra_body(thinking)` `strip_think_tags` `LLM_EXTRA_BODY` | 思考拆分到 `reasoning_content`、思考开关、响应兜底剥离 |
| `retry.py` | 请求健壮性 | `with_retry` `is_retryable` `map_api_error` | 指数退避 + 抖动重试；异常 → 错误码 `RuntimeError` |
| `fallback.py` | 主备回退 | `chat_create` `LLM_CANDIDATES` | **对话调用唯一出口**：主模型失败静默切备用 |
| `call.py` | 一次性调用 | `call_llm` | 不带工具的纯文本/JSON：出题 / 判分 / 图谱分析 |
| `embed.py` | 嵌入（**检索侧**） | `embed_texts` `EMBEDDING_MODEL` `EMBED_BATCH_SIZE` `MAX_EMBED_CHARS` | **检索侧嵌入唯一出口**（kb 向量化 / 图谱 RAG）；语义去重侧 `kb/embedder.py::ApiEmbedder` 共用同名常量与失败语义 |
| `json_extract.py` | 结构化抽取 | `extract_json` | **JSON 提取唯一出口**：三策略定位 + 引号兜底修复 |
| `usage.py` | 用量记账 | `record` `summary` | **token 审计唯一出口**：一次真实调用一行（kind / model / tokens），落在 `agent_runs.db` 的 `llm_usage` 表；**新增 LLM 出口时同步调一次 `record()`** |

---

## 2. 调用链（只有两条真路径）

```
agent/loop.py._chat_once ──┐
                           ├─→ fallback.chat_create ─→ retry.with_retry ─→ clients.client
call.py::call_llm ─────────┘        (主→备静默降级)      (瞬时错误退避)      (AsyncOpenAI)

kb / rag / quiz / graph_analyzer ─→ call_llm ─→ chat_create ─→ …
                                        └─→ json_extract.extract_json（解析响应）

kb_manager / rag.manager ─→ embed_texts ─→ clients.embed_client（阿里 text-embedding-v4）
kb.embedder.ApiEmbedder ─→ 同步 OpenAI（同一模型；语义去重专用，失败可降级 hash）
```

**两条真路径**：带工具的多轮循环走 `agent/loop.py`；不带工具的一次性生成走 `call_llm`。
**其他任何地方都不应该出现 `chat.completions.create`。** 嵌入调用只允许两处：本包的
`embed_texts`（检索侧）与 `kb/embedder.py::ApiEmbedder`（语义去重侧，必须同步 + hash 兜底），
两者共用 `EMBEDDING_MODEL` / `EMBED_BATCH_SIZE` / `MAX_EMBED_CHARS` 与失败语义。

---

## 3. 三份「唯一出口」契约

### 3.1 `chat_create(**kwargs)` —— 对话唯一出口

- **不要传 `model`**：函数按 `LLM_CANDIDATES`（主 → 备用）逐档注入 `model` 并各自做瞬时重试。
- 备用未配置时 `LLM_CANDIDATES` 只有主模型，行为与单模型完全一致。
- 全部档失败 → 抛 `map_api_error` 分类后的 `RuntimeError`（**调用方不必再映射**）。
- **不触发回退的错误**：`400` / `404` / `422` 等"请求本身错了"（换模型也没用），
  以及非 401/402/403/429/5xx 的 `APIStatusError`。回退条件见 `should_fallback`。

### 3.2 `call_llm(system_prompt, messages, max_tokens=2000, thinking=True, kind=..., user_id=...)` —— 一次性文本

**必须记住的一条**：M3 的**思考与正文共享 `max_tokens` 预算**（见 §4.1）。
所以：

| 场景 | 该怎么传 |
|---|---|
| 出题 / 判分 / 图谱分析（要模型想清楚，输出不长） | 默认 `thinking=True`，`max_tokens` 按需 |
| 批量结构化抽取（图谱生成 / 整份 Markdown） | **`thinking=False`** + 显式调大 `max_tokens` |

### 3.3 `embed_texts(texts, max_chars)` —— 检索侧嵌入唯一出口

- 返回与入参**等长**的向量列表；**空输入 / API 失败 / 条数对不上**一律返回 `[]`，**不抛异常**。
- 调用方据此降级：RAG 跳过注入，KB 退化为 BM25-only（向量列写 NULL）。
  这是"嵌入 API 欠费时不炸全局"的原因。
- 单次最多 10 条（`EMBED_BATCH_SIZE`，DashScope 硬限制，超限报 400）。
- **语义去重侧**（图谱生成同义合并 / 先修推断 C5）走 `kb/embedder.py::ApiEmbedder`：必须**同步**（调用方 `prerequisite` / `graph_generator` 都是同步流程）且需要 **hash 兜底**（无 key / 欠费时仍要能跑），故保留独立实现；但模型名/批大小/截断/失败语义与本模块一致（2026-09-15 收口，`tests/test_api_embedder.py` 锁死不再分叉）。

### 3.4 `extract_json(raw, kind="object"|"array")` —— JSON 唯一出口

- 返回解析结果；**失败返回 `None`** —— 注意 `[]` 是合法结果，**判空必须用 `is None`**。
- 两级兜底：① 三策略定位（原文 / ```json 代码块 / 括号配平片段，跳过字符串内括号）
  ② 修复字符串值里未转义的英文双引号。
- `kind` 会做类型校验：`kind="array"` 时返回 dict 视为失败。

---

## 4. 真机踩过的坑（改前必读）

### 4.1 思考与正文共享输出预算 → "空回复"

M3 把思考裹在 `ϩ…ϩ` 标签里（`thinking.py`），且**思考消耗的是同一个 `max_tokens`**。
实测学科图谱生成 8 个分块里 3 块是「思考 26000+ 字符、completion 顶满 8000、正文 0 字符」
→ 表现为 `E-LLM-006 空回复`。**根治办法：批量抽取类任务 `thinking=False`。**

### 4.2 空回复自动重试（已内置在 `call_llm`）

思考长度随机，重发一次通常就有正文。`call_llm` 撞到空回复会等 2 秒、把预算抬到
8000（`_EMPTY_RETRY_TOKENS`）再试一次 —— 所有一次性调用（出题/判分/图谱分析）自动受益。
调用方**不需要**自己写重试。

### 4.3 「输出被截断」不能只看有没有正文

`finish_reason == "length"` 时**即便正文非空也告警**：JSON 类响应已被硬切断，
调用方只会看到"解析失败"，不知道真因是 `max_tokens` 不够。
排查顺序：先看日志里的 `finish=` 与 `call_llm token usage`。

### 4.4 `AsyncOpenAI` 单例与事件循环

三个 client 在**模块导入时**创建。跨事件循环复用会报 `Event loop is closed` ——
具体表现为工具在 `asyncio.to_thread` 工作线程里 `asyncio.run(...)` 后崩。
这是"工具必须有异步入口"的根因（见 `agent_tools/README.md` §5 不变量 2）。

### 4.5 服务商差异

默认对话模型 MiniMax-M3（`LLM_API_KEY` / `LLM_BASE_URL` / `MODEL_NAME`），
嵌入固定阿里 `text-embedding-v4`（`DASHSCOPE_API_KEY` / `EMBED_BASE_URL`，**key 独立**）。
`extra_body` 里的 `thinking.type=disabled` **仅 M3 支持**，换模型后该字段可能被忽略或报错。

---

## 5. 新增一次 LLM 调用的正确姿势

1. **要工具多轮循环？** → 不要在这里加东西，用 `agent/loop.py::run_agent_loop`。
2. **一次性文本 / JSON？** → 用 `call_llm`。结构化抽取记得 `thinking=False`，长 JSON 调大 `max_tokens`。
3. **要解析模型返回的 JSON？** → 只用 `extract_json`，别自己写正则（这就是它存在的理由）。
4. **要向量？** → 只用 `embed_texts`，并且处理 `[]` 降级分支。
5. **只用一次对话，但要更细的控制（如自定义 temperature）？** → 走 `chat_create`（**别 import `openai`**）。

---

## 6. 测试

```bash
cd backend
venv/Scripts/python.exe -m pytest tests/test_llm_call.py tests/test_llm_fallback.py \
    tests/test_minimax_thinking.py tests/test_json_extract.py tests/test_embed_texts.py -q
venv/Scripts/python.exe -m pytest tests/ -q -m "not llm_api"     # 离线全量
venv/Scripts/python.exe -m pytest tests/test_agent_loop_real_api.py -q -m llm_api   # 真实付费 API（需 pytest-asyncio）
```

---

## 7. 相关文档

| 位置 | 关系 |
|---|---|
| `../../agent/loop.py` | 带工具的多轮调用方（唯一使用 `chat_create` 的业务路径） |
| `docs/AgentLoop/AgentLoop_业界调研与学习路线.md` · `docs/AgentLoop/AgentLoop_重构设计讨论.md` | Agent Loop 侧设计 |
| `docs/上下文工程/token_consumption_prediction_research.md` | token 计费与预估（`agent/estimator.py` 的依据） |
| `AGENTS.md` §0 | 三段 key / 模型配置的真值位置 |
