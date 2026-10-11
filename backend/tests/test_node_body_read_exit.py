"""节点正文读取唯一出口守卫（A4-1 / P1-3）。

不变量（AGENTS.md §2）：**读**节点正文一律走 `KnowledgeGraph.node_content_text`（小节优先）。
绕过它直读 `nodes_dir/{id}.md` 会拿到骨架占位「待完善...」或空串 —— 小节化节点没有主 MD
（设计 D1），正文只在小节 MD 里；图谱上下文注入 / RAG 索引 / 出题依据 / 先修推断都会因此失明。

做法：静态扫 `backend/app`（`core/kg/` 包是唯一出口定义处，自有其读写写法，排除），
任何仍把 `nodes_dir` 与 `.md` 拼成**单节点正文路径**的地方都必须进白名单并写明理由。
白名单之外的命中即禁 —— 防"再冒出一个直读"。

注意：本节只守**正文读取**；写路径、小节 manifest 管理、图谱结构方法都在 `core/kg/`
内部（已排除），不在此扫描范围。
"""
import re
from pathlib import Path

# tests/ → backend → <repo>
_REPO = Path(__file__).resolve().parents[2]
_APP_DIR = _REPO / "backend" / "app"

# 命中「把 nodes_dir 与 .md 拼成 单节点正文路径」的读写法（去注解后判断）
_MD_CONCAT = re.compile(r"nodes_dir\s*/\s*[^\n=]*\.md")

# 白名单（有意为之，各附理由）。按**代码内容**匹配而不是行号：行号会随无关编辑漂移
# （2026-10-11 就因此误报过一次 —— 上面加几行 flush 就把它顶走了）。
_ALLOW = {
    # 节点详情元数据端点：小节化节点 content 刻意为空串（前端按节读），
    #   只有老单 MD 节点才回单文件全文（D5 契约，见 test_sectioned_node_metadata_no_body）。
    "api/v1/knowledge.py": ['file_path = kg.nodes_dir / f"{node_id}.md"'],
    # node_content_text 读失败的降级（logger.debug 后）；且只对缺该方法的老假对象生效。
    "core/rag/manager.py": ['md_path = kg.nodes_dir / f"{node_id}.md"'],
}


def _offenders() -> list[str]:
    hits = []
    for path in _APP_DIR.rglob("*.py"):
        rel = path.relative_to(_APP_DIR).as_posix()
        # 唯一出口实现处：拆分后落在 core/kg/ 包（knowledge_graph.py 只剩转发层）
        if rel == "core/knowledge_graph.py" or rel.startswith("core/kg/"):
            continue
        allowed = _ALLOW.get(rel, [])
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0].rstrip()  # 去内联/整行注解，防注释误报
            if not _MD_CONCAT.search(code) or code.strip() in allowed:
                continue
            hits.append(f"{rel}:{lineno}: {code.strip()}")
    return hits


def test_node_body_read_stays_behind_node_content_text():
    """除白名单外，禁止在 knowledge_graph.py 之外再直读 nodes_dir/*.md 当正文。"""
    offenders = _offenders()
    assert not offenders, (
        "读节点正文必须走 KnowledgeGraph.node_content_text（小节优先），禁止再写 "
        "`nodes_dir / f'{node_id}.md'` 直读：\n" + "\n".join(offenders)
    )
