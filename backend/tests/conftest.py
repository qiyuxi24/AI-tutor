"""
pytest 全局配置：路径设置与共享 fixtures。

设计：
- 让 `import app.*` 在 pytest 下可用（无论从哪个目录启动 pytest）
- 单元测试不依赖真实网络/LLM：所有涉及 embedding / LLM 的路径均在测试内 mock
- 测试不得污染开发库：用量记账默认落 `backend/app/data/agent_runs/`，见 _isolate_usage_db
"""
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


@pytest.fixture(autouse=True)
def _isolate_usage_db(monkeypatch):
    """用量记账默认落开发库（backend/app/data/agent_runs/agent_runs.db）→ 测试里必须显式传 db_dir。

    假响应同样带 prompt_tokens，不隔离的话跑一次全量测试就往开发库塞几十行假数据。
    这里让"不传 db_dir"的记账直接抛错 —— `usage.record` 内部会吞掉只打 warning，
    所以被测逻辑不受影响；直连记账的测试（test_llm_usage.py）显式传 tmp_path，照常可写。
    """
    from app.core.llm import usage as usage_mod
    real_connect = usage_mod._connect

    def guarded(db_dir=None):
        if db_dir is None:
            raise RuntimeError("测试隔离：llm_usage 只接受显式 db_dir 写入")
        return real_connect(db_dir)

    monkeypatch.setattr(usage_mod, "_connect", guarded)
