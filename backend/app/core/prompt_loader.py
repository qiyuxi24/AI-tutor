"""提示词加载器：从文件加载 Jinja2 模板并渲染

组装逻辑：
  1. 先渲染通用模板 system_prompt_common.j2（含知识图谱摘要占位符）
  2. 再渲染模式模板（含学生消息占位符）
  3. 拼接返回完整系统提示词

递归模式（recursive）：
  额外需要 knowledge_graph_framework 和 current_node 两个参数，
  它们不通过通用模板渲染，而是直接注入模式模板。
"""
from jinja2 import Environment, FileSystemLoader
from pathlib import Path

# 找到项目根目录下的 data/prompts 文件夹
PROMPT_DIR = Path(__file__).parent.parent.parent.parent / "data" / "prompts"

# 创建 Jinja2 环境
env = Environment(loader=FileSystemLoader(str(PROMPT_DIR)))

# 模式与模板文件名的映射
MODE_TEMPLATE_MAP = {
    "adaptive": "system_prompt_adaptive.j2",
    "free_talk": "system_prompt_free_talk.j2",
    "recursive": "system_prompt_recursive.j2",
}

# 通用模板名
COMMON_TEMPLATE = "system_prompt_common.j2"


def get_system_prompt(mode: str, student_message: str, graph_summary: str = "",
                     user_profile: str = "", **extra_kwargs) -> str:
    """
    根据模式、用户消息、图谱摘要和用户画像，生成最终的完整系统提示词。

    参数:
        mode:            引导模式（adaptive / free_talk / recursive）
        student_message: 学生当前消息内容
        graph_summary:   知识图谱摘要文本（可选，由调用方构建后传入）
        user_profile:    用户画像 Markdown（可选，用于个性化教学）
        **extra_kwargs:  额外参数（递归模式需要 current_node, knowledge_graph_framework）

    返回:
        拼接后的完整 system prompt 字符串

    抛出:
        ValueError: 未知的引导模式
    """
    template_name = MODE_TEMPLATE_MAP.get(mode)
    if not template_name:
        raise ValueError(f"未知引导模式：{mode}")

    # 1. 渲染通用模板（含知识图谱摘要 + 用户画像）
    common = env.get_template(COMMON_TEMPLATE).render(
        knowledge_graph_summary=graph_summary,
        user_profile=user_profile,
    )

    # 2. 渲染模式模板（含学生消息 + 额外参数）
    render_kwargs = {"student_message": student_message}
    render_kwargs.update(extra_kwargs)

    mode_prompt = env.get_template(template_name).render(**render_kwargs)

    return common + "\n\n" + mode_prompt
