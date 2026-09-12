"""
用户画像 —— 业务门面（外部唯一入口）。

UserProfile 把三层组合起来：存储（store）/ 结构（schema）/ Markdown（markdown），
并在实例内缓存一次读取结果——一次请求里画像常被读多次（提示词 + 检索模式），
实例内只落一次盘。
"""

from pathlib import Path
from typing import Optional

from .markdown import parse_markdown_to_data, render_profile_markdown
from .schema import (
    EDITABLE_SECTIONS,
    FIELD_WEIGHTS,
    TOTAL_WEIGHT,
    get_path,
    is_empty_profile,
    is_filled,
    merge_patch,
    new_note_id,
    now,
    set_path,
)
from .store import ProfileStore


class UserProfile:
    """单个用户的画像（实例用完即弃，不要跨请求/协程共享）"""

    def __init__(self, user_id: int, data_dir: Optional[Path] = None):
        self.user_id = user_id
        self._store = ProfileStore(user_id, data_dir)
        self._data: Optional[dict] = None

    @property
    def json_path(self) -> Path:
        """画像文件路径（测试与诊断用）"""
        return self._store.path

    # ---------- 读 ----------

    def exists(self) -> bool:
        """画像文件是否已存在"""
        return self._store.exists()

    def get(self) -> dict:
        """结构化画像数据（实例内缓存，首次读盘；纯读不落盘）"""
        if self._data is None:
            self._data = self._store.load()
        return self._data

    def to_markdown(self) -> str:
        """渲染完整 Markdown（前端预览用，始终渲染；空画像渲染为待填写模板）"""
        return render_profile_markdown(self.get())

    def get_summary(self) -> str:
        """注入 LLM 系统提示词的画像摘要。

        画像无任何实质内容时返回 ""，避免把"全字段待填写"的空模板塞进提示词白烧 token。
        """
        data = self.get()
        return "" if is_empty_profile(data) else render_profile_markdown(data)

    def get_usage_mode(self) -> str:
        """版权/用途模式（personal / commercial）；字段缺失或取值异常一律回退 personal"""
        prefs = self.get().get("preferences") or {}
        mode = prefs.get("usage_mode") or "personal"
        return mode if mode in ("personal", "commercial") else "personal"

    def get_completeness(self) -> dict:
        """完整度统计：{percent, filled, total, fields: {路径: 是否已填}}"""
        data = self.get()
        fields = {}
        filled = 0
        for path, weight in FIELD_WEIGHTS.items():
            done = is_filled(get_path(data, path))
            fields[path] = done
            if done:
                filled += weight
        return {
            "percent": round(filled / TOTAL_WEIGHT * 100) if TOTAL_WEIGHT else 0,
            "filled": filled,
            "total": TOTAL_WEIGHT,
            "fields": fields,
        }

    # ---------- 写 ----------

    def update_data(self, patch: dict) -> dict:
        """结构化更新：只覆盖 patch 中显式给出的字段。

        嵌套 dict（basic/learning/preferences）逐字段合并，未给出的字段保留原值；
        列表与字符串（goals/knowledge_background/ai_notes）整体替换。
        调用方若只想改某一个字段，用 PATCH + 部分字段即可（api 层用 exclude_unset 保留"未提交"语义）。
        """
        data = self.get()
        merge_patch(data, {k: patch[k] for k in EDITABLE_SECTIONS if k in patch})
        self._save(data)
        return data

    def update_field(self, path: str, value) -> dict:
        """更新单个字段（路径如 'basic.name' / 'goals' / 'knowledge_background'）"""
        if path not in FIELD_WEIGHTS:
            raise ValueError(f"未知画像字段：{path}")
        data = self.get()
        if path == "goals":
            # 兼容 str（按行拆分）与 list
            if isinstance(value, str):
                value = [v.strip() for v in value.splitlines() if v.strip()]
            else:
                value = [str(v).strip() for v in (value or []) if str(v).strip()]
        set_path(data, path, value)
        self._save(data)
        return data

    # ---------- AI 观察笔记 ----------

    def add_note(self, content: str, source: str = "ai") -> str:
        """追加一条观察笔记，返回笔记 id"""
        content = (content or "").strip()
        if not content:
            raise ValueError("观察笔记内容不能为空")
        note = {"id": new_note_id(), "content": content, "created_at": now()}
        if source == "ai":
            note["source"] = "ai"
        self.get().setdefault("ai_notes", []).append(note)
        self._save(self._data)
        return note["id"]

    def delete_note(self, note_id: str) -> bool:
        """删除一条观察笔记，返回是否删除成功"""
        notes = self.get().get("ai_notes", [])
        for i, note in enumerate(notes):
            if note.get("id") == note_id:
                del notes[i]
                self._save(self._data)
                return True
        return False

    # ---------- 兼容旧接口：Markdown 文本 ----------

    def update(self, content: str, mode: str = "replace") -> str:
        """以 Markdown 文本更新画像，返回渲染后的 Markdown。

        replace：全量解析替换（仅保留新文本能还原的内容）
        append ：解析出的新信息合并进现有画像（只补空缺字段 + 追加观察笔记）
        """
        incoming = parse_markdown_to_data(content, self.user_id)
        if mode != "append":
            self._save(incoming)
            return render_profile_markdown(incoming)

        current = self.get()
        # 基本信息/学习/偏好：仅当现有为空或占位时采用新值
        for section in ("basic", "learning", "preferences"):
            for k, v in incoming.get(section, {}).items():
                if is_filled(v) and not is_filled(current.get(section, {}).get(k)):
                    current.setdefault(section, {})[k] = v
        # 目标去重合并
        goals = list(current.get("goals", []))
        for g in incoming.get("goals", []):
            if g and g not in goals:
                goals.append(g)
        current["goals"] = goals
        # 知识背景：仅补空缺
        if is_filled(incoming.get("knowledge_background", "")) and \
                not is_filled(current.get("knowledge_background", "")):
            current["knowledge_background"] = incoming["knowledge_background"]
        # 观察笔记全部追加
        current.setdefault("ai_notes", []).extend(incoming.get("ai_notes", []))
        self._save(current)
        return render_profile_markdown(current)

    # ---------- 内部 ----------

    def _save(self, data: dict) -> None:
        """落盘并同步实例缓存（data 通常就是 get() 返回的同一对象）"""
        self._store.save(data)
        self._data = data
