"""Token 计数模块 —— 本地预计数 + API 真值提取（两层方案）

设计依据：调研 LangChain / LiteLLM / LlamaIndex / tiktoken 的开源实践
（见 docs/token_counting_research.md）

两层互补：
  Layer 1（预计数）：发送请求前用 tiktoken 本地编码估算 token 数
    → 上下文窗口检查、历史消息裁剪、预算控制
  Layer 2（真值）：请求完成后从 response.usage 提取供应商返回的精确 token 数
    → 计费审计、trace 落盘、成本监控

模型适配：
  - Qwen/DashScope：官方推荐 o200k_base 编码，近似精确
  - MiniMax-M3：Python tiktoken 无 MiniMax 编码，用 o200k_base + 校准系数
  - 通用 OpenAI 兼容：o200k_base 作为安全兜底
"""
import logging
from dataclasses import dataclass

logger = logging.getLogger("ai-tutor")

# tiktoken 惰性加载（首次调用才 import + 下载编码文件，避免冷启动开销）
_encoding = None

# 默认编码：o200k_base（Qwen 官方推荐，对中文友好，也是 GPT-4o 系列编码）
_DEFAULT_ENCODING = "o200k_base"

# 模型校准系数：本地 o200k_base 编码与目标模型实际 tokenizer 的经验比值
# < 1.0 = 本地计数偏高需下调；> 1.0 = 本地计数偏低需上调
_MODEL_CALIBRATION = {
    "minimax": 1.15,   # MiniMax M3 tokenizer 与 o200k_base 有差异，经验值 ~1.15
    "abab":   1.15,    # MiniMax 旧模型系列前缀
    "qwen":   1.0,     # Qwen 官方推荐 o200k_base，近似精确
    "gpt":    1.0,     # OpenAI 系列原生支持
    "deepseek": 1.1,   # DeepSeek V3 tokenizer 与 o200k_base 有微小差异
}

# 每条消息的结构开销（role 标签、分隔符等 framing tokens）
# 参考 OpenAI cookbook：每条 message 约 4 tokens overhead
_MESSAGE_OVERHEAD = 4

# 对话级固定开销（priming + assistant 前缀）
_CONVERSATION_OVERHEAD = 3


def _get_encoding():
    """惰性加载 tiktoken 编码，全局复用单例。"""
    global _encoding
    if _encoding is None:
        try:
            import tiktoken
            _encoding = tiktoken.get_encoding(_DEFAULT_ENCODING)
        except ImportError:
            logger.warning(
                "tiktoken 未安装，token 预计数将退化为字符估算（1 token ≈ 3 字符）。"
                "安装方式：pip install tiktoken"
            )
            return None
        except Exception as e:
            logger.warning(f"tiktoken 编码加载失败，退化为字符估算: {e}")
            return None
    return _encoding


def _get_calibration_factor(model: str) -> float:
    """根据模型名匹配校准系数。"""
    model_lower = (model or "").lower()
    for prefix, factor in _MODEL_CALIBRATION.items():
        if prefix in model_lower:
            return factor
    return 1.0


def count_tokens(text: str, model: str | None = None) -> int:
    """本地预计数：对单段文本估算 token 数。

    优先使用 tiktoken o200k_base 编码；tiktoken 不可用时退化为字符估算。
    对 MiniMax 等非原生支持的模型，按校准系数修正。

    参数:
        text:  要计数的文本
        model: 模型名（如 "MiniMax-M3", "qwen-plus"），用于匹配校准系数

    返回:
        预估 token 数（int）
    """
    if not text:
        return 0

    enc = _get_encoding()
    if enc is not None:
        raw_count = len(enc.encode(text))
    else:
        # 退化方案：中英混合约 3 字符/token（经验值）
        raw_count = max(1, len(text) // 3)

    factor = _get_calibration_factor(model or "")
    return int(raw_count * factor)


def count_messages_tokens(messages: list[dict], model: str | None = None) -> int:
    """本地预计数：对完整对话消息列表估算 token 数。

    包含每条消息的结构开销（role/framing）和对话级固定开销，
    模拟 OpenAI API 实际计入的 token 构成。

    参数:
        messages: [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}, ...]
        model:    模型名（用于校准系数）

    返回:
        预估总 token 数（含 framing 开销）
    """
    enc = _get_encoding()
    factor = _get_calibration_factor(model or "")

    total = _CONVERSATION_OVERHEAD
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, list):
            content = " ".join(str(p) for p in content)
        content = str(content) if content else ""

        if enc is not None:
            raw_tokens = len(enc.encode(content))
        else:
            raw_tokens = max(1, len(content) // 3)

        total += int(raw_tokens * factor) + _MESSAGE_OVERHEAD

    return total


@dataclass
class TokenUsage:
    """从 API 响应提取的 token 使用量（Layer 2 真值）。"""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0

    def add(self, other: "TokenUsage") -> "TokenUsage":
        """累加另一个 TokenUsage（多轮 agent 循环用）。"""
        return TokenUsage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
            cached_tokens=self.cached_tokens + other.cached_tokens,
            reasoning_tokens=self.reasoning_tokens + other.reasoning_tokens,
        )

    def to_dict(self) -> dict:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cached_tokens": self.cached_tokens,
            "reasoning_tokens": self.reasoning_tokens,
        }


def _safe_int(val) -> int:
    try:
        return int(val) if val is not None else 0
    except (TypeError, ValueError):
        return 0


def extract_usage(response) -> TokenUsage:
    """从 API 响应对象提取 token 使用量（Layer 2 真值）。

    兼容 OpenAI 标准格式和供应商扩展字段：
      - prompt_tokens / completion_tokens / total_tokens（标准）
      - prompt_tokens_details.cached_tokens（缓存命中）
      - completion_tokens_details.reasoning_tokens（推理 token）

    参数:
        response: client.chat.completions.create() 的返回对象，
                  或流式响应的最后一个 chunk

    返回:
        TokenUsage dataclass；response 无 usage 字段时全部为 0
    """
    usage = getattr(response, "usage", None)
    if usage is None:
        return TokenUsage()

    result = TokenUsage(
        prompt_tokens=_safe_int(getattr(usage, "prompt_tokens", 0)),
        completion_tokens=_safe_int(getattr(usage, "completion_tokens", 0)),
        total_tokens=_safe_int(getattr(usage, "total_tokens", 0)),
    )

    # 扩展字段：缓存命中 token
    prompt_details = getattr(usage, "prompt_tokens_details", None)
    if prompt_details:
        result.cached_tokens = _safe_int(getattr(prompt_details, "cached_tokens", 0))

    # 扩展字段：推理 token（MiniMax M3 / o1 等 reasoning 模型）
    completion_details = getattr(usage, "completion_tokens_details", None)
    if completion_details:
        result.reasoning_tokens = _safe_int(
            getattr(completion_details, "reasoning_tokens", 0)
        )

    # total_tokens 缺失时自动补算
    if result.total_tokens == 0:
        result.total_tokens = result.prompt_tokens + result.completion_tokens

    return result


def extract_streaming_usage(chunk) -> TokenUsage:
    """从流式响应 chunk 提取 token 使用量。

    OpenAI 兼容 API 通常在最后一个 chunk 返回 usage。
    DashScope 需设置 stream_options={"include_usage": true}。

    参数:
        chunk: 流式响应的一个 chunk 对象

    返回:
        TokenUsage；chunk 不含 usage 时全部为 0
    """
    return extract_usage(chunk)
