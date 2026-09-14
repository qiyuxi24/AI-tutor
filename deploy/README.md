# TutorAgent Docker 部署指南

本项目已内置 Docker 化部署，一条命令即可在服务器上跑起来。

## 架构

```
浏览器 ──HTTP──▶ Nginx(容器内 :80)
                 ├── /           → 前端静态资源（Vue 构建产物 dist）
                 └── /api/*      → 反代到 Uvicorn(容器内 :8000) FastAPI
                                     ├── SQLite 知识图谱 / 对话 / 画像
                                     ├── 上传知识库 RAG + Whoosh BM25
                                     └── LLM API（阿里云百炼，出网）
```

- **单容器**：Nginx + Uvicorn 同镜像，`docker compose up -d` 一键启动
- **数据持久化**：通过 volume 挂载宿主机目录，重建容器数据不丢
- **SSE 流式对话**：Nginx 已关闭 proxy_buffering，token 实时推送

## 部署文件清单

| 文件 | 作用 |
|------|------|
| `Dockerfile` | 多阶段构建（node 构建前端 → python 运行时） |
| `docker-compose.yml` | 服务编排、端口、环境变量、数据卷、健康检查 |
| `nginx.conf` | 静态托管 + `/api` 反代 + SSE 支持 |
| `entrypoint.sh` | 容器启动脚本（拉起 Nginx + Uvicorn） |
| `.dockerignore` | 构建上下文排除（密钥/运行时数据/本地依赖） |
| `.env.example` | 部署配置模板（复制为 `.env`） |

---

## 一、本地验证（Windows/Mac 均可）

```powershell
# 1. 配置环境变量
Copy-Item .env.example .env
# 编辑 .env，填入 DASHSCOPE_API_KEY 和 SECRET_KEY（SECRET_KEY 用 openssl rand -hex 32 生成）

# 2. 构建并启动（首次构建较慢，需下载依赖）
docker compose up -d --build

# 也可用仓库根目录的一键脚本：自动判断要不要重建、等健康检查、打印访问地址
#   .\start-docker.ps1            日常（仅当源码比镜像新才重建）
#   .\start-docker.ps1 -Force     强制重建
#   .\start-docker.ps1 -Port 8081 换端口

# 3. 验证
docker compose ps                    # 状态应为 healthy
curl http://localhost:8080/api/health
# 浏览器打开 http://localhost:8080  → 应看到登录页
# 若配置了 DEFAULT_ADMIN_PASSWORD，首次登录用 admin / 该密码

# 4. 查看日志
docker compose logs -f

# 5. 停止 / 清理
docker compose down                  # 停止（数据保留在挂载目录）
docker compose down -v               # 停止并删除数据卷
```

---

## 二、服务器部署步骤（阿里云 / 腾讯云 Ubuntu 为例）

### 1. 安装 Docker 与 Compose

```bash
# 一键安装脚本（Ubuntu/Debian）
curl -fsSL https://get.docker.com | sh
# 或按云厂商文档安装；国内服务器建议同时配置 Docker 镜像加速器（见下文）

sudo systemctl enable --now docker
docker --version
docker compose version
```

### 2. 上传代码

方式 A（推荐，代码在 Git 仓库）：

```bash
cd /opt
git clone <你的仓库地址> ai-tutor
cd ai-tutor
```

方式 B（本地打包上传）：

```bash
# 本地：git archive 或直接 scp 源码目录（排除 venv/node_modules/data 等大目录）
```

### 3. 配置环境变量

```bash
cd /opt/ai-tutor
cp .env.example .env
# 编辑 .env：
#   DASHSCOPE_API_KEY=<你的百炼 Key>
#   SECRET_KEY=<openssl rand -hex 32>
#   PORT=80          # 若服务器 80 端口空闲，直接用 80 免端口号访问
#   DEFAULT_ADMIN_PASSWORD=<初始管理员密码>
```

