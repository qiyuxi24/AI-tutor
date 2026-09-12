"""
用户画像 —— 与 Markdown 的互转。

两个方向、两个用途：
  render_profile_markdown  结构化 → Markdown（注入系统提示词 / 前端预览）
  parse_markdown_to_data   Markdown → 结构化（旧版画像文件迁移、Markdown 高级编辑）

渲染与解析共用同一套字段命名（见 schema.FIELD_WEIGHTS），互为逆操作的地方要保持同步。
"""

import re

from .schema import default_profile_data, is_filled, new_note_id, now, set_path

# 旧版模板中的字段行：`- **字段名**：值`
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

# 属于"观察笔记"的区块标题（其余标题的内容整体并入额外笔记）
_NOTE_SECTION_TITLES = ("AI 教学笔记", "AI 教学记录", "AI 观察记录")

# 已知的结构化区块标题（非笔记且已知时，区块内容不再当笔记收集）
_KNOWN_SECTION_TITLES = ("基本信息", "学习状态", "性格与偏好")

# 模板说明文字，不应作为观察笔记
_TEMPLATE_FOOTER_MARKERS = ("以下是 AI 在教学中观察到的内容",)


def parse_markdown_to_data(md: str, user_id: int) -> dict:
    """
    将旧版自由 Markdown 画像解析为结构化数据。

    - 按 `## ` 区块分组，区块内匹配 `- **字段名**：值` 行
    - AI 教学笔记区块的 blockquote 与段落拆分为独立观察笔记
    - 无法识别的散落内容收集为额外笔记
    """
    data = default_profile_data(user_id)
    notes: list[str] = []

    # 按 ## 标题切块
    blocks: list[tuple[str, str]] = []
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
                if path and is_filled(value):
                    if path == "goals":
                        if value not in data["goals"]:
                            data["goals"].append(value)
                    else:
                        set_path(data, path, value)
                    continue
            # 2) 非字段行：笔记区块 → 收集为观察笔记；其他区块 → 忽略
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
        elif title not in _KNOWN_SECTION_TITLES:
            extra = "\n".join(l for l in body.splitlines() if l.strip())
            if extra.strip():
                notes.append(extra.strip())

    for content in notes:
        content = content.strip()
        if not is_filled(content):
            continue
        if any(content.startswith(marker) for marker in _TEMPLATE_FOOTER_MARKERS):
            continue
        data["ai_notes"].append({
            "id": new_note_id(),
            "content": content,
            "created_at": now(),
        })
    return data


def render_profile_markdown(data: dict) -> str:
    """将结构化画像渲染为统一 Markdown（供 AI 提示词注入与前端预览）"""
    basic = data.get("basic", {})
    learning = data.get("learning", {})
    prefs = data.get("preferences", {})
    goals = data.get("goals", [])

    def field(value, label):
        if not is_filled(value):
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
    lines.append("> 以下是 AI 在教学中观察到的内容，可随时更新。")
    if not notes:
        lines.append(">")
        lines.append("> （待 AI 填写，如：该学生对概念理解较快但做题容易粗心，建议多出小练习检验）")
    else:
        for note in notes:
            ts = (note.get("created_at") or "")[:10]
            content = (note.get("content") or "").replace("\n", " ").strip()
            lines.append(f"- ({ts}) {content}")
    lines.append("")

    return "\n".join(lines)
