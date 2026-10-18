#!/bin/bash
# ══════════════════════════════════════════════════════════════
#  AI Tutor 演示模式开关（宿主 nginx）
#    on      : 开放主站（免通行 Cookie）+ 关注册 + 在线页面/对话流各限 3
#    off     : 恢复原有「网关口令」行为
#    status  : 查看当前状态
#    restore : 把站点配置恢复成原始版本（应急用）
#  设计：演示模式 = /etc/nginx/tutor-demo.d/demo.conf 是否存在；
#        nginx -t 失败自动回滚，绝不带着坏配置 reload。
#  部署与语义见 deploy/tutor-demo/README.md
# ══════════════════════════════════════════════════════════════
set -u
DIR=/etc/nginx/tutor-demo.d
DEMO=$DIR/demo.conf
TPL=/root/tutor-demo/demo.conf.template
BASE=/root/tutor-demo/default.base.conf
ORIG=/root/tutor-demo/original-default.conf
SITE=/etc/nginx/sites-available/default
LOG=/tmp/tutor-demo-nginx-t.log

test_config() {
    if ! nginx -t >"$LOG" 2>&1; then
        echo "!!! nginx -t 失败："
        sed 's/^/    /' "$LOG"
        return 1
    fi
    return 0
}

reload() {
    systemctl reload nginx
    echo "    nginx 已 reload"
    # 优雅 reload 时旧 worker 仍会短暂处理存量连接，等 1.5s 再取验证码，避免误判
    sleep 1.5
}

do_on() {
    if [ ! -f "$TPL" ]; then echo "!!! 缺少模板 $TPL"; exit 1; fi
    mkdir -p "$DIR"
    if [ -f "$DEMO" ]; then
        echo "演示模式已经是开启状态（$DEMO 已存在）"
        return 0
    fi
    cp "$TPL" "$DEMO"
    if ! test_config; then
        rm -f "$DEMO"
        echo "!!! 演示模式未开启（已回滚）"
        exit 1
    fi
    reload
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1/)
    echo "演示模式已开启：/ → HTTP $code（200 = 免口令直达）"
    echo "   · 注册已关闭：POST /api/v1/auth/register → 403"
    echo "   · 在线页面上限 3（实时通道）+ 并发对话上限 3"
    echo "   · /admin 与 /gate 仍需口令"
}

do_off() {
    if [ ! -f "$DEMO" ]; then
        echo "演示模式本来就是关闭的"
        return 0
    fi
    rm -f "$DEMO"
    if ! test_config; then
        cp "$TPL" "$DEMO"
        echo "!!! nginx -t 失败，已改回开启状态，未 reload"
        exit 1
    fi
    reload
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1/)
    echo "演示模式已关闭：/ → HTTP $code（302 = 已恢复门口口令）"
}

do_status() {
    echo "站点配置 : $SITE"
    if [ -f "$DEMO" ]; then echo "演示模式 : 开启（$DEMO）"; else echo "演示模式 : 关闭（无 $DEMO）"; fi
    echo -n "本机 /   : "; curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' --max-time 5 http://127.0.0.1/ || true
    echo -n "网关口令 : "; curl -s -o /dev/null -w '%{http_code}\n' --max-time 5 http://127.0.0.1/gate || true
    echo -n "健康检查 : "; docker inspect --format '{{.State.Health.Status}}' ai-tutor 2>/dev/null || echo "n/a"
    echo -n "nginx    : "; systemctl is-active nginx
}

do_restore() {
    if [ ! -f "$ORIG" ]; then echo "!!! 缺少原始配置 $ORIG"; exit 1; fi
    rm -f "$DEMO"
    cp "$ORIG" "$SITE"
    if ! test_config; then
        cp "$BASE" "$SITE"
        echo "!!! 原始配置 nginx -t 失败，已恢复演示版基础配置，未 reload"
        exit 1
    fi
    reload
    echo "已恢复原始站点配置（演示开关文件已删除）"
}

case "${1:-status}" in
    on)      do_on ;;
    off)     do_off ;;
    status)  do_status ;;
    restore) do_restore ;;
    *) echo "用法: $0 {on|off|status|restore}"; exit 2 ;;
esac
