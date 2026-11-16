"""
JWT 认证模块
- 密码哈希/验证 (bcrypt)
- JWT token 生成/解析（验签 + 账号状态校验）
- 获取当前登录用户的依赖注入
"""
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from jose import JWTError, jwt
import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from app.core import storage
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
    # timezone-aware（2026-11-04 技术债）：`datetime.utcnow()` 自 Python 3.12 起弃用。
    # `jose.jwt.encode` 对 naive datetime 按 UTC 解释，换 aware 后编码结果**逐字节不变**，
    # 只是把"隐含 UTC"这个约定写进类型里。
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode = {
        "sub": str(user_id),  # sub 字段存储用户 ID
        "exp": expire,
        "iat": now,
    }
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user_from_token(token: str) -> int:
    """
    从原始 token 字符串中解析用户 ID（不通过 Depends）
    用于 SSE 等无法使用 OAuth2 标准头部的场景

    ⚠️ 验签通过 ≠ 账号仍可用：JWT 是无状态的，运维把 `users.status` 改成 disabled
    后旧 token 依然签名有效。所以这里**必须**再查一次当前状态（否则禁用只挡住了
    下一次登录，已登录的人可以继续用到 token 自然过期 —— 真实的权限绕过）。

    :return: user_id (int)
    :raises HTTPException: 401 —— token 签名不合法 / 已过期 / payload 缺 sub / 用户已不存在
    :raises HTTPException: 403 —— 账号已被禁用
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id_str: str | None = payload.get("sub")
        if user_id_str is None:
            raise ValueError("token payload 缺少 sub 字段")
        user_id = int(user_id_str)
    except (JWTError, ValueError) as exc:
        # 仅鉴权真正失败时才告警（成功路径必须静默，否则日志噪音会掩盖真故障）
        detail = log_error(ErrorCode.AUTH_TOKEN_INVALID, detail=str(exc))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            headers={"WWW-Authenticate": "Bearer"},
        )
    # 放在 try 之外：状态校验的 401/403 不能被上面的 token 解析分支吞成同一条报错
    _ensure_user_active(user_id)
    return user_id


def _users_db_path():
    """users 表所在库的取径（唯一声明处 = `core/storage.py`）。

    ⚠️ 真值是 `data/knowledge/knowledge.db`；`data/knowledge.db` 是 0 字节空壳，连错会
    得到一个空库（于是所有人都查不到 → 全员 401），历史上踩过这个坑。
    """
    return storage.knowledge_db()


def _connect_users_db() -> sqlite3.Connection:
    """只读打开 users 库。

    `mode=ro` 是刻意的：认证依赖**只读**，绝不能因为路径写错/事务残留而在请求路径上
    改动或凭空创建数据库文件。`as_uri()` 负责 Windows 盘符、空格与中文的正确转义。
    """
    return sqlite3.connect(f"{_users_db_path().as_uri()}?mode=ro", uri=True, timeout=5.0)


def _ensure_user_active(user_id: int) -> None:
    """校验用户仍存在且未被禁用；不满足就抛 HTTPException。

    每次请求一次主键 SELECT（SQLite 主键查找微秒级，连接用 `closing` 保证归还），
    **不做进程内缓存**：禁用必须立刻生效，缓存会把"改完还能用 N 秒"变成本 bug 的另一形态。

    `status` 为 NULL / 空串的老库行按正常处理（列由 `ensure_user_columns` 补，历史行可为 NULL），
    只有明确的 `disabled` 才拒绝 —— 老库兼容不能靠"猜默认值"。
    """
    with closing(_connect_users_db()) as conn:
        row = conn.execute("SELECT status FROM users WHERE id = ?", (user_id,)).fetchone()

    if row is None:
        detail = log_error(ErrorCode.AUTH_USER_NOT_FOUND, context={"user_id": user_id})
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            headers={"WWW-Authenticate": "Bearer"},
        )
    if row[0] == "disabled":
        # 文案与登录端点（api/v1/auth.py::login）逐字一致：前端按同一句话提示
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="账号已被禁用",
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
