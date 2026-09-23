# tools —— 原生工具目录（**一个工具一个模块**）

> **一句话**：模型能调的 11 个原生工具，每个占一个文件；一个文件里装着一个工具的**完整契约**
> （模型侧说明 + 参数 schema + 提示词侧说明 + 执行体）。找 / 改 / 删一个工具只动一个文件。
>
> 上游（机制层）见 `../README.md`；MCP 工具（第 12 个）见 `../../../../mcp_servers/README.md`。
>
> 建立：2026-09-15 —— 原先 11 个工具的 spec 挤在单文件 `native.py`（347 行），
> 且部分 handler 还要跳到 `impl/` 找实现，改一个工具要翻两处。

---

## 1. 十一个工具，一个一张表

| 工具名（模型侧） | 文件 | 组 | 落点（真实逻辑在哪） | 最要命的那条约束 |
|---|---|---|---|---|
| `add_knowledge_node` | `add_knowledge_node.py` | 图谱 | `core/knowledge_writer.py` | `from_nodes` 只填**真前置**，别把"相关"当"前置" |
| `update_node_content` | `update_node_content.py` | 图谱 | `KnowledgeGraph` | `node_id` 必须从摘要**逐字复制** |
| `update_mastery` | `update_mastery.py` | 图谱 | `KnowledgeGraph` | **只认 3 种硬证据**；主信号是出题判分 |
| `add_edge` | `add_edge.py` | 图谱 | `KnowledgeGraph` | 关系不明确**不要**连边（质量 > 数量） |
| `delete_node` | `delete_node.py` | 图谱 | `KnowledgeGraph` | 人类建的删不掉，别重试 |
| `update_user_profile` | `update_user_profile.py` | 画像 | `core/profile/` | 与已有笔记重复的不要再记 |
| `fetch_webpage` | `fetch_webpage.py` | 资料 | 本模块 + `core/knowledge_writer.create_node_from_webpage`（+ `../net_guard.py`、`core/open_source.py`） | **只读**返回；抓到的正文同时存档为图谱节点（origin="web"），但**只存档开放许可来源** |
| `download_resource` | `download_resource.py` | 资料 | 本模块（+ `../net_guard.py`、`core/open_source.py`） | **留存**，入库才能被检索；**只采开放许可来源**（fail-closed） |
| `rag_search` | `rag_search.py` | 资料 | `core/rag_pipeline/` | `hops` 只反向补前置，字面不相似但必须先学 |
| `quiz_generate` | `quiz_generate.py` | 检验 | `core/quiz/chat_quiz.py` | **`async def`** + 后台任务 + `timeout_secs=90` |
| `grade_answer` | `grade_answer.py` | 检验 | `core/quiz/chat_quiz.py` | **`async def`**；掌握度**唯一主信号** |

「组」只是 `NATIVE_SPECS` 里的排列顺序（影响提示词 diff，无功能含义），
**不代表文件要合并** —— 见 §3。

---

## 2. 一个模块的固定形状（照抄即可）

```python
"""<工具名> —— <做什么> / <最要命的边界要点>（给人看的是这段）"""

from ..registry import _spec            # 需要领域模块就一并 import

DESCRIPTION = "..."                     # ① 模型侧：随 tools= 每轮发给模型，写短写准
PARAMETERS = {                          # ② JSON Schema（与 MCP inputSchema 同构）
    "type": "object",
    "properties": {...},
    "required": [...],
}
GUIDANCE = """                          # ③ 提示词侧：何时用 / **何时别用**
...
"""


def handler(args, kg) -> str:           # ④ 执行体：(args, kg) -> 给模型看的文本
    ...


SPEC = _spec("<工具名>", DESCRIPTION, PARAMETERS, handler, guidance=GUIDANCE)   # ⑤
```

然后在 `tools/__init__.py` 加两行：`from . import <工具名>` + `NATIVE_SPECS` 里加 `<工具名>.SPEC`。

**不需要**改：`KG_TOOLS`、分发逻辑、提示词、本目录以外的任何文件。

### 为什么要分成四个常量而不是一个大 `_spec(...)`

`DESCRIPTION` / `PARAMETERS` / `GUIDANCE` 三个名字在 11 个文件里位置完全一致 ——
换任意一个工具文件，你都知道该看哪一段；diff 时也一眼能看出"改的是给模型的话还是给提示词的话"。

---

