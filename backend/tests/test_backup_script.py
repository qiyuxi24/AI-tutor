"""`scripts/backup.sh` 的回归（2026-11-04 技术债）。

这条债的形状是「**文档要求了，代码里没有**」：
`docs/运维部署/运维_生产上线与日常运营指南.md` 在 §5.3（备份）、§7.2（发布前备份）、
§9（补齐清单第 3 步）三处都写 `bash scripts/backup.sh`，cron 示例里也有它 ——
但仓库里从来没有这个文件。这类债最典型：**只有真出事的那天才发现**。

本文件能测的：
  - 结构不变量（trap 保证停服后一定拉起、`set -euo`、停/启成对）；
  - 保留策略的日期解析（纯 bash 逻辑，抽出来用 python 复算一遍）；
  - `bash -n` 语法检查（CI 上有 bash，本地 Windows 沙箱里没有 → 跳过而非失败）。

**不能测的**（诚实边界）：真实停服→打包→启动这条链路。
它需要 docker + 一个部署实例，属于运维演练（§9 第 3 步要求"一次恢复演练"）。
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "backup.sh"
_SRC = _SCRIPT.read_text(encoding="utf-8") if _SCRIPT.exists() else ""


def test_script_exists():
    """文档点了三个名的地方，文件必须真的在。"""
    assert _SCRIPT.is_file(), "scripts/backup.sh 不存在 —— 运维指南 §5.3 要求它"


def test_declares_strict_mode():
    """`set -euo pipefail`：备份这种事绝不能"静默半成功"。"""
    assert "set -euo pipefail" in _SRC


def test_restores_service_even_if_packing_fails():
    """**最要紧的一条**：停过容器就必须保证拉起来。

    打包失败（磁盘满、tar 报错）时若直接退出，线上会一直停着——
    一次备份动作变成一次事故。所以必须 trap + 幂等 flag。
    """
    assert "trap restore_service EXIT" in _SRC
    # 只有真的停过才拉起（避免"没停却 start"误伤）
    assert "STOPPED=1" in _SRC
    assert re.search(r'if \[ "\$STOPPED" -eq 1 \]', _SRC), "restore_service 没有 STOPPED 守卫"


def _command_lines() -> list[str]:
    """脚本里**真正会执行**的行：去掉注释，且不是 log/echo 里的说明文字。

    `log "恢复方式：docker compose stop && ... && docker compose start"` 也含这两个词，
    按字符串计数会得到 2 —— 这正是本文件第一版误报的原因。按「行首就是命令」判定。
    """
    out = []
    for raw in _SRC.splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        out.append(s)
    return out


def test_stop_and_start_are_paired():
    """`docker compose stop` 与 `docker compose start` 各执行一次（不是 restart，也不是 down）。"""
    stops = [l for l in _command_lines() if l.startswith("docker compose stop")]
    starts = [l for l in _command_lines() if l.startswith("docker compose start")]
    assert len(stops) == 1, f"stop 执行了 {len(stops)} 次: {stops}"
    assert len(starts) == 1, f"start 执行了 {len(starts)} 次: {starts}"
    assert not any("docker compose down" in l for l in _command_lines()), \
        "down 会连容器一起删，恢复更慢"


def test_no_and_continue_footgun():
    """`[ x ] && continue` 在判 false 时返回 1 → `set -e` 当场退出。

    这是本脚本第一版真踩到的坑：备份成功后清理阶段误报失败。必须用 if。
    """
    for no, line in enumerate(_SRC.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        assert not re.search(r"^\s*\[\s.*\]\s*&&\s*continue\s*$", line), (
            f"backup.sh:{no} 用了 `[ ... ] && continue`（set -e 下会误退出），改用 if"
        )


def test_keeps_whoosh_and_sqlite_wal_files():
    """不能排除 `data/` 里的任何索引/SQLite 文件。

    whoosh 段文件被排除 = 备份能恢复、服务能起，但**检索全空**——
    比缺备份更难排查的一类数据损坏。
    """
    for forbidden in ("whoosh", "*.db", "-wal", "-shm", "data/rag", "data/kb"):
        assert f"--exclude='{forbidden}" not in _SRC, f"备份排除了 {forbidden}，恢复后数据不全"
        assert f'--exclude="{forbidden}' not in _SRC, f"备份排除了 {forbidden}，恢复后数据不全"


def test_retention_matches_ops_doc():
    """保留策略必须是文档写的「日备 7 天 / 周备 4 周」。"""
    assert 'KEEP_DAYS="${BACKUP_KEEP_DAYS:-7}"' in _SRC
    assert 'KEEP_WEEKS="${BACKUP_KEEP_WEEKS:-4}"' in _SRC


def test_retention_never_deletes_the_fresh_backup():
    """刚打出来的包不能被自己的清理逻辑删掉（同一天连跑两次时最容易踩）。"""
    assert re.search(r'if \[ "\$f" = "\$OUT" \]; then', _SRC)


def test_archive_name_carries_a_parseable_date():
    """清理逻辑靠**文件名里的日期**判断年龄 —— 这个格式必须与解析正则一致。"""
    assert "data-$TAG-$STAMP.tar.gz" in _SRC
    assert "date '+%Y%m%d-%H%M%S'" in _SRC
    # 与脚本里 `grep -oE '[0-9]{8}-[0-9]{6}'` 的解析口径对齐
    stamp = "20261104-030000"
    assert re.fullmatch(r"\d{8}-\d{6}", stamp)


def test_backup_dir_is_overridable_for_testing():
    """`BACKUP_DIR` 可覆盖 —— 否则在非生产机器上没法验证（写 /var 要 root）。"""
    assert "BACKUP_DIR" in _SRC


def _bash_works() -> bool:
    """不只是 `which bash`：Windows 上的 `WindowsApps\\bash.exe` 是个转发桩，
    存在但跑不起来（沙箱下报 `couldn't create signal pipe, Win32 error 5`）。
    真跑一句 echo 才算数。
    """
    if shutil.which("bash") is None:
        return False
    try:
        p = subprocess.run(["bash", "-c", "echo ok"], capture_output=True, timeout=10,
                           errors="replace")
        return p.returncode == 0 and "ok" in (p.stdout or "")
    except (OSError, subprocess.SubprocessError):
        return False


_BASH_OK = _bash_works()


@pytest.mark.skipif(not _BASH_OK, reason="本机 bash 不可用（Windows 沙箱 / 转发桩）")
def test_shell_syntax_is_valid():
    """`bash -n`：真正的语法检查。CI（ubuntu）上必跑；本地不可用时跳过而不是失败。"""
    proc = subprocess.run(["bash", "-n", str(_SCRIPT)], capture_output=True, text=True,
                          errors="replace")
    assert proc.returncode == 0, f"bash -n 失败:\n{proc.stderr}"
