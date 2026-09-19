"""
JWT 认证模块
- 密码哈希/验证 (bcrypt)
- JWT token 生成/解析
- 获取当前登录用户的依赖注入
"""
from datetime import datetime, timedelta
from jose import JWTError, jwt
import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from app.core.config import settings
from app.core.error_codes import ErrorCode, log_error

# OAuth2 密码流，tokenUrl 指向登录接口
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

# JWT 配置（统一从 Settings 读取，SECRET_KEY 缺失时校验拒绝启动）
settings.require_secret_key()
SECRET_KEY = settings.secret_key
ALGORITHM = settings.jwt_algorithm
ACCESS_TOKEN_EXPIRE_MINUTES = settings.access_token_expire_minutes


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证明文密码与哈希是否匹配"""
    return bcrypt.checkpw(
        plain_password.encode("utf-8"),
        hashed_password.encode("utf-8") if isinstance(hashed_password, str) else hashed_password,
    )


def get_password_hash(password: str) -> str:
    """对密码进行 bcrypt 哈希（自动截断超过 72 字节的密码）"""
    password_bytes = password.encode("utf-8")[:72]
    return bcrypt.hashpw(password_bytes, bcrypt.gensalt()).decode("utf-8")


def create_access_token(user_id: int) -> str:
    """
    生成 JWT access token
    :param user_id: 用户数据库 ID
    :return: 编码后的 JWT 字符串
    """
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode = {
        "sub": str(user_id),  # sub 字段存储用户 ID
        "exp": expire,
        "iat": datetime.utcnow(),
    }
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user_from_token(token: str) -> int:
    """
    从原始 token 字符串中解析用户 ID（不通过 Depends）
    用于 SSE 等无法使用 OAuth2 标准头部的场景
    :return: user_id (int)
    :raises HTTPException: 401 —— token 签名不合法 / 已过期 / payload 缺 sub
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id_str: str | None = payload.get("sub")
        if user_id_str is None:
            raise ValueError("token payload 缺少 sub 字段")
        return int(user_id_str)
    except (JWTError, ValueError) as exc:
        # 仅鉴权真正失败时才告警（成功路径必须静默，否则日志噪音会掩盖真故障）
        detail = log_error(ErrorCode.AUTH_TOKEN_INVALID, detail=str(exc))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            headers={"WWW-Authenticate": "Bearer"},
        )


def get_current_user(token: str = Depends(oauth2_scheme)) -> int:
    """
    从请求头 Authorization: Bearer <token> 中解析当前用户 ID
    用作 FastAPI 路由的依赖注入
    :return: user_id (int)
    """
    return get_current_user_from_token(token)


# users 表新增列：老库建表时只有 id/username/password_hash/created_at，
# 登录逻辑需要的 status（账号状态）/ role（角色）/ last_login_at（最后登录时间）需幂等补齐。
_USER_EXTRA_COLUMNS = (
    ("status", "TEXT DEFAULT 'active'"),
    ("role", "TEXT DEFAULT 'user'"),
    ("last_login_at", "TEXT"),
)


def ensure_user_columns(conn) -> None:
    """幂等补齐 users 表新增列（老库升级用）。调用方负责 commit。"""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
    for name, ddl in _USER_EXTRA_COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE users ADD COLUMN {name} {ddl}")
