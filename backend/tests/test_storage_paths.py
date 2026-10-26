"""数据根取径唯一性守卫（P0-1）。

三张网：
1. `core/storage.py` 声明的路径 == 相对仓库根的**显式期望值** —— 防 storage 自己算错深度；
2. `backend/app` 内除 `storage.py` 外不得再出现"直算数据根"的代码（同一行同时含
   `__file__`/`parents[` 与 `"data"` 路径段）—— 这条即 P0-1 的验收线
   （`grep -rn 'parents\[' backend/app | grep -c data` = 0）；
3. `backend-admin` 的取径是 storage 的镜像（两 app 同名不能 import）→ 断言两边解析结果逐字相同
   —— 这条专盯 P0-1 的后果 1（后台读 9 月就停更的空库）；
4. 数据根的 env 优先级（DSH 插件用 `AI_TUTOR_DATA_ROOT`，A1b 历史变量 `DATA_ROOT` 次之）
   —— 纯函数直测 + 子进程真 import，锁住"插件改目录不会写到开发机的 data/"。

迁移背景见 `docs/架构/整体架构_问题清单与重构方案.md` §2 P0-1 / §3 A1a。
"""
import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

import dotenv

from app.core import storage

# tests/ → backend → <repo>
_REPO = Path(__file__).resolve().parents[2]
_APP_DIR = _REPO / "backend" / "app"

_RAW_ROOT = re.compile(r"parents\[\d+\]|__file__")
_DATA_SEG = re.compile(r"""["']data["']""")


# _DATA_SEG 的父级默认值：tests/ → backend → <repo>


def test_default_constants_are_repo_anchored():
    """未覆写时，根常量 = 相对仓库根的显式期望值（防 storage 自己算错深度）。"""
    assert storage.REPO_ROOT == _REPO
    assert storage.DATA_ROOT == _REPO / "data"
    assert storage.BACKEND_DATA_ROOT == _REPO / "backend" / "data"
    assert storage.RECORDS_ROOT == _REPO / "backend" / "app" / "data"
    assert storage.EVAL_DATA_ROOT == _REPO / "backend" / "eval_data"


def _force_roots(monkeypatch, data_root, legacy_backend, legacy_records):
    monkeypatch.setattr(storage, "DATA_ROOT", data_root)
    monkeypatch.setattr(storage, "LEGACY_BACKEND_DATA_ROOT", legacy_backend)
    monkeypatch.setattr(storage, "LEGACY_RECORDS_ROOT", legacy_records)


def test_single_root_layout_when_no_legacy(monkeypatch, tmp_path):
    """无旧数据（fresh 部署 / 已迁移）→ 所有域收敛到 `DATA_ROOT/<域>`（A1b 单根）。

    这是 A1b 的**目标布局**：kb/rag/quiz/collector/llm/agent_runs/agent_debug 全部并入单根。
    """
    data_root, legacy_backend, legacy_records = (tmp_path / "d"), (tmp_path / "b"), (tmp_path / "a")
    _force_roots(monkeypatch, data_root, legacy_backend, legacy_records)
    # 只建空的根目录，**不建**任何 legacy 子目录 → 触不到回退，验证纯单根布局。
    for p in (data_root, legacy_backend, legacy_records):
        p.mkdir(parents=True, exist_ok=True)

    assert storage.prompts_dir() == data_root / "prompts"
    assert storage.profiles_dir() == data_root / "profiles"
    assert storage.keys_dir() == data_root / "keys"
    assert storage.kb_dir() == data_root / "kb"
    assert storage.rag_dir() == data_root / "rag"
    assert storage.quiz_dir() == data_root / "quiz"
    assert storage.collector_dir() == data_root / "collector"
    assert storage.llm_dir() == data_root / "llm"
    assert storage.records_dir("agent_runs") == data_root / "agent_runs"
    assert storage.records_dir("agent_debug") == data_root / "agent_debug"
    assert storage.knowledge_dir() == data_root / "knowledge"
    assert storage.knowledge_db() == data_root / "knowledge" / "knowledge.db"
    assert storage.nodes_dir(7) == data_root / "knowledge" / "nodes" / "7"
    assert storage.conversations_dir() == data_root / "conversations"


def test_legacy_fallback_only_when_legacy_exists_and_new_missing(monkeypatch, tmp_path):
    """A1b 兼容网：旧目录存在且新目录不存在 → 用旧路径；新目录存在 → 用新路径。"""
    data_root, legacy_backend, legacy_records = (tmp_path / "d"), (tmp_path / "b"), (tmp_path / "a")
    _force_roots(monkeypatch, data_root, legacy_backend, legacy_records)
    data_root.mkdir(parents=True, exist_ok=True)
    (legacy_backend / "kb").mkdir(parents=True)                 # 旧根有 kb
    (legacy_backend / "rag").mkdir(parents=True)                # 旧根有 rag
    (legacy_records / "agent_runs").mkdir(parents=True)         # 旧根有 agent_runs

    # 旧存在、新不存在 → 回退旧（存量部署数据不丢）
    assert storage.kb_dir() == legacy_backend / "kb"
    assert storage.rag_dir() == legacy_backend / "rag"
    assert storage.records_dir("agent_runs") == legacy_records / "agent_runs"
    # 旧不存在 → 单根
    assert storage.quiz_dir() == data_root / "quiz"
    assert storage.llm_dir() == data_root / "llm"
    # 新存在（即便旧也存在）→ 用新（迁移后切走）
    (data_root / "kb").mkdir(parents=True, exist_ok=True)
    assert storage.kb_dir() == data_root / "kb"