### 4. 迁移本地数据（可选，首次部署可跳过）

把本地已有的用户数据传到服务器**对应挂载目录**：

```bash
# 本地生成：
mkdir -p data/knowledge data/conversations data/profiles backend/data

# 本地执行（PowerShell）：
scp -r data/knowledge    root@服务器IP:/opt/ai-tutor/data/
scp -r data/conversations root@服务器IP:/opt/ai-tutor/data/
scp -r data/profiles     root@服务器IP:/opt/ai-tutor/data/
scp -r backend/data      root@服务器IP:/opt/ai-tutor/backend/
```

> 注意：`data/prompts`（提示词模板）随镜像走，**不要**从本地覆盖，否则容器内模板与代码版本不一致。

### 5. 构建并启动

```bash
docker compose up -d --build
docker compose ps        # 等待 ai-tutor 变为 healthy（首次启动约 30s）
```

### 6. 访问

- 配置了 `PORT=80` → `http://服务器IP`
- 默认 8080 → `http://服务器IP:8080`
- 防火墙/安全组记得放行对应端口

---

## 三、HTTPS（域名 + 证书）

生产环境建议用域名 + HTTPS：

```bash
# 方案 A：云厂商 CDN/负载均衡 + 免费证书（阿里云/腾讯云控制台操作，推荐）
# 方案 B：Let's Encrypt 自动签证书（服务器已有域名时）
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com
```

HTTPS 与容器无冲突——Nginx 反代到容器内已是 `http://127.0.0.1:8000`，前端走相对路径 `/api`，不需要改任何代码。

---

## 四、运维常见问题

### Q1: `docker compose up` 报错 "DASHSCOPE_API_KEY 未配置"
根目录 `.env` 没建或没填。复制 `.env.example` 为 `.env` 并填入。

### Q2: 容器一直 restarting / unhealthy
```bash
docker compose logs ai-tutor
```
常见原因：
- `SECRET_KEY` 为空或太短（后端拒绝启动）
- API Key 失效（`/api/health` 本身不依赖 Key，看聊天时日志）
- 挂载目录权限不足：`chown -R 1000:1000 data backend/data`（容器内以 root 运行一般无此问题）

### Q3: 为什么必须单 worker？
事件总线（图谱更新后向前端 SSE 推送）与速率限制是**进程内状态**。`--workers 1` 是硬约束，entrypoint.sh 已固定，勿改。

### Q4: 国内服务器拉取基础镜像慢？
Docker Hub 镜像加速（二选一）：
```bash
# 编辑 /etc/docker/daemon.json
{
  "registry-mirrors": ["https://docker.m.daocloud.io", "https://mirror.ccs.tencentyun.com"]
}
sudo systemctl restart docker
```
构建时 npm/pip 已默认走国内镜像源（npmmirror / 清华），构建本身不会慢在依赖下载上。

### Q5: 更新版本
```bash
git pull
docker compose up -d --build --no-deps
```

### Q6: 备份数据
只需备份挂载的四个目录即可（SQLite 文件可在线复制，最稳先停容器）：

```bash
docker compose stop
tar czf ai-tutor-backup-$(date +%F).tar.gz data backend/data
docker compose start
```

### Q7: 上传大文件失败？
Nginx 已配置 `client_max_body_size 100m`。超过 100MB 的文档请先压缩或拆分。

---

## 五、定制建议

- **不需要 OCR**：注释掉 `requirements.txt` 中 `rapidocr-onnxruntime` / `onnxruntime` 可显著减小镜像并加速构建（图片 OCR 功能将不可用）
- **海外服务器**：构建慢可将 `Dockerfile` 中 pip 的 `-i https://pypi.tuna.tsinghua.edu.cn/simple` 移除，改用官方源
- **拆分容器**：中小项目单容器够用；如需独立扩展，可将 `backend` 与 `frontend(nginx)` 拆为 compose 两个服务
