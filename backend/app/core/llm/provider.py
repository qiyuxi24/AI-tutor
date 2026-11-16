"""服务商方言表 —— 换 provider 只改这张表（2026-11-16）。

## 为什么需要它
`chat_create` 一直把 `extra_body={"reasoning_split": True}` 无条件发给**所有**模型：
那是 MiniMax-M3 的字段。换 deepseek / GLM 后，严格网关会 400，宽容网关静默忽略 ——
后者最贵：不报错，但思考永远拿不到。

同理三条方言差异各自散落在别处，改 provider 时得记住四个文件：
    · 请求侧 extra_body 键名（本模块）
    · 响应侧思考字段（`reasoning_details` vs `reasoning_content`，见 `reasoning_texts`）
    · token 校准系数（原先硬编码在 `token_counter._MODEL_CALIBRATION`）
    · 连通性探测的 max_tokens（reasoner 类拒绝 1，见 `probe_tokens`）

**不查表的一条**：多轮回填思考块（`agent/context.py::assistant_snapshot`）按网关
**实际下发的字段**原样回填 —— 它给过的它必然认得，查表反而会多塞一个它没给过的字段。

## 解析顺序（绝不猜错）
显式 `provider` 字段（`models.json`）→ 模型名前缀 → **兜底零配置**。

兜底那条是刻意的：未知 provider **一个字段都不发**，而不是"发个默认值试试"。
`reasoning_split` 对 MiniMax 是必需、对别人是未知字段 —— 把"必需"和"未知"混成
同一个常量，就是这个 bug 的根因。用户接的自定义网关若确实是 MiniMax 兼容，
在设置页显式选 MiniMax 即可（`provider` 字段就是给这种情况留的）。

## 没有做（YAGNI）
- 没有 per-provider client 子类：全部是 OpenAI 兼容 HTTP，`clients.py` 的惰性单例够用。
- 没有引 LiteLLM：它自带重试 / 回退 / 计费，与 `retry.py` / `fallback.py` / `usage.py`
  三处职责重叠，接进来会把已跑通的降级链换成黑盒。
- 没有"思考文本协议"（不支持 tool_calls 的模型走文本动作）：触发条件未到，见
  `docs` 里动作层的搁置理由。
"""
from dataclasses import dataclass

__all__ = ["Provider", "PROVIDERS", "DEFAULT_KEY", "provider_for",
           "extra_body_for", "presets", "reasoning_texts", "is_known"]


@dataclass(frozen=True)
class Provider:
    """一个服务商的方言。全部字段都是数据 —— 加 provider = 加一行表项。"""
    key: str
    label: str
    base_url: str = ""                       # 设置页「选预设自动填地址」
    models: tuple[str, ...] = ()             # 设置页「模型名」提示
    calibration: float = 1.0                 # token 校准系数（本地 tiktoken → 该 tokenizer）
    reasoning_split: bool = False            # 请求要带 reasoning_split（把思考拆到独立字段）
    thinking_off: tuple | None = None        # thinking=False 时发的 (键, 值)；None = 关不掉/没必要
    probe_tokens: int = 1                    # 连通性探测的 max_tokens（reasoner 类下限更高）


# 表顺序只是展示顺序；解析不走顺序，走 `_BY_PREFIX`。
PROVIDERS: dict[str, Provider] = {
    "minimax": Provider(
        key="minimax", label="MiniMax",
        base_url="https://api.minimaxi.com/v1",
        models=("MiniMax-M3", "MiniMax-M2.1"),
        calibration=1.15,                 # M3 tokenizer 与 o200k_base 有差异（实测经验值）
        reasoning_split=True,             # 官方推荐：思考拆到 reasoning_content，content 保持纯净
        thinking_off=("thinking", {"type": "disabled"}),
    ),
    "deepseek": Provider(
        key="deepseek", label="DeepSeek",
        base_url="https://api.deepseek.com/v1",
        models=("deepseek-chat", "deepseek-reasoner"),
        calibration=1.1,                  # V3 tokenizer 与 o200k_base 微小差异
        # 不发 reasoning_split（不是它的字段）；思考直接落在 reasoning_content。
        thinking_off=("thinking", {"type": "disabled"}),
        probe_tokens=64,                  # reasoner 类拒绝 max_tokens=1
    ),
    "glm": Provider(
        key="glm", label="智谱 GLM",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        models=("glm-4.6", "glm-4.5", "glm-5"),
        calibration=1.0,
        thinking_off=("thinking", {"type": "disabled"}),
        probe_tokens=16,
    ),
    "qwen": Provider(
        key="qwen", label="通义千问（DashScope）",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        models=("qwen-plus", "qwen-max"),
        calibration=1.0,                  # 官方推荐 o200k_base，近似精确
    ),
    "openai": Provider(
        key="openai", label="OpenAI",
        base_url="https://api.openai.com/v1",
        models=("gpt-4o", "gpt-4.1"),
        calibration=1.0,
    ),
}

