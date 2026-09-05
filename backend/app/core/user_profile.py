"""
用户画像核心模块（结构化版 v2）

每个用户对应一个 JSON 文件（data/profiles/{user_id}.json），
内部是结构化字段（基本信息 / 学习目标 / 知识背景 / 学习偏好 / AI 观察笔记），
避免旧版自由 Markdown 造成的字段重复、内容堆积问题。

设计要点：
1. 存储即结构化 —— AI 与前端都基于字段读写，不再依赖 Markdown 文本解析。
2. 保留 Markdown 渲染 —— 注入系统提示词 / 前端预览时渲染为统一模板，AI 视角不变。
3. 兼容迁移 —— 首次访问时若发现旧版 {user_id}.md，自动解析迁移到 JSON（原文件保留为 .md.bak）。

对外保留兼容接口：
    get()          → dict（结构化数据）
    get_summary()  → str（渲染后的 Markdown，注入系统提示词用）
    update(...)    → 兼容旧 API（接收 Markdown 文本）
"""

import json
import re
import uuid
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Optional

# ================================================================
# 常量
# ================================================================

PROFILE_SCHEMA_VERSION = 2

# 字段权重（用于完整度统计）：{路径: 权重}
FIELD_WEIGHTS = {
    "basic.name": 1,
    "basic.age": 1,
    "basic.stage": 1,
    "goals": 2,
    "knowledge_background": 2,
    "learning.pace": 1,
    "learning.weekly_hours": 1,
    "preferences.personality": 1,
    "preferences.teaching_style_like": 1,
    "preferences.teaching_style_avoid": 1,
    # usage_mode 是版权合规开关（personal/commercial），需进白名单供 update_field 更新，
    # 但 weight=0：不计入画像完整度
    "preferences.usage_mode": 0,
}
TOTAL_WEIGHT = sum(FIELD_WEIGHTS.values())

# 旧版默认模板（用于识别"待填写"占位值）
_PLACEHOLDER_RE = re.compile(r"^\s*[（(]\s*待")

# 模板说明文字（不应作为观察笔记）
_TEMPLATE_FOOTER_MARKERS = ("以下是 AI 在教学中观察到的内容",)

# 旧版 MD 解析：`- **字段名**：值`
_FIELD_LINE_RE = re.compile(r"^\s*[-*]\s*\*\*([^*]+)\*\*\s*[:：]\s*(.*)$")

# 旧版字段名 → 结构化路径（多级用 . 分隔）
_MD_FIELD_MAP = {
    "姓名/昵称": "basic.name",
    "年龄": "basic.age",
    "年级/阶段": "basic.stage",
    "当前学习目标": "goals",
    "学习目标": "goals",
    "知识背景": "knowledge_background",
    "学习节奏偏好": "learning.pace",
    "每周学习时间": "learning.weekly_hours",
    "性格特点": "preferences.personality",
    "喜欢的教学方式": "preferences.teaching_style_like",
    "需要避免的方式": "preferences.teaching_style_avoid",
}

# 区块标题中属于"观察笔记"的标题（其余标题内容合并为一条额外笔记）
_NOTE_SECTION_TITLES = ("AI 教学笔记", "AI 教学记录", "AI 观察记录")

# ================================================================
# 工具函数
# ================================================================


def _now() -> str:
    """当前时间 ISO 字符串（秒级精度）"""
    return datetime.now().isoformat(timespec="seconds")


def _is_filled(value) -> bool:
    """判断字段是否已填写（去掉占位符/空白后非空）"""
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return False
        return not _PLACEHOLDER_RE.match(text)
    if isinstance(value, list):
        return any(_is_filled(v) for v in value)
    return value is not None


def _set_path(data: dict, path: str, value):
    """按 'a.b.c' 路径写入值（自动创建中间 dict / 过滤空值）"""
    parts = path.split(".")
    cur = data
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    cur[parts[-1]] = value


def _get_path(data: dict, path: str, default=None):
    cur = data
    for p in path.split("."):
        if not isinstance(cur, dict) or p not in cur:
            return default
        cur = cur[p]
    return cur


def _new_note_id() -> str:
    return f"n_{uuid.uuid4().hex[:10]}"


# ================================================================
# 默认结构化数据
# ================================================================

def default_profile_data(user_id: int) -> dict:
    """返回一份全新的默认结构化画像"""
    return {
        "version": PROFILE_SCHEMA_VERSION,
        "user_id": user_id,
        "created_at": _now(),
        "updated_at": _now(),
        "basic": {"name": "", "age": "", "stage": ""},
        "goals": [],
        "knowledge_background": "",
        "learning": {"pace": "", "weekly_hours": ""},
        "preferences": {
            "personality": "",
            "teaching_style_like": "",
            "teaching_style_avoid": "",
            "usage_mode": "personal",   # personal / commercial（版权双模式，采集合规）
        },
        "ai_notes": [],
    }


