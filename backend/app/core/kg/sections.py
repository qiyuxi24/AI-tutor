"""原 `core/knowledge_graph.py` 的 _SectionsMixin 分域（逐字搬运，行为不变）。"""
import json
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from ._common import *  # noqa: F401,F403


class _SectionsMixin:
    def manifest_path(self, node_id: str) -> Path:
        """节点 manifest.json 的绝对路径（存在与否不代表小节化，用 `has_sections` 判）。"""
        return self.nodes_dir / node_id / "manifest.json"

    def read_manifest(self, node_id: str) -> dict | None:
        """读节点 manifest；**无 manifest 或 JSON 损坏 → None**（老节点 / 未小节化）。

        无锁读：manifest 一律原子写（临时文件 + os.replace），读侧不会看到半截文件。
        """
        path = self.manifest_path(node_id)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            logger.warning(f"manifest 解析失败，视为无小节：{path}")
            return None
        return data if isinstance(data, dict) else None

    def has_sections(self, node_id: str) -> bool:
        """节点是否**已小节化**（有 manifest 且至少一个小节）。

        以"有无小节"为准而非"有无 manifest 文件"：仅挂过试卷引用（`add_quiz_ref`）但尚未
        建节的节点，仍应按老节点走单文件读路径（守 D5 不迁移）。
        """
        manifest = self.read_manifest(node_id)
        return bool(manifest and manifest.get("sections"))

    def list_sections(self, node_id: str) -> list[dict]:
        """小节清单（**只元数据、不含正文**）；无 manifest → []。

        元素即 manifest.sections 的条目：`{id, title, kind, file, status, brief,
        origin, sources, created_at, updated_at}`。前端列表页据此渲染，点开某节再 `read_section`。

        `sources` 是**本节**的资料来源（`[{doc_id, doc_name, section, chunk_id}, ...]`，
        对齐 `get_sources` 的条目形状）；老 manifest 无此键 → 前端按空列表处理。
        """
        manifest = self.read_manifest(node_id)
        if not manifest:
            return []
        return list(manifest.get("sections") or [])

    def read_section(self, node_id: str, section_id: str) -> str:
        """按 manifest 路由读单个小节 MD 正文；无 manifest / 无此节 / 文件缺失 → ""。"""
        entry = self._find_section(self.read_manifest(node_id), section_id)
        if entry is None:
            return ""
        path = self.nodes_dir / node_id / str(entry.get("file") or "")
        try:
            return path.read_text(encoding="utf-8") if path.is_file() else ""
        except OSError:
            return ""

    def create_section(self, node_id: str, title: str, kind: str = "custom",
                       content: str = "", brief: str = "", origin: str = "section_gen",
                       sources: list[dict] | None = None) -> str:
        """建一个小节条目并分配 `section_id`（`s01`/`s02`…，现有最大编号 +1），返回它。

        参数:
            node_id: 节点 ID，**必须存在且属当前用户**（否则 ValueError，防跨用户写入）
            title:   小节标题（同时用于文件名安全化）
            kind:    软标签（`definition`/`formula`/`method`/`example`/`mistake`/`custom`…），
                     **不做枚举校验**（配合设计 D4"模板仅供参考"）
            content: 正文；传了 → `filled`，没传 → `pending`（生成管线阶段①先建条目、阶段②再填）
            brief:   一句话说明（供列表/生成参考）
            origin:  产出方（默认 `section_gen`）
            sources: 本节的资料来源（GQ-18 同款五键 `{doc_id, doc_name, section, chunk_id, extracted_at}`）；
                     与 `nodes.sources` 的差别是**粒度到节**、且是**本次生成时的快照**，不入库、随小节走
        返回:
            新小节的 `section_id`
        副作用:
            落盘小节 MD（空内容也占位，保证 manifest↔文件一致）+ 原子写 manifest；
            失效该节点的内容缓存
        """
        node = self.get_node(node_id)
        if node is None:
            raise ValueError(f"节点不存在：{node_id}")

        with self._manifest_lock:
            manifest = self.read_manifest(node_id) or self._new_manifest(node_id, node)
            section_id = self._next_section_id(manifest.get("sections") or [])
            filename = section_filename(section_id, title)
            now = datetime.now().isoformat()
            manifest.setdefault("sections", []).append({
                "id": section_id,
                "title": title,
                "kind": kind,
                "file": filename,
                "status": SECTION_STATUS_FILLED if (content or "").strip()
                          else SECTION_STATUS_PENDING,
                "brief": brief,
                "origin": origin,
                # 复用 L0 的规范化：去未知键（如材料自带的 text）、补 extracted_at、
                # 丢非法条目 —— 调用方直接甩 materials 进来也不会把 manifest 撑大。
                "sources": [e for e in (normalize_source_entry(s) for s in (sources or [])) if e],
                # 学习状态与生成状态分开存（见 LEARN_MARK_* 注释）
                "learn": new_learn_state(),
                "created_at": now,
                "updated_at": now,
            })
            # 先落小节文件、再写 manifest：manifest 宁可缺一行，也不指向不存在的文件
            target = self.nodes_dir / node_id
            target.mkdir(parents=True, exist_ok=True)
            (target / filename).write_text(content or "", encoding="utf-8")
            self._write_manifest(node_id, manifest)
        self.invalidate_content_cache(node_id)
        return section_id

    def write_section(self, node_id: str, section_id: str, content: str) -> None:
        """写单个小节正文，并把它标为 `filled`、刷新 `updated_at`。

        异常:
            ValueError: 无 manifest 或该 `section_id` 不存在
        """
        with self._manifest_lock:
            manifest = self.read_manifest(node_id)
            entry = self._find_section(manifest, section_id)
            if entry is None:
                raise ValueError(f"小节不存在：{node_id}/{section_id}")
            (self.nodes_dir / node_id / str(entry["file"])).write_text(content or "", encoding="utf-8")
            entry["status"] = SECTION_STATUS_FILLED
            entry["updated_at"] = datetime.now().isoformat()
            self._write_manifest(node_id, manifest)
        self.invalidate_content_cache(node_id)

    def set_section_status(self, node_id: str, section_id: str, status: str) -> None:
        """置小节状态（生成管线用 `failed` 标记单节失败，便于单独重试）。

        异常:
            ValueError: 无 manifest 或该 `section_id` 不存在
        """
        with self._manifest_lock:
            manifest = self.read_manifest(node_id)
            entry = self._find_section(manifest, section_id)
            if entry is None:
                raise ValueError(f"小节不存在：{node_id}/{section_id}")
            entry["status"] = status
            entry["updated_at"] = datetime.now().isoformat()
            self._write_manifest(node_id, manifest)
        self.invalidate_content_cache(node_id)

    def get_section_learn(self, node_id: str, section_id: str) -> dict:
        """读某小节的学习状态（老 manifest 无该字段 → 默认值，**不写盘**）。

        异常: ValueError: 无 manifest / 该 section_id 不存在
        """
        entry = self._find_section(self.read_manifest(node_id), section_id)
        if entry is None:
            raise ValueError(f"小节不存在：{node_id}/{section_id}")
        return read_learn_state(entry)

    def set_section_learn(self, node_id: str, section_id: str, *,
                          mark: str | None = None, read: bool | None = None,
                          passed: bool | None = None, mark_by: str | None = None,
                          bump_attempts: bool = False) -> dict:
        """局部更新小节学习状态：`None` 的字段不动；`bump_attempts` 给 attempts +1。

        `mark_by` 只在**同时给了 mark** 时才是声明"谁标的"（`user` 学生自评 / `ai` 讲完自动记；
        标记退回 `unknown` 时来源一并清空）—— 调用方给 mark 就请一并给 mark_by，
        否则前端只能显示中性的"已懂"，看不出是学生说的还是 AI 记的。

        返回**更新后的完整状态**（调用方直接拿去刷新前端，不用再读一次）。

        异常:
            ValueError: 无 manifest / 该 section_id 不存在 / mark 或 mark_by 非法
        """
        if mark is not None and mark not in LEARN_MARKS:
            raise ValueError(f"非法的小节学习标记：{mark}")
        if mark_by is not None and mark_by not in LEARN_MARK_BYS:
            raise ValueError(f"非法的小节标记来源：{mark_by}")
        with self._manifest_lock:
            manifest = self.read_manifest(node_id)
            entry = self._find_section(manifest, section_id)
            if entry is None:
                raise ValueError(f"小节不存在：{node_id}/{section_id}")
            learn = read_learn_state(entry)
            if mark is not None:
                learn["mark"] = mark
                if mark_by is not None:
                    learn["mark_by"] = mark_by
                elif mark == LEARN_MARK_UNKNOWN:
                    learn["mark_by"] = ""      # 取消标记 → 来源也清掉，别留旧值
            if read is not None:
                learn["read"] = bool(read)
            if passed is not None:
                learn["passed"] = bool(passed)
            if bump_attempts:
                learn["attempts"] = int(learn.get("attempts") or 0) + 1
            learn["updated_at"] = datetime.now().isoformat()
            entry["learn"] = learn
            self._write_manifest(node_id, manifest)
        return learn

    def section_learn_progress(self, node_id: str) -> dict:
        """该节点的小节学习进度汇总（进度条 + 「全部通过 → 节点掌握」判定用）。

        返回 `{total, passed, read, mark_unknown, mark_understood, mark_confused, all_passed}`。
        `all_passed` 只在**有至少一节**时为 True —— 没有小节的节点根本不适用这套机制。
        """
        states = [read_learn_state(s) for s in self.list_sections(node_id)]
        passed = sum(1 for st in states if st["passed"])
        return {
            "total": len(states),
            "passed": passed,
            "read": sum(1 for st in states if st["read"]),
            **{f"mark_{m}": sum(1 for st in states if st["mark"] == m)
               for m in LEARN_MARKS},
            "all_passed": bool(states) and passed == len(states),
        }

    def upsert_learning_session(self, node_id: str, *, section_id: str = "",
                                intended_action: str = "", consent: str = "",
                                reason: str = "") -> None:
        """写/更新该用户在该节点的学习会话（每人每节点一行，幂等 UPSERT）。

        `updated_at` 由本层统一盖章（调用方不传时间，避免各处各写一个 now）。
        """
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO learning_sessions
                    (user_id, node_id, section_id, intended_action, consent, reason, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, node_id) DO UPDATE SET
                    section_id      = excluded.section_id,
                    intended_action = excluded.intended_action,
                    consent         = excluded.consent,
                    reason          = excluded.reason,
                    updated_at      = excluded.updated_at
                """,
                (self.user_id, node_id, section_id, intended_action, consent, reason,
                 datetime.now().isoformat()),
            )

    def get_learning_session(self, node_id: str) -> Optional[dict]:
        """读该用户在该节点的学习会话；没有则 None（不建行 —— 读操作零副作用）。"""
        row = self._conn.execute(
            "SELECT node_id, section_id, intended_action, consent, reason, updated_at "
            "FROM learning_sessions WHERE user_id = ? AND node_id = ?",
            (self.user_id, node_id),
        ).fetchone()
        return dict(row) if row else None

    def delete_section(self, node_id: str, section_id: str) -> bool:
        """删小节：删 manifest 条目 + 删对应 MD 文件。返回是否真删掉。

        无 manifest / 无此节 → False。仅清本小节，不动节点本体与其他节。
        """
        with self._manifest_lock:
            manifest = self.read_manifest(node_id)
            entry = self._find_section(manifest, section_id)
            if entry is None:
                return False
            manifest["sections"].remove(entry)
            path = self.nodes_dir / node_id / str(entry.get("file") or "")
            if path.is_file():
                path.unlink()
            self._write_manifest(node_id, manifest)
        self.invalidate_content_cache(node_id)
        return True

    def clear_sections(self, node_id: str) -> int:
        """清掉该节点的**全部**小节（删 MD 文件 + manifest 条目），返回删除数量。

        与 `delete_section` 的差别：本方法一次性清空，并**顺带摘掉指向这些小节的试卷引用**
        —— `add_quiz_ref` 存的是 `{id, section_id}`，小节没了引用就成悬空路由（详情页的
        「属 xx」会指向不存在的小节）。节点级引用（`section_id=""`）不属任何小节，保留。

        无 manifest / 无小节 → 0（不改任何文件）。
        """
        with self._manifest_lock:
            manifest = self.read_manifest(node_id)
            sections = list(manifest.get("sections") or []) if manifest else []
            if not sections:
                return 0
            gone = {s.get("id") for s in sections}
            root = self.nodes_dir / node_id
            for s in sections:
                path = root / str(s.get("file") or "")
                if path.is_file():
                    path.unlink()
            manifest["sections"] = []
            manifest["quizzes"] = [q for q in (manifest.get("quizzes") or [])
                                   if (q.get("section_id") or "") not in gone]
            self._write_manifest(node_id, manifest)
        self.invalidate_content_cache(node_id)
        return len(sections)

    def add_quiz_ref(self, node_id: str, quiz_id: str, section_id: str = "") -> None:
        """把小节（或节点级，`section_id=""`）挂载一条试卷引用（第一版只做路由，不建题）。

        节点无 manifest 时按其节点信息新建空 manifest（`sections` 空 → `has_sections`
        仍为 False，老节点照旧按单文件读）。多次挂载即追加，不去重。

        异常:
            ValueError: 节点不存在或不属于当前用户
        """
        node = self.get_node(node_id)
        if node is None:
            raise ValueError(f"节点不存在：{node_id}")
        with self._manifest_lock:
            manifest = self.read_manifest(node_id) or self._new_manifest(node_id, node)
            manifest.setdefault("quizzes", []).append({
                "id": quiz_id,
                "section_id": section_id,
                "source": "quiz_store",
                "created_at": datetime.now().isoformat(),
            })
            self._write_manifest(node_id, manifest)
        self.invalidate_content_cache(node_id)

    def _new_manifest(self, node_id: str, node: dict) -> dict:
        """按节点行造一个空 manifest（name/subject 用 `node_subject` 兜底，与全库写法一致）。"""
        return {
            "node_id": node_id,
            "name": node.get("name", ""),
            "subject": self.node_subject(node),
            "version": 1,
            "sections": [],
            "quizzes": [],
        }

    @staticmethod
    def _find_section(manifest: dict | None, section_id: str) -> dict | None:
        """在 manifest.sections 里按 id 找条目（无 manifest / 未命中 → None）。"""
        if not manifest:
            return None
        for s in manifest.get("sections") or []:
            if s.get("id") == section_id:
                return s
        return None

    @staticmethod
    def _next_section_id(sections: list[dict]) -> str:
        """下一个 `section_id`：扫现有 `sNN` 取最大编号 +1，零填充两位。"""
        max_n = 0
        for s in sections:
            m = re.match(r"s(\d+)$", str(s.get("id") or ""))
            if m:
                max_n = max(max_n, int(m.group(1)))
        return f"s{max_n + 1:02d}"

    def _write_manifest(self, node_id: str, manifest: dict) -> None:
        """原子写 manifest：写临时文件再 `os.replace`（同目录内 rename 原子）。

        必须在 `self._manifest_lock` 内调用（本方法自身不加锁，避免不可重入死锁）。
        """
        target = self.manifest_path(node_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.parent / f".manifest.{uuid.uuid4().hex}.tmp"
        tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, target)  # 原子替换：旧文件要么整体保留、要么被整体换掉
