"""
传输加密跨语言往返冒烟测试（离线，零网络）：

1. 用后端模块生成临时 RSA 密钥
2. 调用 node 运行前端同款加密逻辑（frontend/scripts/verify-crypto.mjs）加密密码
3. 用后端私钥解密，断言与原文一致

用法（从 backend 目录）: venv\\Scripts\\python.exe scripts\\smoke_transport_crypto.py
"""
import subprocess
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BACKEND_DIR.parent / "frontend"
sys.path.insert(0, str(BACKEND_DIR))

from app.core import transport_crypto as tc  # noqa: E402

PASSWORD = "跨语言Passw0rd!"


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        key = tc.load_or_create_key(Path(td) / "k.pem")
        pem_path = Path(td) / "pub.pem"
        pem_path.write_text(tc.public_key_pem(key), encoding="ascii")

        result = subprocess.run(
            ["node", "scripts/verify-crypto.mjs", str(pem_path), PASSWORD],
            cwd=FRONTEND_DIR,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            print("[FAIL] node 加密脚本执行失败:")
            print(result.stderr)
            return 1

        cipher_b64 = result.stdout.strip()
        plain = tc.decrypt_with_key(key, cipher_b64)
        assert plain == PASSWORD, f"往返不一致: {plain!r} != {PASSWORD!r}"
        print(f"[OK] 前端加密 -> 后端解密往返一致 ({len(cipher_b64)} 字符密文)")
        return 0


if __name__ == "__main__":
    sys.exit(main())
