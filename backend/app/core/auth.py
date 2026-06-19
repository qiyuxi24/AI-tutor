"""
JWT 认证模块
- 密码哈希/验证 (bcrypt)
- JWT token 生成/解析
- 获取当前登录用户的依赖注入
"""
import os
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv
from jose import JWTError, jwt
import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from app.core.error_codes import ErrorCode, log_error

# 加载 .env 文件（使用相对于本文件的绝对路径，确保无论从哪个目录启动都能找到）
_ENV_PATH = Path(__file__).resolve().parent.parent.parent / ".env"
if _ENV_PATH.exists():
    load_dotenv(dotenv_path=_ENV_PATH, override=True)

# OAuth2 密码流，tokenUrl 指向登录接口
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

# JWT 配置（SECRET_KEY 必须在环境变量中配置，否则拒绝启动）
SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY 环境变量未配置，服务拒绝启动。请在 .env 中添加强随机密钥。")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 小时


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
    """
    detail = log_error(ErrorCode.AUTH_TOKEN_INVALID)
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id_str: str | None = payload.get("sub")
        if user_id_str is None:
            raise credentials_exception
        return int(user_id_str)
    except (JWTError, ValueError):
        raise credentials_exception


def get_current_user(token: str = Depends(oauth2_scheme)) -> int:
    """
    从请求头 Authorization: Bearer <token> 中解析当前用户 ID
    用作 FastAPI 路由的依赖注入
    :return: user_id (int)
    """
    return get_current_user_from_token(token)
