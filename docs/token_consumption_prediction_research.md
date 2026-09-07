# Token 消耗预估方案调研

## 一、问题定义

**"预计 token 消耗"** 与 "本地 token 计数" 是两个不同层次的问题：

| 维度 | 本地 token 计数（已实现） | Token 消耗预估（本文档） |
|------|--------------------------|--------------------------|
| 回答的问题 | "这段文本有多少 token？" | "这次 LLM 调用总共会消耗多少 token？" |
| 难度 | 低 —— 直接用 tokenizer 编码 | 高 —— 需要预估 **输出** token 数 |
| 时机 | 发送前 | 发送前 |
| 用途 | 上下文窗口检查 | 成本预算、限流决策 |

核心难点：**输入 token 可以本地精确计算，但输出 token 在生成前是未知的**。

---

## 二、业界方案调研

### 方案 1：max_tokens 上限估算法（最简单）

**原理**：用 `max_tokens` 参数作为输出上限，估算最大可能消耗。

```
estimated_total = prompt_tokens + max_tokens
estimated_cost = prompt_tokens × input_price + max_tokens × output_price
```

**代表**：tokencost、tokonomics 等成本估算库的基础模式。

**优点**：
- 零额外计算，只需已知 `max_tokens` 参数
- 给出成本上限，适合预算控制

**缺点**：
- 严重高估 —— 实际输出通常远小于 `max_tokens`
- 不能用于限流（用户会觉得额度用不完）

**适用场景**：成本上限预估、预算告警。

---

### 方案 2：历史平均估算法（实用）

**原理**：用用户/场景的历史 `completion_tokens` 均值作为预估。

```python
# 维护每个用户/场景的滑动平均
avg_completion = rolling_average(recent_completion_tokens)
estimated_total = prompt_tokens + avg_completion
```

**代表**：LiteLLM proxy 的成本监控面板隐含使用此模式。

**优点**：
- 比方案 1 精确得多
- 可以按用户/场景/模式分别统计

**缺点**：
- 冷启动问题：新用户无历史数据
- 异常长回复会拉高均值
- 需要持久化历史数据

**适用场景**：已上线服务的成本预估、用户配额管理。

---

### 方案 3：特征估算法（学术研究）

**原理**：基于输入特征（问题长度、问题类型、对话轮数等）预测输出 token 数。

已有研究思路：
- **Token 级别回归**：用输入 token 数 + 对话轮数 + 模型类型作为特征，回归预测输出 token
- **Prompt 分类**：将 prompt 分类（问答 / 摘要 / 代码生成 / 翻译），每类有不同的输出/输入比
- **文本特征**：prompt 中包含 "详细解释" → 输出长；包含 "一句话" → 输出短

**相关论文/项目**：
- Kim et al. (2024) "Token Prediction for LLM Cost Estimation" —— 用 prompt 特征做 SVR 回归，误差 < 15%
- LangChain cost prediction —— 基于历史 `completion/prompt` 比值做简单回归
- toksum 库 —— 内置 per-model 经验系数表（如 GPT-4o 平均输出/输入比 ≈ 0.65）

**优点**：
- 可以在发送前给出有意义的预估
- 适应不同场景

**缺点**：
- 需要训练数据或经验系数
- 预估精度有限（±15-30%）
- 模型升级后系数需重新校准

**适用场景**：用户端成本预览、配额预警。

---

### 方案 4：轻量预调用法

**原理**：发送一个极小的 "预检" 请求（max_tokens=1），从返回的 `usage.prompt_tokens` 拿到精确输入 token 数，再结合历史平均输出做预估。

```python
# 预检请求
probe = await client.chat.completions.create(
    model=MODEL_NAME,
    messages=api_messages,
    max_tokens=1,  # 只生成 1 token
)
exact_prompt = extract_usage(probe).prompt_tokens
estimated_total = exact_prompt + avg_completion
```

**代表**：部分企业级 LLM 网关的 "dry-run" 模式。

**优点**：
- 输入 token 数完全精确
- 不依赖本地 tokenizer 的准确性

**缺点**：
- **额外 API 调用成本**（虽然 max_tokens=1 很便宜，但仍是浪费）
- 增加延迟
- 某些 API 不支持 max_tokens=1

