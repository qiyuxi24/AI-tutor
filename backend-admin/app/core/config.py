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

# 仓库默认密钥：源码可见 → 生产必须用 ADMIN_SECRET_KEY 覆盖（见 require_secret_key）。
DEFAULT_ADMIN_SECRET = "admin-secret-key-change-in-production"


@dataclass
class Settings:
    # ⚠️ 下面这几条路径是**主系统 `core/storage.py` 的镜像**（两个 app 包名都叫 `app`，
    # 不能互相 import）—— 任何一条改错 = 运维后台读另一个库 / 删别的目录（P0-1 就是这么来的）。
    # 一致性由主系统 `backend/tests/test_storage_paths.py` 的 admin 对照用例锁死。

    # 复用主系统的 SQLite 数据库（= storage.knowledge_db）
    db_path: str = str(_PROJECT_ROOT / "data" / "knowledge" / "knowledge.db")

    # 主系统其余存储的落点（= storage 的 DATA_ROOT / conversations_dir / profiles_dir）：
    # **A1b 后全部收敛进单一 DATA_ROOT**（<repo>/data）：知识库·图谱向量·题库·模型配置·
    # 运行记录（agent_runs/llm_usage）·debug_log 均在 DATA_ROOT/<域> 下。
    # 仅用于「查看用户数据量」与「删除用户时清理数据」，读不到/表不存在时按 0 处理。
    backend_data_dir: str = str(_PROJECT_ROOT / "data")
    records_dir: str = str(_PROJECT_ROOT / "data")
    conversations_db: str = str(_PROJECT_ROOT / "data" / "conversations" / "conversations.db")
    # 用户画像与头像（主系统 core/profile/store.py::default_data_dir）：
    # 头像落 <profiles_dir>/avatars/{uid}.png，删号必须一并清（avatar_store.AVATAR_DIRNAME）
    profiles_dir: str = str(_PROJECT_ROOT / "data" / "profiles")

    # 运维后台独立密钥：与主系统 SECRET_KEY 分开，两边 token 互不通用
    secret_key: str = DEFAULT_ADMIN_SECRET
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
            records_dir=os.getenv("ADMIN_RECORDS_DIR", cls.records_dir),
            conversations_db=os.getenv("ADMIN_CONVERSATIONS_DB", cls.conversations_db),
            profiles_dir=os.getenv("ADMIN_PROFILES_DIR", cls.profiles_dir),
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

    def require_secret_key(self) -> None:
        """拒绝用仓库默认密钥启动（默认值公开在源码里 → 任何人可伪造后台 JWT / 拿删号权限）。

        与主系统 `core/auth.py::Settings.require_secret_key` 同一纪律：启动即校验，
        宁可起不来也不裸奔。
        """
        if self.secret_key == DEFAULT_ADMIN_SECRET:
            raise RuntimeError(
                "ADMIN_SECRET_KEY 未设置：拒绝使用仓库默认密钥启动运维后台。"
                "请在根 .env 配置 ADMIN_SECRET_KEY（随机长字符串）后重启。"
            )


settings = Settings.from_env()
