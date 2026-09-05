# ═══════════════════════════════════════════════════════════════
# AI Tutor 多阶段构建镜像
# 产物：单容器 = Nginx(前端静态 + /api 反代) + Uvicorn(后端 API)
#
# 构建：docker compose build（或 docker build -t ai-tutor:latest .）
# 说明：pip 默认清华源；海外服务器构建慢可把 -i 参数改回官方源
# ═══════════════════════════════════════════════════════════════

# ──────────────────────────────────────────────
# Stage 1：构建前端静态资源
# ──────────────────────────────────────────────
FROM node:22-alpine AS frontend-build

WORKDIR /build

# 先拷贝依赖清单，充分利用层缓存
COPY frontend/package*.json ./
RUN npm config set registry https://registry.npmmirror.com \
    && npm install --no-audit --no-fund

# 拷贝源码并构建
COPY frontend/ ./
RUN npm run build

# ──────────────────────────────────────────────
# Stage 2：运行时镜像（Python 后端 + Nginx）
# ──────────────────────────────────────────────
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai

# 系统依赖：nginx（托管前端+反代）、curl（健康检查）、tzdata（时区）
RUN apt-get update \
    && apt-get install -y --no-install-recommends nginx curl tzdata \
    && rm -rf /var/lib/apt/lists/* \
    # 删除 Debian nginx 默认站点，避免与自定义配置的 listen 80 冲突
    && rm -f /etc/nginx/sites-enabled/default

WORKDIR /app

# 先拷贝依赖清单，利用层缓存
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install -i https://pypi.tuna.tsinghua.edu.cn/simple \
    -r /app/backend/requirements.txt

# 拷贝后端代码（.dockerignore 已排除 backend/data、.env、venv）
COPY backend/ /app/backend/

# 前端静态资源
COPY --from=frontend-build /build/dist /usr/share/nginx/html

# Nginx 配置 + 启动脚本（sed 兜底 Windows CRLF 换行问题）
COPY nginx.conf /etc/nginx/conf.d/default.conf
RUN sed -i 's/\r$//' /etc/nginx/conf.d/default.conf
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh && sed -i 's/\r$//' /entrypoint.sh

EXPOSE 80
CMD ["/entrypoint.sh"]
