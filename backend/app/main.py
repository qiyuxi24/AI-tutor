import asyncio
import logging
from logging.handlers import RotatingFileHandler
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app.api.v1.chat import router as chat_router
from app.api.v1.knowledge import router as knowledge_router
from app.api.v1.auth import router as auth_router
from app.api.v1.conversations import router as conversations_router
from app.api.v1.profile import router as profile_router
from app.api.v1.rag import router as rag_router
from app.api.v1.kb import router as kb_router
from app.api.v1.quiz import router as quiz_router
from app.api.v1.collector import router as collector_router
from app.api.v1.agent_runs import router as agent_runs_router
from app.core.config import settings
from app.core.error_codes import ErrorCode, log_error

# 结构化日志：控制台 + 文件轮转（双输出）
_log_fmt = logging.Formatter(
    "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# 文件轮转：LOG_DIR 下 tutor.log + .1/.2/... 备份
_log_dir = Path(settings.log_dir)
_log_dir.mkdir(parents=True, exist_ok=True)
_file_handler = RotatingFileHandler(
    _log_dir / "tutor.log",
    maxBytes=settings.log_max_bytes,
    backupCount=settings.log_backup_count,
    encoding="utf-8",
)
_file_handler.setFormatter(_log_fmt)
_file_handler.setLevel(settings.log_level)

# 挂到 "ai-tutor" logger（所有模块共用此名）
logger = logging.getLogger("ai-tutor")
logger.setLevel(settings.log_level)
logger.addHandler(_file_handler)

# ════════════════════════════════════════════
#  Lifespan：启动/关闭生命周期管理（替代旧 on_event）
# ════════════════════════════════════════════

_gc_task: "asyncio.Task | None" = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时创建管理员 + 挂载 GC，关闭时取消 GC。"""
    global _gc_task

    # ── 启动：确保默认管理员账户存在 ──
    import sqlite3
    from app.core.auth import ensure_user_columns, get_password_hash

    db_path = Path(__file__).resolve().parent.parent.parent / "data" / "knowledge" / "knowledge.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now')),
                status TEXT DEFAULT 'active',
                role TEXT DEFAULT 'user',
                last_login_at TEXT
            )
        """)
        conn.commit()

        # 老库（无上述三列）启动时自动补齐，否则登录会报 no such column
        ensure_user_columns(conn)
        conn.commit()

        existing = conn.execute(
            "SELECT id FROM users WHERE username = ?", ("admin",)
        ).fetchone()

        if not existing:
            default_password = settings.default_admin_password
            if not default_password:
                logger.warning("未设置 DEFAULT_ADMIN_PASSWORD 环境变量，无法创建默认管理员账户")
            else:
                password_hash = get_password_hash(default_password)
                conn.execute(
                    "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                    ("admin", password_hash),
                )
                conn.commit()
                logger.info("默认管理员账户已创建: admin")
    finally:
        conn.close()

    # ── 启动：agent_runs 分层 GC + 调试日志保留清理 ──
    from app.core.agent import debug_log, store as run_store

    async def _daily_prune():
        while True:
            await asyncio.sleep(24 * 3600)
            try:
                res = run_store.prune()
                if res["deleted"] or res["stripped"]:
                    logger.info(f"agent_runs 每日清理: {res}")
            except Exception as e:
                logger.warning(f"agent_runs 每日清理失败: {e}")
            try:
                n = debug_log.prune()
                if n:
                    logger.info(f"调试日志每日清理: 删除 {n} 条")
            except Exception as e:
                logger.warning(f"调试日志每日清理失败: {e}")

    try:
        res = run_store.prune()
        if res["deleted"] or res["stripped"]:
            logger.info(f"agent_runs 启动清理: {res}")
    except Exception as e:
        logger.warning(f"agent_runs 启动清理失败: {e}")
    try:
        n = debug_log.prune()
        if n:
            logger.info(f"调试日志启动清理: 删除 {n} 条")
    except Exception as e:
        logger.warning(f"调试日志启动清理失败: {e}")
    _gc_task = asyncio.create_task(_daily_prune())

    yield

    # ── 关闭：取消 GC 任务 ──
    if _gc_task is not None:
        _gc_task.cancel()
        _gc_task = None


app = FastAPI(title="TutorAgent API", version="0.1.0", lifespan=lifespan)

# ════════════════════════════════════════════
#  CORS 跨域配置（白名单/方法/头从统一 Settings 读取）
# ════════════════════════════════════════════
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins,
    allow_methods=settings.cors_allow_methods,
    allow_headers=settings.cors_allow_headers,
)

# 注册路由
app.include_router(chat_router, prefix="/api/v1")
app.include_router(knowledge_router, prefix="/api/v1")
app.include_router(auth_router, prefix="/api/v1")
app.include_router(conversations_router, prefix="/api/v1")
app.include_router(profile_router, prefix="/api/v1")
app.include_router(rag_router, prefix="/api/v1")
app.include_router(kb_router, prefix="/api/v1")
app.include_router(quiz_router, prefix="/api/v1")
app.include_router(collector_router, prefix="/api/v1")
app.include_router(agent_runs_router, prefix="/api/v1")


# ════════════════════════════════════════════
#  全局异常处理器：捕获未处理的异常，返回结构化错误信息
# ════════════════════════════════════════════

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """将所有未捕获异常转为 500 + 可读错误码"""
    error_msg = log_error(
        ErrorCode.COMM_SERVER_ERROR,
        detail=f"{type(exc).__name__}: {str(exc)}",
        exception=exc,
        context={"path": str(request.url.path), "method": request.method},
    )
    return JSONResponse(
        status_code=500,
        content={"detail": error_msg},
    )


@app.get("/api/health")
async def health_check():
    return {"status": "ok"}