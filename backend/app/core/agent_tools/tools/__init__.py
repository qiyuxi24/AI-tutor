"""原生工具目录 —— **一个工具一个模块**（2026-09-15 自单文件 native.py 拆分）。

每个模块统一五段（新增/改动工具照抄）：
    ① DESCRIPTION  模型侧说明「这个工具做什么」（随 tools= 参数每轮发给模型）
    ② PARAMETERS   JSON Schema（与 MCP 的 inputSchema 同构）
    ③ GUIDANCE     提示词侧说明「何时用 / 何时别用」（进 system prompt 的工具指南段落）
    ④ handler      (args, kg) -> str，执行体；返回给模型看的文本
    ⑤ SPEC         _spec(name, DESCRIPTION, PARAMETERS, handler, guidance=GUIDANCE)

三条硬约定（详见 README.md）：
    - **永不抛异常**：失败返回给模型看的文本，业务错自己给可操作提示。
    - **handler 保持薄壳**：真实逻辑留在领域模块或本模块内部函数，handler 只搬参数。
    - **异步只在必要时**：需要工具内 `asyncio.create_task` 起后台任务（见 quiz_generate）才写 async def。

接入注册表 = 本文件加两行（import 模块 + 加进 NATIVE_SPECS）。
之后 `KG_TOOLS`、执行分发、提示词「工具调用指南」三份产物自动生效，不需要改其他文件。

文件命名 = 工具名（模型侧 name）：找 / 改 / 删一个工具只动一个文件。
共用的重型能力放包外（如 `../net_guard.py` 的 SSRF 防护）。
"""

# 工具模块（顺序无关；下面 NATIVE_SPECS 的显式顺序才是产物顺序）
# noqa: F401 ← 保留模块名可见：测试/脚本可直接用 tools.<name>.handler / tools.<name>.SPEC
from . import (
    add_edge,
    add_knowledge_node,
    delete_node,
    download_resource,
    fetch_webpage,
    grade_answer,
    quiz_generate,
    rag_search,
    update_mastery,
    update_node_content,
    update_user_profile,
)

# 顺序 = KG_TOOLS 与提示词「工具调用指南」段落的顺序。
# 分三组排列（图谱 → 画像/资料 → 教学检验）；改动只影响提示词 diff，无功能影响。
NATIVE_SPECS = [
    # 图谱工具
    add_knowledge_node.SPEC,
    update_node_content.SPEC,
    update_mastery.SPEC,
    add_edge.SPEC,
    delete_node.SPEC,
    # 画像 / 资料
    update_user_profile.SPEC,
    fetch_webpage.SPEC,
    download_resource.SPEC,
    rag_search.SPEC,
    # 教学检验
    quiz_generate.SPEC,
    grade_answer.SPEC,
]
