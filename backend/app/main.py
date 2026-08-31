import logging

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
from app.core.config import settings
from app.core.error_codes import ErrorCode, log_error

# 配置 "ai-tutor" 日志器，输出到控制台（FastAPI/Uvicorn 默认输出目标）
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

app = FastAPI(title="AI Tutor API", version="0.1.0")

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


# ════════════════════════════════════════════
#  启动事件：确保默认管理员账户存在
# ════════════════════════════════════════════

@app.on_event("startup")
async def ensure_default_admin():
    """应用启动时自动创建默认管理员账户（如果不存在）"""
    import sqlite3
    from app.core.auth import get_password_hash

    # 使用与 KnowledgeGraph 相同的数据库路径（项目根 data/knowledge/knowledge.db）
    db_path = Path(__file__).resolve().parent.parent.parent / "data" / "knowledge" / "knowledge.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        # 确保 users 表存在
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.commit()

        # 检查默认管理员是否存在
        existing = conn.execute(
            "SELECT id FROM users WHERE username = ?", ("admin",)
        ).fetchone()

        if not existing:
            default_password = settings.default_admin_password
            if not default_password:
                logging.getLogger("ai-tutor").warning(
                    "未设置 DEFAULT_ADMIN_PASSWORD 环境变量，无法创建默认管理员账户"
                )
            else:
                password_hash = get_password_hash(default_password)
                conn.execute(
                    "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                    ("admin", password_hash),
                )
                conn.commit()
                logging.getLogger("ai-tutor").info(
                    "默认管理员账户已创建: admin"
                )
    finally:
        conn.close()


@app.get("/api/health")
async def health_check():
    return {"status": "ok"}