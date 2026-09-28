"""用户头像 —— 文件存储层。

每个用户一张头像图片：`data/profiles/avatars/{user_id}.png`（覆盖写，无历史版本）。

为什么落在 `data/profiles/` 这个数据根下：
    docker-compose 已经挂了 `./data/profiles:/app/data/profiles`，头像落在卷内
    → 零 compose 改动就能持久化。另起一个 `data/avatars/` 就得再加一条卷，漏了就是
    容器一重建头像全丢。

为什么单独放文件、不塞进画像 JSON（`{user_id}.json`）：
    画像会被 `UserProfile.get_summary()` 渲染成 Markdown **注入系统提示词**；二进制/base64
    大字段既白烧 token，又多一份泄漏面。且 `PATCH /profile` 是全量覆盖语义，来回传大字段不划算。

本模块只管「字节进、字节出」，不碰任何图片处理（那是 `avatar_image.py` 的事）。
"""

import os
from pathlib import Path
from typing import Optional

from .store import default_data_dir

#: 头像子目录名（相对画像目录）
AVATAR_DIRNAME = "avatars"
#: 统一存 PNG：处理管线已把任何输入转成 RGBA PNG，扩展名固定便于猜缓存
AVATAR_SUFFIX = ".png"


def avatar_dir(data_dir: Optional[Path] = None) -> Path:
    """头像目录：`<画像目录>/avatars`（默认 `data/profiles/avatars`）"""
    base = Path(data_dir) if data_dir is not None else default_data_dir()
    return base / AVATAR_DIRNAME


class AvatarStore:
    """单用户头像文件读写（实例用完即弃，不跨请求共享）"""

    def __init__(self, user_id: int, data_dir: Optional[Path] = None):
        self.user_id = user_id
        self.dir = avatar_dir(data_dir)
        self.path = self.dir / f"{user_id}{AVATAR_SUFFIX}"

    def exists(self) -> bool:
        return self.path.is_file()

    def read(self) -> Optional[bytes]:
        """读出头像字节；不存在或不可读返回 None（调用方按"没有头像"处理）"""
        try:
            return self.path.read_bytes()
        except OSError:
            return None

    def save(self, raw: bytes) -> int:
        """原子写入（同目录临时文件 + os.replace），返回写入字节数。

        与 `store.ProfileStore.save` 同一手法：中途崩溃不会留下半截文件被当成有效头像。
        """
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f"{self.path.name}.tmp")
        tmp.write_bytes(raw)
        os.replace(tmp, self.path)
        return len(raw)

    def delete(self) -> bool:
        """删除头像；返回是否真的删掉了一份（不存在返回 False，但也算成功）"""
        try:
            self.path.unlink()
            return True
        except OSError:
            return False

    def signature(self) -> str:
        """`{mtime_ns}-{size}`，用作 ETag 素材；文件不可读时给个恒定值"""
        try:
            st = self.path.stat()
        except OSError:
            return "0-0"
        return f"{st.st_mtime_ns}-{st.st_size}"
