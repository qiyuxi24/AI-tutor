"""每用户 LLM 模型配置：存储 + 状态机。

背景：对话模型原先只能整机切换（根 .env 的 `LLM_*`），所有用户共用同一套服务商。
本模块让每个用户维护自己的模型列表（接入地址 / Key / 模型名），互不影响；
未配置任何模型的用户回落到 .env 的「系统默认档」（见 `fallback.chat_create`）。

真值源 = `backend/data/llm/{user_id}/models.json`（按用户分目录，删号整目录清理）。
数据量极小（个位数模型），用 JSON 原子写而非 SQLite：零 schema 迁移负担。

**两级状态**（两个正交维度，别混）：
- 配置态 `status`：由连通性测试驱动，解释「这个模型现在能不能用」
    unverified --测试成功--> available --测试失败--> unavailable
    任意 --停用--> disabled --启用--> unverified
    （改动接入参数 → 退回 unverified：旧结论对新地址无效）
- 运行时态 `effective_id`：由真实调用结果回写 = 最近一次**实际生效**的模型。
    界面拿它显示「当前生效档位」——主模型故障降级到备用时，它指向备用那个。
"""
import json
import os
import time
import uuid
from pathlib import Path
from typing import Optional

# ─── 配置态取值 ───
STATUS_UNVERIFIED = "unverified"    # 新建 / 改过接入参数 / 从停用恢复 → 未验证
STATUS_AVAILABLE = "available"      # 连通性测试通过
STATUS_UNAVAILABLE = "unavailable"  # 连通性测试失败（last_error 说明原因）
STATUS_DISABLED = "disabled"        # 用户停用：不进候选链

# 改动后需要重测的字段（接入参数）
_ACCESS_FIELDS = ("name", "base_url", "api_key", "model")


def default_data_dir() -> Path:
    """模型配置目录：backend/data/llm（本文件 → 上溯 3 层 = backend）。"""
    return Path(__file__).resolve().parents[3] / "data" / "llm"


def mask_key(key: str) -> str:
    """API Key 掩码（对外唯一形态）：只留头 4 位 + 尾 4 位，短 key 全掩。
    故意不返回明文——列表接口/前端都不该拿到可用于调用的完整凭据。"""
    return f"{key[:4]}****{key[-4:]}" if len(key) > 8 else "****"


