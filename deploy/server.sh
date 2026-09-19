#!/usr/bin/env bash
# TutorAgent 服务器端运维入口（deploy.ps1 每次部署自动更新本文件）
# 用法：./server.sh {up|restart|stop|logs|status}    # 不带参数 = status
#   up      重新构建并启动（改了代码/配置后用）
#   restart 只重启容器（不重建镜像）
cd "$(dirname "$0")/.." || exit 1
p=$(grep -E '^PORT=' .env 2>/dev/null | cut -d= -f2); p=${p:-8080}
case "${1:-status}" in
  up)      docker compose up -d --build ;;
  restart) docker compose restart ;;
  stop)    docker compose stop ;;
  logs)    docker logs -f ai-tutor ;;
  status)  docker compose ps; echo; curl -s -m 5 "http://127.0.0.1:$p/api/health"; echo ;;
  *)       echo "用法：$0 {up|restart|stop|logs|status}"; exit 1 ;;
esac