# 兜底档：不是一家服务商，而是"OpenAI 兼容但不认识" —— 什么都不发。
DEFAULT_KEY = "openai-compat"
_DEFAULT_PROVIDER = Provider(key=DEFAULT_KEY, label="OpenAI 兼容（通用）")

# 模型名前缀 → provider。**顺序有意义**（先匹配到的赢），别按字母排。
_BY_PREFIX: tuple[tuple[str, str], ...] = (
    ("minimax", "minimax"), ("abab", "minimax"),
    ("deepseek", "deepseek"),
    ("glm", "glm"), ("chatglm", "glm"),
    ("qwen", "qwen"),
    ("gpt", "openai"), ("o1", "openai"), ("o3", "openai"), ("o4", "openai"),
)


def is_known(key: str) -> bool:
    """设置页 / 接口校验用（空串合法 = 自动判定，见 `provider_for`）。

    `openai-compat` 也合法 —— 它是"我知道是 OpenAI 兼容、但别猜我的方言"的显式选择。
    """
    return not key or key in PROVIDERS or key == DEFAULT_KEY


def provider_for(model: str = "", explicit: str | None = None) -> Provider:
    """解析 provider：显式声明 > 模型名前缀 > 兜底零配置。

    显式声明认不出时**退回自动判定**而不是报错：配置漂移不该让对话瘫痪
    （与 `ModelStore._load` 的损坏兜底同一口径）。

    例外：显式写 `openai-compat` = 用户明确要求"什么都别发" → **不再按模型名猜**。
    否则接了个叫 MiniMax-M3 的自建网关、又显式选了通用档，仍会被塞 reasoning_split。
    """
    key = (explicit or "").strip().lower()
    if key == DEFAULT_KEY:
        return _DEFAULT_PROVIDER
    if key in PROVIDERS:
        return PROVIDERS[key]
    name = (model or "").strip().lower()
    for prefix, pkey in _BY_PREFIX:
        if name.startswith(prefix):
            return PROVIDERS[pkey]
    for prefix, pkey in _BY_PREFIX:      # 前缀不中再退一步：包含匹配（如 org/MiniMax-M3）
        if prefix in name:
            return PROVIDERS[pkey]
    return _DEFAULT_PROVIDER


def extra_body_for(provider: Provider, thinking: bool = True) -> dict:
    """该 provider 本次请求要带的 extra_body（空 dict = 一个字段都不发）。"""
    body: dict = {}
    if provider.reasoning_split:
        body["reasoning_split"] = True
    if not thinking and provider.thinking_off:
        k, v = provider.thinking_off
        body[k] = dict(v)
    return body


def reasoning_texts(obj) -> list[str]:
    """从响应 message 或流式 delta 里取思考片段（0~N 片，顺序即到达顺序）。

    两个方言：
      · MiniMax / 部分网关：`reasoning_details` = [{"type": "reasoning.text", "text": ...}]
      · DeepSeek / GLM：`reasoning_content` = "..."（裸字符串）
    按**实际出现的字段**取，不查表 —— 网关回显什么才是真的（同 `_resp_model` 的口径）。
    """
    out: list[str] = []
    rd = getattr(obj, "reasoning_details", None)
    if rd:
        for r in (rd if isinstance(rd, list) else [rd]):
            text = r.get("text") if isinstance(r, dict) else getattr(r, "text", None)
            if text:
                out.append(text)
    if not out:
        rc = getattr(obj, "reasoning_content", None)
        if isinstance(rc, str) and rc:
            out.append(rc)
    return out


def presets() -> list[dict]:
    """设置页用的预设列表：`[{key, label, base_url, models}]`。

    `openai-compat` 显式列在末尾 —— 它是"我知道是 OpenAI 兼容、但别猜我的方言"的逃生口。
    """
    items = [{"key": p.key, "label": p.label, "base_url": p.base_url, "models": list(p.models)}
             for p in PROVIDERS.values()]
    items.append({"key": DEFAULT_KEY, "label": _DEFAULT_PROVIDER.label, "base_url": "", "models": []})
    return items
