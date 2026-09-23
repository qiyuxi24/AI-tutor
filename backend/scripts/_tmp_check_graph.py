"""一次性排查脚本（跑完即删）：定位 fetch_webpage 被超时掐断的根因，输出写 UTF-8 文件。"""
import sqlite3
from datetime import datetime
from pathlib import Path

repo = Path(__file__).resolve().parents[2]
out = repo / "backend" / "_tmp_diag.txt"
lines: list[str] = []


def w(s: str = "") -> None:
    lines.append(s)


dbg = repo / "backend" / "app" / "data" / "agent_debug" / "debug_log.db"
c = sqlite3.connect(f"file:{dbg}?mode=ro", uri=True)
c.row_factory = sqlite3.Row

w("=== 含 fetch_webpage 的调试日志 ===")
for r in c.execute("SELECT * FROM agent_debug_logs WHERE data LIKE '%fetch_webpage%'"
                   " ORDER BY ts").fetchall():
    d = dict(r)
    w(f"[{datetime.fromtimestamp(d['ts']):%m-%d %H:%M:%S}] scope={d['scope']}"
      f" event={d['event']} level={d['level']}")
    w(f"  message: {d['message']}")
    w(f"  data   : {d['data']}")
    w()

w("=== 最近 20 条 tool_result（各工具耗时/成功与否）===")
for r in c.execute("SELECT ts, level, message, data FROM agent_debug_logs"
                   " WHERE event='tool_result' ORDER BY ts DESC LIMIT 20").fetchall():
    d = dict(r)
    w(f"[{datetime.fromtimestamp(d['ts']):%m-%d %H:%M:%S}] {d['level']} {d['message']}"
      f" | {d['data']}")
c.close()

w()
w("=== 当前进程里的超时配置 ===")
try:
    from app.core.agent.loop import AGENT_TOOL_TIMEOUT_SECS
    w(f"AGENT_TOOL_TIMEOUT_SECS = {AGENT_TOOL_TIMEOUT_SECS}")
except Exception as e:
    w(f"import loop 失败: {e}")

try:
    from app.core.agent_tools.registry import _TOOL_SPECS
    for s in _TOOL_SPECS:
        if s.get("name") in ("fetch_webpage", "download_resource"):
            w(f"{s['name']}.timeout_secs = {s.get('timeout_secs')!r}")
except Exception as e:
    w(f"import registry 失败: {e}")

try:
    from app.core.config import settings
    for k in dir(settings):
        if "timeout" in k.lower():
            w(f"settings.{k} = {getattr(settings, k)!r}")
except Exception as e:
    w(f"import settings 失败: {e}")

out.write_text("\n".join(lines), encoding="utf-8")
print("written:", out)
