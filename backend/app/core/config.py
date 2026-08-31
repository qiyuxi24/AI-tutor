"""
统一配置模块 —— 应用所有环境变量的唯一读取入口。

设计原则：
- 集中管理：所有配置字段在此定义，避免各模块散落 os.getenv()。
- 零新依赖：基于 dataclass + python-dotenv，不引入 pydantic-settings。
- 单一加载：load_dotenv 只在此执行一次，其余模块直接 from app.core.config import settings。
- 类型转换 + 校验：CORS 白名单解析为 list，SECRET_KEY 缺失即拒绝启动。
"""
from dataclasses import dataclass, field
from pathlib import Path
import os
from dotenv import load_dotenv

# 项目根目录（backend/.env），确保无论从哪个目录启动都能找到
_ENV_PATH = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH, override=True)


def _parse_list(value: str | None, default: list[str]) -> list[str]:
    """解析逗号分隔的环境变量为 list，自动去除空白和空项。"""
    if not value:
        return default
    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_bool(value: str | None, default: bool = False) -> bool:
    """解析布尔环境变量（1/true/yes/on 视为 True，忽略大小写）。"""
    if not value:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    """应用全局配置。字段名与 .env 环境变量一一对应。"""

    # ─── LLM 服务 ───
    dashscope_api_key: str = ""
    model_name: str = "qwen-plus"
    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_timeout: float = 120.0

    # ─── 认证 ───
    secret_key: str = ""
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24  # 24 小时

    # ─── CORS 跨域 ───
    cors_allow_origins: list[str] = field(
        default_factory=lambda: ["http://localhost:5173"]
    )
    cors_allow_methods: list[str] = field(
        default_factory=lambda: ["*"]
    )
    cors_allow_headers: list[str] = field(
        default_factory=lambda: ["*"]
    )

    # ─── 启动 / 管理员 ───
    default_admin_password: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        """从环境变量构建配置（统一在此读取，含类型转换）。"""
        return cls(
            dashscope_api_key=os.getenv("DASHSCOPE_API_KEY", ""),
            model_name=os.getenv("MODEL_NAME", "qwen-plus"),
            llm_base_url=os.getenv(
                "LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
            ),
            llm_timeout=float(os.getenv("LLM_TIMEOUT", "120")),
            secret_key=os.getenv("SECRET_KEY", ""),
            jwt_algorithm=os.getenv("JWT_ALGORITHM", "HS256"),
            access_token_expire_minutes=int(
                os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", str(60 * 24))
            ),
            cors_allow_origins=_parse_list(
                os.getenv("CORS_ALLOW_ORIGINS"), ["http://localhost:5173"]
            ),
            cors_allow_methods=_parse_list(os.getenv("CORS_ALLOW_METHODS"), ["*"]),
            cors_allow_headers=_parse_list(os.getenv("CORS_ALLOW_HEADERS"), ["*"]),
            default_admin_password=os.getenv("DEFAULT_ADMIN_PASSWORD", ""),
        )

    def require_secret_key(self) -> None:
        """校验 SECRET_KEY 已配置，否则拒绝启动。"""
        if not self.secret_key:
            raise RuntimeError(
                "SECRET_KEY 环境变量未配置，服务拒绝启动。请在 .env 中添加强随机密钥。"
            )


# 全局单例：模块首次导入即完成加载，后续各模块直接引用
settings = Settings.from_env()
