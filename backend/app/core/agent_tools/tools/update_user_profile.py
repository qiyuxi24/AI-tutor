"""工具 `update_user_profile` —— 维护学生画像的两条写入路径（**薄壳**）。

1. **结构化字段**（`field` + `value`）：把学生**明确说出**的稳定事实（姓名/年级/目标/知识背景/偏好）
   写进画像对应字段 —— 这是"AI 观察到 → 结构化画像 → 下一轮注入系统提示词"闭环的写入端。
2. **观察笔记**（`content`）：教学中**推断**出的特点追加一条带时间戳的笔记。

本文件只做三件事：参数校验（模型入参是信任边界）、调 `UserProfile` 门面、拼给模型看的回执。
画像语义（可写字段白名单 / goals 追加合并 / 笔记去重）全部在画像包内
（`AI_WRITABLE_FIELDS`、`UserProfile.append_goals` / `has_note`），本工具不复制第二份。

⚠️ 两条提示词要点（去掉就会被灌水/写错）：
- 结构化字段只填学生**明说**的事实，凭猜测推断的一律写笔记；
- 与已有笔记重复的不要再记（软约束 + 门面逐字去重兜底）。
"""

from ..registry import _spec
from app.core.profile import AI_WRITABLE_FIELDS, UserProfile

DESCRIPTION = (
    "更新学生用户画像。两种写入：① 学生明确说出的稳定事实（姓名/年级/学习目标/知识背景/学习偏好）"
    "写进结构化字段；② 教学观察或推断出的特点追加为一条带时间戳的笔记。"
    "与已有笔记重复的内容不要重复记录。"
)

PARAMETERS = {
    "type": "object",
    "properties": {
        "field": {
            "type": "string",
            "enum": list(AI_WRITABLE_FIELDS),
            "description": "要写入的结构化画像字段，须与 value 配对使用。",
        },
        "value": {
            "type": "string",
            "description": "字段值。goals 只写新目标即可，会自动与已有目标合并去重。",
        },
        "content": {
            "type": "string",
            "description": "一条教学观察笔记（推断出的性格/习惯/薄弱点），与已有内容重复的不要写。",
        },
    },
}

GUIDANCE = """
维护学生画像：**学生明确说出的稳定事实写结构化字段，教学中推断出的特点写观察笔记**。

结构化字段（`field` + `value`），共 10 个：
- `basic.name` 姓名/昵称 · `basic.age` 年龄 · `basic.stage` 年级/阶段
- `goals` 学习目标 · `knowledge_background` 知识背景
- `learning.pace` 学习节奏 · `learning.weekly_hours` 每周学习时间
- `preferences.personality` 性格 · `preferences.teaching_style_like` 喜欢的教学方式 ·
  `preferences.teaching_style_avoid` 需要避免的方式

- **何时用**：学生说出"我要考研数学""我大二""我基础还行""别催我、喜欢慢慢讲"这类**事实**时，
  立刻写进对应字段（本工具**免确认**，直接调用）。画像会在下一轮注入你的上下文，写进去下次就记得。
- **何时别用**（写下限，务必遵守）：
  ① 不要凭猜测填结构化字段 —— "他做题快"是你推断的，不是他说的，这类写 `content` 笔记；
  ② 与已有笔记内容重复的不要再记；③ 一次只写一个字段，不要为了"填满画像"而凑数。
"""


def handler(args, kg) -> str:
    profile = UserProfile(user_id=kg.user_id)
    done = []

    field = str(args.get("field") or "").strip()
    value = args.get("value")
    if field:
        if field not in AI_WRITABLE_FIELDS:
            return f"操作失败: 不支持写入画像字段 {field}（可写：{'、'.join(AI_WRITABLE_FIELDS)}）"
        if value is None or not str(value).strip():
            return "操作失败: 更新结构化字段时必须同时给出 field 与 value"
        if field == "goals":
            profile.append_goals(value)
        else:
            profile.update_field(field, value)
        done.append(f"已更新画像字段 {field}")

    content = str(args.get("content") or "").strip()
    if content:
        if profile.has_note(content):
            done.append("该观察笔记已存在，未重复记录")
        else:
            done.append(f"已新增观察笔记（{profile.add_note(content, source='ai')}）")

    if not done:
        return "操作失败: 至少需要 field+value（结构化字段）或 content（观察笔记）之一"
    return "；".join(done)


SPEC = _spec("update_user_profile", DESCRIPTION, PARAMETERS, handler, guidance=GUIDANCE)
