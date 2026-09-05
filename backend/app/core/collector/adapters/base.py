"""
采集源适配器协议与注册表（仿 kb/parsers 注册表模式，B1.2）

- BaseAdapter：抽象基类，search/fetch 默认抛 NotImplementedError；
  _http 惰性（实例化不建客户端，导入期零副作用，仅调用时才 lazy 建）。
- AdapterRegistry：按名注册/覆盖/注销/路由，拒绝非适配器对象。
- 真实内置适配器（wikipedia/wikibooks）在 __init__.py 注册到全局 registry，
  候选发现（search 并行遍历）与内容拉取（fetch）都经由本模块。
"""

from typing import Optional, Union

from app.core.collector.types import CollectCandidate

FetchResult = Union[bytes, str]  # 抓取内容：原始二进制 或 纯文本/Markdown


class BaseAdapter:
    """采集源适配器基类

    子类覆写：
    - name:          唯一名称（注册键，也写入候选 source/meta.adapter）
    - license_level: 该源默认授权等级（候选未标注时继承）
    - search():      按关键词发现候选（默认未实现）
    - fetch():       按候选拉取内容（默认未实现）
    """

    name: str = ""
    license_level: str = "L0"

    def __init__(self, http=None) -> None:
        # 惰性：注册/实例化阶段必须为 None；search/fetch 时才创建客户端
        self._http = http

    async def search(self, query: str) -> list[CollectCandidate]:
        raise NotImplementedError(f"{self.name or self.__class__.__name__}.search 未实现")

    async def fetch(self, candidate: CollectCandidate) -> FetchResult:
        raise NotImplementedError(f"{self.name or self.__class__.__name__}.fetch 未实现")


class AdapterRegistry:
    """按名路由适配器（register 覆盖同名；unregister 返回被移除实例）"""

    def __init__(self) -> None:
        self._items: dict[str, BaseAdapter] = {}

    def register(self, adapter: BaseAdapter) -> BaseAdapter:
        if not isinstance(adapter, BaseAdapter):
            raise TypeError(f"只接受 BaseAdapter，收到 {type(adapter).__name__}")
        self._items[adapter.name] = adapter
        return adapter

    def unregister(self, name: str) -> Optional[BaseAdapter]:
        return self._items.pop(name, None)

    def get(self, name: str) -> Optional[BaseAdapter]:
        return self._items.get(name)

    def names(self) -> list[str]:
        return list(self._items.keys())

    def all(self) -> list[BaseAdapter]:
        return list(self._items.values())


# 全局注册表实例（内置源在 adapters/__init__.py 注册）
registry = AdapterRegistry()