class ModelStore:
    """单个用户的模型配置读写（不含网络调用，纯数据 + 状态迁移）。"""

    def __init__(self, user_id: int, data_dir: Optional[Path] = None):
        self.user_id = user_id
        self.dir = Path(data_dir) if data_dir is not None else default_data_dir()
        self.path = self.dir / str(user_id) / "models.json"

    # ══════════════ 读取 ══════════════

    def snapshot(self) -> dict:
        """对外快照：当前激活 / 当前生效档位 / 模型列表（Key 已掩码）。"""
        doc = self._load()
        return {
            "active_id": doc["active_id"],
            "effective_id": doc["effective_id"],
            "effective_at": doc["effective_at"],
            "models": [self._public(m) for m in doc["models"]],
        }

    def get(self, model_id: str) -> dict:
        """单个模型的对外形态；不存在抛 KeyError。"""
        return self._public(self._find(model_id))

    def secret(self, model_id: str) -> dict:
        """含明文 api_key 的内部形态（仅真正发起调用时用）。"""
        return dict(self._find(model_id))

    def candidates(self) -> list[dict]:
        """运行时候选链（主 → 备），供 `fallback.chat_create` 顺序尝试。

        - 当前激活模型排首位（用户显式选择优先，即便尚未验证也放行——否则
          「新建 → 激活」后要再等一次测试才生效，反直觉）
        - 其余 `available` 模型按创建顺序补位（备用）
        - `disabled` / 未验证 / 测试失败的模型不进链
        - 空链 = 该用户没有可用自定义模型 → 调用方回落到 .env 系统默认档
        """
        doc = self._load()
        models = doc["models"]
        active_id = doc["active_id"]
        ranked = sorted(
            models,
            key=lambda m: (m["id"] != active_id, m["created_at"]),
        )
        out = []
        for m in ranked:
            if m["status"] == STATUS_DISABLED:
                continue
            is_active = m["id"] == active_id
            if is_active or m["status"] == STATUS_AVAILABLE:
                out.append({
                    "id": m["id"], "name": m["name"], "model": m["model"],
                    "base_url": m["base_url"], "api_key": m["api_key"],
                })
        return out

    # ══════════════ 写入 ══════════════

    def add(self, *, name: str, base_url: str, api_key: str, model: str) -> dict:
        """新建模型（未验证态）。"""
        now = time.time()
        entry = {
            "id": f"m_{uuid.uuid4().hex[:8]}",
            "name": name, "base_url": base_url, "api_key": api_key, "model": model,
            "status": STATUS_UNVERIFIED,
            "last_error": "", "last_check_at": 0.0,
            "created_at": now, "updated_at": now,
        }
        doc = self._load()
        doc["models"].append(entry)
        self._save(doc)
        return self._public(entry)

    def update(self, model_id: str, **fields) -> dict:
        """改接入参数。改了参数 → 退回未验证（旧结论对新地址无效）。"""
        doc = self._load()
        entry = self._find(model_id, doc)
        for k in _ACCESS_FIELDS:
            if k in fields and fields[k] is not None:
                entry[k] = fields[k]
        entry["status"] = STATUS_UNVERIFIED
        entry["last_error"] = ""
        entry["last_check_at"] = 0.0
        entry["updated_at"] = time.time()
        # 当前生效档位若指向本模型，参数已变 → 失效，退回系统默认
        if doc["effective_id"] == model_id:
            doc["effective_id"] = None
        self._save(doc)
        return self._public(entry)

    def delete(self, model_id: str) -> bool:
        """删除模型（幂等：不存在返回 False）。删的是激活模型则回落系统默认档。"""
        doc = self._load()
        before = len(doc["models"])
        doc["models"] = [m for m in doc["models"] if m["id"] != model_id]
        if len(doc["models"]) == before:
            return False
        if doc["active_id"] == model_id:
            doc["active_id"] = None
        if doc["effective_id"] == model_id:
            doc["effective_id"] = None
        self._save(doc)
        return True

    def set_active(self, model_id: str) -> dict:
        """把某个模型设为当前使用的模型（状态迁移：active_id 指向它）。"""
        doc = self._load()
        entry = self._find(model_id, doc)
        doc["active_id"] = model_id
        self._save(doc)
        return self._public(entry)

    def set_status(self, model_id: str, status: str) -> dict:
        """直接设置配置态（仅供「停用 / 恢复」这类显式操作使用）。"""
        doc = self._load()
        entry = self._find(model_id, doc)
        entry["status"] = status
        entry["updated_at"] = time.time()
        if status == STATUS_DISABLED and doc["active_id"] == model_id:
            doc["active_id"] = None
        self._save(doc)
        return self._public(entry)

    def mark_test(self, model_id: str, ok: bool, error: str = "") -> dict:
        """连通性测试结果回写（available ↔ unavailable）。"""
        doc = self._load()
        entry = self._find(model_id, doc)
        entry["status"] = STATUS_AVAILABLE if ok else STATUS_UNAVAILABLE
        entry["last_error"] = "" if ok else (error or "连通性测试失败")
        entry["last_check_at"] = time.time()
        self._save(doc)
        return self._public(entry)

    def mark_effective(self, model_id: str) -> None:
        """真实调用成功 → 记录「当前生效档位」（界面据此显示主/备降级情况）。"""
        doc = self._load()
        entry = self._find(model_id, doc)
        doc["effective_id"] = model_id
        doc["effective_at"] = time.time()
        entry["last_error"] = ""
        entry["status"] = STATUS_AVAILABLE
        self._save(doc)

    # ══════════════ 内部 ══════════════

    def _empty(self) -> dict:
        return {"version": 1, "active_id": None, "effective_id": None,
                "effective_at": 0.0, "models": []}

    def _load(self) -> dict:
        """读配置；文件缺失/损坏按空配置兜底（纯读，不落盘）。"""
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return self._empty()
        if not isinstance(raw, dict) or not isinstance(raw.get("models"), list):
            return self._empty()
        doc = {**self._empty(), **raw}
        doc["models"] = [m for m in doc["models"] if isinstance(m, dict)]
        return doc

    def _save(self, doc: dict) -> None:
        """原子写（同目录临时文件 + os.replace），避免半截 JSON。

        ponytail: 无进程间锁——uvicorn 单 worker + 单事件循环下，同用户并发写
        极小概率互相覆盖（代价 = 丢一次状态回写，下次调用会补上）。要严格串行
        就加文件锁，当前不值得。
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def _find(self, model_id: str, doc: Optional[dict] = None) -> dict:
        doc = doc or self._load()
        for m in doc["models"]:
            if m["id"] == model_id:
                return m
        raise KeyError(f"模型不存在: {model_id}")

    @staticmethod
    def _public(entry: dict) -> dict:
        out = {k: v for k, v in entry.items() if k != "api_key"}
        out["api_key_masked"] = mask_key(entry.get("api_key", ""))
        return out
