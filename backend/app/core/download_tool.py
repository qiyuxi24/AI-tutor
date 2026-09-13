"""资源下载工具实现（MCP 风格）：把 URL 指向的文档下载并入库到用户知识库。

与 web_tool.fetch_webpage 的分工（都抓 URL，但目的不同）：
- fetch_webpage    ：只读。网页正文 → 文本返回给模型看，不落盘。
- download_resource：留存。任意受支持格式（pdf/epub/docx/pptx/txt/html…）→
                     下载 bytes → 解析 → 分块 → 入知识库（KbManager），
                     之后可被 rag_search(source="kb") 检索到。

安全边界（SSRF 防护沿用 web_tool，唯一实现不重复）：
- 协议限 http/https；拒绝内网/回环/私有 IP
- 体积上限 max_mb（默认 50MB）：content-length 预检 + 流式累计双重校验
- 扩展名白名单 = kb 解析器注册表（is_supported），不支持则不入库半成品

工具 spec / 分发注册在 agent_tools._TOOL_SPECS（handler 薄壳调 download_resource）。
"""

import asyncio
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional
from urllib.parse import unquote, urlparse

import httpx

from app.core.error_codes import ErrorCode, log_error
from app.core.kb.parsers import is_supported, supported_label
from app.core.web_tool import is_blocked_url

logger = logging.getLogger("ai-tutor")

# 下载超时（秒）：电子书/PDF 比网页大，给得比 fetch_webpage 宽松
_DOWNLOAD_TIMEOUT = 60.0
# 体积上限（MB）：默认 50，硬上限 200（防模型传超大 max_mb 拖垮进程）
_DEFAULT_MAX_MB = 50
_MAX_MB_LIMIT = 200
# 流式读取的分片大小
_CHUNK_BYTES = 64 * 1024
# 入库落点：知识库根目录下的固定文件夹（与采集模块的「自动采集」并行不冲突）
_KB_FOLDER = "AI 下载"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36 TutorAgent/1.0",
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

# URL 无扩展名时的 content-type → 扩展名兜底（判名后仍需通过注册表白名单）
_CT_EXT = {
    "application/epub+zip": ".epub",
    "application/pdf": ".pdf",
    "application/x-mobipocket-ebook": ".mobi",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/xml": ".xml",
    "text/plain": ".txt",
    "text/markdown": ".md",
    "text/html": ".html",
}

# 同步→异步桥：与 rag_tool / mcp_host / kg_taxonomy 同一约定（各持单例池互不干扰）
_POOL = ThreadPoolExecutor(max_workers=1)


