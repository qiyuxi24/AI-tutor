"""TutorAgent 后端根包。

**导入即配置日志**（`core/logging_setup.setup_logging()`，幂等）—— 目的是让
`python scripts/xxx.py` 这类不经 `main.py` 的入口也拿到同一套日志。历史坑：
脚本直跑时 `ai-tutor` logger 既无 handler 也无级别，INFO 全丢且不写文件，
事后排查"这次建图/索引干了什么"只能靠猜。

测试进程下自动跳过文件 handler（见 logging_setup），不会污染开发时的 tutor.log。
"""
from app.core.logging_setup import setup_logging

setup_logging()