## 3. 为什么是「一工具一文件」而不是「按领域合并」

这是对外部惯例的选择（2026-09-15 调研）：

| 来源 | 做法 |
|---|---|
| LangChain《项目结构建议》方案 1（小项目） | `tools/` 下**每个工具一个文件**：`search_tool.py` / `calculator.py` / `database.py` |
| Claude Code 目录架构 | `src/tools/<ToolName>/index.ts` —— **一个工具一个模块** |
| ToolRegistry 架构文档 | 统一注册入口 + 适配器下沉；工具按来源/命名空间归类 |

选它的真正理由不是"别人这么做"，而是**规则不能有例外**：
按领域合并就要回答"为什么 `add_edge` 和 `add_knowledge_node` 不在一个文件？"
"为什么 `fetch_webpage` 和 `download_resource` 分开？" —— 每次新增工具都要重新论证一遍。
一工具一文件的规则**不需要判断力**，这对"方便以后开发"是决定性的。

代价：`delete_node.py` 只有 20 来行。接受 —— 20 行的文件比"该放哪"的反复讨论便宜。

---

## 4. 三条硬约定（破一条就会出问题）

| # | 约定 | 为什么 |
|---|---|---|
| 1 | **永不抛异常**：失败返回给模型看的文本（"抓取网页超时：…，请稍后重试"） | 异常会被 `dispatch._tool_error` 兜住，但那样只剩一句干巴巴的"工具执行出错"，模型无从纠偏。这里自己给出**可操作**的提示 |
| 2 | **handler 保持薄壳**：只搬参数，真实逻辑放领域模块或本模块内部函数 | `add_knowledge_node` / `rag_search` / 两个 quiz 工具的真身都在领域模块（可被 API、脚本复用）。handler 一旦长起来，先问"这逻辑别人要不要用" |
| 3 | **异步只在必要时**：`async def` 只在工具内要 `asyncio.create_task` 时用 | **不要**为了"想 await"把 handler 改成协程再 `asyncio.run` —— `llm/clients.py` 的 AsyncOpenAI 单例跨事件循环会报 `Event loop is closed`。目前只有 2 个工具是 async |

另外两条通用要求：

- **入参来自模型，一律先夹取再使用**：`top_k` 夹到 1~5、`hops` 0~3、`max_chars` ≥500 且 ≤20000、
  `max_mb` ≤200。模型会传 `top_k=100` / `max_mb=99999`；夹取比报错好（模型无须重试）。
- **`user_id` 是前置条件不是权限**：拿不到就回"无法确定用户上下文，请基于已有知识回答"，不发请求。

---

## 5. SSRF 防护 = `../net_guard.py`（一个能力层，不是三份检查）

所有「按 URL 取内容」的工具（目前 `fetch_webpage` / `download_resource`）都能被模型
**任意构造 URL** 调用 —— SSRF 是这条路径上唯一的安全防线，所以它单独成模块：

- 协议必须 http/https；host 命中 `localhost` / `127.0.0.1` / `::1` / `0.0.0.0` / `169.254.*` → 拒绝
- **解析 DNS 之后**再判 IP（`ipaddress`：private / loopback / link-local / reserved / multicast）
  → 防 `attacker.com` 解析到 `10.0.0.1`

**新增 URL 类工具必须过 `is_blocked_url`** —— 属于「永不简化」的范畴。

### 5.1 兄弟能力层：`core/open_source.py`（只采开源来源，2026-09-23）

同一批 URL 入口还有第二道横切判定：**这个来源能不能留存**。它同样只有一个实现
（`core/open_source.py`：开放来源表 / 关闭来源表 / 页面许可声明识别，fail-closed 到 L3）：

- `fetch_webpage` 与「联网搜索后自动存档」→ 判定收口在 **`../web_archive.py`**（唯一存档出口），
  工具与宿主**零改动**：非开放来源**只看不留**（返回文本不变，建不出图谱节点）；
- `download_resource` → 下载前 `is_open(url)`，非开放来源回填文案、不下载不入库（它产出的是
  「可被检索」的内容，属真正的采集，故 fail-closed）。

**新增会留存的 URL 入口必须同时过 `is_blocked_url` 与 `is_open`**，并且**不要另写一份判定**。

---

## 6. 同步↔异步桥：四份拷贝，刻意为之

`rag_search.py` / `download_resource.py` 里各有一个 `_run_async`，与
`../mcp_host.py` / `core/kg_taxonomy.py` 的完全同构，**各自持单例线程池**：

