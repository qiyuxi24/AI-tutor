"""数据根与取径的**唯一声明处**（P0-1 归位 → A1b 单根合并）。

背景（2026-10-07 架构审计 P0-1；A1b 见 `docs/架构/整体架构_问题清单与重构方案.md`）：
  原先每个模块各自用 `Path(__file__).parents[N] / "data"` **数深度**得出"数据根"。A1a 已把
  声明收敛进本模块；A1b 再把 **四个根合并成单一 `DATA_ROOT`**（默认 `<repo>/data`，env 可覆写），
  其余域全部 `DATA_ROOT/<域>`。为保证线上/存量数据不因位置变化而失联，保留**一个版本的
  旧路径回退**：旧目录存在且新目录不存在 → 用旧路径 + `logger.warning`（跑 `migrate_data_root.py`
  并入单根后再切走）。

  单根布局（`migrate_data_root.py` 的 SRC/DST 由本模块的 `LEGACY_*` 与 `DATA_ROOT` 派生）：
     DATA_ROOT          <repo>/data               图谱 / conversations / profiles / prompts / keys
                                                        + kb / rag / quiz / collector / llm / agent_runs / agent_debug
     EVAL_DATA_ROOT     <repo>/backend/eval_data  离线评测夹具（非用户数据，不动）

约定：`backend-admin` 与本包同名 `app`、不能互相 import → 它把同样的默认路径**再声明一份**
      （`backend-admin/app/core/config.py`），由 `tests/test_storage_paths.py` 的
      「admin 解析路径 == 本模块声明」用例锁死一致性。
"""
import logging
import os
from pathlib import Path

logger = logging.getLogger("ai-tutor")

# backend/app/core/storage.py → parents[3] = 仓库根
REPO_ROOT = Path(__file__).resolve().parents[3]

# 单一数据根（env `DATA_ROOT` 可覆写；默认 <repo>/data）。**用户数据唯一活点**。
DATA_ROOT = Path(os.environ.get("DATA_ROOT", REPO_ROOT / "data"))

# A1b 前的旧根（只用于**回退**与 `migrate_data_root.py` 定位 SRC；不再作为主数据根）。
# ⚠️ 注意：`backend/data/agent_runs`（旧 P0-1 停更库）**不是**迁徙源 —— 主记录库在
#    `backend/app/data/agent_runs`；留一个版本回退也以 `RECORDS_ROOT` 为准。
LEGACY_BACKEND_DATA_ROOT = REPO_ROOT / "backend" / "data"      # 旧 kb/rag/quiz/collector/llm
LEGACY_RECORDS_ROOT = REPO_ROOT / "backend" / "app" / "data"   # 旧 agent_runs / agent_debug

EVAL_DATA_ROOT = REPO_ROOT / "backend" / "eval_data"

# ── 向后兼容别名（A1b 前的老常量，仍被 records.py 等直接引用）──
# 语义 = A1b 前的**旧根**（保留旧部署可用，直至 migrate_data_root 并入单根）。
# 新代码应改走 *_dir() 系列函数（单根 + 旧路径回退），别再直接引用这两个常量。
BACKEND_DATA_ROOT = LEGACY_BACKEND_DATA_ROOT
RECORDS_ROOT = LEGACY_RECORDS_ROOT


def _resolve(new: Path, legacy: Path) -> Path:
    """单根域名路径 + 旧路径回退（**A1b 的兼容网**）。

    旧目录存在而新目录不存在 → 用旧路径并告警（存量部署数据不丢，跑 `migrate_data_root.py`
    并入单根后再切走）；否则一律新路径。**幂等**、不创建目录（创建由各 manager 负责）。
    """
    if legacy.exists() and not new.exists():
        logger.warning(
            "数据根 %s 不存在，回退到旧存储 %s（尚未迁移；跑 backend/scripts/migrate_data_root.py 并入单根）",
            new, legacy,
        )
        return legacy
    return new


def _single_root(domain: str, old_root: Path, old_domain: str | None = None) -> Path:
    """`<DATA_ROOT>/<domain>`，按旧布局回退（缺省旧域名 = 同名）。"""
    legacy = old_root / (old_domain or domain)
    return _resolve(DATA_ROOT / domain, legacy)


# ── DATA_ROOT 域（A1a 起就在 DATA_ROOT，路径不变）──
def prompts_dir() -> Path:
    """系统提示词模板目录（`system_prompt_common.j2`）"""
    return DATA_ROOT / "prompts"


def profiles_dir() -> Path:
    """用户画像 JSON 目录（`{user_id}.json` + `avatars/`）"""
    return DATA_ROOT / "profiles"


def keys_dir() -> Path:
    """RSA 私钥等本地凭据目录（运行时数据，不进代码库）"""
    return DATA_ROOT / "keys"


def knowledge_dir() -> Path:
    """图谱域根（`knowledge.db` + `nodes/`）"""
    return DATA_ROOT / "knowledge"


def knowledge_db() -> Path:
    """图谱 SQLite 库（唯一真值文件）"""
    return knowledge_dir() / "knowledge.db"


def nodes_dir(user_id: int) -> Path:
    """某用户的节点 MD 目录（`nodes/{user_id}/`）"""
    return knowledge_dir() / "nodes" / str(user_id)


def conversations_dir() -> Path:
    """对话同步库目录（`conversations.db`）"""
    return DATA_ROOT / "conversations"


# ── BACKEND_DATA_ROOT → DATA_ROOT 收敛（A1b 起移入单根）──
# 注意：kb/rag/quiz/collector 的 manager 接的是**域根**，用户子目录（/{user_id}）
# 由各 manager 自己拼（`self.data_dir / str(user_id)`），别在这里提前下钻。
def kb_dir() -> Path:
    """上传知识库域根（`kb/{user_id}/kb.db` + `rag.db` + whoosh + `files/`）"""
    return _single_root("kb", LEGACY_BACKEND_DATA_ROOT)


def rag_dir() -> Path:
    """图谱 RAG 域根（`rag/{user_id}/rag.db` + whoosh 索引）"""
    return _single_root("rag", LEGACY_BACKEND_DATA_ROOT)


def quiz_dir() -> Path:
    """题库域根（`quiz/{user_id}/quiz.db`）"""
    return _single_root("quiz", LEGACY_BACKEND_DATA_ROOT)


def collector_dir() -> Path:
    """资源采集域根（`collector/{user_id}/collector.db` + `subjects.json`）"""
    return _single_root("collector", LEGACY_BACKEND_DATA_ROOT)


def llm_dir() -> Path:
    """每用户模型配置域根（`llm/{user_id}/models.json`）"""
    return _single_root("llm", LEGACY_BACKEND_DATA_ROOT)


# ── RECORDS_ROOT → DATA_ROOT 收敛（A1b 起移入单根；记录型库统一走 core/records.py）──
def records_dir(kind: str) -> Path:
    """某个记录库的目录（kind ∈ `agent_runs` / `agent_debug`）"""
    return _single_root(kind, LEGACY_RECORDS_ROOT)
