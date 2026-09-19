"""运维后台配置。

沿用主系统 backend/app/core/config.py 的约定：dataclass + os.getenv，不引入
pydantic-settings（少一个依赖，两套服务配置风格一致）。
"""
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(dotenv_path=_PROJECT_ROOT / ".env", override=False)


@dataclass
class Settings:
    # 复用主系统的 SQLite 数据库（与 backend/app/main.py 指向同一文件）
    db_path: str = str(_PROJECT_ROOT / "data" / "knowledge" / "knowledge.db")

    # 主系统其余存储的落点（照抄 backend/app/core/* 里的相对路径约定：
    # 知识库/图谱向量在 backend/data，对话在根 data/conversations）。
    # 仅用于「查看用户数据量」与「删除用户时清理数据」，读不到/表不存在时按 0 处理。
    backend_data_dir: str = str(_PROJECT_ROOT / "backend" / "data")
    conversations_db: str = str(_PROJECT_ROOT / "data" / "conversations" / "conversations.db")

    # 运维后台独立密钥：与主系统 SECRET_KEY 分开，两边 token 互不通用
    secret_key: str = "admin-secret-key-change-in-production"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 8

    # 初始超级管理员密码，仅首次建号时使用
    default_admin_password: str = "admin123"

    # 运维前端来源（开发态经 vite proxy 同源访问，此配置供直连场景）
    cors_origins: list[str] = field(default_factory=lambda: ["http://localhost:5174"])

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            db_path=os.getenv("ADMIN_DB_PATH", cls.db_path),
            backend_data_dir=os.getenv("ADMIN_BACKEND_DATA_DIR", cls.backend_data_dir),
            conversations_db=os.getenv("ADMIN_CONVERSATIONS_DB", cls.conversations_db),
            secret_key=os.getenv("ADMIN_SECRET_KEY", cls.secret_key),
            algorithm=os.getenv("ADMIN_JWT_ALGORITHM", "HS256"),
            access_token_expire_minutes=int(
                os.getenv("ADMIN_TOKEN_EXPIRE_MINUTES", str(60 * 8))
            ),
            default_admin_password=os.getenv("ADMIN_DEFAULT_PASSWORD", "admin123"),
            cors_origins=[
                origin.strip()
                for origin in os.getenv("ADMIN_CORS_ORIGINS", "http://localhost:5174").split(",")
                if origin.strip()
            ],
        )


settings = Settings.from_env()