```python
def _run_async(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)                          # 当前线程无 loop → 直接跑
    return _POOL.submit(asyncio.run, coro).result()       # 有 loop → 丢线程池另起一个 loop
```

**为什么需要**：同步 handler 由 `dispatch.execute_kg_tool_async` 放进 `asyncio.to_thread` 的
**工作线程**执行，那里没有运行中的事件循环 —— 工具内的 async 调用（RAG 检索、`kb_manager`
入库）无处可跑。

**为什么不合并成一份**：各池容量按各自场景定（`rag_search` 2 个 worker / `download_resource`
1 个），合并后一次慢下载会占满共享池并阻塞检索。**这是有意的隔离，不是遗漏。**
`ponytail:` 将来要统一，先做"每工具隔离队列"再合并。

---

## 7. 什么**不**放这个目录

| 东西 | 为什么不在 | 在哪 |
|---|---|---|
| 写图谱的业务函数 | 它是「AI 写图谱层」，`chat_service` 也在用 | `core/knowledge_writer.py` |
| 出题 / 判分的业务 | 是完整业务（后台任务 + 入库 + 事件 + 掌握度），有独立 API 与存储 | `core/quiz/chat_quiz.py` |
| 画像的读写 | 画像有自己的分层包与公开入口 | `core/profile/` |
| 检索的实现 | 四层 RAG 体系（图谱 / kb / 混合检索 / 注入） | `core/rag_pipeline/` 等 |
| MCP 工具 | 定义在 server 侧（协议直通，宿主不维护第二份） | `app/mcp_servers/` |

**判据**：本目录只放「**给模型看的契约 + 一段不长于几十行的执行体**」。
一旦某模块的执行体开始长得像业务，就该把它推回领域模块，handler 退回薄壳。

---

## 8. 改动检查清单

改/加任何工具时：

1. [ ] `DESCRIPTION`（给模型）与 `GUIDANCE`（给提示词）**都**更新了吗？两者分工不同，
       漏一个就会出现"模型知道有这工具但不知道什么时候用"。
2. [ ] `GUIDANCE` 里有没有写清「**何时别用**」？触发类工具（`quiz_generate`）只写正向条件
       会被更强的既有教学原则盖过 —— 实测踩过。
3. [ ] 失败分支是否**返回文本**而不是抛异常？
4. [ ] 新的 URL 入口是否过了 `is_blocked_url`？会**留存**内容的还要过 `is_open`（只采开源来源）？
5. [ ] 慢工具是否显式给了 `timeout_secs`？（默认 60s）
6. [ ] 模型侧 `PARAMETERS` 的 `required` 是否与 handler 里的 `args[...]` 一致？
7. [ ] `tools/__init__.py` 的 `NATIVE_SPECS` 加了吗（漏了工具不会注册，静默消失）？
8. [ ] 跑测试：

```bash
cd backend
venv/Scripts/python.exe -m pytest tests/test_tools_registry.py -q    # 一致性守卫（7 例）
venv/Scripts/python.exe -m pytest tests/test_chat_quiz.py -q         # 分发三条路径 + 两个 quiz 工具
venv/Scripts/python.exe -m pytest tests/test_download_tool.py tests/test_rag_tool.py -q
venv/Scripts/python.exe -m pytest tests/ -q -m "not llm_api"         # 离线全量
```

改完先自测一眼「导入 + 产物规模」是否与预期一致（期望 `12 <N>`，N 只增不减）：

```bash
venv/Scripts/python.exe -c "from app.core.agent_tools import KG_TOOLS, TOOLS_PROMPT; print(len(KG_TOOLS), len(TOOLS_PROMPT))"
```

---

## 9. 相关文档

| 位置 | 关系 |
|---|---|
| `../README.md` | **机制层**：注册表 / 分发 / 不变量 / 三份产物怎么生成 |
| `../net_guard.py` | 本目录 URL 类工具共用的 SSRF 防御 |
| `../mcp_host.py` · `app/mcp_servers/README.md` | 第 12 个工具（MCP）从哪来 |
| `AGENTS.md` §2 | 「加工具 = 1 处定义 + 3 份产物自动生成」的契约行 |
| `docs/项目历程_决策与效果记录.md` | 「为什么掌握度主信号是判分」「为什么要跨调用去重」的历史 |
