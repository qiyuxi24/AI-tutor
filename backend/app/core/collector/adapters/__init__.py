"""
采集适配器包入口（B1.2）

内置真实源注册到全局 registry（导入即注册，实例不建 HTTP 客户端——惰性）：
- wikipedia：中文维基百科
- wikibooks：中文维基教科书（复用 wikipedia 同栈逻辑）
- oiwiki：OI-wiki（竞赛算法百科，mkdocs.yml 目录发现 + raw Markdown 抓取）
- web_page：任意网页正文（B3.2，候选由用户给定 URL，search 恒空）
候选去重/过滤由 manager.search 统一完成。
"""

from app.core.collector.adapters.base import AdapterRegistry, BaseAdapter, registry
from app.core.collector.adapters.oiwiki import OiwikiAdapter
from app.core.collector.adapters.web_page import WebPageAdapter
from app.core.collector.adapters.wikibooks import WikibooksAdapter
from app.core.collector.adapters.wikipedia import WikipediaAdapter

registry.register(WikipediaAdapter())
registry.register(WikibooksAdapter())
registry.register(OiwikiAdapter())
registry.register(WebPageAdapter())

__all__ = ["AdapterRegistry", "BaseAdapter", "registry", "get_registry", "get_adapter"]


def get_registry() -> AdapterRegistry:
    return registry


def get_adapter(name: str) -> BaseAdapter:
    return registry.get(name)
