"""工具 `update_user_profile` —— 把一次教学观察追加到学生画像。

落点在 `core/profile/`（分层包，公开入口 `UserProfile.add_note`）。
本工具追加的是画像里的「AI 教学笔记」分区，每次一条带时间戳的观察。

⚠️ 提示词要点：必须写"与已有笔记重复的不要再记" —— 否则模型每轮都会记一遍
"学生喜欢图形化解释"，画像被灌水。
"""

from ..registry import _spec

DESCRIPTION = ("更新学生用户画像。当你在教学中观察到学生的性格特点、学习习惯、知识薄弱点等新信息时，"
               "应主动更新用户画像，以便后续更好地个性化教学。每次调用会作为一条带时间戳的观察笔记"
               "记录到画像的「AI 教学笔记」部分；若本次观察与已有笔记内容重复，则无需再次记录。")

PARAMETERS = {
    "type": "object",
    "properties": {"content": {"type": "string", "description": "本次观察到的学生信息（一句话到一小段均可），如：学习风格、知识薄弱点、性格特点、偏好等。避免重复记录已有内容。"}},
    "required": ["content"],
}

GUIDANCE = """
把一次教学观察追加到学生画像（一条带时间戳的「AI 教学笔记」）。
- **何时用**：观察到学生的性格特点、学习习惯、知识薄弱点等新信息时**主动**记录，
  例如"害怕数学公式""喜欢图形化解释""做题容易粗心"。
- 每次写一条，一句话到一小段均可；与已有笔记重复的内容不要再记。
"""


def handler(args, kg) -> str:
    from app.core.profile import UserProfile
    note_id = UserProfile(user_id=kg.user_id).add_note(args["content"], source="ai")
    return f"已为用户画像新增观察笔记（{note_id}）"


SPEC = _spec("update_user_profile", DESCRIPTION, PARAMETERS, handler, guidance=GUIDANCE)
