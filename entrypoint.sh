#!/bin/sh
# ════════════════════════════════════════════
# AI Tutor 容器启动脚本
# 同时拉起 Nginx（前端 + 反代）与 Uvicorn（后端 API）
# ════════════════════════════════════════════
set -e

# 启动 Nginx（前台模式运行，保持容器存活）
echo "[entrypoint] 启动 Nginx ..."
nginx -g "daemon off;" &
NGINX_PID=$!

# 启动后端（注意：必须单 worker —— 事件总线 / 速率限制是进程内状态，
# 多 worker 会导致图谱更新 SSE 推送不跨进程）
echo "[entrypoint] 启动 Uvicorn ..."
cd /app/backend
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1 &
UVICORN_PID=$!

# 等待后端就绪（最多 60s）
for i in $(seq 1 60); do
    if curl -fsS http://127.0.0.1:8000/api/health > /dev/null 2>&1; then
        echo "[entrypoint] 后端已就绪 ✓"
        break
    fi
    sleep 1
done

# 任一进程退出则整个容器退出（触发 restart 策略），兼容 dash 的轮询
while kill -0 $NGINX_PID 2>/dev/null && kill -0 $UVICORN_PID 2>/dev/null; do
    sleep 2
done

echo "[entrypoint] 服务进程退出，容器停止"
exit 1
