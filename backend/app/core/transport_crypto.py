"""
传输加密模块：登录/注册密码的 RSA 加密传输。

流程：
- 后端首次使用时生成 2048 位 RSA 密钥对，私钥持久化到 data/keys/rsa_private.pem
- 前端经 GET /api/v1/auth/public-key 拿公钥（PEM），用 RSA-OAEP-SHA256 加密密码
- 后端用私钥解密后交给认证模块（bcrypt/JWT，见 core/auth.py），认证逻辑零改动

设计要点：
- 密钥丢失（如容器重建）会自动重新生成；客户端下次解密失败时刷新公钥重试即可
- 所有加解密失败统一抛 ValueError，由路由层转 400 (E-AUTH-007)
"""
import base64
import logging
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

logger = logging.getLogger(__name__)

# 私钥默认落盘位置：项目根 data/keys/（运行时数据，不进代码库）
KEY_PATH = Path(__file__).resolve().parents[3] / "data" / "keys" / "rsa_private.pem"

_OAEP = padding.OAEP(
    mgf=padding.MGF1(algorithm=hashes.SHA256()),
    algorithm=hashes.SHA256(),
    label=None,
)

# 模块级密钥缓存（进程内单例）
_private_key: rsa.RSAPrivateKey | None = None


def load_or_create_key(key_path: Path) -> rsa.RSAPrivateKey:
    """读取私钥文件；不存在则生成 2048 位 RSA 并落盘（目录自动创建）"""
    if key_path.exists():
        return serialization.load_pem_private_key(
            key_path.read_bytes(), password=None
        )
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    logger.info("已生成 RSA 传输密钥: %s", key_path)
    return key


def reset_key_cache() -> None:
    """清空密钥缓存（测试隔离用；下次访问会按 KEY_PATH 重新加载/生成）"""
    global _private_key
    _private_key = None


def public_key_pem(key: rsa.RSAPrivateKey) -> str:
    """导出公钥 PEM（SPKI 格式，浏览器 Web Crypto 可直接导入）"""
    return key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")


def decrypt_with_key(key: rsa.RSAPrivateKey, cipher_b64: str) -> str:
    """解密 base64 编码的 RSA-OAEP-SHA256 密文，还原明文密码；任何失败抛 ValueError"""
    try:
        cipher = base64.b64decode(cipher_b64, validate=True)
        plain = key.decrypt(cipher, _OAEP)
        return plain.decode("utf-8")
    except Exception as exc:
        raise ValueError("密码密文解密失败") from exc


def get_public_key_pem() -> str:
    """进程内单例密钥的公钥 PEM（懒加载，供 /auth/public-key 端点）"""
    return public_key_pem(_get_private_key())


def decrypt_password(cipher_b64: str) -> str:
    """用进程内单例私钥解密密码（供 login/register 路由）"""
    return decrypt_with_key(_get_private_key(), cipher_b64)


def _get_private_key() -> rsa.RSAPrivateKey:
    global _private_key
    if _private_key is None:
        _private_key = load_or_create_key(KEY_PATH)
    return _private_key