def _merge_with_defaults(base: dict, raw: dict) -> dict:
    """将历史数据并入默认结构：子 dict 字段级合并（保留 base 新增字段的默认值），其余整体覆盖"""
    merged = dict(base)
    for k, v in raw.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k] = {**merged[k], **v}
        else:
            merged[k] = v
    return merged


# ================================================================
# Markdown 解析（旧版迁移）与渲染
# ================================================================

def parse_markdown_to_data(md: str, user_id: int) -> dict:
    """
    将旧版自由 Markdown 画像解析为结构化数据。

    - 按 `## ` 区块分组，区块内匹配 `- **字段名**：值` 行
    - AI 教学笔记区块的 blockquote 与段落拆分为独立观察笔记
    - 无法识别的散落内容收集到 extra_notes
    """
    data = default_profile_data(user_id)
    notes = []

    # 按 ## 标题切块
    blocks: list[tuple[str, str]] = []   # (title, body)
    current_title = ""
    current_lines: list[str] = []
    for line in md.splitlines():
        if line.startswith("## "):
            if current_title:
                blocks.append((current_title, "\n".join(current_lines)))
            current_title = line[3:].strip()
            current_lines = []
        else:
            current_lines.append(line)
    if current_title:
        blocks.append((current_title, "\n".join(current_lines)))

    for title, body in blocks:
        is_note_section = title in _NOTE_SECTION_TITLES
        chunk_lines: list[str] = []
        for line in body.splitlines():
            # 1) 字段行（任意区块内都能识别，值非占位才写入）
            m = _FIELD_LINE_RE.match(line)
            if m:
                field_name, value = m.group(1).strip(), m.group(2).strip()
                path = _MD_FIELD_MAP.get(field_name)
                if path and _is_filled(value):
                    if path == "goals":
                        if value not in data["goals"]:
                            data["goals"].append(value)
                    else:
                        _set_path(data, path, value)
                    continue
            # 2) 非字段行：
            #    - 笔记区块 → 收集为观察笔记
            #    - 其他区块 → 忽略（未知区块整体并入额外笔记）
            if not is_note_section:
                continue
            stripped = line.strip()
            if stripped.startswith(">"):
                stripped = stripped.lstrip(">").strip()
            if not stripped:
                if chunk_lines:
                    notes.append(" ".join(chunk_lines))
                    chunk_lines = []
                continue
            chunk_lines.append(stripped)
        # 区块收尾：剩余笔记 / 未知区块整体并入额外笔记
        if is_note_section:
            if chunk_lines:
                notes.append(" ".join(chunk_lines))
        elif title not in ("基本信息", "学习状态", "性格与偏好"):
            extra = "\n".join(l for l in body.splitlines() if l.strip())
            if extra.strip():
                notes.append(extra.strip())

    for content in notes:
        content = content.strip()
        if not content or _PLACEHOLDER_RE.match(content):
            continue
        if any(content.startswith(marker) for marker in _TEMPLATE_FOOTER_MARKERS):
            continue
        data["ai_notes"].append({
            "id": _new_note_id(),
            "content": content,
            "created_at": _now(),
        })
    return data


def render_profile_markdown(data: dict) -> str:
    """将结构化画像渲染为统一 Markdown（供 AI 提示词注入与前端预览）"""
    basic = data.get("basic", {})
    learning = data.get("learning", {})
    prefs = data.get("preferences", {})
    goals = data.get("goals", [])

    def field(value, label):
        if not _is_filled(value):
            return f"- **{label}**：（待填写）"
        return f"- **{label}**：{value}"

    lines = ["# 用户画像", ""]
    lines.append("## 基本信息")
    lines.append(field(basic.get("name", ""), "姓名/昵称"))
    lines.append(field(basic.get("age", ""), "年龄"))
    lines.append(field(basic.get("stage", ""), "年级/阶段"))
    lines.append("")

    lines.append("## 学习状态")
    if goals:
        lines.append("- **当前学习目标**：")
        lines.extend(f"  - {g}" for g in goals)
    else:
        lines.append("- **当前学习目标**：（待填写，如：备战高考数学、学习 Python 编程）")
    lines.append(field(data.get("knowledge_background", ""), "知识背景"))
    lines.append(field(learning.get("pace", ""), "学习节奏偏好"))
    lines.append(field(learning.get("weekly_hours", ""), "每周学习时间"))
    lines.append("")

    lines.append("## 性格与偏好")
    lines.append(field(prefs.get("personality", ""), "性格特点"))
    lines.append(field(prefs.get("teaching_style_like", ""), "喜欢的教学方式"))
    lines.append(field(prefs.get("teaching_style_avoid", ""), "需要避免的方式"))
    lines.append("")

    lines.append("## AI 教学笔记")
    notes = data.get("ai_notes", [])
    if not notes:
        lines.append("> 以下是 AI 在教学中观察到的内容，可随时更新。")
        lines.append(">")
        lines.append("> （待 AI 填写，如：该学生对概念理解较快但做题容易粗心，建议多出小练习检验）")
    else:
        lines.append("> 以下是 AI 在教学中观察到的内容，可随时更新。")
        for note in notes:
            ts = (note.get("created_at") or "")[:10]
            content = (note.get("content") or "").replace("\n", " ").strip()
            lines.append(f"- ({ts}) {content}")
    lines.append("")

    return "\n".join(lines)


