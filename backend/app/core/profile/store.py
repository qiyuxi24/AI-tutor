"""
用户画像 —— 文件存储层。

每个用户一个 JSON 文件：data/profiles/{user_id}.json。
写入走"同目录临时文件 + os.replace"，避免进程中途崩溃留下半截 JSON 把画像写坏。

旧版 {user_id}.md 首次读取时自动解析迁移，原文件重命名保留为 .md.bak（可回滚）。
"""

import json
import os
from pathlib import Path
from typing import Optional

from .markdown import parse_markdown_to_data
from .schema import PROFILE_SCHEMA_VERSION, default_profile_data, merge_patch, now


def default_data_dir() -> Path:
    """画像目录：项目根 data/profiles（本文件 → 上溯 4 层 = 项目根）"""
    return Path(__file__).resolve().parents[4] / "data" / "profiles"


class ProfileStore:
    """画像 JSON 读写（不含业务逻辑，不知道笔记/完整度等概念）"""

    def __init__(self, user_id: int, data_dir: Optional[Path] = None):
        self.user_id = user_id
        self.dir = Path(data_dir) if data_dir is not None else default_data_dir()
        self.path = self.dir / f"{user_id}.json"
        self.legacy_path = self.dir / f"{user_id}.md"

    def exists(self) -> bool:
        """画像是否已存在（含待迁移的旧版 MD）"""
        return self.path.exists() or self.legacy_path.exists()

    def load(self) -> dict:
        """读取结构化画像；文件缺失/损坏时返回默认结构（纯读，不落盘）。

        旧 JSON（version<2）结构接近，直接并入默认结构补全缺失字段即可。
        """
        raw = self._read_json()
        if raw is None and self.legacy_path.exists():
            migrated = self._migrate_from_md()
            if migrated is not None:
                return migrated
        data = merge_patch(default_profile_data(self.user_id), raw or {})
        data["version"] = PROFILE_SCHEMA_VERSION
        return data

    def save(self, data: dict) -> None:
        """原子写回，元字段（version/user_id/updated_at）统一在此校正"""
        data["version"] = PROFILE_SCHEMA_VERSION
        data["user_id"] = self.user_id
        data["updated_at"] = now()
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f"{self.path.name}.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)   # 同目录替换，POSIX/Windows 均为原子操作

    # ---------- 内部 ----------

    def _read_json(self) -> Optional[dict]:
        """读取 JSON；文件缺失/损坏/不是对象时返回 None（调用方走默认结构兜底）"""
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        return raw if isinstance(raw, dict) else None

    def _migrate_from_md(self) -> Optional[dict]:
        """旧版 Markdown 画像 → 结构化并落盘；原文件备份为 .md.bak"""
        try:
            md = self.legacy_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None
        data = parse_markdown_to_data(md, self.user_id)
        self.save(data)
        try:
            self.legacy_path.rename(self.dir / f"{self.user_id}.md.bak")
        except OSError:
            pass
        return data
