"""原 `core/knowledge_graph.py` 的 _ContentMixin 分域（逐字搬运，行为不变）。"""
from datetime import datetime
from typing import Optional

from ._common import *  # noqa: F401,F403


class _ContentMixin:
    def node_content_text(self, node_id: str) -> str:
        """
        节点正文全文 —— **所有"读节点正文"的调用方的唯一出口**。

        有小节 → 拼全部小节 MD（新数据结构的正文在 `{node_id}/` 里）；否则 → 单 MD。
        为什么必须收口：小节化节点的主 MD 只剩骨架占位（甚至没有主 MD，见小节化方案 D1），
        绕过这里直读 `nodes_dir/{id}.md` 会拿到"待完善..."或空串 —— 图谱上下文注入、
        RAG 索引、出题依据、先修推断全会因此失明。
        """
        sections = self.list_sections(node_id)
        if sections:
            parts = [self.read_section(node_id, s["id"])
                     for s in sections if s.get("id")]
            joined = "\n\n".join(p for p in parts if p and p.strip())
            if joined:
                return joined

        md_path = self.nodes_dir / f"{node_id}.md"
        if not md_path.is_file():
            return ""
        try:
            return md_path.read_text(encoding="utf-8")
        except OSError:
            return ""

    def get_node_content_preview(
        self, node_id: str, max_lines: int = 30, max_chars: int = 1000
    ) -> str:
        """
        读取节点正文的前 N 行摘要（带缓存，减少文件 I/O 开销）；正文口径见 `node_content_text`

        参数:
            node_id: 节点 ID
            max_lines: 最大读取行数
            max_chars: 最大返回字符数

        返回:
            正文预览字符串（小节化节点 = 各小节拼接后的前 N 行）
        """
        cache_key = f"{node_id}:{max_lines}:{max_chars}"
        if cache_key in self._content_cache:
            return self._content_cache[cache_key]

        if self.get_node(node_id) is None:
            return ""

        text = self.node_content_text(node_id)
        content = "".join(text.splitlines(keepends=True)[:max_lines])[:max_chars]

        self._content_cache[cache_key] = content
        return content

    def invalidate_content_cache(self, node_id: str | None = None) -> None:
        """
        清除内容缓存（MD 文件更新后调用）

        参数:
            node_id: 指定节点则只清除该节点缓存，None 则清空全部
        """
        if node_id is None:
            self._content_cache.clear()
        else:
            keys_to_remove = [k for k in self._content_cache if k.startswith(f"{node_id}:")]
            for k in keys_to_remove:
                del self._content_cache[k]

    def update_node_content(self, node_id: str, content: str, mode: str = "append",
                            caller: str = "human",
                            content_status: Optional[str] = CONTENT_STATUS_FILLED) -> None:
        """
        更新节点对应 MD 文件的内容

        参数:
            node_id: 节点 ID
            content: 要写入的内容
            mode: "replace" 替换全文 / "append" 追加到末尾
            caller: 调用方标识（"human" 或 "ai"），用于权限检查
            content_status: 写入后落到的填充状态（默认 filled —— 写正文即视为已填充，
                       两阶段建图的骨架节点据此转 filled）。写空内容不改状态，传 None 也不改。

        异常:
            ValueError: 节点不存在或不支持的写入模式
            PermissionError: AI 试图修改人类创建的节点
        """
        self._guard_human_content(caller, node_id)

        node = self.get_node(node_id)
        if node is None:
            raise ValueError(f"节点不存在：{node_id}")

        md_path = self.nodes_dir / f"{node_id}.md"

        if mode == "replace":
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(content)
        elif mode == "append":
            with open(md_path, "a", encoding="utf-8") as f:
                f.write(f"\n\n{content}")
        else:
            raise ValueError(f"不支持的写入模式：{mode}，仅支持 replace 和 append")

        # 正文变更也算节点变更 → 刷新行的 updated_at（顺带作废节点缓存，否则读到旧时间戳）
        # 两阶段建图：写入正文即把骨架节点转 filled；清空正文不改状态（否则误标"已填充"）
        now = datetime.now().isoformat()
        mark = content_status if (content or "").strip() else None
        with self._conn:
            if mark:
                self._conn.execute(
                    "UPDATE nodes SET updated_at = ?, content_status = ?"
                    " WHERE id = ? AND user_id = ?",
                    (now, mark, node_id, self.user_id),
                )
            else:
                self._conn.execute(
                    "UPDATE nodes SET updated_at = ? WHERE id = ? AND user_id = ?",
                    (now, node_id, self.user_id),
                )
        self.invalidate_content_cache(node_id)
        self._invalidate_cache()
        # P0-1 改正文即重索引：登记到 RAG 增量队列（检索里不该留旧正文）。
        if mark:
            mark_node_dirty(self.user_id, node_id)

    def set_content_status(self, node_id: str, status: str, caller: str = "human") -> None:
        """
        只改填充状态、**不碰正文**（小节化节点的正文在 manifest / 小节 MD 里）。

        为什么需要它：新概念由小节管线成文后，节点 MD 仍是骨架占位（小节节点没有主 MD），
        `update_node_content` 又只在"有正文"时才翻状态 —— 不走这一步，节点会永远停在
        `skeleton`：断点续填反复重跑、体检把它误判为空壳。

        参数:
            node_id: 节点 ID
            status:  CONTENT_STATUS_* 之一
            caller:  "human" / "ai"（AI 不得改人类创建的节点）

        异常:
            ValueError: 节点不存在
            PermissionError: AI 试图修改人类创建的节点
        """
        self._guard_human_content(caller, node_id)
        if self.get_node(node_id) is None:
            raise ValueError(f"节点不存在：{node_id}")
        with self._conn:
            self._conn.execute(
                "UPDATE nodes SET content_status = ?, updated_at = ? WHERE id = ? AND user_id = ?",
                (status, datetime.now().isoformat(), node_id, self.user_id),
            )
        self._invalidate_cache()

    def remove_skeleton_md(self, node_id: str) -> bool:
        """小节化成功后删除主 MD 的**骨架占位**（A4-3）。

        为什么：`create_node_with_content` 对无正文的新节点写入的 `{id}.md` 只是占位
        （`## 概述\n\n待完善...\n`，见 `ORIGIN_NOTES`）；正文经小节管线落进 `{id}/` 小节 MD
        后，这张单 MD 就只剩"同一节点两种形态并存"的冗余，且直读它会拿到"待完善..."。

        安全边界（只删**骨架**，绝不动老节点真实正文）：
        - 仅当节点**已小节化**（`has_sections`，内容已在小节里）才可能删；
        - 单 MD 必须含骨架占位符「待完善」才删 —— 老节点/有正文的单 MD 就算被小节化，
          也**保留原样**（那才是它的唯一内容）。
        - 幂等：单 MD 不存在（已删/本就无）→ False。

        返回 True 表示有文件被删除。是文件层操作，**不动 nodes/edges**（图谱检索仍只看 SQLite）。
        """
        if not self.has_sections(node_id):
            return False
        md_path = self.nodes_dir / f"{node_id}.md"
        if not md_path.is_file():
            return False
        try:
            text = md_path.read_text(encoding="utf-8")
        except OSError:
            return False
        if "待完善" not in text:
            return False
        try:
            md_path.unlink()
        except OSError:
            return False
        return True
