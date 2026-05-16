"""提示词加载器：从文件加载 Jinja2 模板并渲染"""
from jinja2 import Environment, FileSystemLoader
from pathlib import Path

# 找到项目根目录下的 data/prompts 文件夹
PROMPT_DIR = Path(__file__).parent.parent.parent.parent / "data" / "prompts"

# 创建 Jinja2 环境
env = Environment(loader=FileSystemLoader(str(PROMPT_DIR)))

# 模式与模板文件名的映射
MODE_TEMPLATE_MAP = {
    "scaffolding": "system_prompt_scaffolding.j2",
    "think_first": "system_prompt_think_first.j2",
    "reverse_teaching": "system_prompt_reverse_teaching.j2",
}

def get_system_prompt(mode: str, student_message: str) -> str:
    """根据模式和用户消息，生成最终的提示词"""
    template_name = MODE_TEMPLATE_MAP.get(mode)
    if not template_name:
        raise ValueError(f"未知引导模式：{mode}")
    
    template = env.get_template(template_name)
    return template.render(student_message=student_message)
