"""语音转写出口（OpenAI 兼容 `/audio/transcriptions`）—— 全库唯一实现。

为什么单独一个模块：转写是**第三种 LLM 原语**，与另外两种并列 ——
`fallback.chat_create`（对话）、`embed.embed_texts`（检索侧嵌入）。它有自己的一套
约束，放在别处各写一份必然漂移：

1. **凭证复用用户已配的模型**（`user_models.ModelStore` 的 base_url + api_key），
   候选链与降级语义与对话完全一致（`resolve_candidates` + `should_fallback`）——
   "用户配了就用用户的、没配回落 .env 系统档"这条规则只能有一份实现。
2. **模型名单独取**：ASR 模型与对话模型**不是同一个**（把 `deepseek-chat` 发到
   `/audio/transcriptions` 必然 400），故由 `asr_model` 参数 / `ASR_MODEL` 配置决定，
   绝不沿用候选链里的对话模型名。
3. **音频体积先拒**：接口会把整段音频读进内存并整体上传，超限在本地先拒，
   而不是等服务端报错（也不是清晰度问题，是资源保护）。

调用方：`kb/video_import.py`（视频课程导入，字幕拿不到时才走这里）。
"""
from __future__ import annotations

import logging

from app.core.config import settings
from app.core.llm.fallback import resolve_candidates, should_fallback
from app.core.llm.retry import map_api_error, with_retry

logger = logging.getLogger("ai-tutor")

# 单次转写音频体积上限（转写服务普遍有请求体上限；本地先拒给出明确原因）
# **本模块不做切段**：调用方（`kb/video_import.py`）已按 2 分钟切好段，
# 每次调用只送一段（≈0.5MB）。这里保留上限是为了挡住"别处直接调用时传了一整段音乐"，
# 而不是给视频设封顶 —— 长视频由分段解决。
MAX_AUDIO_BYTES = 25 * 1024 * 1024


async def transcribe_audio(audio: bytes, filename: str = "audio.mp3", *,
                           user_id: int | None = None,
                           model: str | None = None,
                           language: str | None = None) -> str:
    """把音频转成文字，返回正文（可能为空字符串）。

    参数:
        audio:    音频二进制（mp3/m4a/wav/webm 等，由服务端支持范围决定）
        filename: 文件名，仅用于 multipart 声明与格式推断
        user_id:  用它配置的 base_url/api_key；未配 → 回落 .env 系统默认档
        model:    ASR 模型名；留空取 `settings.asr_model`
        language: 可选语言提示（"zh" 等），不传则由服务端自动判定

    抛:
        ValueError    —— 参数/体积/未配置模型名（调用方直接回给用户，不需重试）
        RuntimeError  —— 候选链全部失败（经 map_api_error 分类，带错误码，与对话出口同形）
    """
    model = (model or settings.asr_model or "").strip()
    if not model:
        raise ValueError(
            "未配置语音转写模型名：请在 .env 设置 ASR_MODEL（如 whisper-1、"
            "qwen3-asr-flash），或在请求里显式指定 asr_model"
        )
    if not audio:
        raise ValueError("音频内容为空")
    if len(audio) > MAX_AUDIO_BYTES:
        raise ValueError(
            f"音频过大（{len(audio) // 1024 // 1024}MB，上限 "
            f"{MAX_AUDIO_BYTES // 1024 // 1024}MB）：请截取片段后重试"
        )

    candidates = resolve_candidates(user_id)
    for idx, cand in enumerate(candidates):
        cand_client = cand[0]
        try:
            resp = await with_retry(
                cand_client.audio.transcriptions.create,
                model=model,
                file=(filename, audio),
                **({"language": language} if language else {}),
            )
        except Exception as e:
            if idx == len(candidates) - 1 or not should_fallback(e):
                raise map_api_error(e) from e
            logger.warning("转写候选失败（%s），自动降级到下一档: %s",
                           type(e).__name__, str(e)[:150])
            continue
        return (getattr(resp, "text", "") or "").strip()
    return ""
