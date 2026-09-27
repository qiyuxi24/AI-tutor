"""提示词加载器：渲染唯一一套系统提示词模板。

2026-09-26：原三种引导模式（adaptive / free_talk / recursive）合并为一套最简提示词，
不再按 mode 分派模板。唯一模板 `system_prompt_common.j2` 包含：
  全局底线 + 框架约束({{ knowledge_graph_summary }}) + 当前位置 + 掌握度分档
  + 学生画像 + 教学方式({{ student_message }})

检索区块、工具能力说明、空图谱提示由调用方（`chat_service._build_system_prompt`）另行拼接。
"""
from jinja2 import Environment, FileSystemLoader
from pathlib import Path

# 找到项目根目录下的 data/prompts 文件夹
PROMPT_DIR = Path(__file__).parent.parent.parent.parent / "data" / "prompts"

# 创建 Jinja2 环境
env = Environment(loader=FileSystemLoader(str(PROMPT_DIR)))

# 唯一模板名
TEMPLATE = "system_prompt_common.j2"


def get_system_prompt(student_message: str, graph_summary: str = "",
                     user_profile: str = "", current_position: str = "") -> str:
    """
    生成完整的系统提示词。

    参数:
        student_message:  学生当前消息内容
        graph_summary:    知识图谱摘要文本（可选，由调用方构建后传入）
        user_profile:     用户画像 Markdown（可选，用于个性化教学）
        current_position: 学生当前所处位置（未指定时由调用方给出显式「未指定」文案）

    返回:
        渲染后的完整 system prompt 字符串
    """
    return env.get_template(TEMPLATE).render(
        student_message=student_message,
        knowledge_graph_summary=graph_summary,
        user_profile=user_profile,
        current_position=current_position,
    )
