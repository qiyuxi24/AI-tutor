"""日志配置唯一出口（控制台 + 轮转文件）。

为什么独立成模块（2026-09-20）：
  原先这段配置写在 `main.py` 顶层 —— 只有经 uvicorn/main.py 启动才生效。用
  `python scripts/xxx.py` 直跑重活（建图 / 索引 / 评测）时，`ai-tutor` logger
  既没有 handler 也没有级别 → 落到 root 的 WARNING，**INFO 全部丢弃、且不写任何文件**。
  这正是"某次建图花了多少 token 事后查不到"的原因之一。
  抽到此处后由 `app/__init__.py` 自动调用：任何 `import app.*` 都拿到同一套配置。

落点：`settings.log_dir`（默认 `backend/logs/tutor.log`），**相对路径按 backend 目录解析**，
  不随 CWD 漂移。历史坑：之前是相对 CWD，从项目根跑和从 backend 跑会写出两个不同的
  `tutor.log`，排查时看到一个"没有最新记录"的旧文件。

约定：
- 全项目统一用 `logging.getLogger("ai-tutor")` 及其子 logger（`ai-tutor.debug` 等）；
- 级别由 `LOG_LEVEL` 控制（默认 INFO）；文件轮转 `LOG_MAX_BYTES` × `LOG_BACKUP_COUNT`；
- **幂等**：重复 import / 重复调用不会重复加 handler。
"""
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

# logging_setup.py → core → app → backend
BACKEND_DIR = Path(__file__).resolve().parents[2]

_LOGGER_NAME = "ai-tutor"
_FMT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"
_MARK = "_ai_tutor_logging_configured"   # 幂等标记（挂在 logger 对象上）


def log_dir() -> Path:
    """日志目录绝对路径（`LOG_DIR` 是相对路径时按 backend 目录解析）。"""
    from app.core.config import settings
    p = Path(settings.log_dir)
    return p if p.is_absolute() else BACKEND_DIR / p


def setup_logging() -> logging.Logger:
    """配置 `ai-tutor` logger（控制台 + 轮转文件）并返回它。幂等。"""
    logger = logging.getLogger(_LOGGER_NAME)
    if getattr(logger, _MARK, False):
        return logger
    setattr(logger, _MARK, True)

    from app.core.config import settings

    level = getattr(logging, str(settings.log_level).upper(), logging.INFO)
    formatter = logging.Formatter(_FMT, datefmt=_DATEFMT)

    # root：给第三方库（httpx / openai / uvicorn）用；已有 handler 时 basicConfig 自动跳过
    logging.basicConfig(level=level, format=_FMT, datefmt=_DATEFMT)

    logger.setLevel(level)
    # 本 logger 自己出控制台，故不再向 root 传播（否则控制台打印两遍）
    logger.propagate = False

    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    stream.setLevel(level)
    logger.addHandler(stream)

    # 测试进程不写文件：pytest 会 import app.*，每个用例往 logs/ 追加毫无价值，
    # 还会污染开发时真正要看的 tutor.log。
    if "pytest" not in sys.modules:
        try:
            target = log_dir()
            target.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                target / "tutor.log",
                maxBytes=settings.log_max_bytes,
                backupCount=settings.log_backup_count,
                encoding="utf-8",
            )
            file_handler.setFormatter(formatter)
            file_handler.setLevel(level)
            logger.addHandler(file_handler)
        except OSError as e:   # 目录不可写（只读卷/权限）→ 只留控制台，不影响启动
            logger.warning(f"日志文件 handler 初始化失败，仅输出到控制台: {e}")

    return logger