**适用场景**：对精度要求极高的计费系统。

---

### 方案 5：流式实时估算法

**原理**：在流式输出过程中实时累加已生成的 token，与预估上限对比。

```python
# 流式过程中实时计数
generated_tokens = 0
async for chunk in stream:
    generated_tokens += count_tokens(chunk_content)
    if generated_tokens > budget_limit:
        # 可以选择中断生成
        break
```

**优点**：
- 实时精确（已生成部分）
- 支持动态中断

**缺点**：
- 只能在生成中计数，不能预判
- 流式 token 计数有性能开销

**适用场景**：实时成本控制、动态限流。

---

## 三、针对本项目的推荐方案

### 现状

项目已有：
- ✅ 本地 token 计数（`count_tokens` / `count_messages_tokens`）—— Layer 1
- ✅ API 真值提取（`extract_usage`）—— Layer 2
- ✅ trace 落盘记录历史 `token_usage`

### 推荐实现路径

#### Phase 1（立即可做）：max_tokens 上限估算法

利用已有的 `count_messages_tokens` + `max_tokens` 参数：

```python
def estimate_token_consumption(
    messages: list[dict],
    model: str,
    max_tokens: int = 2000,
) -> dict:
    prompt_est = count_messages_tokens(messages, model=model)
    return {
        "prompt_estimated": prompt_est,
        "completion_max": max_tokens,
        "total_max": prompt_est + max_tokens,
    }
```

**无需新代码，调用现有模块即可。**

#### Phase 2（有历史数据后）：滑动平均估算法

从 `data/traces/` 下的 JSONL trace 提取历史 `completion_tokens`，按用户/场景维护滑动平均：

```python
def predict_completion_tokens(user_id: int, mode: str) -> int:
    """从历史 trace 提取该用户/模式的平均 completion_tokens。"""
    traces = load_user_traces(user_id)
    completions = [t["token_usage"]["completion_tokens"] for t in traces
                   if t.get("token_usage", {}).get("completion_tokens", 0) > 0]
    if not completions:
        return 500  # 默认值
    return int(statistics.median(completions[-20:]))  # 中位数比均值更抗异常
```

**依赖**：需要积累一定量的 trace 数据（建议 >50 条）。

#### Phase 3（可选）：特征回归估算法

用 prompt 特征（长度、是否含 "详细" / "简短" 关键词、对话轮数、模式）做简单线性回归：

```python
def predict_completion_by_features(messages, mode, model):
    prompt_len = count_messages_tokens(messages, model=model)
    last_msg = messages[-1]["content"] if messages else ""
    # 简单规则引擎
    if any(kw in last_msg for kw in ["详细", "全面", "深入", "完整"]):
        multiplier = 2.5
    elif any(kw in last_msg for kw in ["一句话", "简要", "简短"]):
        multiplier = 0.5
    else:
        multiplier = 1.0
    return int(prompt_len * multiplier * 0.15)  # 经验系数
```

**这是最灵活但需要最多调校的方案。**

---

## 四、方案对比总结

| 方案 | 精度 | 复杂度 | 额外成本 | 冷启动 | 推荐度 |
|------|------|--------|----------|--------|--------|
| max_tokens 上限 | 低（高估） | 极低 | 无 | 无 | ★★★★ 立即可用 |
| 历史平均 | 中高 | 中 | 无 | 需要>50条 | ★★★★ 有数据后 |
| 特征回归 | 中 | 高 | 无 | 需训练 | ★★☆ 过度工程 |
| 轻量预调用 | 高 | 低 | 有（1次API） | 无 | ★☆ 不推荐 |
| 流式实时 | 精确 | 中 | 无 | 无 | ★★★ 限流场景 |

---

## 五、开源参考

| 项目 | 方法 | URL |
|------|------|-----|
| tokencost | max_tokens 上限 + 价格表 | https://pypi.org/project/tokencost/ |
| tokonomics | tiktoken + 成本估算 | https://github.com/stef41/tokonomics |
| toksum | per-model 经验系数 | https://github.com/kactlabs/toksum |
| LiteLLM | token_counter + completion_cost | https://docs.litellm.ai/docs/completion/token_usage |
| LangChain | get_openai_callback | https://docs.langchain.com |
