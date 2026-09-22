"""日志配置（core/logging_setup）的不变量。

锁住三件事：幂等（重复调用不叠 handler）、不向 root 传播（否则控制台打印两遍）、
落点按 backend 解析（否则从项目根跑和从 backend 跑会写出两个 tutor.log）。
"""
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.core import logging_setup
from app.core.config import settings

_LOGGER_NAME = "ai-tutor"


def test_setup_logging_is_idempotent():
    logger = logging.getLogger(_LOGGER_NAME)
    before = len(logger.handlers)
    logging_setup.setup_logging()
    logging_setup.setup_logging()
    assert len(logger.handlers) == before


def test_has_explicit_level_and_does_not_propagate():
    logger = logging_setup.setup_logging()
    assert logger.level != logging.NOTSET      # 没级别就会掉到 root 的 WARNING，INFO 全丢
    assert logger.propagate is False           # 否则经 root 再打印一遍


def test_log_dir_is_absolute_and_anchored_to_backend():
    target = Path(settings.log_dir)
    resolved = logging_setup.log_dir()
    assert resolved.is_absolute()
    if not target.is_absolute():
        assert resolved == logging_setup.BACKEND_DIR / target


def test_test_process_never_writes_log_file():
    """pytest 会 import app.* → 每个用例都往 logs/ 追加毫无价值，还会污染开发时看的文件。"""
    logger = logging_setup.setup_logging()
    assert not any(isinstance(h, RotatingFileHandler) for h in logger.handlers)


def test_other_app_loggers_reach_the_configured_handlers():
    """业务模块用 getLogger("ai-tutor") 或其子 logger（如 ai-tutor.debug）—— 都能到 handler。"""
    logger = logging_setup.setup_logging()
    child = logging.getLogger(f"{_LOGGER_NAME}.debug")
    records = []

    class _Collect(logging.Handler):
        def emit(self, record):
            records.append(record)

    collector = _Collect()
    logger.addHandler(collector)
    try:
        child.info("子 logger 应经 propagate 到达 ai-tutor 的 handler")
    finally:
        logger.removeHandler(collector)
    assert [r.name for r in records] == [f"{_LOGGER_NAME}.debug"]
