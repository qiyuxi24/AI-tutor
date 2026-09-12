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

# 唯一真值文件 = 项目根 .env（本地与 Docker 共源），确保无论从哪个目录启动都能找到
_ENV_PATH = Path(__file__).resolve().parents[3] / ".env"
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

    # ─── LLM 服务（对话/Agent 主模型，OpenAI 兼容）───
    # dashscope_api_key 语义收紧为「阿里云 Key」：仅用于 text-embedding-v4 嵌入
    dashscope_api_key: str = ""
    # 对话主模型专用 Key（接 MiniMax 等其它 OpenAI 兼容服务时填写；缺省回退 dashscope_api_key）
    llm_api_key: str = ""
    model_name: str = "qwen-plus"
    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_timeout: float = 120.0
    # 对话发送预算（tokens）：system_prompt + 历史超预算时自动裁掉最旧轮次（见 core/context_guard.py）
    llm_ctx_budget: int = 32_000
    # 备用对话服务（可选，模型回退链）：主模型配额耗尽/认证失败/持续异常时自动静默降级。
    # 三件套缺任一即不启用（留空=保持单模型旧行为）。key 缺省回退 DASHSCOPE_API_KEY。
    fallback_llm_api_key: str = ""
    fallback_llm_base_url: str = ""
    fallback_model_name: str = ""
    # 嵌入服务地址（默认阿里云；与对话 base 分离，切换对话模型不影响嵌入）
    embed_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"

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

    # ─── 日志 ───
    log_level: str = "INFO"
    log_dir: str = "logs"
    log_max_bytes: int = 10 * 1024 * 1024  # 10 MB
    log_backup_count: int = 5

    @classmethod
    def from_env(cls) -> "Settings":
        """从环境变量构建配置（统一在此读取，含类型转换）。"""
        return cls(
            dashscope_api_key=os.getenv("DASHSCOPE_API_KEY", ""),
            llm_api_key=os.getenv("LLM_API_KEY", ""),
            model_name=os.getenv("MODEL_NAME", "qwen-plus"),
            llm_base_url=os.getenv(
                "LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
            ),
            llm_timeout=float(os.getenv("LLM_TIMEOUT", "120")),
            llm_ctx_budget=int(os.getenv("LLM_CTX_BUDGET", str(32_000))),
            fallback_llm_api_key=os.getenv("FALLBACK_LLM_API_KEY", ""),
            fallback_llm_base_url=os.getenv("FALLBACK_LLM_BASE_URL", ""),
            fallback_model_name=os.getenv("FALLBACK_MODEL_NAME", ""),
            embed_base_url=os.getenv(
                "EMBED_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
            ),
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
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            log_dir=os.getenv("LOG_DIR", "logs"),
            log_max_bytes=int(os.getenv("LOG_MAX_BYTES", str(10 * 1024 * 1024))),
            log_backup_count=int(os.getenv("LOG_BACKUP_COUNT", "5")),
        )

    def require_secret_key(self) -> None:
        """校验 SECRET_KEY 已配置，否则拒绝启动。"""
        if not self.secret_key:
            raise RuntimeError(
                "SECRET_KEY 环境变量未配置，服务拒绝启动。请在 .env 中添加强随机密钥。"
            )


# 全局单例：模块首次导入即完成加载，后续各模块直接引用
settings = Settings.from_env()
