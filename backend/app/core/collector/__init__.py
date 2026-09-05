"""
教育资源采集模块包入口（Batch 1 — V1 最小闭环）

结构：
- types.py   候选/任务类型 + 授权等级常量
- store.py   采集记录存储（tasks/resources 单文件 SQLite，仿 quiz_store）
- manager.py 采集服务/任务编排（B1.3：搜索过滤、断点续传、协作取消、hash 去重）
- adapters/  来源适配器注册表（BaseAdapter + Registry；真实源 B1.2 注册）

对外：collector_manager 单例
"""

from app.core.collector.manager import CollectorManager, collector_manager

__all__ = ["CollectorManager", "collector_manager"]
