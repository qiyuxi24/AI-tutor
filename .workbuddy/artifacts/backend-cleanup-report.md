# 后端代码冗余清理报告

> 2026-06-16 | 涉及 6 个文件 | 净减少 ~90 行重复代码

---

## 修复总览

| # | 严重度 | 类别 | 描述 | 减少行数 |
|---|--------|------|------|----------|
| 1 | 🔴 高 | 逻辑重复 | llm_client: 消息格式转换 3→1 | ~12 |
| 2 | 🔴 高 | 逻辑重复 | llm_client: 异常处理 3→1 模板 | ~41 |
| 3 | 🟡 中 | 逻辑重复 | chat_service: 两个 prompt 函数合并 | ~30 |
| 4 | 🟡 中 | 数据隔离 | ai_suggestions.json 按用户隔离 | fix |
| 5 | 🟡 中 | 逻辑重复 | 错误事件发布 3→1 公共函数 | ~15 |
| 6 | 🟢 低 | 清理 | 废弃字段 + 内联 import | ~3 |

---

## 各修复详细说明

### Fix1: 消息格式转换

**问题**: `call_llm()` / `call_llm_stream()` / `call_llm_tools()` 三处各有相同的 6 行代码将 Pydantic/dict 消息转成 API 格式。

**修复**: 提取为 `_build_api_messages(system_prompt, messages) -> list[dict]`

```python
def _build_api_messages(system_prompt: str, messages: list) -> list[dict]:
    api_messages = [{"role": "system", "content": system_prompt}]
    for msg in messages:
        api_messages.append({
            "role": msg["role"] if isinstance(msg, dict) else msg.role,
            "content": msg["content"] if isinstance(msg, dict) else msg.content
        })
    return api_messages
```

### Fix2: LLM API 异常分类

**问题**: `call_llm()` 和 `call_llm_stream()` 各有 20+ 行的 try/except 链捕获 6 种异常类型，完全相同。

**修复**: 提取为 `_map_api_error(e, prefix="") -> RuntimeError`

```python
def _map_api_error(e: Exception, prefix: str = "") -> RuntimeError:
    if isinstance(e, APITimeoutError):
        user_msg = log_error(ErrorCode.LLM_API_TIMEOUT, ...)
    elif isinstance(e, RateLimitError):
        ...
    return RuntimeError(user_msg)
```

调用方从 21 行变成 2 行: `try: ... except Exception as e: raise _map_api_error(e) from e`

### Fix3: Prompt 构建函数合并

**问题**: `_build_stream_prompt` (流式) 和 `_build_chat_prompt` (后台) 90% 相同，仅 `detailed` 参数和 `TOOL_CAPABILITY_PROMPT` 注入不同。

**修复**: 合并为 `_build_system_prompt(messages, mode, kg, inject_tools=False)`

同时清理了 `process_message_stream()` 中两处 `import json as _json` 内联导入，改为文件顶部 `import json`。

### Fix4: ai_suggestions.json 用户隔离

**问题**: `_load_suggestions` / `_save_suggestions` 写文件到 `kg.data_dir`（共享目录），多用户冲突。

**修复**: 调用方改为 `kg.nodes_dir`（`data/knowledge/nodes/{user_id}/`），每人独立。

### Fix5: 错误事件发布

**问题**: 同样的 try-except `publish("error", ...)` 模式在 `graph_analyzer.py` (1处) 和 `chat_service.py` (2处) 重复。

**修复**: 新增 `error_codes.publish_error_event(code_tuple, message, module, detail)`，一处定义，三处调用。

### Fix6: 废弃代码清理

- `schemas.py`: 移除 `ChatRequest.user_id` 字段（user_id 已由 JWT 提供）
- `chat_service.py`: 内联 `from app.core.event_bus import publish` 移到文件顶部

---

## 修改文件清单

| 文件 | 修改内容 |
|------|----------|
| `app/core/llm_client.py` | +2 辅助函数, -53 行重复 |
| `app/core/error_codes.py` | +1 `publish_error_event()` |
| `app/core/graph_analyzer.py` | -9 行, 改用 `publish_error_event` |
| `app/services/chat_service.py` | 合并 prompt 函数, -30 行, 清内联 import |
| `app/models/schemas.py` | 移除废弃 `user_id` 字段 |
| `app/api/v1/knowledge.py` | 无需改动（仅调用方变更） |

---

## 验证

全部 6 个文件 Python `py_compile` 语法检查通过。
