"""用户头像 —— 图片处理管线。

输入：用户上传的原始字节。输出：中心正方形裁剪 + 缩放到 `AVATAR_SIZE` 的 **RGBA PNG** 字节。

三条硬约定：
1. **只认白名单扩展名**。HEIC（iPhone 默认格式）Pillow 不吃，与 KB 图片解析器的
   `_IMAGE_EXTS` 保持同一口径 —— 明确报错让用户转格式，而不是静默失败。
2. **必须做 `exif_transpose`**。手机竖拍照片带 EXIF Orientation，不修正会出现"上传后躺倒"。
3. **统一转 RGBA 再存 PNG**。调色板图/灰度图直接存 PNG 会在前端渲染出意外配色。

错误一律抛 `AvatarError`（带 `code`），由 API 层映射成 413/415/422。
"""

import io
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

#: 头像边长方图尺寸。显示尺寸只有 26~32px，256 已足够（含高分屏留白）
AVATAR_SIZE = 256
#: 上传原始文件大小上限
MAX_UPLOAD_BYTES = 2 * 1024 * 1024
#: 允许的扩展名（与 `core/kb/parsers/image.py::_IMAGE_EXTS` 对齐，不含 heic/tiff/gif/动图）
ALLOWED_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


class AvatarError(ValueError):
    """头像校验/解码失败。`code` 供 API 层映射 HTTP 状态码。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def validate_upload(filename: str, raw: bytes) -> None:
    """上传前置校验（在解码之前做，避免超大/非法文件先进 Pillow）"""
    if not raw:
        raise AvatarError("empty", "文件内容为空")
    if len(raw) > MAX_UPLOAD_BYTES:
        limit_mb = MAX_UPLOAD_BYTES // 1024 // 1024
        raise AvatarError("too_large", f"头像文件不能超过 {limit_mb}MB")
    ext = Path(filename or "").suffix.lower()
    if ext not in ALLOWED_EXTS:
        shown = ext or "未知"
        raise AvatarError("unsupported", f"不支持的图片格式（{shown}），请用 PNG / JPG / WebP")


def process_avatar(filename: str, raw: bytes) -> bytes:
    """校验 + 处理，返回可直接落盘的 PNG 字节"""
    validate_upload(filename, raw)

    try:
        img = Image.open(io.BytesIO(raw))
        img.load()   # 提前触发解码：截断/损坏的图在这里就报错，而不是 resize 时
    except (UnidentifiedImageError, OSError, ValueError) as e:
        raise AvatarError("decode_failed", "图片无法解析，请换一张试试") from e

    img = ImageOps.exif_transpose(img) or img    # 修正手机照片方向（旧版 Pillow 可能返回 None）
    img = center_square(img).resize((AVATAR_SIZE, AVATAR_SIZE), Image.Resampling.LANCZOS)
    if img.mode != "RGBA":
        img = img.convert("RGBA")

    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()


def center_square(img: Image.Image) -> Image.Image:
    """按短边做中心正方形裁剪（不做放大，小图交给 resize 处理）"""
    w, h = img.size
    side = min(w, h)
    left, top = (w - side) // 2, (h - side) // 2
    return img.crop((left, top, left + side, top + side))
