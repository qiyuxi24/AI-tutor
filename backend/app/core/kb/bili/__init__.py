"""B 站专用接入：链接归一化 + 平台字幕直取。

借鉴 `bili-archive`（MIT，`bili_core` 层）的**结论与算法**，但**不引入其依赖树**：
- 只搬三件事：URL/BV 归一化、两源字幕直取、WBI 签名
- HTTP 用本项目已有的 httpx（不新添 requests / urllib 客户端）

为什么不直接依赖 `bili_core`：它的发行包会把 sherpa-onnx / faster-whisper /
imageio-ffmpeg 整棵树拖进来 —— 我们只用字幕那几十行，不值得为它换依赖图。
"""