# ================================================================
# UserProfile 管理器
# ================================================================

class UserProfile:
    """用户画像管理器（每个用户一个 JSON 文件，自动迁移旧版 MD）"""

    def __init__(self, user_id: int, data_dir: Optional[Path] = None):
        if data_dir is None:
            data_dir = Path(__file__).parent.parent.parent.parent / "data" / "profiles"
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.user_id = user_id
        self.json_path = self.data_dir / f"{user_id}.json"
        self.md_path = self.data_dir / f"{user_id}.md"

    # ---------- 底层读写 ----------

    def exists(self) -> bool:
        """是否已存在画像（JSON 或待迁移的旧 MD）"""
        return self.json_path.exists() or self.md_path.exists()

    def _load_data(self) -> dict:
        """读取结构化数据（不存在时自动迁移/初始化）"""
        if self.json_path.exists():
            try:
                raw = json.loads(self.json_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict) and raw.get("version", 1) >= PROFILE_SCHEMA_VERSION:
                    # 补全缺失字段，保证结构完整（子 dict 字段级合并，旧 JSON 缺新字段自动补默认）
                    return _merge_with_defaults(default_profile_data(self.user_id), deepcopy(raw))
                # 版本过旧：尝试作为旧结构迁移
                return self._migrate_from_md(raw)
            except (json.JSONDecodeError, OSError):
                pass

        if self.md_path.exists():
            return self._migrate_from_md()

        data = default_profile_data(self.user_id)
        self._save_data(data)
        return data

    def _save_data(self, data: dict) -> None:
        """保存结构化数据到 JSON"""
        data["version"] = PROFILE_SCHEMA_VERSION
        data["user_id"] = self.user_id
        data["updated_at"] = _now()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.json_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _migrate_from_md(self, legacy: Optional[dict] = None) -> dict:
        """
        迁移旧数据到结构化格式：
        - legacy dict：旧 JSON 结构（v1）
        - None：读取 {user_id}.md 解析
        迁移成功后原 MD 文件重命名为 .md.bak（不删除，可回滚）。
        """
        if legacy is not None and isinstance(legacy, dict):
            # 旧 JSON v1：结构接近新版，并入默认结构补全缺失字段
            data = _merge_with_defaults(default_profile_data(self.user_id), deepcopy(legacy))
            self._save_data(data)
            return data

        if self.md_path.exists():
            try:
                md = self.md_path.read_text(encoding="utf-8")
                data = parse_markdown_to_data(md, self.user_id)
                self._save_data(data)
                # 原文件保留为备份
                try:
                    self.md_path.rename(self.data_dir / f"{self.user_id}.md.bak")
                except OSError:
                    pass
                return data
            except (OSError, UnicodeDecodeError):
                pass

        data = default_profile_data(self.user_id)
        self._save_data(data)
        return data

    # ---------- 对外接口（结构化） ----------

    def get(self) -> dict:
        """获取结构化画像数据（dict）"""
        return self._load_data()

    def to_dict(self) -> dict:
        """别名：获取结构化数据"""
        return self.get()

    def get_summary(self) -> str:
        """
        获取用户画像的 Markdown 摘要（用于注入 AI 系统提示词）。
        画像不存在时返回空字符串。
        """
        if not self.exists():
            return ""
        return render_profile_markdown(self.get())

    def to_markdown(self) -> str:
        """渲染完整 Markdown（前端预览用）"""
        return render_profile_markdown(self.get())

    def get_usage_mode(self) -> str:
        """
        读取版权/用途模式（preferences.usage_mode，个人/商用）。

        画像不存在或字段缺失时返回默认 "personal"（不抛错、不建文件，
        B1.5 落地前该字段本就缺省为 personal）。
        """
        if not self.exists():
            return "personal"
        try:
            prefs = self.get().get("preferences", {}) or {}
            mode = prefs.get("usage_mode") or "personal"
        except Exception:
            return "personal"
        return mode if mode in ("personal", "commercial") else "personal"

    def get_completeness(self) -> dict:
        """
        画像完整度统计：
        {percent, filled, total, fields: {路径: 是否已填}}
        """
        data = self.get()
        filled = 0
        fields = {}
        for path, weight in FIELD_WEIGHTS.items():
            value = _get_path(data, path)
            done = _is_filled(value)
            fields[path] = done
            if done:
                filled += weight
        return {
            "percent": round(filled / TOTAL_WEIGHT * 100) if TOTAL_WEIGHT else 0,
            "filled": filled,
            "total": TOTAL_WEIGHT,
            "fields": fields,
        }

    # ---------- 结构化字段更新 ----------

    def update_data(self, data: dict) -> dict:
        """全量结构化更新（保留 AI 观察笔记，字段缺失时保留原值）"""
        current = self.get()
        for key in ("basic", "learning", "preferences", "goals",
                    "knowledge_background", "ai_notes"):
            if key in data:
                current[key] = deepcopy(data[key])
        current.setdefault("basic", {})
        current.setdefault("learning", {})
        current.setdefault("preferences", {})
        current.setdefault("goals", [])
        current.setdefault("ai_notes", [])
        self._save_data(current)
        return current

    def update_field(self, path: str, value) -> dict:
        """更新单个字段（路径如 'basic.name' / 'goals' / 'knowledge_background'）"""
        data = self.get()
        if path not in FIELD_WEIGHTS:
            raise ValueError(f"未知画像字段：{path}")
        if path == "goals":
            # 支持 str（按行拆分）或 list
            if isinstance(value, str):
                value = [v.strip() for v in value.splitlines() if v.strip()]
            else:
                value = [str(v).strip() for v in (value or []) if str(v).strip()]
        _set_path(data, path, value)
        self._save_data(data)
        return data

    # ---------- AI 观察笔记 ----------

    def add_note(self, content: str, source: str = "ai") -> str:
        """添加一条 AI 观察笔记，返回笔记 id"""
        content = content.strip()
        if not content:
            raise ValueError("观察笔记内容不能为空")
        data = self.get()
        note = {
            "id": _new_note_id(),
            "content": content,
            "created_at": _now(),
        }
        if source == "ai":
            note["source"] = "ai"
        data.setdefault("ai_notes", []).append(note)
        self._save_data(data)
        return note["id"]

    def delete_note(self, note_id: str) -> bool:
        """删除一条观察笔记，返回是否删除成功"""
        data = self.get()
        notes = data.get("ai_notes", [])
        for i, note in enumerate(notes):
            if note.get("id") == note_id:
                del notes[i]
                self._save_data(data)
                return True
        return False

    # ---------- 兼容旧接口 ----------

    def save(self, content: str) -> None:
        """兼容：保存 Markdown 文本（解析回结构化）"""
        data = parse_markdown_to_data(content, self.user_id)
        self._save_data(data)

    def update(self, content: str, mode: str = "replace") -> str:
        """
        兼容旧接口：以 Markdown 文本更新画像。
        - replace：全量解析替换（但保留已有 ai_notes 中无法从文本还原的部分？）
                  为保持与旧语义一致，replace 直接全量替换。
        - append：解析出的新信息合并进现有结构化数据。
        返回: 渲染后的 Markdown
        """
        if mode == "append":
            incoming = parse_markdown_to_data(content, self.user_id)
            current = self.get()
            # 合并基本信息/学习/偏好：仅当现有为空或占位时采用新值
            for section in ("basic", "learning", "preferences"):
                for k, v in incoming.get(section, {}).items():
                    if _is_filled(v) and not _is_filled(current.get(section, {}).get(k)):
                        current.setdefault(section, {})[k] = v
            # 合并目标（去重）
            current_goals = list(current.get("goals", []))
            for g in incoming.get("goals", []):
                if g and g not in current_goals:
                    current_goals.append(g)
            current["goals"] = current_goals
            # 知识背景
            if _is_filled(incoming.get("knowledge_background", "")) and \
                    not _is_filled(current.get("knowledge_background", "")):
                current["knowledge_background"] = incoming["knowledge_background"]
            # AI 笔记全部追加
            current.setdefault("ai_notes", []).extend(incoming.get("ai_notes", []))
            self._save_data(current)
            return render_profile_markdown(current)
        else:
            data = parse_markdown_to_data(content, self.user_id)
            self._save_data(data)
            return render_profile_markdown(data)
