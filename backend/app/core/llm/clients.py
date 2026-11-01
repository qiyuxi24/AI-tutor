"""OpenAI 兼容客户端单例：对话主模型 client + 嵌入 embed_client（可选备用 fallback_client）。

经 LLM_API_KEY / LLM_BASE_URL / MODEL_NAME 可整体切换对话服务商（默认 MiniMax-M3）；
嵌入固定阿里云 text-embedding-v4（独立 key/base，见 config.embed_base_url / DASHSCOPE_API_KEY）。

## 为什么是惰性的（2026-10-26，P2-4 冷启动）
实测本模块导入期原本要 **1.98s**：`import openai` 1.0s + 每个 `AsyncOpenAI(...)` 构造
≈0.7s（httpx 传输层初始化，与 ssl 无关）。而外部宿主（MCP server / DSH worker）的
只读工具里有 2/3 根本不碰 LLM —— 让它们在 `tools/list` 阶段先等 2 秒是纯浪费。

做法：模块级 `__getattr__`（PEP 562）。`from app.core.llm.clients import client` 这种
写法**照旧可用**，只是第一次取属性时才构造；构造完写回 `globals()`，之后是真属性
（零开销，且 `monkeypatch.setattr(clients, "client", fake)` 依旧能覆盖）。
`MODEL_NAME` 保持导入期就绪：它只是读配置，且被多处当作常量引用。

⚠️ 依赖顺序：不要在本模块再 `from openai import ...`（那 1.0s 就回来了），
也**不要**在别的模块顶层 `from app.core.llm.clients import client` 之后再期待"没代价"——
顶层 from-import 会立刻触发构造（`fallback.py` / `embed.py` 已改成模块引用 + 调用时取值）。
"""
from app.core.config import settings

MODEL_NAME = settings.model_name

# 惰性单例缓存（键 = 公开属性名）。用 dict 而不是 global 变量：
# `__getattr__` 里写 globals() 需要区分"尚未构造"与"构造出来就是 None"（fallback_client 可能是 None）。
_CACHE: dict[str, object] = {}


def _make(**kwargs):
    """构造一个 AsyncOpenAI（openai 包在此处才真正导入）。"""
    from openai import AsyncOpenAI
    return AsyncOpenAI(**kwargs)


def _build(name: str):
    if name == "client":  # 对话主模型（OpenAI 兼容，默认 MiniMax-M3）
        return _make(
            api_key=settings.llm_api_key or settings.dashscope_api_key,
            base_url=settings.llm_base_url,
            timeout=settings.llm_timeout,
        )
    if name == "embed_client":  # 嵌入专用：固定阿里云 text-embedding-v4
        return _make(
            api_key=settings.dashscope_api_key,
            base_url=settings.embed_base_url,
            timeout=settings.llm_timeout,
        )
    # 备用服务客户端（模型回退链用，见 fallback.py）：FALLBACK_* 三件套齐全才创建
    # ⚠️ 字段名必须用 config.Settings 的**实际**名字 `fallback_llm_base_url` / `fallback_llm_api_key`。
    #    这里原先写成 `settings.fallback_base_url`（不存在的属性）—— 条件是 `and` 短路，
    #    FALLBACK_MODEL_NAME 为空时侥幸不报错；一旦真去配置备用（三件套填齐），
    #    本模块**导入期**就会 AttributeError，后端直接起不来 → 备用通道永远配不上。
    #    2026-09-29 定位「建图全挂但无降级」时发现。
    if settings.fallback_model_name and settings.fallback_llm_base_url:
        return _make(
            api_key=settings.fallback_llm_api_key or settings.dashscope_api_key,
            base_url=settings.fallback_llm_base_url,
            timeout=settings.llm_timeout,
        )
    return None


def __getattr__(name: str):
    """PEP 562：`client` / `embed_client` / `fallback_client` 首次访问时构造。"""
    if name not in ("client", "embed_client", "fallback_client"):
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    if name not in _CACHE:
        _CACHE[name] = _build(name)
    value = _CACHE[name]
    globals()[name] = value  # 落成真属性：后续访问不再进 __getattr__
    return value
