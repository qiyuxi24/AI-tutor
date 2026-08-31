"""
pytest 全局配置：路径设置与共享 fixtures。

设计：
- 让 `import app.*` 在 pytest 下可用（无论从哪个目录启动 pytest）
- 单元测试不依赖真实网络/LLM：所有涉及 embedding / LLM 的路径均在测试内 mock
"""
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
