"""AI Tutor 后端根包。

**导入即配置日志**（`core/logging_setup.setup_logging()`，幂等）—— 目的是让
`python scripts/xxx.py` 这类不经 `main.py` 的入口也拿到同一套日志。历史坑：
脚本直跑时 `ai-tutor` logger 既无 handler 也无级别，INFO 全丢且不写文件，
事后排查"这次建图/索引干了什么"只能靠猜。

测试进程下自动跳过文件 handler（见 logging_setup），不会污染开发时的 tutor.log。
"""
from app.core.logging_setup import setup_logging

# 版本单一真源：`main.py` 的 FastAPI(version=…) 读它，CHANGELOG / tag 与之对齐。
# 发版流程 = 改这里 + frontend/package.json → 写 CHANGELOG → `git tag vX.Y.Z`。
__version__ = "1.0.0"

setup_logging()
