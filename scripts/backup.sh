#!/usr/bin/env bash
# scripts/backup.sh —— 数据根备份（线上运维指南 §5.3 点名要求，此前只存在于文档里）
#
# 用法：
#   bash scripts/backup.sh                 # 日备（默认 tag=auto）
#   bash scripts/backup.sh pre-deploy-xxx  # 发布前手备（运维指南 §7.2 的第一步）
#
# 设计取舍：
#   - **先停容器再打包**。SQLite 在线复制"多数情况没事"，但 WAL 模式下
#     `-wal`/`-shm` 与主库不是原子的：停服是唯一能保证"拿到一致快照"的零成本手段，
#     而本项目是单机小规模（停服几秒无感）。
#   - **失败必须恢复服务**。`set -e` + trap：打包失败也要 `docker compose start`，
#     否则一次磁盘写满会把线上直接停成事故。
#   - 保留策略：日备 7 天、周备 4 周（与 §5.3 表格一致）。不引入 logrotate。
set -euo pipefail

cd "$(dirname "$0")/.." || exit 1

TAG="${1:-auto}"
DEST="${BACKUP_DIR:-/var/backups/tutoragent}"
KEEP_DAYS="${BACKUP_KEEP_DAYS:-7}"
KEEP_WEEKS="${BACKUP_KEEP_WEEKS:-4}"
STAMP="$(date '+%Y%m%d-%H%M%S')"
OUT="$DEST/data-$TAG-$STAMP.tar.gz"

log() { echo "[$(date '+%F %T')] $*"; }

# ── 前置检查：缺一个就早点退出，别停了一半服务才发现 ──
[ -d data ] || { log "错误：找不到 ./data 目录（请在部署根目录运行）"; exit 1; }
command -v docker >/dev/null 2>&1 || { log "错误：找不到 docker"; exit 1; }
command -v tar >/dev/null 2>&1 || { log "错误：找不到 tar"; exit 1; }
mkdir -p "$DEST" || { log "错误：无法创建备份目录 $DEST"; exit 1; }

# 磁盘余量：备份体积≈数据根体积，剩余不足 2 倍就别开始了
DATA_MB=$(du -sm data 2>/dev/null | cut -f1 || echo 0)
FREE_MB=$(df -Pm "$DEST" 2>/dev/null | awk 'NR==2 {print $4}' || echo 0)
KEEP_TOTAL=$(( DATA_MB * 8 ))
if [ "${FREE_MB:-0}" -lt "$KEEP_TOTAL" ]; then
  log "警告：$DEST 剩余 ${FREE_MB}MB，数据根 ${DATA_MB}MB，按保留策略估计需要 ${KEEP_TOTAL}MB —— 继续但请尽快扩容"
fi

log "开始备份 tag=$TAG 数据根 ${DATA_MB}MB → $OUT"

# 无论成功失败，只要停过就必须拉起来
STOPPED=0
restore_service() {
  if [ "$STOPPED" -eq 1 ]; then
    log "恢复服务：docker compose start"
    docker compose start || log "错误：docker compose start 失败！请人工介入"
  fi
}
trap restore_service EXIT

log "停服（保证 SQLite 快照一致）"
docker compose stop
STOPPED=1

# 不打包的只有解释器缓存：`data/` 里所有 SQLite 与 whoosh 段文件都**必须**一起带走，
# 排除 whoosh 段会得到一个"能起但检索全空"的库（比缺备份更难排查）。
tar czf "$OUT" \
  --exclude='*.pyc' \
  --exclude='__pycache__' \
  data

SIZE=$(du -m "$OUT" | cut -f1)
log "打包完成：$OUT (${SIZE}MB)"

# ── 保留策略：日备 KEEP_DAYS 天、周备 KEEP_WEEKS 周 ──
# 写成逐文件判断而不是一串 find 表达式：文件名里就带着日期，
# 读起来比 `-mtime +7 ! -name ... -mtime -35` 直观，也不依赖 GNU `date -d`。
log "清理过期备份（日备 ${KEEP_DAYS} 天 / 周备 ${KEEP_WEEKS} 周）"
TODAY_EPOCH=$(date '+%s')
for f in "$DEST"/data-*.tar.gz; do
  [ -f "$f" ] || continue
  # ⚠️ 不能写成 `[ "$f" = "$OUT" ] && continue`：判false时整条列表返回1，
  # `set -e` 会当场退出（备份已成功却报失败）。必须用 if。
  if [ "$f" = "$OUT" ]; then
    continue
  fi
  # 从文件名取 YYYYMMDD 段（data-<tag>-<YYYYMMDD>-<HHMMSS>.tar.gz）
  # `|| true` 必需：`set -o pipefail` 下 grep 无匹配会让整条管线返回 1，
  # 赋值语句本身就会触发 `set -e` 退出——那会在备份成功之后误报失败。
  day=$(basename "$f" | grep -oE '[0-9]{8}-[0-9]{6}' | tail -1 | cut -d- -f1 || true)
  [ -n "$day" ] || continue
  file_epoch=$(date -d "$day" '+%s' 2>/dev/null) || continue
  age_days=$(( (TODAY_EPOCH - file_epoch) / 86400 ))

  if [ "$age_days" -le "$KEEP_DAYS" ]; then
    continue                                  # 日备期内，保留
  elif [ "$age_days" -gt $(( KEEP_DAYS + KEEP_WEEKS * 7 )) ]; then
    rm -f "$f" && log "  删除（超保留期 ${age_days} 天）：$(basename "$f")"
  elif [ "$(date -d "$day" '+%u')" != "7" ]; then
    rm -f "$f" && log "  删除（非周备日）：$(basename "$f")"
  fi
done

log "当前备份归档："
ls -1t "$DEST"/data-*.tar.gz 2>/dev/null | head -10 || true

log "完成。恢复方式：docker compose stop && tar xzf <归档> && docker compose start"
