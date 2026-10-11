"""
pytest 全局配置：路径设置与共享 fixtures。

设计：
- 让 `import app.*` 在 pytest 下可用（无论从哪个目录启动 pytest）
- 单元测试不依赖真实网络/LLM：所有涉及 embedding / LLM 的路径均在测试内 mock
- 测试不得污染开发库：用量记账 / 待投递通知默认落 `backend/app/data/agent_runs/`，
  见 _isolate_usage_db / _isolate_notices_db
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


@pytest.fixture(autouse=True)
def _isolate_rag_index(tmp_path, monkeypatch):
    """图谱 RAG 索引（P0-1 起写路径会"建完即入索引"）在测试里必须隔离。

    `KnowledgeGraph` 的写出口现在会登记 dirty，端点/服务再调
    `rag_manager.index_node`（全局单例，data_dir 默认仓库 `data/rag`）——
    不隔离的话，跑一次测试就会往真实索引库写文档，并触发**真实嵌入网络调用**
    （与 `_isolate_usage_db` / `_isolate_notices_db` 同一动机）。

    注意只替换**全局实例**并把 `_embed` 挂成**实例属性**：
    直接 patch `RagManager._embed` 会打穿 `test_embed_texts` 对类方法的断言
    （那里要求在自建 manager 上验证 `_embed` 是薄委托）。
    """
    from app.core.rag import manager as rag_mod

    fresh = rag_mod.RagManager(data_dir=tmp_path / "rag")

    async def _no_embed(texts):        # 隔离网络：嵌入腿判失败，只验证索引是否被写
        return []

    fresh._embed = _no_embed           # 实例级（类级会打穿自建 manager 的用例）
    monkeypatch.setattr(rag_mod, "rag_manager", fresh)


@pytest.fixture(autouse=True)
def _isolate_notices_db(monkeypatch):
    """待投递通知（A2-3）与用量同库同根：不隔离就会往开发库塞测试通知。

    与 _isolate_usage_db 同一手法，但抛的是 `sqlite3.OperationalError` ——
    `notices.record` / `mark_delivered` / `take_pending` 都以 `except sqlite3.Error`
    兜底（推送主流程不受影响，只是没落库）；直连通知存储的测试显式传 tmp_path 照常可写。
    """
    import sqlite3

    from app.core import notices as notices_mod
    real_connect = notices_mod._connect

    def guarded(db_dir=None):
        if db_dir is None:
            raise sqlite3.OperationalError("测试隔离：pending_notices 只接受显式 db_dir 写入")
        return real_connect(db_dir)

    monkeypatch.setattr(notices_mod, "_connect", guarded)
