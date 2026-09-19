"""
认证相关 API 路由
- POST /api/v1/auth/register  用户注册
- POST /api/v1/auth/login     用户登录
- GET  /api/v1/auth/me        获取当前用户信息
"""
import sqlite3
from fastapi import APIRouter, Depends, HTTPException, status, Request
from pydantic import BaseModel, field_validator

from app.core.auth import (
    get_password_hash,
    verify_password,
    create_access_token,
    get_current_user,
)
from app.core import transport_crypto
from app.core.rate_limiter import login_rate_limiter
from app.core.error_codes import ErrorCode, log_error

router = APIRouter(tags=["认证"])

# 数据库路径（从项目根目录解析）
from pathlib import Path as _Path
_PROJECT_ROOT = _Path(__file__).resolve().parent.parent.parent.parent.parent
DB_PATH = str(_PROJECT_ROOT / "data" / "knowledge" / "knowledge.db")


# ---------- 请求/响应模型 ----------

class RegisterRequest(BaseModel):
    """注册请求体（password 为 RSA-OAEP 加密后的 base64 密文，长度校验在解密后进行）"""
    username: str
    password: str

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 3 or len(v) > 20:
            raise ValueError("用户名长度需在 3-20 个字符之间")
        return v


def _decrypt_req_password(cipher_b64: str) -> str:
    """解密请求中的密码密文；解密失败或明文长度不合法时抛 HTTPException"""
    try:
        plain = transport_crypto.decrypt_password(cipher_b64)
    except ValueError as exc:
        detail = log_error(ErrorCode.AUTH_CRYPTO_INVALID, exception=exc)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)
    if len(plain) < 6 or len(plain) > 50:
        detail = log_error(ErrorCode.AUTH_VALIDATION_ERROR)
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail)
    return plain


class LoginRequest(BaseModel):
    """登录请求体"""
    username: str
    password: str


class UserInfo(BaseModel):
    """用户信息响应"""
    user_id: int
    username: str
    role: str = "user"


# ---------- 数据库辅助函数 ----------

def _get_db() -> sqlite3.Connection:
    """获取数据库连接（每次新建，线程安全）"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _get_user_by_username(conn: sqlite3.Connection, username: str) -> sqlite3.Row | None:
    """按用户名查找用户，不存在返回 None（参数化查询防注入）"""
    cursor = conn.execute(
        "SELECT id, username, password_hash FROM users WHERE username = ?",
        (username,),
    )
    return cursor.fetchone()


def _get_user_by_id(conn: sqlite3.Connection, user_id: int) -> sqlite3.Row | None:
    """按 ID 查找用户"""
    cursor = conn.execute(
        "SELECT id, username FROM users WHERE id = ?",
        (user_id,),
    )
    return cursor.fetchone()


# ---------- 路由 ----------

@router.get("/auth/public-key")
async def get_public_key():
    """下发 RSA 公钥（PEM），前端用它加密密码字段"""
    return {"public_key": transport_crypto.get_public_key_pem()}


@router.post("/auth/register", status_code=status.HTTP_201_CREATED)
async def register(req: RegisterRequest):
    """
    用户注册
    - 密码为 RSA 加密密文，先解密再校验
    - 检查用户名是否已存在
    - 对密码进行 bcrypt 哈希后存储
    """
    password = _decrypt_req_password(req.password)
    conn = _get_db()
    try:
        # 检查用户名是否已存在
        existing = _get_user_by_username(conn, req.username)
        if existing:
            detail = log_error(ErrorCode.AUTH_USERNAME_TAKEN, context={"username": req.username})
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=detail,
            )

        # 哈希密码并插入
        password_hash = get_password_hash(password)
        cursor = conn.execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            (req.username, password_hash),
        )
        conn.commit()
        user_id = cursor.lastrowid

        # 注册成功后自动签发 token，前端可直接进入登录态
        access_token = create_access_token(user_id)
        return {
            "message": "注册成功",
            "user_id": user_id,
            "token": access_token,
            "user": {"id": user_id, "username": req.username},
        }
    finally:
        conn.close()


@router.post("/auth/login")
async def login(req: LoginRequest, request: Request):
    """
    用户登录
    - 限制同一 IP 每分钟最多 5 次登录尝试
    - 验证用户名和密码（密码为 RSA 加密密文，先解密）
    - 返回 JWT token
    """
    # 获取客户端 IP
    client_ip = request.client.host if request.client else "unknown"

    # 速率限制检查
    if not login_rate_limiter.is_allowed(client_ip):
        retry_after = login_rate_limiter.get_retry_after(client_ip)
        detail = log_error(ErrorCode.AUTH_RATE_LIMITED, context={"client_ip": client_ip})
        raise HTTPException(
            status_code=429,
            detail=detail,
            headers={"Retry-After": str(retry_after)},
        )

    password = _decrypt_req_password(req.password)
    conn = _get_db()
    try:
        # 查找用户
        user = _get_user_by_username(conn, req.username)
        if not user:
            detail = log_error(ErrorCode.AUTH_INVALID_CREDENTIALS, context={"username": req.username})
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=detail,
            )

        # 验证密码
        if not verify_password(password, user["password_hash"]):
            detail = log_error(ErrorCode.AUTH_INVALID_CREDENTIALS, context={"username": req.username})
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=detail,
            )

        # 检查用户状态（局部变量不得命名为 status：会遮蔽 fastapi.status 模块）
        acct = conn.execute("SELECT status, role FROM users WHERE id = ?", (user["id"],)).fetchone()
        if acct and acct["status"] == "disabled":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="账号已被禁用",
            )

        # 生成 JWT token
        access_token = create_access_token(user["id"])

        # 更新最后登录时间
        conn.execute(
            "UPDATE users SET last_login_at = datetime('now') WHERE id = ?",
            (user["id"],),
        )
        conn.commit()

        return {
            "token": access_token,
            "user": {"id": user["id"], "username": user["username"], "role": acct["role"] if acct else "user"},
        }
    finally:
        conn.close()


@router.get("/auth/me", response_model=UserInfo)
async def get_me(user_id: int = Depends(get_current_user)):
    """
    获取当前登录用户信息
    需要携带有效的 JWT token
    """
    conn = _get_db()
    try:
        user = conn.execute(
            "SELECT id, username, role FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if not user:
            detail = log_error(ErrorCode.AUTH_USER_NOT_FOUND, context={"user_id": user_id})
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=detail,
            )
        return UserInfo(user_id=user["id"], username=user["username"], role=user["role"] or "user")
    finally:
        conn.close()