def test_no_direct_data_root_arithmetic_outside_storage():
    """除 storage.py 外，不得再自己数父级拼数据根（P0-1 的验收线）。"""
    offenders = []
    for path in _APP_DIR.rglob("*.py"):
        rel = path.relative_to(_APP_DIR).as_posix()
        if rel == "core/storage.py":
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _RAW_ROOT.search(line) and _DATA_SEG.search(line):
                offenders.append(f"{rel}:{lineno}: {line.strip()}")
    assert not offenders, (
        "数据根取径应统一走 core/storage.py，禁止再写 Path(__file__).../\"data\"：\n"
        + "\n".join(offenders)
    )


_ADMIN_CONFIG = _REPO / "backend-admin" / "app" / "core" / "config.py"


def _load_admin_settings(monkeypatch):
    """按文件路径加载 backend-admin 的 config（两个 app 包名都叫 `app`，不能 import）。

    屏蔽 `dotenv.load_dotenv`：本用例断言的是**默认落点**，不该被开发机根 `.env` 里的
    `ADMIN_*` 覆盖项干扰。
    """
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: None)
    spec = importlib.util.spec_from_file_location("_admin_core_config_for_parity", _ADMIN_CONFIG)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.settings


def test_admin_resolves_same_paths_as_storage(monkeypatch):
    """运维后台与主系统必须指向同一批文件 —— P0-1 那两处事实性错误的守卫。

    A1b 后：kb/rag/quiz/collector/agent_runs/agent_debug 全部并入主系统单一 `DATA_ROOT`；
    后台 `backend_data_dir`/`records_dir` 收敛到同一 `DATA_ROOT`（`/{kind}/{user_id}` 由后台拼）。
    """
    s = _load_admin_settings(monkeypatch)
    assert Path(s.db_path) == storage.knowledge_db()
    assert Path(s.backend_data_dir) == storage.DATA_ROOT
    assert Path(s.records_dir) == storage.DATA_ROOT
    assert Path(s.conversations_db) == storage.conversations_dir() / "conversations.db"
    assert Path(s.profiles_dir) == storage.profiles_dir()

# ── 数据根 env 优先级（DSH 插件 / 便携运行时，2026-10-26）─────────────────

def test_resolve_data_root_precedence_is_pure():
    """纯函数直测：`AI_TUTOR_DATA_ROOT` > `DATA_ROOT` > `<repo>/data`，不需要 reload。"""
    resolve = storage._resolve_data_root
    assert resolve({}) == _REPO / "data"
    assert resolve({"DATA_ROOT": str(_REPO / "legacy-root")}) == _REPO / "legacy-root"
    assert resolve({
        "AI_TUTOR_DATA_ROOT": str(_REPO / "plugin-root"),
        "DATA_ROOT": str(_REPO / "legacy-root"),
    }) == _REPO / "plugin-root"
    # 空白不当作"设置了"（继续看下一个 key）；否则插件传空串会静默把数据根打回默认
    assert resolve({"AI_TUTOR_DATA_ROOT": "   ",
                    "DATA_ROOT": str(_REPO / "legacy-root")}) == _REPO / "legacy-root"
    assert resolve({"AI_TUTOR_DATA_ROOT": "  "}) == _REPO / "data"


def _run_storage_probe(monkeypatch, tmp_path, env_overrides):
    """在子进程里 import storage 并打印 DATA_ROOT（隔离本进程已算好的模块常量）。"""
    env = dict(os.environ)
    env.pop("AI_TUTOR_DATA_ROOT", None)
    env.pop("DATA_ROOT", None)
    env.update(env_overrides)
    env["LOG_DIR"] = str(tmp_path / "logs")     # 别污染开发机 backend/logs
    proc = subprocess.run(
        [sys.executable, "-c",
         "from app.core import storage; print(storage.DATA_ROOT)"],
        cwd=str(_REPO / "backend"), env=env, capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def test_env_override_is_visible_to_a_fresh_import(monkeypatch, tmp_path):
    """真 import：两个变量同时存在时用 `AI_TUTOR_DATA_ROOT`（插件专属名）。"""
    plugin_root = tmp_path / "plugin-data"
    legacy_root = tmp_path / "legacy-data"
    out = _run_storage_probe(monkeypatch, tmp_path, {
        "AI_TUTOR_DATA_ROOT": str(plugin_root),
        "DATA_ROOT": str(legacy_root),
    })
    assert Path(out) == plugin_root


def test_legacy_data_root_env_still_works(monkeypatch, tmp_path):
    """向后兼容：只给 A1b 历史变量 `DATA_ROOT` 时行为不变（老脚本/运维文档照旧）。"""
    legacy_root = tmp_path / "legacy-data"
    out = _run_storage_probe(monkeypatch, tmp_path, {"DATA_ROOT": str(legacy_root)})
    assert Path(out) == legacy_root


def test_no_env_falls_back_to_repo_data(monkeypatch, tmp_path):
    out = _run_storage_probe(monkeypatch, tmp_path, {})
    assert Path(out) == _REPO / "data"