def _run_async(coro):
    """在同步上下文里跑协程（当前线程无事件循环则直接 run，否则丢线程池）。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    return _POOL.submit(asyncio.run, coro).result()


def _sanitize(name: str, max_len: int = 80) -> str:
    """文件名净化：去路径分隔符与系统非法字符（空则返回空串，由调用方兜底）。"""
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", (name or "").strip())
    name = re.sub(r"\s+", " ", name)
    return name[:max_len].strip()


def _resolve_filename(url: str, headers: httpx.Headers) -> str:
    """
    推断落库文件名：Content-Disposition → URL 路径 → 按 content-type 补扩展名。

    URL 形如 /download?id=7 时无文件名可依，此时靠 content-type 兜底（如 epub/pdf），
    最终仍要过解析器白名单；推不出来就返回空串，由调用方提示不支持入库。
    """
    name = ""
    cd = (headers.get("content-disposition") or "") if headers else ""
    m = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)", cd, re.I)
    if m:
        name = m.group(1).strip()
    if not name:
        name = Path(unquote(urlparse(url).path)).name
    name = _sanitize(name, max_len=120)

    if not Path(name).suffix:
        ctype = (headers.get("content-type") or "") if headers else ""
        name += _CT_EXT.get(ctype.split(";")[0].strip().lower(), "")
    return name


async def _save_to_kb(user_id: int, filename: str, content: bytes,
                      subject: str = "") -> int:
    """入库：AI 下载/AI 下载+学科 目录下建文件节点（解析 + 分块 + 索引）。"""
    from app.core.kb.kb_manager import kb_manager

    parts = [_KB_FOLDER]
    sub = _sanitize(subject, max_len=40)
    if sub:
        parts.append(sub)
    parent = kb_manager.ensure_folder(user_id, parts)
    return await kb_manager.upload_and_index(user_id, filename, content, parent)


def download_resource(url: str, title: str = "", subject: str = "",
                      user_id: Optional[int] = None,
                      max_mb: int = _DEFAULT_MAX_MB) -> str:
    """
    下载 URL 指向的文档并存入用户知识库（MCP 风格资源下载工具）。

    参数:
        url:      资源完整 URL（http/https）
        title:    显示名（可选）：给定则用作知识库文件名主体，如「傲慢与偏见」
        subject:  学科名（可选）：落「AI 下载/{学科}/」子目录便于归类
        user_id:  用户 ID（由 handler 从 kg 传入；None 时无法入库）
        max_mb:   体积上限（MB，默认 50，最大 200）

    返回:
        结果文本（成功=文件信息 + 节点 ID；各类失败=友好提示，不抛异常，
        让 LLM 直接向用户转述）。
    """
    if not url or not str(url).strip():
        return "下载失败：未提供 URL。"

    try:
        limit_mb = max(1, min(int(max_mb or _DEFAULT_MAX_MB), _MAX_MB_LIMIT))
    except (TypeError, ValueError):
        limit_mb = _DEFAULT_MAX_MB
    limit = limit_mb * 1024 * 1024

    blocked = is_blocked_url(url)
    if blocked:
        log_error(ErrorCode.WEB_FETCH_BLOCKED, detail=f"{url}: {blocked}")
        return f"无法下载：{blocked}。请提供一个公网 http/https 地址。"

    try:
        with httpx.Client(timeout=_DOWNLOAD_TIMEOUT, headers=_HEADERS,
                          follow_redirects=True) as client:
            with client.stream("GET", url) as resp:
                if resp.status_code != 200:
                    log_error(ErrorCode.WEB_FETCH_FAILED,
                              detail=f"HTTP {resp.status_code}: {url}")
                    return f"无法下载：HTTP 状态码 {resp.status_code}"

                size_hint = int(resp.headers.get("content-length") or 0)
                if size_hint > limit:
                    return (f"文件过大（约 {size_hint / 1048576:.1f}MB，"
                            f"上限 {limit_mb}MB），未下载。")

                filename = _resolve_filename(url, resp.headers)

                chunks: list[bytes] = []
                got = 0
                for chunk in resp.iter_bytes(_CHUNK_BYTES):
                    got += len(chunk)
                    if got > limit:
                        return f"文件超过 {limit_mb}MB 上限，已中止下载。"
                    chunks.append(chunk)
                content = b"".join(chunks)
    except httpx.TimeoutException as e:
        log_error(ErrorCode.WEB_FETCH_TIMEOUT, detail=str(e), context={"url": url})
        return f"下载超时：{url}。该资源可能过大或站点较慢，可稍后重试。"
    except httpx.HTTPError as e:
        log_error(ErrorCode.WEB_FETCH_FAILED, detail=str(e), context={"url": url})
        return f"下载失败：{str(e)}"
    except Exception as e:
        log_error(ErrorCode.WEB_FETCH_FAILED, detail=str(e), exception=e,
                  context={"url": url})
        return f"下载出错：{str(e)}"

    if not content:
        return "下载失败：内容为空。"

    if not filename or not is_supported(filename):
        return (f"文件已下载，但格式无法入库（推断文件名：{filename or '未知'}）。"
                f"当前支持：{supported_label()}")

    # 显示名优先：用 title 作文件名主体，保留原扩展名
    stem_ext = Path(filename).suffix
    clean_title = _sanitize(title, max_len=60)
    if clean_title:
        filename = clean_title + stem_ext

    if not user_id:
        return "下载失败：无法确定用户上下文，未入库。"

    try:
        node_id = _run_async(_save_to_kb(user_id, filename, content, subject))
    except ValueError as e:
        # 业务错：解析文本过短/不支持/解析失败（kb 侧已有友好文案）
        log_error(ErrorCode.WEB_FETCH_FAILED, detail=str(e), context={"url": url})
        return f"文件已下载，但入库失败：{e}"
    except Exception as e:
        log_error(ErrorCode.WEB_FETCH_FAILED, detail=str(e), exception=e,
                  context={"url": url})
        return f"文件已下载，但入库出错：{str(e)}"

    folder = _KB_FOLDER + (f"/{_sanitize(subject, 40)}" if _sanitize(subject, 40) else "")
    return (f"已下载并存入知识库：{filename}（{len(content) / 1048576:.2f}MB，"
            f"目录「{folder}」，文件节点 ID {node_id}）。"
            f"后续可用 rag_search(source=\"kb\") 检索其内容。")
