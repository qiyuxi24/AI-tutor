"""知识图谱包（原单体 `core/knowledge_graph.py` 按高内聚拆分）。

对外契约不变：`from app.core.knowledge_graph import KnowledgeGraph` 仍可用
（`core/knowledge_graph.py` 保留为转发层）。`KnowledgeGraph` 由各分域混入类组合而成，
**一域一文件**，改哪一域的读写只动对应文件：

    _common.py   共享常量与纯函数（唯一真值）
    core.py      连接 / 事务 / 缓存 / 行转换 / 人类内容护栏（基类 _KGBase）
    schema.py    建表与自动迁移
    nodes.py     节点查询 + 学科/板块 + 节点 CRUD + 别名/同名并轨 + AI 建议
    edges.py     边 CRUD
    paths.py     前置/拓扑排序/学习路径
    content.py   节点正文读写（单 MD / 小节拼接）+ 填充状态
    sections.py  节点小节（manifest / 小节 / 学习状态 / 试卷引用 / 学习会话）
    mastery.py   掌握度（唯一写入入口 + 事件账本）
    sources.py   溯源（nodes.sources）+ 资料↔节点增补标记（doc_node_marks）

数据真值分工、写入收口等不变量见各文件 docstring 与 `AGENTS.md §1/§2`。
"""
from ._common import *  # noqa: F401,F403
from ._common import __all__ as _common_all
from .core import _KGBase
from .schema import _SchemaMixin
from .nodes import _NodesMixin
from .edges import _EdgesMixin
from .sections import _SectionsMixin
from .mastery import _MasteryMixin
from .paths import _PathsMixin
from .content import _ContentMixin
from .sources import _SourcesMixin


class KnowledgeGraph(
    _SchemaMixin,
    _NodesMixin,
    _EdgesMixin,
    _SectionsMixin,
    _MasteryMixin,
    _PathsMixin,
    _ContentMixin,
    _SourcesMixin,
    _KGBase,
):
    """知识图谱管理类（SQLite 后端）。

    :param user_id: 当前登录用户 ID，所有查询/写入都按此隔离

    方法体分散在各分域混入类里，本类只负责组合；实例接口与拆分前完全一致。
    """


__all__ = [*_common_all, "KnowledgeGraph"]
